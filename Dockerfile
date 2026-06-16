FROM python:3.12-slim

# Install all system dependencies in one layer
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    libgl1-mesa-glx \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    wget \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Verify Tesseract installed correctly
RUN tesseract --version && tesseract --list-langs

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . .

# Render uses PORT env variable
ENV PORT=10000
EXPOSE 10000

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000", "--workers", "1", "--timeout", "300", "--log-level", "info"]
