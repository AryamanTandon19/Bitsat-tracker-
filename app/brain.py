"""The behaviour brain — a learned judgement over how a person moved near a
vehicle, not over what the scene looked like.

Where it sits
-------------
The detector (YOLO) is the *eyes*: it says "person here, car there, this frame".
The free layer (`app/rules.py`, `app/normalcy.py`) is *reflexes*: fast, hand-
written, per-frame. This module is the *brain*: it reads a few seconds of a
tracked (person, vehicle) pair, reduces it to the scale-free geometry features
in `training/features.py`, and returns one calibrated suspicion score with the
reasons behind it.

Why geometry and not pixels
---------------------------
`app/specialist.py` records how the full-frame video model failed: it "drifted
up to ~0.72-0.78 on unrelated live scenes" because it had learned what those
scenes *looked like*. A vector built from "how close, for how long, moving how"
cannot drift that way — it never sees a scene. It is also the only kind of
model a guard can argue with: "stayed within half a car-length for 34s, circled
twice, never walked away" is checkable against the video; a softmax is not.
This matches the training plan's ranked priorities — fewer false alarms, then
recall, then laptop-runnable, then explainable — better than any pixel model,
and it trains on a CPU in seconds.

Two heads, because data arrives in two stages
---------------------------------------------
* **anomaly** (unsupervised): learns the shape of *ordinary* interactions from
  negatives alone — which is all real early footage gives you — and scores how
  far a new one sits from normal. This head ships without a single labelled
  crime.
* **supervised** (a gradient-boosted tree): switches on automatically once real
  suspicious examples exist, and then leads the decision.

Both read the exact same feature vector, and a hand-written **gate** (from
`training/tier0.py`) sits underneath as an interpretable floor. No single one of
them is trusted alone — see `to_evidence()` and `app/fusion.py`, which still
require independent corroboration before anything pages a human.

Degrades gracefully: with no trained model on disk the brain reports
``ready == False`` and scores nothing, exactly like the specialist bank — the
free layer never depended on it.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from training import features as F
from training.tier0 import gate_fires

log = logging.getLogger(__name__)

# The brain's headline name inside the fusion evidence bundle. Kept distinct
# from the pixel specialists ("break_in", "vehicle") so both can score the same
# window and fusion can see they agree.
NAME = "behavior"

# Below this many suspicious training rows the supervised head is not fit — a
# tree told apart by a handful of positives has memorised them, not learned.
# Until then the anomaly head + gate carry the decision.
MIN_POSITIVES = 8

# A constant feature has no spread; use 1.0 for its scale so a robust z-score
# is defined (and always 0) instead of dividing by zero.
_EPS = 1e-9


@dataclass
class BrainVerdict:
    """One window's judgement, and why."""
    score: float                       # 0..1 headline suspicion
    suspicious: bool                   # score >= threshold OR the gate fired
    reasons: list[str] = field(default_factory=list)
    anomaly: float = 0.0               # how far from ordinary (0..1)
    supervised: float | None = None    # tree probability, if the head is trained
    gate: bool = False                 # the interpretable floor fired
    threshold: float = 0.5

    def as_dict(self) -> dict:
        return {"score": round(self.score, 4), "suspicious": self.suspicious,
                "anomaly": round(self.anomaly, 4),
                "supervised": (None if self.supervised is None
                               else round(self.supervised, 4)),
                "gate": self.gate, "threshold": round(self.threshold, 4),
                "reasons": self.reasons}


