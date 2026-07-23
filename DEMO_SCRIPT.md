# VisionGuard — Demo Recording Script

A tight, repeatable script to screen-record for investors. Goal: **90 seconds**
that show detection working on *bad real footage*, one clean incident, and
Claude's verdict. Record 2–3 takes and keep the best; also keep one full
screen-capture as a **backup** in case live glitches during the pitch.

## Before you record (setup checklist)
- [ ] `update.bat` run; `start_watchdog.bat` running; dashboard open at
      `http://localhost:8000`.
- [ ] `config.yaml`: `model: "yolo11s.pt"`, `imgsz: 1280`, `confidence: 0.15`,
      `low_light: "auto"`, `trigger.disturb_thresh: 60`,
      `trigger.disturb_needs_person: false`.
- [ ] `ANTHROPIC_API_KEY` set in the shell before launch (for the verdict).
- [ ] Two clips ready: **(A)** the real night theft clip; **(B)** [FILL: a
      clearer/normal clip that stays quiet — proves no false alarms].
- [ ] Browser zoom 100%, window maximized, notifications silenced, clean desktop.
- [ ] Screen recorder ready (OBS / Xbox Game Bar / Loom) at 1080p.

## Take 1 — the catch (your hero clip, ~60s)
1. **Open on the Forensic Lab** (empty state). *Say:* "This is real society CCTV
   — dark, low-resolution, the footage every AI camera fails on."
2. **Tick "Smart AI Review."** Drag in clip **A**. Click **Analyze**.
3. **While it runs:** *Say:* "It's enhancing the frames so it can see in the
   dark, tracking people and vehicles, and reasoning about what's happening."
4. **On results — point, in this order:**
   - **Claude verdict banner:** read it aloud — *"HIGH — car driven away after
     tampering, likely theft."*
   - **Incident card:** *"One clean incident — the whole theft, not fifty
     alerts. Culprit tracked, timed 11–60 seconds."*
   - **Click the card** → video jumps to the moment. Let it play 3–4s.
5. *Close:* "It caught the break-in at the car window and explained it in plain
   English — the instant it happened."

## Take 2 — no false alarm (the trust proof, ~20s)
1. Upload clip **B** (normal / owner activity).
2. Show it stays **NORMAL/quiet** — no incident, or Claude says *"nothing clearly
   suspicious."* *Say:* "Just as important — it stays silent on normal activity.
   That's why guards will actually keep it on."

## Take 3 — under the hood (optional, for technical investors, ~20s)
1. Open the annotated video; point to the **boxes on the person + vehicle** and
   the **HYBRID overlay line** (scores/decision).
2. *Say:* "Every decision is explainable — you can see exactly what it saw and
   why it fired."

## Narration one-liners (pick what feels natural)
- "Existing AI cameras are demoed on clean daylight video. Real CCTV is dark and
  grainy — that's what we tuned for."
- "No single signal can raise an alarm — that's how we kill the false alarms
  that get these systems switched off."
- "Detection runs on-site for free. Claude only reviews the flagged clip —
  paise per incident, not a cloud subscription."
- "Raw footage never leaves the building. Only the evidence does."

## Editing
- Trim dead air during analysis (or speed it 2×) — keep the reveal crisp.
- Add a one-line caption per section (Verdict / Incident / Jump-to-moment).
- Export 1080p MP4. Keep the **raw full-length** capture as the pitch backup.

## If something misbehaves on the day
- Don't debug live — cut to the **backup recording**.
- If the verdict is slow (API), pre-run the analysis so the result is cached and
  you re-open the finished job.
