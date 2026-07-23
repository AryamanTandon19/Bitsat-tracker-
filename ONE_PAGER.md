# VisionGuard — One-Pager

**AI that catches vehicle theft & break-ins the moment they happen — on the
cheap, dark cameras societies already own.**

### The problem
Residential societies run dozens of CCTV cameras nobody watches. The footage is
low-resolution, dark, and low-frame-rate — exactly where today's "AI cameras"
fail, because they're built for clean daylight video. Guards learn about a theft
hours later from the recording. Generic motion alerts are so noisy they get
switched off. A noisy security system is worse than none.

### The product
VisionGuard turns existing cameras into a watchful AI:
- **Sees in the dark** — enhances low-light frames to detect the person a raw
  feed hides.
- **Understands the act** — fuses a localized break-in motion, a person at the
  vehicle, and the car leaving into a single *theft* conclusion.
- **Explains itself** — Claude reviews the flagged clip and writes a
  plain-English verdict with timestamps and severity.
- **Alerts instantly** — owner and guard notified at the smash, not after.

### Why it wins
- **Tuned for real bad footage**, not lab video — proven on actual 452×342 night
  CCTV (break-in signal measured 90–160 vs quiet ~15).
- **Fusion, not a single model** — no lone signal can raise a critical alert, so
  false alarms (the reason competitors get turned off) are designed out.
- **Local-first & explainable** — raw footage never leaves the building; every
  alert shows its evidence.
- **Cost** — detection is free on-site; AI verdicts are paise per flagged clip,
  not a per-camera cloud subscription.

### Status (honest)
Working end-to-end prototype (enhance → detect → free layer → specialist models +
temporal confirmation + fusion → incident memory → Claude verdict → dashboard +
Telegram). Validated qualitatively on real night footage. A published accuracy
number requires a labeled holdout — the deliberate gap this raise closes.

### The ask
Raising **[FILL: ₹ amount]** to **[FILL: milestone]** — funding data collection +
GPU to harden the models, pilot deployments, and the product build.

**[FILL: Founder name · email · phone]**
