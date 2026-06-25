import { useState } from 'react';
import { useApp } from '../context/AppContext';
import { clockLabel, daysBetween, prettyDate, todayISO } from '../lib/dates';
import { TERMINAL_REVISION_DAYS, phase3StartISO } from '../lib/dates';
import { SectionTitle } from './ui';

export default function SettingsPanel() {
  const { state, dispatch } = useApp();
  const s = state.settings;
  const [confirmReset, setConfirmReset] = useState(false);

  const today = todayISO();
  const p3 = phase3StartISO(s);

  return (
    <div className="space-y-5">
      <SectionTitle>Settings & Calendar Engine</SectionTitle>

      <div className="card grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="label">College (VIT-AP) start — Phase 1 → 2</span>
          <input
            type="date"
            className="input mt-1"
            value={s.collegeStartDate}
            onChange={(e) =>
              dispatch({ type: 'UPDATE_SETTINGS', settings: { collegeStartDate: e.target.value } })
            }
          />
          <span className="mt-1 block text-xs text-slate-400">
            {prettyDate(s.collegeStartDate)} ·{' '}
            {Math.max(0, daysBetween(today, s.collegeStartDate))} days away
          </span>
        </label>

        <label className="block">
          <span className="label">BITSAT exam date</span>
          <input
            type="date"
            className="input mt-1"
            value={s.examDate}
            onChange={(e) =>
              dispatch({ type: 'UPDATE_SETTINGS', settings: { examDate: e.target.value } })
            }
          />
          <span className="mt-1 block text-xs text-slate-400">
            {prettyDate(s.examDate)} · {Math.max(0, daysBetween(today, s.examDate))} days away
          </span>
        </label>

        <label className="block">
          <span className="label">Daily study start time</span>
          <input
            type="range"
            min={4}
            max={10}
            step={0.25}
            className="mt-3 w-full accent-brand"
            value={s.studyStartHour}
            onChange={(e) =>
              dispatch({
                type: 'UPDATE_SETTINGS',
                settings: { studyStartHour: Number(e.target.value) },
              })
            }
          />
          <span className="mt-1 block text-xs text-slate-400">
            Schedule begins at {clockLabel(s.studyStartHour)}
          </span>
        </label>

        <div className="rounded-xl border border-edge bg-ink/30 p-3 text-sm">
          <div className="label">Phase 3 (Terminal Revision) begins</div>
          <div className="mt-1 font-semibold">{prettyDate(p3)}</div>
          <div className="text-xs text-slate-400">
            Final {TERMINAL_REVISION_DAYS} days are locked to mocks + weak-link drilling.
          </div>
        </div>
      </div>

      <div className="card border-bad/40">
        <SectionTitle>Danger Zone</SectionTitle>
        {!confirmReset ? (
          <button className="btn-ghost text-bad" onClick={() => setConfirmReset(true)}>
            Reset all progress & logs
          </button>
        ) : (
          <div className="flex items-center gap-3">
            <span className="text-sm text-slate-300">
              This wipes all blocks, mocks, errors and drops. Are you sure?
            </span>
            <button
              className="btn bg-bad text-white"
              onClick={() => {
                dispatch({ type: 'RESET_ALL' });
                setConfirmReset(false);
              }}
            >
              Yes, reset
            </button>
            <button className="btn-ghost" onClick={() => setConfirmReset(false)}>
              Cancel
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
