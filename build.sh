#!/usr/bin/env bash
set -e

echo "Installing system dependencies..."
apt-get update -y
apt-get install -y tesseract-ocr tesseract-ocr-eng libgl1

echo "Installing Python dependencies..."
pip install -r requirements.txt

echo "Build complete."