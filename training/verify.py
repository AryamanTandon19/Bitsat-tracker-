"""Is this cut clip actually worth keeping?

Between "ffmpeg exited 0" and "this is a training example" sit a surprising
number of ways to end up with a file that trains the model on nothing. Every
check here exists because of a specific failure it catches:

  **decodes**       ffmpeg can exit 0 having written a header and no frames,
                    usually when the seek landed past the end of the source.

  **duration**      `-ss` seeks to a keyframe. Ask for 6 seconds starting at
                    42 and you can get 2. A manifest that says 6 s while the
                    file holds 2 trains the model on padding it will never see
                    in production.

  **not blank**     a uniform frame — the lens cap, a dropped stream, the grey
                    frame a decoder emits on error. Checked by VARIANCE, never
                    by brightness: MEVA's genuine night footage sits at mean
                    brightness 30, and a brightness floor would throw away
                    precisely the data the product is short of.

  **not frozen**    consecutive frames identical. A stalled RTSP stream or a
                    duplicated-frame encode gives a clip with no motion in it,
                    which teaches a *video* model exactly nothing.

  **has the object**  a clip labelled NORMAL_VEHICLE_ACTIVITY with no vehicle
                    in it is not a normal vehicle activity, it is grass. This
                    is the only check that needs a model, so it is optional
                    and says so when it is skipped.

Each check returns a reason in words, because these are read by a person
deciding whether their extraction settings are wrong, not by a program.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# A frame flatter than this is a lens cap, a dropped stream or a decoder error
# frame. Genuine night footage is dark but textured — measured at variance well
# above this on MEVA's 23:55 clips — so this rejects blankness, not darkness.
MIN_VARIANCE = 12.0

# Mean absolute difference between sampled frames.
#
# Calibrated DOWNWARD after a real run: MEVA's quiet car park gives a largest
# frame-to-frame change of 0.42-0.49, and a floor of 0.6 threw those clips
# away. For a security product that is exactly backwards — "nothing is
# happening" IS the negative class, and uneventful footage is the data the
# system must learn to stay quiet through.
#
# What this check is actually for is a STALLED stream or a duplicated-frame
# encode, where consecutive frames are identical and the difference is
# essentially zero. Real footage always carries sensor noise, so a low floor
# separates the two cleanly without discarding a still scene.
MIN_MOTION = 0.1

# How far the real duration may be from what was asked for. Keyframe seeking
# routinely costs a fraction of a second; losing two thirds of the clip is a
# different matter.
DURATION_TOLERANCE_S = 0.75


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Verdict:
    ok: bool
    checks: list = field(default_factory=list)
    frames: int = 0
    duration_s: float = 0.0
    skipped: list = field(default_factory=list)

    @property
    def failures(self) -> list:
        return [c for c in self.checks if not c.ok]

    @property
    def reason(self) -> str:
        return "; ".join(f"{c.name}: {c.detail}" for c in self.failures)

    def public(self) -> dict:
        return {"ok": self.ok, "frames": self.frames,
                "duration_s": round(self.duration_s, 2),
                "failed": [c.name for c in self.failures],
                "reason": self.reason, "skipped": list(self.skipped)}


# ------------------------------------------------------------ pure checks
def frame_variance(frame) -> float:
    """Spread of pixel values. Zero means every pixel is the same."""
    import numpy as np
    return float(np.asarray(frame, dtype="float32").std())


def motion_between(a, b) -> float:
    """Mean absolute difference between two frames."""
    import numpy as np
    fa = np.asarray(a, dtype="float32")
    fb = np.asarray(b, dtype="float32")
    if fa.shape != fb.shape:
        return 255.0                       # different sizes: certainly not frozen
    return float(np.abs(fa - fb).mean())


def check_duration(actual_s: float, wanted_s: float,
                   tolerance_s: float = DURATION_TOLERANCE_S) -> Check:
    off = abs(actual_s - wanted_s)
    return Check(
        "duration", off <= tolerance_s,
        f"{actual_s:.2f}s on disk against {wanted_s:.2f}s asked for — the seek "
        "landed on a different keyframe" if off > tolerance_s else "")


def check_not_blank(variances) -> Check:
    """Every sampled frame must have some texture."""
    if not variances:
        return Check("not_blank", False, "no frames to look at")
    worst = min(variances)
    flat = sum(1 for v in variances if v < MIN_VARIANCE)
    return Check(
        "not_blank", flat == 0,
        f"{flat} of {len(variances)} frames are flat (lowest spread {worst:.1f}, "
        f"floor {MIN_VARIANCE}) — a lens cap or a decoder error frame, not "
        "darkness" if flat else "")


def check_not_frozen(motions) -> Check:
    if not motions:
        return Check("not_frozen", False, "fewer than two frames")
    moved = max(motions)
    return Check(
        "not_frozen", moved >= MIN_MOTION,
        f"consecutive frames are identical (largest change {moved:.2f}, floor "
        f"{MIN_MOTION}) — a stalled stream or a duplicated-frame encode, not "
        "merely a quiet scene" if moved < MIN_MOTION else "")


# ------------------------------------------------------------- the driver
def sample_frames(path, n: int = 8):
    """Decode `n` frames spread across a clip. Returns (frames, total, seconds).

    Samples rather than decoding everything: verification runs on every clip
    and eight frames answers all of these questions.
    """
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return [], 0, 0.0
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        seconds = total / fps if fps > 0 else 0.0
        if total <= 0:
            # some containers do not report a count; fall back to reading
            frames = []
            while len(frames) < n * 4:
                ok, fr = cap.read()
                if not ok:
                    break
                frames.append(fr)
            total = len(frames)
            seconds = total / fps if fps > 0 else 0.0
            step = max(1, total // n)
            return frames[::step][:n], total, seconds

        want = {min(total - 1, int(i * (total - 1) / max(1, n - 1)))
                for i in range(n)}
        frames, idx = [], 0
        while idx <= max(want, default=0):
            ok, fr = cap.read()
            if not ok:
                break
            if idx in want:
                frames.append(fr)
            idx += 1
        return frames, total, seconds
    finally:
        cap.release()


def verify_clip(path, wanted_s: float, samples: int = 8,
                detector=None, want_classes=()) -> Verdict:
    """Run every check on a cut clip.

    `detector` is optional and duck-typed: anything with `.track(frame)`
    returning objects that have `.cls_name`. When it is absent the object
    check is *skipped and reported as skipped*, never silently passed — a
    verification that quietly stops verifying is worse than none.
    """
    frames, total, seconds = sample_frames(path, samples)
    v = Verdict(ok=False, frames=total, duration_s=seconds)

    if not frames:
        v.checks.append(Check("decodes", False,
                              "no frames could be read — ffmpeg wrote a "
                              "header and nothing else, usually a seek past "
                              "the end of the source"))
        return v
    v.checks.append(Check("decodes", True))
    v.checks.append(check_duration(seconds, wanted_s))
    v.checks.append(check_not_blank([frame_variance(f) for f in frames]))
    v.checks.append(check_not_frozen(
        [motion_between(a, b) for a, b in zip(frames, frames[1:])]))

    if want_classes:
        if detector is None:
            v.skipped.append(
                f"has_object ({'/'.join(want_classes)}) — no detector was "
                "given, so nobody has checked the clip contains what its "
                "label claims")
        else:
            found = set()
            for f in frames:
                try:
                    found |= {d.cls_name for d in detector.track(f)}
                except Exception as e:                       # noqa: BLE001
                    v.checks.append(Check("has_object", False,
                                          f"the detector failed: {e}"))
                    found = None
                    break
            if found is not None:
                hit = found & set(want_classes)
                v.checks.append(Check(
                    "has_object", bool(hit),
                    f"no {' or '.join(want_classes)} anywhere in the clip — "
                    f"saw {', '.join(sorted(found)) or 'nothing'}. A clip "
                    "labelled for an object that is not in it is not a "
                    "training example." if not hit else ""))

    v.ok = all(c.ok for c in v.checks)
    return v
