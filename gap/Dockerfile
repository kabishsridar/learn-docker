FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-tk \
    python3-numpy \
    python3-pil \
    python3-pil.imagetk \
    python3-opencv \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libx11-6 \
    libxext6 \
    libxrender1 \
    libxtst6 \
    libxi6 \
    libcamera-v4l2 \
    libcamera-tools \
    v4l-utils \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app/

RUN pip3 install --no-cache-dir --break-system-packages \
    picamera2 || echo "Skipping picamera2 on non-ARM host platform"

# Uncomment the line below if you have a requirements.txt file!
# RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

CMD ["python3", "app.py"]
