# VisionGuard — Investor Deck (content, slide by slide)

> This is the **content** for your pitch, written to be dropped into slides
> (Pitch/Canva/Google Slides). Anything only you can answer is marked
> **[FILL: …]** — do not let anyone invent these; investors' diligence will
> catch fabricated numbers. The architecture image (`architecture.png`) is your
> "how it works" slide. Honesty note: where accuracy is mentioned, it is framed
> as *validated-on-real-footage prototype*, not production accuracy — that's the
> credible position and it's what the raise funds.

---

## Slide 1 — Title
**VisionGuard** — AI that catches vehicle theft & break-ins the moment they
happen, on the cheap cameras societies already own.
[FILL: your name, logo, contact, date]

## Slide 2 — The problem
- Residential societies run dozens of CCTV cameras that **nobody watches**.
- Footage is **low-resolution, dark, low-frame-rate** — and existing "AI
  cameras" fail on exactly this quality (they're demoed on clean daylight video).
- Guards find out about a theft **hours later, from the recording** — too late.
- Generic motion alerts are so noisy they get **switched off**. A noisy security
  system is worse than none.

## Slide 3 — Who feels it
- **Societies / RWAs:** liability, angry residents, no deterrent.
- **Residents:** vehicles broken into overnight, no timely alert.
- **Guards:** can't watch 30 feeds; blamed after the fact.
[FILL: a real anecdote or a local theft stat / news headline — powerful here]

## Slide 4 — The solution
**VisionGuard turns existing cameras into a watchful AI** that:
1. **Sees in the dark** — enhances low-light frames so it detects the person a
   raw feed hides.
2. **Understands the act** — a person at a car + a localized break-in motion +
   the car leaving = a theft, not "some motion."
3. **Explains itself** — Claude watches the flagged clip and writes a
   plain-English verdict with timestamps.
4. **Alerts instantly** — owner and guard notified at the smash, not after.

## Slide 5 — How it works  *(use `architecture.png`)*
Local-first: cheap detection runs on-site; **only flagged evidence** goes to
Claude for a verdict; **raw CCTV never leaves the box**. One clean pipeline:
enhance → detect → free-layer + specialist models → fusion → incident → Claude
verdict → dashboard & alerts.

## Slide 6 — Why we win where others fail  (the moat)
- **Tuned for bad footage, not lab video.** We diagnosed and fixed detection on
  real 452×342 night CCTV — low-light enhancement + a **localized** break-in
  detector (measured: break-in signal 90–160 vs quiet ~15).
- **Fusion, not a single model.** No lone heuristic or model score can raise a
  critical alert — that's how we kill false alarms (the reason competitors get
  turned off).
- **Explainable + private by design.** Every alert shows its evidence; footage
  stays local. This matters for trust and for regulation.
- **Cost structure.** Detection is free on-site; AI verdicts cost **paise per
  flagged clip**, not a per-camera cloud fee.

## Slide 7 — Live demo  *(the moment that sells)*
Upload real theft CCTV → watch VisionGuard:
- box the thief a raw feed couldn't even see,
- flag the break-in at the car window,
- collapse it into **one incident card**,
- and show **Claude's verdict**: *"HIGH — car driven away after tampering,
  likely theft."*
[Screen-record this — see `DEMO_SCRIPT.md`. Have a backup video of the run.]

## Slide 8 — Status (honest)
- **Working prototype**, end-to-end: enhance → detect → free layer → hybrid
  (specialist R3D-18 models + temporal confirmation + fusion) → incident memory
  → Claude verdict → dashboard + Telegram alerts.
- **Validated qualitatively on real low-res night footage** (detection and the
  localized break-in signal proven on the actual clip).
- **Not yet** a published accuracy number — that needs an untouched, labeled
  multi-camera holdout. **This is a deliberate, honest gap the raise closes.**

## Slide 9 — Market
[FILL: # of gated societies / apartment complexes in your target city/country,
# of CCTV cameras per society, and a bottom-up TAM. Start with your city, then
India, then export.] Anchor: this runs on **cameras already installed** — no
hardware sale needed to start.

## Slide 10 — Business model
- **SaaS per society / per camera**, tiered by AI-review volume. [FILL: price]
- AI verdicts metered (transparent cost meter is in-product).
- Optional: managed install + guard training.
[FILL: your pricing hypothesis + a simple unit-economics line: revenue/society
vs. AI cost/society/month.]

## Slide 11 — Go-to-market
- Land **1–3 pilot societies** [FILL: any you already have access to?].
- Prove: fewer false alarms + a caught incident → word-of-mouth to neighboring
  societies and the RWA/facility-management channel.
[FILL: any design partners, security-agency or DVR-installer channel contacts.]

## Slide 12 — Roadmap (what the money builds)
- **Now → 4 wks:** validate on a labeled holdout; live multi-camera; incident
  timeline; pilot deployment.
- **1–3 mo:** harden the specialist models on domain-matched data (see training
  roadmap); mobile alerts; email/SMS routing.
- **3–6 mo:** multi-society, roles/permissions, analytics; the polished
  front-end (in build with Emergent).

## Slide 13 — Training-data roadmap  (turns "needs GPU" into a plan)
To make the models production-grade, in priority for *our* domain:
1. **Our own real society footage** — the highest-value data (matches deployment
   exactly); doubles as the honest holdout. [FILL: hours you can collect]
2. **SPHAR + VIRAT** — the only public sets shot from CCTV/surveillance angles;
   cut domain-shift false positives most.
3. Something-Something V2 / UCF101 — breadth (weaker domain match).
4. Compute: [FILL: GPU hours / cloud budget you're requesting].
> We deliberately do **not** destabilize the working system with heavy training
> frameworks pre-raise; training runs in an isolated environment.

## Slide 14 — The ask / use of funds
**Raising [FILL: ₹ amount] to reach [FILL: milestone — e.g. 10 paying societies
/ validated 95%+ recall].** Allocation:
- **Data + GPU** (collect + label footage, train/harden models) — [FILL: %]
- **Product** (live pipeline, mobile, multi-society, front-end) — [FILL: %]
- **Pilots + GTM** (deployments, install, sales) — [FILL: %]
- **Team** — [FILL: %]

## Slide 15 — Team
[FILL: founders, roles, relevant background. If solo: what you've built (this
whole system), and the hires the raise funds.]

## Slide 16 — Close
"Every gated society already paid for the cameras. **We make them actually
protect people** — seeing in the dark, catching the theft at the smash, and
explaining it in plain English. [FILL: contact]"

---

### Appendix slides (keep in back pocket for technical investors)
- The false-alarm defense (fusion state machine: NORMAL/WATCH/AI_REVIEW/
  CONFIRMED; no single signal alarms).
- The measured break-in signal (localized peak 90–160 vs quiet ~15 on real
  footage) and the low-light before/after.
- Privacy/security posture (local-first, audit hash-chain, name+reason to delete
  a clip).
- Cost transparency (in-product ₹ spend meter).
