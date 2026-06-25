import { useState } from 'react';
import type { ScheduledBlock } from '../types';
import { useApp } from '../context/AppContext';
import { SUBJECT_META } from '../data/syllabus';
import { prettyDate } from '../lib/dates';
import { EmptyState, Pill, SectionTitle } from './ui';

const KIND_LABEL: Record<ScheduledBlock['kind'], string> = {
  study: 'Study',
  englr: 'English & LR',
  mock: 'Mock Test',
  erroranalysis: 'Error Analysis',
  revision: 'Revision',
  formula: 'Formula Flash',
  drill: 'Speed Drill',
};

function subjectColor(b: ScheduledBlock): string {
  if (b.subject === 'mixed') return '#94a3b8';
  return SUBJECT_META[b.subject].color;
}

export function BlockCard({ block }: { block: ScheduledBlock }) {
  const { state, dispatch, derived } = useApp();
  const existing = block.unitId ? state.entries[block.unitId] : undefined;
  const resolvedToday = existing && existing.date === derived.today ? existing : undefined;

  const [target, setTarget] = useState<number>(
    resolvedToday?.targetQ ?? block.targetQuestions
  );
  const [actual, setActual] = useState<number>(resolvedToday?.actualQ ?? 0);

  const color = subjectColor(block);
  const trackable = !!block.unitId;
  // Formula Flash is qualitative (no new theory, no question target).
  const quantified = trackable && block.targetQuestions > 0;
  const currentState = resolvedToday?.state ?? 'pending';

  const resolve = (s: 'green' | 'red') => {
    if (!block.unitId) return;
    dispatch({
      type: 'RESOLVE_BLOCK',
      unitId: block.unitId,
      state: s,
      targetQ: target,
      actualQ: actual,
    });
  };

  return (
    <div
      className="card relative overflow-hidden"
      style={{ borderColor: `${color}55` }}
    >
      <div
        className="absolute left-0 top-0 h-full w-1.5"
        style={{ background: color }}
      />
      <div className="flex flex-wrap items-start justify-between gap-3 pl-2">
        <div>
          {/* Header: Block Number, Duration, Subject */}
          <div className="flex flex-wrap items-center gap-2">
            <Pill color={color} text={color}>
              Block #{block.index + 1}
            </Pill>
            <Pill>{KIND_LABEL[block.kind]}</Pill>
            <span className="text-xs text-slate-400">
              {block.startLabel} – {block.endLabel} · {block.durationHours}h
            </span>
          </div>
          {/* Topic Parameter */}
          <div className="mt-2 text-base font-bold">{block.topicName}</div>
          {block.chapterName && (
            <div className="text-xs text-slate-400">
              {block.subject !== 'mixed' ? SUBJECT_META[block.subject].label : 'Mixed'} ·{' '}
              {block.chapterName}
            </div>
          )}
        </div>
        {currentState !== 'pending' && (
          <Pill
            color={currentState === 'green' ? '#22c55e' : '#ef4444'}
            text={currentState === 'green' ? '#22c55e' : '#ef4444'}
          >
            {currentState === 'green' ? '✓ Target Met' : '✕ Backlogged'}
          </Pill>
        )}
      </div>

      {trackable ? (
        <>
          {/* Quantifiable target */}
          {quantified ? (
            <div className="mt-4 grid grid-cols-2 gap-3 pl-2">
              <label className="block">
                <span className="label">Target Questions</span>
                <input
                  type="number"
                  min={0}
                  className="input mt-1"
                  value={target}
                  onChange={(e) => setTarget(Math.max(0, Number(e.target.value)))}
                />
              </label>
              <label className="block">
                <span className="label">Actual Solved</span>
                <input
                  type="number"
                  min={0}
                  className="input mt-1"
                  value={actual}
                  onChange={(e) => setActual(Math.max(0, Number(e.target.value)))}
                />
              </label>
            </div>
          ) : (
            <div className="mt-3 pl-2 text-sm text-slate-400">
              Qualitative maintenance block — no question target. Read through and
              mark done.
            </div>
          )}

          {/* Binary state toggle */}
          <div className="mt-4 grid grid-cols-2 gap-3 pl-2">
            <button
              onClick={() => resolve('red')}
              className={`btn py-3 text-base ${
                currentState === 'red'
                  ? 'bg-bad text-white ring-2 ring-bad/60'
                  : 'border border-bad/60 bg-bad/10 text-bad hover:bg-bad/20'
              }`}
            >
              ✕ RED · Block Failed
            </button>
            <button
              onClick={() => resolve('green')}
              className={`btn py-3 text-base ${
                currentState === 'green'
                  ? 'bg-good text-white ring-2 ring-good/60'
                  : 'border border-good/60 bg-good/10 text-good hover:bg-good/20'
              }`}
            >
              ✓ GREEN · Target Met
            </button>
          </div>
          {currentState !== 'pending' && (
            <button
              onClick={() => dispatch({ type: 'RESET_BLOCK', unitId: block.unitId! })}
              className="mt-2 pl-2 text-xs text-slate-400 underline-offset-2 hover:underline"
            >
              Reset this block
            </button>
          )}
        </>
      ) : (
        <div className="mt-3 pl-2 text-sm text-slate-400">
          {block.kind === 'mock'
            ? 'Log the full result on the Mock Test & Analytics page.'
            : 'No question target — qualitative block.'}
        </div>
      )}
    </div>
  );
}

export default function BlockExecution() {
  const { derived } = useApp();
  const blocks = derived.todayBlocks;

  return (
    <div className="space-y-4">
      <SectionTitle
        right={<span className="text-sm text-slate-400">{prettyDate(derived.today)}</span>}
      >
        Daily Block Execution
      </SectionTitle>
      {blocks.length === 0 ? (
        <EmptyState>
          No blocks scheduled for today — the syllabus is fully cleared, or today
          falls outside the active study window. Check the Syllabus Manager.
        </EmptyState>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {blocks.map((b) => (
            <BlockCard key={b.id} block={b} />
          ))}
        </div>
      )}
    </div>
  );
}
