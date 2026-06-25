import { useState } from 'react';
import { useApp } from '../context/AppContext';
import { SUBJECT_META } from '../data/syllabus';
import type { ExamBlackout } from '../types';
import { daysBetween, prettyDate, todayISO } from '../lib/dates';
import { Pill, SectionTitle } from './ui';

const ACCENT = '#a78bfa';

function ScheduleForm({
  initial,
  onClose,
}: {
  initial: ExamBlackout | null;
  onClose: () => void;
}) {
  const { dispatch } = useApp();
  const today = todayISO();
  const [label, setLabel] = useState(initial?.label ?? 'VIT-AP Mid-Term Exams');
  const [startDate, setStartDate] = useState(initial?.startDate ?? today);
  const [endDate, setEndDate] = useState(initial?.endDate ?? today);

  const valid = startDate <= endDate;
  const span = valid ? daysBetween(startDate, endDate) + 1 : 0;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    dispatch({
      type: 'SET_BLACKOUT',
      blackout: { label: label.trim() || 'Exam Week', startDate, endDate, resolved: false },
    });
    onClose();
  };

  return (
    <form className="mt-3 space-y-3 rounded-xl border border-edge bg-ink/40 p-3" onSubmit={submit}>
      <label className="block">
        <span className="label">Exam label</span>
        <input className="input mt-1" value={label} onChange={(e) => setLabel(e.target.value)} />
      </label>
      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="label">Start date</span>
          <input
            type="date"
            className="input mt-1"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
          />
        </label>
        <label className="block">
          <span className="label">End date</span>
          <input
            type="date"
            className="input mt-1"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
          />
        </label>
      </div>
      <div className="flex items-center gap-3">
        <button className="btn-brand" disabled={!valid} style={{ background: ACCENT }}>
          Activate Blackout ({span} day{span === 1 ? '' : 's'})
        </button>
        <button type="button" className="btn-ghost" onClick={onClose}>
          Cancel
        </button>
        {!valid && <span className="text-xs text-bad">End date must be on/after start date.</span>}
      </div>
    </form>
  );
}

export default function BlackoutPanel() {
  const { state, dispatch, derived } = useApp();
  const [editing, setEditing] = useState(false);
  const bo = state.settings.examBlackout;
  const status = derived.blackout;
  const today = derived.today;

  const upcoming = bo && today < bo.startDate;
  const past = bo && today > bo.endDate;

  return (
    <div className="card" style={{ borderColor: `${ACCENT}55` }}>
      <SectionTitle
        right={
          bo ? (
            <div className="flex gap-2">
              <button className="btn-ghost text-xs" onClick={() => setEditing((v) => !v)}>
                {editing ? 'Close' : 'Edit'}
              </button>
              <button
                className="btn-ghost text-xs text-bad"
                onClick={() => {
                  dispatch({ type: 'CLEAR_BLACKOUT' });
                  setEditing(false);
                }}
              >
                Clear
              </button>
            </div>
          ) : (
            <button
              className="btn text-xs text-ink"
              style={{ background: ACCENT }}
              onClick={() => setEditing(true)}
            >
              🎓 Schedule Exam Week
            </button>
          )
        }
      >
        University Semester Exam Blackout
      </SectionTitle>

      {!bo && !editing && (
        <p className="text-sm text-slate-400">
          Activate during VIT-AP mid-terms or term-end exams. Daily capacity drops to a
          strict <span className="font-semibold text-slate-200">1.5h maintenance boundary</span>{' '}
          (Formula Flash + Micro Speed Drill). Syllabus chapters in the window are buffered as
          “Delayed by Exam Blackout” — never marked RED — and auto-rebalanced after the exams.
        </p>
      )}

      {editing && <ScheduleForm initial={bo} onClose={() => setEditing(false)} />}

      {bo && !editing && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Pill color={ACCENT} text={ACCENT}>
              {bo.label}
            </Pill>
            {status.activeToday && (
              <Pill color="#ef4444" text="#fca5a5">● ACTIVE — Maintenance 1.5h</Pill>
            )}
            {upcoming && <Pill color="#f59e0b" text="#fbbf24">Upcoming</Pill>}
            {past && <Pill color="#22c55e" text="#22c55e">Completed · re-balanced</Pill>}
          </div>

          <div className="text-sm text-slate-300">
            {prettyDate(bo.startDate)} → {prettyDate(bo.endDate)}
            {status.activeToday && (
              <span className="ml-2 text-slate-400">
                · {Math.max(0, daysBetween(today, bo.endDate))} day(s) of blackout remaining
              </span>
            )}
            {upcoming && (
              <span className="ml-2 text-slate-400">
                · starts in {daysBetween(today, bo.startDate)} day(s)
              </span>
            )}
          </div>

          {status.activeToday && (
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="rounded-lg border border-edge bg-ink/40 p-3 text-sm">
                <div className="font-semibold">📖 Block 1 · Active Formula Flash</div>
                <div className="text-xs text-slate-400">
                  45 min · Read Formula Diary / Short Notes for high-weightage covered topics. No new theory.
                </div>
              </div>
              <div className="rounded-lg border border-edge bg-ink/40 p-3 text-sm">
                <div className="font-semibold">⚡ Block 2 · Micro Speed Drill</div>
                <div className="text-xs text-slate-400">
                  45 min · Strict target of 15 high-yield, mixed-subject questions.
                </div>
              </div>
            </div>
          )}

          {/* Buffered chapters — "Delayed by Exam Blackout" */}
          {!past && status.deferredChapters.length > 0 && (
            <div className="rounded-lg border border-warn/40 bg-warn/5 p-3">
              <div className="text-sm font-semibold text-warn">
                Delayed by Exam Blackout — {status.deferredChapters.length} chapter(s),{' '}
                {status.deferredUnitCount} topic(s) buffered
              </div>
              <div className="mt-1 text-xs text-slate-400">
                Held safely in a buffer (not counted as RED). They auto-redistribute across the
                remaining Phase-2 days the moment the blackout ends.
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                {status.deferredChapters.slice(0, 10).map((c) => (
                  <Pill key={c.id} color="#f59e0b" text="#fbbf24">
                    {SUBJECT_META[c.subject].short} · {c.name}
                  </Pill>
                ))}
                {status.deferredChapters.length > 10 && (
                  <Pill color="#f59e0b" text="#fbbf24">
                    +{status.deferredChapters.length - 10} more
                  </Pill>
                )}
              </div>
            </div>
          )}

          {past && (
            <div className="rounded-lg border border-good/40 bg-good/5 p-3 text-sm text-slate-300">
              ✓ Blackout window cleared. The Iterative Re-balancing Engine smoothed the buffered
              chapters across your remaining college-day calendar within the 5h limit.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
