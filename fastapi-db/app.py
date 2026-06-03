import os
import io
import secrets
import logging
from flask import Flask, request, jsonify, render_template
from sqlalchemy import create_engine, Column, Integer, String, LargeBinary, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql import func
from werkzeug.utils import secure_filename
from PIL import Image

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = Flask(__name__)

# Configure upload limits (10MB max)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Database URL setup
# Fallback logic securely resolved (does not store credentials in code)
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    # TODO(security): Secrets should be fetched from secure management solutions in production
    logging.warning("DATABASE_URL environment variable is missing. Checking fallback configuration.")
    # Standard default local database path (useful for local development)
    DATABASE_URL = "postgresql://myuser:mypassword@localhost:5432/fastapidb"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class CameraImage(Base):
    __tablename__ = "camera_images"
    
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    image_data = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# Create tables with a retry loop to wait for database readiness
import time
db_initialized = False
for i in range(10):
    try:
        Base.metadata.create_all(bind=engine)
        logging.info("Database tables verified/created successfully.")
        db_initialized = True
        break
    except Exception as e:
        logging.warning(f"Database not ready yet (attempt {i+1}/10): {str(e)}. Retrying in 2 seconds...")
        time.sleep(2)

if not db_initialized:
    logging.error("Failed to initialize database tables after 10 attempts.")

# Frontend route
@app.route('/')
def index():
    return render_template('index.html')

# Upload image route
@app.route('/upload', methods=['POST'])
def upload_image():
    # 1. Check if the file part is present in request
    if 'image' not in request.files:
        return jsonify({"error": "No image part in the request"}), 400
        
    file = request.files['image']
    
    # 2. Check if file is empty
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
        
    # 3. Check allowed extensions
    if not allowed_file(file.filename):
        return jsonify({"error": "File type not allowed"}), 400

    try:
        # Read file content into memory for validation
        file_data = file.read()
        
        # 4. Enforce file size limit check in memory in addition to MAX_CONTENT_LENGTH
        if len(file_data) > app.config['MAX_CONTENT_LENGTH']:
            return jsonify({"error": "File size exceeds limit"}), 413
        
        # 5. Image Structure Validation using Pillow
        # This verifies that the bytes represent a valid image structure.
        try:
            img = Image.open(io.BytesIO(file_data))
            img.verify()  # Verifies the image data format (stops parsing if corrupt)
        except Exception as img_err:
            logging.warning(f"Image validation failed: {str(img_err)}")
            return jsonify({"error": "Invalid image structure"}), 400
        
        # 6. Sanitize filename using secure_filename
        # Using path.basename to ensure no traversal, and secure_filename to strip bad chars.
        safe_name = secure_filename(os.path.basename(file.filename))
        if not safe_name:
            # Fallback to a safe name using a random hash
            safe_name = f"image_{secrets.token_hex(8)}.{img.format.lower() if img.format else 'jpg'}"

        # 7. Parameterized Insert via SQLAlchemy ORM
        db = SessionLocal()
        try:
            new_image = CameraImage(filename=safe_name, image_data=file_data)
            db.add(new_image)
            db.commit()
            db.refresh(new_image)
            logging.info(f"Successfully stored image ID {new_image.id} ({safe_name}) in the database.")
            return jsonify({
                "message": "Image uploaded successfully",
                "image_id": new_image.id,
                "filename": safe_name
            }), 201
        except Exception as db_err:
            db.rollback()
            logging.error(f"Database error during image insertion: {str(db_err)}")
            # TODO(security): Do not expose SQL errors to the user
            return jsonify({"error": "Failed to store image in the database"}), 500
        finally:
            db.close()
            
    except Exception as e:
        logging.error(f"Unexpected error during upload: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500

# JSON API list images
@app.route('/api/images', methods=['GET'])
def list_images():
    db = SessionLocal()
    try:
        images = db.query(CameraImage).order_by(CameraImage.created_at.desc()).all()
        return jsonify({
            "images": [
                {"id": img.id, "filename": img.filename, "created_at": img.created_at.isoformat() if img.created_at else None}
                for img in images
            ]
        })
    except Exception as e:
        logging.error(f"Database error listing images: {str(e)}")
        return jsonify({"error": "Failed to fetch images list"}), 500
    finally:
        db.close()

# Specific image retrieval endpoint (serves the binary image)
@app.route('/images/<int:image_id>', methods=['GET'])
def get_image(image_id):
    db = SessionLocal()
    try:
        img = db.query(CameraImage).filter(CameraImage.id == image_id).first()
        if not img:
            return jsonify({"error": "Image not found"}), 404
        
        # Determine image format based on file extension
        # Map common extensions to MIME types
        ext = img.filename.rsplit('.', 1)[1].lower() if '.' in img.filename else 'jpg'
        mime_types = {
            'png': 'image/png',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'gif': 'image/gif',
            'webp': 'image/webp'
        }
        mime = mime_types.get(ext, 'image/jpeg')

        from flask import send_file
        return send_file(
            io.BytesIO(img.image_data),
            mimetype=mime,
            as_attachment=False  # Show in browser rather than forcing download
        )
    except Exception as e:
        logging.error(f"Database error retrieving image: {str(e)}")
        return jsonify({"error": "Failed to retrieve image"}), 500
    finally:
        db.close()

# Image deletion endpoint
@app.route('/api/delete/<int:image_id>', methods=['POST'])
def delete_image(image_id):
    db = SessionLocal()
    try:
        img = db.query(CameraImage).filter(CameraImage.id == image_id).first()
        if not img:
            return jsonify({"error": "Image not found"}), 404
        
        db.delete(img)
        db.commit()
        logging.info(f"Image ID {image_id} deleted successfully.")
        return jsonify({"message": f"Image ID {image_id} deleted successfully"})
    except Exception as e:
        db.rollback()
        logging.error(f"Database error deleting image: {str(e)}")
        return jsonify({"error": "Failed to delete image"}), 500
    finally:
        db.close()

if __name__ == '__main__':
    host = os.environ.get("FLASK_RUN_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_RUN_PORT", 5000))
    app.run(host=host, port=port)
