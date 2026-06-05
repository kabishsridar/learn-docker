import os
import io
import base64
import logging
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from ultralytics import YOLO
import cv2 as cv
import numpy as np

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = FastAPI(
    title="YOLOv11 Object Detection Service",
    description="A microservice for running inference on images using YOLOv11 and returning annotations.",
    version="1.0.0"
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load model on startup
MODEL_NAME = os.environ.get("MODEL_NAME", "yolo11n.pt")
logging.info(f"Loading YOLO model: {MODEL_NAME}...")
try:
    model = YOLO(MODEL_NAME)
    logging.info("YOLO model loaded successfully.")
except Exception as e:
    logging.error(f"Error loading YOLO model: {str(e)}")
    raise RuntimeError(f"Could not load model: {str(e)}")

@app.get("/health")
def health_check():
    return {"status": "healthy", "model": MODEL_NAME}

@app.post("/detect")
async def detect_objects(file: UploadFile = File(...)):
    # Validate file format
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid image.")
    
    try:
        # Read raw image bytes
        image_bytes = await file.read()
        
        # Convert bytes to numpy array for OpenCV
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv.imdecode(nparr, cv.IMREAD_COLOR)
        
        if img is None:
            raise HTTPException(status_code=400, detail="Failed to decode image.")
        
        # Run YOLO inference
        # conf=0.25 is standard confidence threshold
        results = model.predict(source=img, conf=0.25, verbose=False)
        result = results[0]
        
        # Parse detections metadata
        detections = []
        if result.boxes is not None:
            for box in result.boxes:
                # Class ID and Name
                cls_id = int(box.cls[0].item())
                label = result.names[cls_id]
                
                # Confidence score
                confidence = float(box.conf[0].item())
                
                # Box coordinates (x1, y1, x2, y2)
                coords = [float(x) for x in box.xyxy[0].tolist()]
                
                detections.append({
                    "label": label,
                    "confidence": round(confidence, 3),
                    "box": coords
                })
                
        # Generate the annotated image (bounding boxes and labels drawn)
        # result.plot() returns a numpy array representing the annotated image (BGR)
        annotated_img = result.plot()
        
        # Encode annotated image back to JPEG bytes
        success, encoded_img = cv.imencode(".jpg", annotated_img, [int(cv.IMWRITE_JPEG_QUALITY), 85])
        if not success:
            raise HTTPException(status_code=500, detail="Failed to encode annotated image.")
        
        # Convert annotated image bytes to base64 string
        annotated_bytes = encoded_img.tobytes()
        base64_encoded = base64.b64encode(annotated_bytes).decode("utf-8")
        
        return {
            "detections": detections,
            "annotated_image": base64_encoded,
            "detected_labels": list(set(d["label"] for d in detections))
        }
        
    except Exception as e:
        logging.error(f"Error during object detection: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Inference error: {str(e)}")
