FROM python:3.12-slim

WORKDIR /app

# Install system deps (PyMuPDF needs libmupdf)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# HF Spaces provides PORT env (default 7860)
EXPOSE 7860

# Use shell form so we can read $PORT
CMD uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-7860}
