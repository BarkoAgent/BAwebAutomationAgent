FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    make \
    gcc \
    g++ \
    cmake \
    libtesseract-dev \
    libleptonica-dev \
    tesseract-ocr \
    libjpeg-dev \
    zlib1g-dev \
    libopenjp2-7-dev \
    libpng-dev \
    libtiff-dev \
    libglib2.0-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "client.py"]