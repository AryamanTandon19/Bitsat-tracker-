# VisionGuard — Certification & Readiness

This certifies what has been **built, reviewed and verified**, and states plainly
what has **not** — because a security product that overstates its own readiness
is the most dangerous kind. There are two different certifications here, and only
one of them is complete.

- **Engineering certification — COMPLETE.** The software is built, secured,
  reviewed, and covered by an automated test suite.
- **Field certification — PENDING.** Whether it is accurate enough on real
  cameras (few false alarms, catches real incidents, holds up at night) has not
  yet been measured on enough real footage. That number comes from the GPU
  training run and a pilot, not from this document.

---

## 1. Engineering certification

### Test suite
**950 automated tests, all passing.** They cover every subsystem: the behaviour
brain, the free-layer rules, the event graph, the incident/fusion decision
logic, learned normalcy, camera auto-connect, zones, retention, the resident
owner app, the self-improve loop, and the training pipeline. Several were written
to lock down bugs found during development (the event-graph dwell bug, the
detector person-recall blind spot, the fusion no-single-model rule).

### Security review — no High/Medium vulnerabilities
The security-critical surface added for this release was audited (see the review
in the session record). Result: **no High or Medium confidence vulnerabilities.**

| Audited | Verdict |
|---|---|
| Resident data scoping (one owner seeing another's alerts/clips/feedback) | Pass — every action re-checks ownership; proven by tests |
| Access tokens | Pass — 256-bit `secrets`, only the SHA-256 stored, revocable, **and now expiring (180 days)** |
| SQL injection | Pass — all queries parameterized |
| XSS in the resident app | Pass — output escaped; data is system/operator-sourced |
| Command injection | Pass — all subprocesses use list-args, no shell |
| Private-video (clip) access & path traversal | Pass — clip paths are system-written and gated by ownership |
| CSRF | Pass — `HttpOnly` + `SameSite=Lax` cookies |

Hardening applied during this pass: resident magic-links now **expire after 180
days** (server-enforced), closing the one defense-in-depth gap the review raised.

### Security model (how it holds together)
- **Two separate identities.** Staff use accounts with roles and expiring
  sessions; residents use per-vehicle magic-links scoped to one plate. Neither
  can reach the other's endpoints.
- **All processing is local.** Only Telegram alerts (and, if enabled, a paid
  AI second-opinion) leave the machine. Passwords/credentials are stored on the
  box, never shipped as defaults — a fresh install generates its own admin
  password and shows it once.
- **Evidence minimisation.** A clip is kept only when the alert is real; a false
  alarm's clip is discarded, not stored.
- **Auditability.** Camera changes, feedback, clip deletions and owner-link
  issuance are written to an append-only hash-chained audit log.

---

## 2. What real footage has proven (and what it hasn't)

Run against real CCTV (public MEVA, 1080p) — see `docs/REALITY_CHECK.md`:

- **Proven:** the whole pipeline runs end-to-end on real frames; vehicle
  detection is strong; the training pipeline mines, extracts and trains on real
  footage in the storage-safe way.
- **Found & fixed:** the default detector was blind to ~75% of people on wide
  outdoor scenes — fixed to `yolo11s @ 1280` (the entire behaviour layer depends
  on seeing the person).
- **Not yet proven:** a real false-alarms-per-hour *rate* over hours of footage;
  **recall** on real incidents (needs UCF-Crime positives); and **night**
  performance. The machinery for all three exists; the numbers do not yet.

---

## 3. Field certification — the go / no-go checklist

VisionGuard is cleared for a **pilot** when, measured on footage it never trained
on (produced by `python -m training.pilot_train --night --ucf <dir>` on a GPU —
see `docs/TRAIN_ON_YOUR_LAPTOP.md`):

- [ ] **False alarms per hour is low** — ideally well under 1 per camera-hour.
- [ ] **Recall is high** — it catches the real incidents (measured with
      UCF-Crime positives).
- [ ] **Both hold at night**, not just in daylight.
- [ ] A short **live soak** on one real camera raises no runaway alerts over 24h.

Until those four boxes are ticked with real numbers, VisionGuard is
**engineering-certified and pilot-*candidate*, not field-certified.** That is the
honest status, and it is a good one: the build is done and sound; what remains is
measurement, which is a footage-and-GPU task, not a code task.

---

## 4. Sign-off

- **Engineering:** built, security-reviewed (no High/Medium findings), hardened,
  950 tests passing. **Certified.**
- **Field accuracy:** pending the real-footage numbers above. **Not yet
  certified — and no accuracy figure should be quoted until it is.**
