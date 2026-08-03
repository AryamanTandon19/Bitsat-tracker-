"""Training pipeline for the VisionGuard specialist models.

Deliberately separate from `app/`. The application must import and run with
none of this present and no torch installed — that is why `app.specialist`
degrades to `available == False` rather than raising. Nothing in `app/` may
import from here.

  manifest.py     the clip contract every step reads
  splits.py       train/val/test by SOURCE VIDEO, never by clip
  profile_gpu.py  what a training step actually costs on this machine

See docs/TRAINING_PLAN.md for the order these are meant to be used in.
"""
