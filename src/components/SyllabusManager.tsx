import { useApp } from '../context/AppContext';
import { CHAPTERS, SUBJECT_META } from '../data/syllabus';
import type { Chapter, Klass, SubjectKey } from '../types';
import { unitState } from '../lib/engine';
import { ProgressBar, SectionTitle, Pill, EmptyState } from './ui';

function chapterProgress(state: ReturnType<typeof useApp>['state'], ch: Chapter) {
  const done = ch.topics.filter((t) => unitState(state, t.id) === 'green').length;
  return { done, total: ch.topics.length, pct: Math.round((done / ch.topics.length) * 100) };
}

function ChapterRow({
  chapter,
  recommended,
}: {
  chapter: Chapter;
  recommended: boolean;
}) {
  const { state, dispatch } = useApp();
  const dropped = state.droppedChapters.includes(chapter.id);
  const { done, total, pct } = chapterProgress(state, chapter);
  const color = SUBJECT_META[chapter.subject].color;

  return (
    <div
      className={`rounded-xl border p-3 ${
        dropped
          ? 'border-edge bg-ink/30 opacity-60'
          : recommended
            ? 'border-warn/60 bg-warn/10'
            : 'border-edge bg-ink/30'
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`truncate font-semibold ${dropped ? 'line-through' : ''}`}>
              {chapter.name}
            </span>
            <Pill color={chapter.tier === 1 ? '#38bdf8' : '#64748b'}>
              Tier {chapter.tier}
            </Pill>
            {recommended && !dropped && (
              <Pill color="#f59e0b" text="#fbbf24">drop suggested</Pill>
            )}
            {dropped && <Pill color="#ef4444" text="#fca5a5">dropped</Pill>}
          </div>
          <div className="mt-1 text-xs text-slate-400">
            {done}/{total} micro-topics · weight {chapter.weight}
          </div>
        </div>
        <button
          className="btn-ghost shrink-0 px-2 py-1 text-xs"
          onClick={() =>
            dispatch({
              type: dropped ? 'RESTORE_CHAPTER' : 'DROP_CHAPTER',
              chapterId: chapter.id,
            })
          }
        >
          {dropped ? 'Restore' : 'Drop'}
        </button>
      </div>
      {!dropped && (
        <div className="mt-2">
          <ProgressBar pct={pct} color={color} height={6} />
        </div>
      )}
    </div>
  );
}

function SubjectColumn({
  subject,
  recommendedIds,
}: {
  subject: SubjectKey;
  recommendedIds: Set<string>;
}) {
  const meta = SUBJECT_META[subject];
  const chapters = CHAPTERS.filter((c) => c.subject === subject);
  const byClass: Record<Klass, Chapter[]> = { 11: [], 12: [] };
  for (const c of chapters) byClass[c.klass].push(c);

  return (
    <div className="card">
      <div className="mb-3 flex items-center gap-2">
        <span className="h-3 w-3 rounded-full" style={{ background: meta.color }} />
        <h3 className="font-bold">{meta.label}</h3>
      </div>
      {([11, 12] as Klass[]).map((klass) =>
        byClass[klass].length ? (
          <div key={klass} className="mb-3">
            <div className="label mb-2">Class {klass}</div>
            <div className="space-y-2">
              {byClass[klass]
                .sort((a, b) => a.tier - b.tier || b.weight - a.weight)
                .map((c) => (
                  <ChapterRow
                    key={c.id}
                    chapter={c}
                    recommended={recommendedIds.has(c.id)}
                  />
                ))}
            </div>
          </div>
        ) : null
      )}
    </div>
  );
}

export default function SyllabusManager() {
  const { dispatch, derived } = useApp();
  const feas = derived.feasibility;
  const recommendedIds = new Set(feas.recommendedDrops.map((d) => d.chapter.id));

  return (
    <div className="space-y-5">
      <SectionTitle
        right={
          <button
            className="btn-brand"
            onClick={() => dispatch({ type: 'REBALANCE' })}
            title="Recompute the schedule across remaining days"
          >
            ↻ Re-balance Syllabus Now
          </button>
        }
      >
        Adaptive Syllabus & Backlog Manager
      </SectionTitle>

      {/* Feasibility / capacity banner */}
      <div
        className={`card ${
          feas.feasible
            ? 'border-good/50'
            : feas.critical
              ? 'border-bad/60 bg-bad/10'
              : 'border-warn/60 bg-warn/10'
        }`}
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="font-bold">
              {feas.feasible
                ? '✓ Timeline is feasible within daily hour limits'
                : feas.critical
                  ? '⚠ CRITICAL: Tier-1 alone exceeds remaining capacity'
                  : '⚠ Timeline compressed — Tier-2 chapters must be de-prioritised'}
            </div>
            <div className="mt-1 text-sm text-slate-300">
              Pending {feas.pendingHours.toFixed(1)}h vs capacity{' '}
              {feas.capacityHours.toFixed(1)}h (Tier-1 {feas.tier1Hours.toFixed(1)}h · Tier-2{' '}
              {feas.tier2Hours.toFixed(1)}h) before terminal revision begins.
            </div>
          </div>
          {!feas.feasible && feas.recommendedDrops.length > 0 && (
            <button
              className="btn-ghost border-warn/60 text-warn"
              onClick={() =>
                dispatch({
                  type: 'APPLY_DROPS',
                  chapterIds: feas.recommendedDrops.map((d) => d.chapter.id),
                })
              }
            >
              Auto-drop {feas.recommendedDrops.length} Tier-2 chapter(s) to secure Tier-1
            </button>
          )}
        </div>
        {!feas.feasible && (
          <div className="mt-2 text-sm text-warn">
            Overflow {feas.overflowHours.toFixed(1)}h. Suggested drops:{' '}
            {feas.recommendedDrops.map((d) => d.chapter.name).join(', ') || '— (none can be shed)'}
          </div>
        )}
      </div>

      {/* Subject progress bars */}
      <div className="card">
        <SectionTitle>Per-Subject Completion (GREEN blocks)</SectionTitle>
        <div className="grid gap-3 sm:grid-cols-2">
          {derived.progress.map((p) => (
            <div key={p.subject}>
              <div className="mb-1 flex justify-between text-sm">
                <span className="font-medium">{SUBJECT_META[p.subject].label}</span>
                <span className="text-slate-400">
                  {p.done}/{p.total} · {p.pct}%
                </span>
              </div>
              <ProgressBar pct={p.pct} color={SUBJECT_META[p.subject].color} />
            </div>
          ))}
        </div>
      </div>

      {/* Backlog section */}
      <div className="card">
        <SectionTitle>Backlog Queue ({derived.backlog.length})</SectionTitle>
        {derived.backlog.length === 0 ? (
          <EmptyState>No backlog. Every RED block has been re-absorbed or cleared.</EmptyState>
        ) : (
          <div className="space-y-2">
            {derived.backlog.map((u) => (
              <div
                key={u.topic.id}
                className="flex items-center justify-between rounded-lg border border-bad/40 bg-bad/5 px-3 py-2 text-sm"
              >
                <div>
                  <span className="font-medium">{u.topic.name}</span>
                  <span className="ml-2 text-xs text-slate-400">
                    {SUBJECT_META[u.chapter.subject].label} · {u.chapter.name}
                  </span>
                </div>
                <button
                  className="btn-ghost px-2 py-1 text-xs"
                  onClick={() => dispatch({ type: 'RESET_BLOCK', unitId: u.topic.id })}
                >
                  Clear from backlog
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Master checklist */}
      <div className="grid gap-4 lg:grid-cols-2">
        <SubjectColumn subject="math" recommendedIds={recommendedIds} />
        <SubjectColumn subject="physics" recommendedIds={recommendedIds} />
        <SubjectColumn subject="chemistry" recommendedIds={recommendedIds} />
        <SubjectColumn subject="english" recommendedIds={recommendedIds} />
      </div>
    </div>
  );
}
