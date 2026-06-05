# Use official lightweight Python base image
FROM python:3.11-slim-bookworm

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies required for OpenCV operations
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory inside the container
WORKDIR /app

# Copy python dependencies file first to leverage Docker build cache
COPY requirements.txt .

# Install lightweight CPU-only PyTorch and Torchvision first to prevent downloading 2.5GB+ CUDA binary wheels
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Install python packages
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the yolo11n.pt model so the app doesn't block on first request
# This downloads it to the standard ultralytics cache location (/root/.config/Ultralytics)
RUN python -c "from ultralytics import YOLO; YOLO('yolo11n.pt')"

# Copy the rest of the application files
COPY . .

# Expose the API port
EXPOSE 8001

# Start the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