class BehaviorBrain:
    """A trainable, saveable behavioural scorer. Fit with ``fit`` (or the
    ``training.brain_train`` CLI), persist with ``save``, load with ``load``."""

    def __init__(self):
        self.feature_names: tuple = F.FEATURES
        self.median: list = []
        self.scale: list = []          # robust spread (IQR) per feature
        self.iso = None                # IsolationForest, the anomaly head
        self.clf = None                # HistGradientBoosting, the supervised head
        self.threshold: float = 0.5
        self._anom_lo: float = 0.0     # anomaly-raw calibration points
        self._anom_hi: float = 1.0
        self.meta: dict = {}

    # -- readiness -----------------------------------------------------------
    @property
    def ready(self) -> bool:
        """True once at least the anomaly head is fit. The live path checks
        this and stays on the free layer when it is False."""
        return self.iso is not None

    # -- fitting -------------------------------------------------------------
    def fit(self, train_rows: list, val_rows: list | None = None,
            *, synthetic: bool = False) -> dict:
        """Learn both heads and calibrate the operating threshold.

        `train_rows` / `val_rows` are the row dicts written by
        `training.extract` / `training.synth`: each has `features` and
        `suspicious`. Returns a small report dict (also stored on `self.meta`).
        """
        from sklearn.ensemble import IsolationForest

        if not train_rows:
            raise ValueError("no training rows")

        X = [self._vector(r["features"]) for r in train_rows]
        y = [bool(r["suspicious"]) for r in train_rows]
        self._fit_scaler(X)

        # anomaly head: model ORDINARY behaviour, so fit on negatives only. If
        # there are no negatives (unlikely) fall back to everything.
        neg = [self._scaled(v) for v, s in zip(X, y) if not s] or \
              [self._scaled(v) for v in X]
        self.iso = IsolationForest(
            n_estimators=200, contamination="auto", random_state=0)
        self.iso.fit(neg)
        self._calibrate_anomaly(neg)

        # supervised head: only when there are enough real positives
        n_pos = sum(y)
        if n_pos >= MIN_POSITIVES and len(set(y)) > 1:
            self.clf = self._fit_supervised(X, y)
        else:
            self.clf = None

        report = self._choose_threshold(val_rows)
        report.update({
            "n_train": len(train_rows), "n_positives": n_pos,
            "supervised_head": self.clf is not None,
            "synthetic": bool(synthetic),
            "threshold": round(self.threshold, 4),
        })
        self.meta = report
        return report

    def _fit_scaler(self, X: list) -> None:
        cols = list(zip(*X)) if X else []
        self.median, self.scale = [], []
        for col in cols:
            s = sorted(col)
            med = s[len(s) // 2]
            q1 = s[int(0.25 * (len(s) - 1))]
            q3 = s[int(0.75 * (len(s) - 1))]
            iqr = q3 - q1
            self.median.append(med)
            self.scale.append(iqr if iqr > _EPS else 1.0)

    def _fit_supervised(self, X: list, y: list):
        from collections import Counter

        from sklearn.ensemble import HistGradientBoostingClassifier

        counts = Counter(y)
        weight = {c: len(y) / (2 * n) for c, n in counts.items()}
        # Same regularised, no-early-stopping setup tier0 arrived at for the
        # few-hundred-example regime; the internal holdout is too small below a
        # couple thousand rows to mean anything.
        clf = HistGradientBoostingClassifier(
            max_depth=4, max_iter=200, learning_rate=0.06,
            l2_regularization=1.0, early_stopping=False, random_state=0)
        clf.fit(X, y, sample_weight=[weight[v] for v in y])
        return clf

    def _calibrate_anomaly(self, scaled_neg: list) -> None:
        """Map IsolationForest's raw score onto [0,1] using the normal set.

        `score_samples` is higher for normal points, so we negate it: raw grows
        with abnormality. We anchor 0 at the median normal point and 1 near the
        edge of the normal cloud (99th percentile), so an ordinary interaction
        lands near 0 and anything past the fringe of normal saturates at 1.
        """
        raw = sorted(-s for s in self.iso.score_samples(scaled_neg))
        if not raw:
            self._anom_lo, self._anom_hi = 0.0, 1.0
            return
        self._anom_lo = raw[len(raw) // 2]                       # p50
        hi = raw[min(len(raw) - 1, int(0.99 * (len(raw) - 1)))]  # p99
        self._anom_hi = hi if hi > self._anom_lo + _EPS else self._anom_lo + 1.0

    def _choose_threshold(self, val_rows: list | None) -> dict:
        """Pick the operating threshold on validation, never on test.

        With positives in val: fix recall >= 0.80 and minimise false alarms —
        not maximise accuracy, which on a mostly-normal set rewards saying
        'normal' every time. Without positives: set the threshold so the false-
        alarm rate on ordinary val footage stays at ~5%.
        """
        if not val_rows:
            self.threshold = 0.5
            return {"threshold_basis": "default (no validation rows)"}

        scored = [(self._raw_score(r["features"]), bool(r["suspicious"]))
                  for r in val_rows]
        has_pos = any(s for _, s in scored)
        if has_pos:
            best = (0.5, None, None)
            for t in [i / 100 for i in range(5, 100, 5)]:
                tp = sum(1 for p, s in scored if s and p >= t)
                fp = sum(1 for p, s in scored if not s and p >= t)
                fn = sum(1 for p, s in scored if s and p < t)
                tn = sum(1 for p, s in scored if not s and p < t)
                recall = tp / (tp + fn) if (tp + fn) else 0.0
                if recall >= 0.80:
                    fpr = fp / (fp + tn) if (fp + tn) else 0.0
                    if best[1] is None or fp < best[1]:
                        best = (t, fp, {"recall": round(recall, 3),
                                        "fpr": round(fpr, 3)})
            self.threshold = best[0]
            return {"threshold_basis": "recall>=0.80, min false alarms on val",
                    "val": best[2]}
        # negatives only: bound the false-alarm rate
        neg = sorted(p for p, _ in scored)
        self.threshold = neg[min(len(neg) - 1, int(0.95 * (len(neg) - 1)))]
        return {"threshold_basis": "95th pct of val-normal scores (no positives)",
                "val_normal_fpr_target": 0.05}

    # -- scoring -------------------------------------------------------------
    def _vector(self, feats: dict) -> list:
        return F.to_vector(feats)

    def _scaled(self, vec: list) -> list:
        return [(v - m) / s for v, m, s in zip(vec, self.median, self.scale)]

    def _anomaly(self, vec: list) -> float:
        raw = -float(self.iso.score_samples([self._scaled(vec)])[0])
        span = self._anom_hi - self._anom_lo
        return max(0.0, min(1.0, (raw - self._anom_lo) / span if span > _EPS else 0.0))

    def _raw_score(self, feats: dict) -> float:
        """Headline score without the gate floor — supervised if trained, else
        anomaly. Used for thresholding so the threshold and the score agree."""
        vec = self._vector(feats)
        if self.clf is not None:
            return float(self.clf.predict_proba([vec])[0][1])
        return self._anomaly(vec)

    def score(self, feats: dict) -> BrainVerdict:
        """Judge one (person, vehicle) window."""
        if not self.ready:
            return BrainVerdict(score=0.0, suspicious=False,
                                reasons=["brain not trained"], threshold=self.threshold)
        vec = self._vector(feats)
        anomaly = self._anomaly(vec)
        supervised = (float(self.clf.predict_proba([vec])[0][1])
                      if self.clf is not None else None)
        base = supervised if supervised is not None else anomaly
        gate = gate_fires(feats)
        suspicious = bool(base >= self.threshold or gate)
        return BrainVerdict(
            score=round(base, 4), suspicious=suspicious,
            anomaly=round(anomaly, 4), supervised=supervised, gate=gate,
            threshold=self.threshold, reasons=self._reasons(feats, vec, gate))

    def _reasons(self, feats: dict, vec: list, gate: bool) -> list:
        """Human-first, then the two most unusual features as backup.

        The curated sentence from `features.explain` leads, because that is the
        thing a guard checks against the video. The robust z-scores follow as
        the technical 'what stood out', so a reviewer can see the model and the
        words point at the same behaviour.
        """
        out = []
        words = F.explain(feats)
        if words and words != "nothing notable":
            out.append(words)
        if gate:
            out.append("meets the plain-language gate: lingered close and did "
                       "not simply walk past")
        # top-2 by absolute robust z, skipping context flags that are not
        # "unusual" so much as categorical
        skip = {"night", "hour_sin", "hour_cos", "vehicle_registered"}
        zs = []
        for name, v, m, s in zip(self.feature_names, vec, self.median, self.scale):
            if name in skip:
                continue
            z = (v - m) / s
            if abs(z) > 2.0:
                zs.append((abs(z), name, z))
        for _, name, z in sorted(zs, reverse=True)[:2]:
            out.append(f"unusual {name} ({z:+.1f}σ vs ordinary)")
        return out or ["within the range of ordinary behaviour"]

    # -- fusion bridge -------------------------------------------------------
    def contribute(self, ev, feats: dict, *, confirmed: bool):
        """Fold this brain's judgement into an existing `app.fusion.Evidence`.

        This is the correct way to wire the brain in live: the free layer builds
        the Evidence (its own independently-detected relationship / pose / zone
        signals), and the brain *adds* its score and confirmation on top. Fusion
        then needs an independent corroborator before it will confirm — so the
        brain never pages a human on its own number.

        Returns ``(evidence, verdict)``. The passed Evidence is mutated in place
        and also returned for convenience.
        """
        v = self.score(feats)
        ev.specialist_scores[NAME] = v.score
        if confirmed:
            ev.specialist_confirmed.add(NAME)
        if feats.get("vehicle_registered"):
            ev.contradictions.add("registered_plate")
        return ev, v

    def to_evidence(self, feats: dict, camera_id: str, *, confirmed: bool):
        """Package a verdict as a *standalone* `app.fusion.Evidence` — the brain
        as the only witness.

        Deliberately does NOT set ``relationship``: the brain's proximity is the
        same observation its score is built from, so counting it as independent
        corroboration would double-count and let one model confirm alone. With
        no other evidence, fusion caps this at WATCH by its domain-shift guard —
        which is exactly the safe behaviour. Real grounding (pose, a state
        chain, the free layer's own relationship signal) is what lifts it to a
        human alert; use `contribute()` to merge those in.
        """
        from .fusion import Evidence

        ev = Evidence(camera_id=str(camera_id))
        return self.contribute(ev, feats, confirmed=confirmed)

    def make_confirmer(self):
        """A temporal confirmer tuned to this brain's calibrated threshold, so a
        single spike never confirms — the same 2-of-3 smoothing the pixel
        specialists use, but anchored on the brain's own operating point."""
        from .temporal import TemporalConfirmer

        return TemporalConfirmer(
            history_size=3, required_hits=2,
            base_threshold=self.threshold,
            strong_gate=min(0.95, self.threshold + 0.15),
            require_strong=False)

    # -- persistence ---------------------------------------------------------
    def save(self, path: str) -> str:
        import joblib
        from pathlib import Path

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({
            "feature_names": list(self.feature_names),
            "median": self.median, "scale": self.scale,
            "iso": self.iso, "clf": self.clf,
            "threshold": self.threshold,
            "anom_lo": self._anom_lo, "anom_hi": self._anom_hi,
            "meta": self.meta,
        }, path)
        return path

    @classmethod
    def load(cls, path: str) -> "BehaviorBrain | None":
        """Load a saved brain, or None if the file is missing/unreadable.
        Never raises — a bad model file must not stop the system booting."""
        import joblib
        from pathlib import Path

        if not Path(path).exists():
            return None
        try:
            blob = joblib.load(path)
        except Exception as e:                       # noqa: BLE001
            log.warning("brain load failed (%s) — running on the free layer", e)
            return None
        b = cls()
        b.feature_names = tuple(blob.get("feature_names", F.FEATURES))
        b.median = blob["median"]
        b.scale = blob["scale"]
        b.iso = blob["iso"]
        b.clf = blob.get("clf")
        b.threshold = float(blob.get("threshold", 0.5))
        b._anom_lo = float(blob.get("anom_lo", 0.0))
        b._anom_hi = float(blob.get("anom_hi", 1.0))
        b.meta = blob.get("meta", {})
        if b.feature_names != F.FEATURES:
            log.warning("brain was trained on a different feature set — "
                        "retrain before trusting it")
        return b
