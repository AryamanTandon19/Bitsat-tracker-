"""Number-plate detection, OCR, normalization and registry fuzzy matching.

Normalization + fuzzy matching are pure Python (unit-testable without any ML
dependency). Detection/OCR backends are loaded lazily and degrade gracefully:
plate YOLO model -> lower-half-of-vehicle crop; fast-plate-ocr -> easyocr -> none.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Indian plate format after normalization, e.g. WB02AB1234, DL8CAF5031
INDIAN_PLATE_RE = re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{3,4}$")

# Common OCR confusions in the state-code (letters-only) positions.
_DIGIT_TO_LETTER = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G"}
_LETTER_TO_DIGIT = {"O": "0", "I": "1", "S": "5", "B": "8", "Z": "2", "G": "6",
                    "Q": "0", "D": "0"}


def normalize_plate(raw: str) -> str:
    """Uppercase, strip spaces/hyphens/dots and any non-alphanumeric chars."""
    return re.sub(r"[^A-Z0-9]", "", (raw or "").upper())


def looks_like_indian_plate(plate: str) -> bool:
    return bool(INDIAN_PLATE_RE.match(plate))


def repair_plate(plate: str) -> str:
    """Best-effort fix of classic OCR confusions using the Indian layout:
    2 letters, 1-2 digits, 1-3 letters, 3-4 digits."""
    if looks_like_indian_plate(plate) or len(plate) < 8:
        return plate
    chars = list(plate)
    # First two chars must be letters
    for i in (0, 1):
        if chars[i].isdigit():
            chars[i] = _DIGIT_TO_LETTER.get(chars[i], chars[i])
    # Last four chars are usually digits
    for i in range(max(2, len(chars) - 4), len(chars)):
        if chars[i].isalpha():
            chars[i] = _LETTER_TO_DIGIT.get(chars[i], chars[i])
    fixed = "".join(chars)
    return fixed if looks_like_indian_plate(fixed) else plate


def levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def fuzzy_match(plate: str, registry: list[str], max_distance: int = 1) -> str | None:
    """Return the registry plate that matches within max_distance, else None."""
    if not plate:
        return None
    best, best_d = None, max_distance + 1
    for reg in registry:
        d = levenshtein(plate, reg)
        if d < best_d:
            best, best_d = reg, d
            if d == 0:
                break
    return best if best_d <= max_distance else None


class PlateReader:
    """Detects plate regions on vehicle crops and OCRs them.

    Call `read(frame, vehicle_xyxy, track_id)` — it self-throttles to
    `ocr_interval_s` per track and stops once a confident read is cached.
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.enabled = cfg.get("enabled", True)
        self.interval = float(cfg.get("ocr_interval_s", 1.0))
        self.min_conf = float(cfg.get("min_confidence", 0.5))
        self._last_try: dict[int, float] = {}      # track_id -> ts
        self._confident: dict[int, str] = {}       # track_id -> plate
        self._detector = None
        self._ocr = None
        self._ocr_kind = None
        if self.enabled:
            self._init_backends()

    def _init_backends(self):
        model_path = self.cfg.get("detector_model", "")
        if model_path and Path(model_path).exists():
            try:
                from ultralytics import YOLO
                self._detector = YOLO(model_path)
                log.info("plate detector loaded: %s", model_path)
            except Exception as e:  # pragma: no cover - env dependent
                log.warning("plate detector unavailable (%s)", e)

        backend = self.cfg.get("ocr_backend", "auto")
        if backend in ("auto", "fast-plate-ocr"):
            model = self.cfg.get("ocr_model", "global-plates-mobile-vit-v2-model")
            try:
                # This ONNX model emits ~20 harmless "MergeShapeInfo ... falling
                # back to lenient merge" warnings at load. On a real box those
                # scroll the actually-useful startup lines off the screen and
                # look like errors to whoever is installing it. Quiet
                # onnxruntime to errors-only before the model is built.
                try:
                    import onnxruntime
                    onnxruntime.set_default_logger_severity(3)  # 3 = error
                except Exception:                                # noqa: BLE001
                    pass
                # fast-plate-ocr >= 1.0 renamed the class; keep the old name
                # working so either version of the package is usable.
                try:
                    from fast_plate_ocr import LicensePlateRecognizer as _Rec
                except ImportError:
                    from fast_plate_ocr import ONNXPlateRecognizer as _Rec
                self._ocr = _Rec(model)
                self._ocr_kind = "fast-plate-ocr"
                log.info("plate OCR ready: fast-plate-ocr (%s)", model)
                return
            except Exception as e:  # pragma: no cover
                if backend == "fast-plate-ocr":
                    log.warning("fast-plate-ocr unavailable: %s", e)
        if backend in ("auto", "easyocr"):
            try:
                import easyocr
                self._ocr = easyocr.Reader(["en"], gpu=False, verbose=False)
                self._ocr_kind = "easyocr"
                return
            except Exception as e:  # pragma: no cover
                log.warning("easyocr unavailable: %s", e)
        if self._ocr is None:
            log.warning("no OCR backend available — plates will read as unreadable")

    # -------------------------------------------------------------------
    def cached_plate(self, track_id: int) -> str | None:
        return self._confident.get(track_id)

    def forget(self, track_id: int):
        self._last_try.pop(track_id, None)
        self._confident.pop(track_id, None)

    def read(self, frame, vehicle_xyxy, track_id: int) -> str | None:
        """Return a normalized plate string or None. Throttled per track."""
        if not self.enabled or self._ocr is None:
            return None
        if track_id in self._confident:
            return self._confident[track_id]
        now = time.time()
        if now - self._last_try.get(track_id, 0) < self.interval:
            return None
        self._last_try[track_id] = now

        crop = self._crop(frame, vehicle_xyxy)
        if crop is None:
            return None
        plate_img = self._find_plate_region(crop)
        text = self._run_ocr(plate_img if plate_img is not None else crop)
        if not text:
            return None
        plate = repair_plate(normalize_plate(text))
        if looks_like_indian_plate(plate):
            self._confident[track_id] = plate
            return plate
        return None

    @staticmethod
    def _crop(frame, xyxy):
        x1, y1, x2, y2 = [int(v) for v in xyxy]
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 - x1 < 40 or y2 - y1 < 30:
            return None
        return frame[y1:y2, x1:x2]

    def _find_plate_region(self, vehicle_crop):
        if self._detector is not None:
            try:
                res = self._detector.predict(vehicle_crop, verbose=False, conf=0.3)[0]
                if len(res.boxes):
                    b = res.boxes[int(res.boxes.conf.argmax())]
                    x1, y1, x2, y2 = [int(v) for v in b.xyxy[0].tolist()]
                    return vehicle_crop[y1:y2, x1:x2]
            except Exception as e:
                log.debug("plate detect failed: %s", e)
        # Fallback: plates sit in the lower half of the vehicle
        h = vehicle_crop.shape[0]
        return vehicle_crop[h // 2:, :]

    def _run_ocr(self, img) -> str | None:
        try:
            if self._ocr_kind == "fast-plate-ocr":
                import cv2
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                out = self._ocr.run(gray)
                if out:
                    # >= 1.0 yields PlatePrediction objects; older versions
                    # yielded plain strings. str() on a prediction would return
                    # the repr, so read .plate when it is there.
                    first = out[0]
                    text = getattr(first, "plate", first)
                    return str(text).replace("_", "")
            elif self._ocr_kind == "easyocr":
                results = self._ocr.readtext(img, detail=1)
                results = [r for r in results if r[2] >= self.min_conf]
                if results:
                    return "".join(r[1] for r in sorted(results, key=lambda r: r[0][0][0]))
        except Exception as e:
            log.debug("ocr failed: %s", e)
        return None
