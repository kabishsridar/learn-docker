import os
import io
import time
import base64
import logging
import requests
from fastapi import FastAPI, Request, File, UploadFile, HTTPException, Depends
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, Column, Integer, String, LargeBinary, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.sql import func
from PIL import Image

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = FastAPI(
    title="ImageVault - Object Detection Dashboard",
    description="FastAPI application connected to PostgreSQL and YOLOv11 detection service.",
    version="1.0.0"
)

# Mount static and templates folders
# Ensure directories exist
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Configure upload limits (10MB max)
MAX_FILE_SIZE = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def allowed_file(filename: str) -> bool:
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Database setup
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://myuser:mypassword@db:5432/fastapidb")
YOLO_SERVICE_URL = os.environ.get("YOLO_SERVICE_URL", "http://yolo-service:8001/detect")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# SQLAlchemy Model
class DetectorImage(Base):
    __tablename__ = "detector_images"
    
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    raw_image = Column(LargeBinary, nullable=False)
    processed_image = Column(LargeBinary, nullable=True)
    detected_objects = Column(String(1000), nullable=True)  # Comma-separated labels
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# Database dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Create tables with a retry loop to wait for PostgreSQL readiness
db_initialized = False
for i in range(12):
    try:
        Base.metadata.create_all(bind=engine)
        logging.info("Database tables verified/created successfully.")
        db_initialized = True
        break
    except Exception as e:
        logging.warning(f"Database not ready yet (attempt {i+1}/12): {str(e)}. Retrying in 2.5 seconds...")
        time.sleep(2.5)

if not db_initialized:
    logging.error("Failed to initialize database tables. Exiting.")

# --- 1. WEB PAGES ---

@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# --- 2. UPLOAD & DETECTION PIPELINE ---

@app.post("/upload")
async def upload_and_detect(file: UploadFile = File(...), db: Session = Depends(get_db)):
    # 1. Validate file format and size
    if not file.filename or not allowed_file(file.filename):
        raise HTTPException(status_code=400, detail="File extension not allowed. Use PNG, JPG, JPEG, WEBP, or GIF.")
        
    try:
        # Read file content into memory
        file_bytes = await file.read()
        
        if len(file_bytes) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="File size exceeds the 10MB limit.")
            
        # 2. Image Structure Validation using Pillow
        try:
            img = Image.open(io.BytesIO(file_bytes))
            img.verify()  # Verifies file format integrity
        except Exception as img_err:
            logging.warning(f"Uploaded file structure validation failed: {str(img_err)}")
            raise HTTPException(status_code=400, detail="Invalid image structure.")
            
        # Re-open image since verify() closes the file pointer
        img = Image.open(io.BytesIO(file_bytes))
        
        # 3. Create initial database record with original image
        safe_name = os.path.basename(file.filename)
        # Clean double quotes and bad chars from name
        safe_name = "".join(c for c in safe_name if c.isalnum() or c in "._-")
        if not safe_name:
            import secrets
            safe_name = f"capture_{secrets.token_hex(6)}.{img.format.lower() if img.format else 'png'}"
            
        new_record = DetectorImage(
            filename=safe_name,
            raw_image=file_bytes,
            processed_image=None,
            detected_objects=""
        )
        db.add(new_record)
        db.commit()
        db.refresh(new_record)
        
        # 4. Request object detection from YOLO Service
        try:
            # We must send the original file bytes as a file upload to the YOLO microservice
            files_payload = {'file': (safe_name, file_bytes, f"image/{img.format.lower() if img.format else 'png'}")}
            response = requests.post(YOLO_SERVICE_URL, files=files_payload, timeout=15)
            
            if response.status_code == 200:
                det_result = response.json()
                
                # Extract base64 encoded processed image
                base64_image = det_result.get("annotated_image", "")
                detections = det_result.get("detections", [])
                detected_labels = det_result.get("detected_labels", [])
                
                # Decode annotated image back to raw bytes
                processed_bytes = base64.b64decode(base64_image)
                
                # Parse labels into a clean format
                detected_str = ", ".join(detected_labels) if detected_labels else "No objects detected"
                
                # Update DB record with processed image & metadata
                new_record.processed_image = processed_bytes
                new_record.detected_objects = detected_str
                db.commit()
                db.refresh(new_record)
                
                logging.info(f"Image ID {new_record.id} successfully processed. Objects: {detected_str}")
                
                return {
                    "message": "Image captured and processed successfully",
                    "image_id": new_record.id,
                    "filename": safe_name,
                    "objects": detected_str,
                    "detections_count": len(detections)
                }
            else:
                logging.error(f"YOLO Service returned error code {response.status_code}: {response.text}")
                # Keep raw image in DB, but flag the error
                new_record.detected_objects = "Error during object detection"
                db.commit()
                return {
                    "message": "Image saved, but object detection failed.",
                    "image_id": new_record.id,
                    "filename": safe_name,
                    "objects": "Error processing detection"
                }
                
        except requests.exceptions.RequestException as req_err:
            logging.error(f"Failed to communicate with YOLO service: {str(req_err)}")
            new_record.detected_objects = "YOLO Service Offline"
            db.commit()
            return {
                "message": "Image saved, but YOLO detection service is currently unreachable.",
                "image_id": new_record.id,
                "filename": safe_name,
                "objects": "YOLO Service Unreachable"
            }

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logging.error(f"Unexpected error during capture/detection pipeline: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error occurred.")

# --- 3. RETRIEVAL & DELETION ENDPOINTS ---

@app.get("/api/images")
def list_images(db: Session = Depends(get_db)):
    try:
        images = db.query(DetectorImage).order_by(DetectorImage.created_at.desc()).all()
        return {
            "images": [
                {
                    "id": img.id,
                    "filename": img.filename,
                    "objects": img.detected_objects or "Processing...",
                    "created_at": img.created_at.isoformat() if img.created_at else None,
                    "has_processed": img.processed_image is not None
                }
                for img in images
            ]
        }
    except Exception as e:
        logging.error(f"Error querying images from db: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch images list.")

@app.get("/images/raw/{image_id}")
def get_raw_image(image_id: int, db: Session = Depends(get_db)):
    img = db.query(DetectorImage).filter(DetectorImage.id == image_id).first()
    if not img or not img.raw_image:
        raise HTTPException(status_code=404, detail="Original image not found.")
    
    # Simple extension mapping for MIME type
    ext = img.filename.rsplit('.', 1)[1].lower() if '.' in img.filename else 'png'
    mime = f"image/{ext}" if ext in ALLOWED_EXTENSIONS else "image/png"
    
    return Response(content=img.raw_image, media_type=mime)

@app.get("/images/processed/{image_id}")
def get_processed_image(image_id: int, db: Session = Depends(get_db)):
    img = db.query(DetectorImage).filter(DetectorImage.id == image_id).first()
    if not img or not img.processed_image:
        raise HTTPException(status_code=404, detail="Processed image not found or not yet generated.")
    
    return Response(content=img.processed_image, media_type="image/jpeg")

@app.post("/api/delete/{image_id}")
def delete_image(image_id: int, db: Session = Depends(get_db)):
    img = db.query(DetectorImage).filter(DetectorImage.id == image_id).first()
    if not img:
        raise HTTPException(status_code=404, detail="Image not found.")
    
    try:
        db.delete(img)
        db.commit()
        logging.info(f"Deleted image ID {image_id} from PostgreSQL.")
        return {"status": "success", "message": f"Image ID {image_id} deleted successfully."}
    except Exception as e:
        db.rollback()
        logging.error(f"Error deleting image ID {image_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete image record.")
