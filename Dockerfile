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

# Pre-download the YOLO weights at build time. Otherwise the very first upload
# stalls (or fails) while it fetches them from GitHub at request time.
RUN python -c "from ultralytics import YOLO; YOLO('yolo11n.pt')"

# Same for the plate-OCR model — it is fetched from HuggingFace on first use,
# which otherwise turns the first analysis of the day into a ~90s stall.
RUN python - <<'PY' || echo "plate OCR prefetch skipped — first read will be slow"
try:
    from fast_plate_ocr import LicensePlateRecognizer as R
except ImportError:
    from fast_plate_ocr import ONNXPlateRecognizer as R
R("global-plates-mobile-vit-v2-model")
PY

# App code.
COPY . .

# On a hosted server we want the REAL working console (real upload + analysis)
# at "/", so drop the static-only demo page. dashboard.py then serves the
# console at "/" automatically.
RUN rm -f app/demo.html

# Cloud profile, derived from config.yaml so there is one source of truth:
#   - no cameras: a hosted box has no CCTV, and pointing at a missing file made
#     the worker retry forever and show a dead tile. Upload/analysis is the
#     feature investors actually use.
#   - no HTTP Basic auth: the app has its own login page; the browser popup on
#     top of it was a confusing double login.
RUN python -c "import yaml; c=yaml.safe_load(open('config.yaml')); c['cameras']=[]; \
c.setdefault('dashboard',{}).setdefault('auth',{})['enabled']=False; \
yaml.safe_dump(c, open('config.cloud.yaml','w'), sort_keys=False)"

EXPOSE 8000
CMD ["python", "-m", "app.main", "--config", "config.cloud.yaml"]
