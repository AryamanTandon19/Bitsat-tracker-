# VisionGuard backend — runs the real detection engine (YOLO + FastAPI).
# Works on x86 and ARM (Oracle Ampere) — pip fetches the right wheels.
FROM python:3.11-slim

WORKDIR /app

# System libraries OpenCV needs on a headless server.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (cached layer).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code.
COPY . .

# On a hosted server we want the REAL working console (real upload + analysis)
# at "/", so drop the static-only demo page. dashboard.py then serves the
# console at "/" automatically.
RUN rm -f app/demo.html

EXPOSE 8000
CMD ["python", "-m", "app.main"]
