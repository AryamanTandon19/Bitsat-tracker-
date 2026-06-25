import { useEffect, useState } from 'react';
import { useApp } from '../context/AppContext';
import { SUBJECT_META } from '../data/syllabus';
import {
  Countdown,
  countdownTo,
  phaseInfo,
  prettyDate,
} from '../lib/dates';
import { TARGET, dailyMetrics } from '../lib/engine';
import { ProgressBar, SectionTitle, Pill } from './ui';
import { BlockCard } from './BlockExecution';

function useCountdown(targetISO: string): Countdown {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 30_000);
    return () => clearInterval(id);
  }, []);
  return countdownTo(targetISO);
}

function CountdownChip({ label, iso, color }: { label: string; iso: string; color: string }) {
  const cd = useCountdown(iso);
  return (
    <div className="rounded-xl border border-edge bg-ink/40 px-4 py-3">
      <div className="label">{label}</div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className="text-2xl font-extrabold" style={{ color }}>
          {cd.days}
        </span>
        <span className="text-sm text-slate-400">days</span>
        <span className="ml-2 font-mono text-sm text-slate-400">
          {String(cd.hours).padStart(2, '0')}:{String(cd.minutes).padStart(2, '0')}
        </span>
      </div>
    </div>
  );
}

export default function Dashboard({ goTo }: { goTo: (page: string) => void }) {
  const { state, derived } = useApp();
  const today = derived.today;
  const info = phaseInfo(today, state.settings);
  const blocks = derived.todayBlocks;
  const metrics = dailyMetrics(state, blocks, today);

  const lastMock = state.mocks[0];
  const phaseColor = info.phase === 1 ? '#22c55e' : info.phase === 2 ? '#f59e0b' : '#ef4444';

  return (
    <div className="space-y-5">
      {/* Phase indicator */}
      <div className="card" style={{ borderColor: `${phaseColor}55` }}>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <span
                className="inline-block h-3 w-3 rounded-full"
                style={{ background: phaseColor }}
              />
              <h1 className="text-xl font-extrabold tracking-tight">
                {info.label}
              </h1>
              <Pill color={phaseColor} text={phaseColor}>
                {info.description}
              </Pill>
            </div>
            <div className="mt-1 text-sm text-slate-400">
              {prettyDate(today)} · Max daily capacity{' '}
              <span className="font-semibold text-slate-200">{info.capacity}h</span>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <CountdownChip
              label="College (VIT-AP) starts"
              iso={state.settings.collegeStartDate}
              color="#f59e0b"
            />
            <CountdownChip
              label="BITSAT exam"
              iso={state.settings.examDate}
              color="#ef4444"
            />
          </div>
        </div>
      </div>

      {/* Daily metric bar */}
      <div className="card">
        <SectionTitle
          right={
            <button className="btn-ghost text-xs" onClick={() => goTo('blocks')}>
              Open Execution View →
            </button>
          }
        >
          Daily Questions Target vs Solved
        </SectionTitle>
        <div className="flex items-center gap-4">
          <div className="flex-1">
            <ProgressBar
              pct={metrics.targetQuestions ? (metrics.solvedQuestions / metrics.targetQuestions) * 100 : 0}
              color="#38bdf8"
              height={16}
            />
          </div>
          <div className="text-right">
            <div className="text-2xl font-extrabold">
              {metrics.solvedQuestions}
              <span className="text-base font-medium text-slate-400">
                {' '}
                / {metrics.targetQuestions}
              </span>
            </div>
            <div className="text-xs text-slate-400">
              {metrics.blocksDone}/{metrics.blocksTotal} blocks resolved
            </div>
          </div>
        </div>
      </div>

      {/* Target engine + accuracy snapshot */}
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="card">
          <div className="label">Target Score</div>
          <div className="mt-1 text-3xl font-extrabold text-brand">
            {TARGET.score}
            <span className="text-base font-medium text-slate-400">/{TARGET.outOf}</span>
          </div>
          <div className="mt-1 text-xs text-slate-400">
            {TARGET.correct} correct × 3 − 6 wrong × 1
          </div>
        </div>
        <div className="card">
          <div className="label">Last Mock Accuracy</div>
          <div
            className="mt-1 text-3xl font-extrabold"
            style={{
              color: lastMock
                ? lastMock.correct / Math.max(1, lastMock.attempted) >= 0.95
                  ? '#22c55e'
                  : '#ef4444'
                : '#94a3b8',
            }}
          >
            {lastMock ? `${((lastMock.correct / Math.max(1, lastMock.attempted)) * 100).toFixed(1)}%` : '—'}
          </div>
          <div className="mt-1 text-xs text-slate-400">
            Floor: strictly &gt; {TARGET.accuracyFloor}%
          </div>
        </div>
        <div className="card">
          <div className="label">Syllabus Cleared</div>
          <div className="mt-1 text-3xl font-extrabold text-good">{derived.overall}%</div>
          <div className="mt-2">
            <ProgressBar pct={derived.overall} color="#22c55e" />
          </div>
        </div>
      </div>

      {/* Accuracy warning */}
      {lastMock && lastMock.correct / Math.max(1, lastMock.attempted) < 0.95 && (
        <div className="card border-bad/60 bg-bad/10">
          <div className="font-bold text-bad">⚠ Accuracy below the 95% boundary</div>
          <div className="mt-1 text-sm text-slate-300">
            Your last mock landed at{' '}
            {((lastMock.correct / Math.max(1, lastMock.attempted)) * 100).toFixed(1)}% accuracy.
            With negative marking, accuracy &gt; 95% is non-negotiable for 330+. Shift focus
            to error-categorisation drilling before chasing new attempts.
          </div>
        </div>
      )}

      {/* Backlog alert box */}
      <div className="card">
        <SectionTitle
          right={
            <button className="btn-ghost text-xs" onClick={() => goTo('syllabus')}>
              Backlog Manager →
            </button>
          }
        >
          Backlog Queue
        </SectionTitle>
        {derived.backlog.length === 0 ? (
          <div className="text-sm text-good">
            ✓ Backlog is clear. Every resolved block has met its target.
          </div>
        ) : (
          <div className="space-y-2">
            <div className="text-sm text-warn">
              {derived.backlog.length} item(s) need urgent absorption into the remaining schedule.
            </div>
            <div className="flex flex-wrap gap-2">
              {derived.backlog.slice(0, 8).map((u) => (
                <Pill key={u.topic.id} color="#ef4444" text="#fca5a5">
                  {SUBJECT_META[u.chapter.subject].short} · {u.topic.name}
                </Pill>
              ))}
              {derived.backlog.length > 8 && (
                <Pill color="#ef4444" text="#fca5a5">+{derived.backlog.length - 8} more</Pill>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Today's chronological blocks */}
      <div>
        <SectionTitle>Today’s Time Blocks</SectionTitle>
        {blocks.length === 0 ? (
          <div className="card text-sm text-slate-400">
            No blocks scheduled for today. Either the syllabus is fully cleared or
            today falls outside the active study window.
          </div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {blocks.map((b) => (
              <BlockCard key={b.id} block={b} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
