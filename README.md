# BITSAT 330+ Adaptive Preparation Tracker

A highly adaptive, **state-aware** BITSAT preparation tracker built for a
partial-dropper balancing a **CSE @ VIT-AP** curriculum alongside their 330+
attempt. The app shifts study budgets by calendar phase, scores every study
block GREEN/RED, and **automatically re-balances the backlog** across the
remaining timeline without ever breaching the daily hour cap.

> **Score target:** `(112 correct × 3) − (6 wrong × 1) = 330 / 390`
> **Exam shape:** 130 Q — Math 40 · Physics 30 · Chemistry 30 · English & LR 30
> **Accuracy floor:** strictly **> 95%** (negative marking is unforgiving).

---

## Tech Stack

- **Vite + React 18 + TypeScript** — fast, modular SPA
- **Tailwind CSS** — utility-first dark UI
- **Recharts** — accuracy & score trend plots
- **localStorage** — local-first persistence; every log survives a refresh
  (no backend, no account, fully offline)

## Quick start

```bash
npm install
npm run dev       # http://localhost:5173
npm run build     # type-check + production build
npm run preview   # serve the production build
```

---

## The Engine

### 1. Dynamic phase shifting (calendar-driven)

| Phase | Window | Daily cap | Behaviour |
|------|--------|-----------|-----------|
| **Phase 1 — Pre-College** | today → Aug 31 | **8.5 h** | Full new-syllabus throughput |
| **Phase 2 — VIT-AP Balance** | Sept 1 → T-60d | **5.0 h** | Auto-downscaled around college |
| **Phase 3 — Terminal Revision** | final **60 days** | locked | Daily full mock + error analysis + weak-link drilling |

Phase boundaries are computed from `collegeStartDate` and `examDate` (both
editable in **Settings**), so the whole schedule re-derives the moment a date
changes or the real calendar rolls into a new phase.

### 2. Iterative backlog re-balancing (the critical feature)

- Every study block is binary: **GREEN (target met)** or **RED (failed/missed)**.
- A **RED** block pushes its micro-topic into the global **Backlog Queue**.
- The scheduler immediately **re-distributes all pending topics** across the
  remaining days of the current phase — backlog items lead the queue — while
  **never exceeding the daily hour limit** (8.5 h / 5 h).
- When the timeline is too compressed to absorb the load, the **feasibility
  analyser** highlights exactly which **Tier-2 (low-weightage)** chapters must
  be **auto-dropped** to protect **Tier-1** mastery, with one-click
  "Auto-drop to secure Tier-1".

Prioritisation order: `RED backlog → Tier-1 → chapter weight → subject interleave`.

### 3. High-weightage register (pre-populated)

- **Mathematics:** Coordinate Geometry (Straight Lines, Circles, Conics),
  Vectors & 3D, Calculus (Limits, Continuity, AoD, Integrals), Matrices &
  Determinants.
- **Physics:** Electrostatics, Current Electricity, Heat & Thermodynamics,
  Modern Physics & Semiconductors, Mechanics Foundations (Work–Energy,
  Rotational MoI).
- **Chemistry:** Chemical Bonding, GOC, Coordination Compounds, Inorganic
  blocks (p/d/f), Physical-Chemistry numericals (Kinetics, Electrochemistry,
  Equilibrium).
- **English & LR:** a continuous 45-minute baseline slot, **4×/week**
  (Mon/Wed/Fri/Sun).

Each chapter carries a `tier` (1 = protect, 2 = droppable), a `weight`, and a
class tag (11/12); every micro-topic carries an hour estimate and a question
target.

---

## Pages

1. **Live Dashboard** — phase indicator + countdowns (college start & exam),
   today's chronological blocks, daily *Questions Target vs Solved* bar,
   accuracy snapshot with a hard **< 95%** warning, and the backlog alert box.
2. **Daily Block Execution** — each block renders its number, duration,
   subject, exact micro-topic, a *Target vs Actual questions* input, and big
   **RED / GREEN** resolution buttons.
3. **Adaptive Syllabus & Backlog Manager** — Class 11 / Class 12 master
   checklist, per-subject progress bars (driven by GREEN blocks), the backlog
   section, and **Re-balance Syllabus Now**.
4. **Mock Test & Accuracy Analytics** — log full mocks (date, attempted,
   correct, incorrect → auto score & accuracy), plotted accuracy/score trends
   against the 95% / 330 reference lines, plus an **Error Log** tagging every
   mistake as `Calculation Error`, `Concept Missing`, or `Time Panic`.

---

## Project layout

```
src/
  data/syllabus.ts       # high-weightage register + full syllabus
  lib/dates.ts           # phase logic, capacities, countdowns
  lib/engine.ts          # scheduler, backlog re-balance, feasibility, metrics
  lib/storage.ts         # localStorage load/save + defaults
  context/AppContext.tsx # reducer store + derived schedule (useMemo)
  components/            # Dashboard, BlockExecution, SyllabusManager,
                         # MockAnalytics, SettingsPanel, ui primitives
```

All state is local to the browser. Use **Settings → Danger Zone** to reset.
