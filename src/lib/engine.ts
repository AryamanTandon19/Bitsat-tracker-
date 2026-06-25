import type {
  AppState,
  Chapter,
  ScheduledBlock,
  SubjectKey,
  Topic,
} from '../types';
import { ALL_TOPIC_IDS, CHAPTERS, TOPIC_INDEX, SUBJECT_META } from '../data/syllabus';
import {
  addDays,
  clockLabel,
  dailyCapacity,
  getPhase,
  isEngLrDay,
  phase3StartISO,
  todayISO,
} from './dates';

// ---------------------------------------------------------------------------
// The adaptive engine: prioritisation, day-walk scheduling, backlog
// re-balancing, and Tier-2 auto-drop feasibility analysis.
// ---------------------------------------------------------------------------

export interface PendingUnit {
  topic: Topic;
  chapter: Chapter;
  isBacklog: boolean; // came from a RED (failed) block
}

/** Status helpers ---------------------------------------------------------- */
export function unitState(state: AppState, unitId: string): 'pending' | 'green' | 'red' {
  return state.entries[unitId]?.state ?? 'pending';
}

export function isChapterDropped(state: AppState, chapterId: string): boolean {
  return state.droppedChapters.includes(chapterId);
}

/**
 * Build the ordered queue of pending units.
 * Priority: RED backlog first, then Tier-1 before Tier-2, then by chapter
 * weight (desc). Subjects are interleaved so a single subject never starves
 * the others. English units are excluded — they ride the Eng/LR slot.
 */
export function buildPendingQueue(state: AppState): PendingUnit[] {
  const pending: PendingUnit[] = [];
  for (const chapter of CHAPTERS) {
    if (chapter.subject === 'english') continue;
    if (isChapterDropped(state, chapter.id)) continue;
    for (const topic of chapter.topics) {
      const st = unitState(state, topic.id);
      if (st === 'green') continue;
      pending.push({ topic, chapter, isBacklog: st === 'red' });
    }
  }

  // Score: lower sorts first.
  const score = (u: PendingUnit) =>
    (u.isBacklog ? 0 : 1) * 1_000_000 +
    (u.chapter.tier - 1) * 100_000 +
    (100 - u.chapter.weight) * 100;

  pending.sort((a, b) => score(a) - score(b));

  // Round-robin interleave by subject to keep variety within a day.
  const buckets: Record<string, PendingUnit[]> = {};
  for (const u of pending) (buckets[u.chapter.subject] ??= []).push(u);
  const order: SubjectKey[] = ['math', 'physics', 'chemistry'];
  const interleaved: PendingUnit[] = [];
  let added = true;
  while (added) {
    added = false;
    for (const subj of order) {
      const b = buckets[subj];
      if (b && b.length) {
        interleaved.push(b.shift()!);
        added = true;
      }
    }
  }
  // Backlog units must still lead regardless of interleave.
  interleaved.sort((a, b) => (a.isBacklog === b.isBacklog ? 0 : a.isBacklog ? -1 : 1));
  return interleaved;
}

/** Pending English units, in syllabus order (for the Eng/LR slot rotation). */
function pendingEnglishUnits(state: AppState): PendingUnit[] {
  const out: PendingUnit[] = [];
  for (const chapter of CHAPTERS) {
    if (chapter.subject !== 'english') continue;
    for (const topic of chapter.topics) {
      if (unitState(state, topic.id) === 'green') continue;
      out.push({ topic, chapter, isBacklog: unitState(state, topic.id) === 'red' });
    }
  }
  return out;
}

export interface DayPlan {
  date: string;
  blocks: ScheduledBlock[];
}

export interface ScheduleResult {
  /** Map of ISO date -> ordered blocks (only days that have blocks). */
  byDate: Map<string, ScheduledBlock[]>;
  /** Units that could not be placed before Phase 3 began. */
  unplaced: PendingUnit[];
  /** Last calendar date that received a study block. */
  lastStudyDate: string | null;
}

const MIN_STUDY_BLOCK = 0.5;

function timeline(
  startISO: string,
  endISO: string,
): string[] {
  const days: string[] = [];
  let cur = startISO;
  let guard = 0;
  while (cur <= endISO && guard < 1200) {
    days.push(cur);
    cur = addDays(cur, 1);
    guard++;
  }
  return days;
}

/**
 * Core scheduler. Walks the calendar from `fromISO` to the exam, placing the
 * ordered pending queue into daily blocks without breaching the phase hour cap.
 * Phase 3 days are locked into revision + mock + error-analysis blocks.
 */
export function buildSchedule(state: AppState, fromISO = todayISO()): ScheduleResult {
  const { settings } = state;
  const queue = buildPendingQueue(state);
  const englishQueue = pendingEnglishUnits(state);
  let englishCursor = 0;

  const byDate = new Map<string, ScheduledBlock[]>();
  const days = timeline(fromISO, settings.examDate);
  let lastStudyDate: string | null = null;

  let qi = 0; // queue cursor

  for (const date of days) {
    const phase = getPhase(date, settings);
    const blocks: ScheduledBlock[] = [];
    let clock = settings.studyStartHour;
    let idx = 0;

    const push = (
      partial: Omit<ScheduledBlock, 'id' | 'index' | 'startLabel' | 'endLabel'>
    ) => {
      const start = clock;
      const end = clock + partial.durationHours;
      blocks.push({
        ...partial,
        id: `${date}#${idx}`,
        index: idx,
        startLabel: clockLabel(start),
        endLabel: clockLabel(end),
      });
      idx++;
      // Insert a short break after blocks longer than 1h.
      clock = end + (partial.durationHours >= 1 ? 1 / 6 : 0);
    };

    if (phase === 3) {
      // Terminal revision: full mock + error analysis + weak-link drilling.
      push({
        kind: 'mock',
        subject: 'mixed',
        topicName: 'Full-Length BITSAT Mock Test (130 Q · 3h)',
        durationHours: 3,
        targetQuestions: 130,
      });
      push({
        kind: 'erroranalysis',
        subject: 'mixed',
        topicName: 'Error Analysis & Mistake Categorisation',
        durationHours: 1,
        targetQuestions: 0,
      });
      const drill = nextRevisionTopic(state, date);
      push({
        kind: 'revision',
        subject: drill.subject,
        chapterId: drill.chapterId,
        chapterName: drill.chapterName,
        topicName: `Weak-Link Drill — ${drill.topicName}`,
        durationHours: 1,
        targetQuestions: 30,
      });
      byDate.set(date, blocks);
      continue;
    }

    let remaining = dailyCapacity(date, settings);

    // English & LR baseline slot (45 min, 4x/week).
    if (isEngLrDay(date) && remaining >= 0.75) {
      const eng = englishQueue.length
        ? englishQueue[englishCursor % englishQueue.length]
        : null;
      englishCursor++;
      const engTopic = eng?.topic;
      push({
        kind: 'englr',
        subject: 'english',
        chapterId: eng?.chapter.id,
        chapterName: eng?.chapter.name,
        unitId: engTopic?.id,
        topicName: engTopic
          ? `${eng!.chapter.name.split('—')[1]?.trim() ?? eng!.chapter.name}: ${engTopic.name}`
          : 'English & LR — Mixed Practice Set',
        durationHours: 0.75,
        targetQuestions: engTopic?.targetQuestions ?? 25,
      });
      remaining -= 0.75;
    }

    // Fill the rest of the day with the prioritised study queue.
    while (qi < queue.length && remaining >= MIN_STUDY_BLOCK) {
      const u = queue[qi];
      const dur = Math.min(u.topic.estHours, Math.max(remaining, MIN_STUDY_BLOCK));
      if (u.topic.estHours > remaining + 1e-6) {
        // Won't fully fit — only split if there is a meaningful chunk left.
        if (remaining < 1) break;
      }
      push({
        kind: 'study',
        subject: u.chapter.subject,
        chapterId: u.chapter.id,
        chapterName: u.chapter.name,
        unitId: u.topic.id,
        topicName: u.topic.name,
        durationHours: Math.round(dur * 100) / 100,
        targetQuestions: u.topic.targetQuestions,
      });
      remaining -= dur;
      qi++;
    }

    if (blocks.some((b) => b.kind === 'study')) lastStudyDate = date;
    if (blocks.length) byDate.set(date, blocks);

    // Once everything is placed we can stop building future days early,
    // but keep generating through today so the dashboard is always complete.
    if (qi >= queue.length && date >= fromISO && date !== fromISO) break;
  }

  // Units left in the queue could not be placed before the exam date.
  return { byDate, unplaced: queue.slice(qi), lastStudyDate };
}

/** Pick a revision topic for a Phase-3 day, rotating across Tier-1 chapters. */
function nextRevisionTopic(state: AppState, date: string) {
  const tier1 = CHAPTERS.filter(
    (c) => c.tier === 1 && c.subject !== 'english' && !isChapterDropped(state, c.id)
  );
  const pool = tier1.length ? tier1 : CHAPTERS.filter((c) => c.subject !== 'english');
  const seed = Number(date.replace(/-/g, '')) % pool.length;
  const chapter = pool[seed];
  const topic = chapter.topics[seed % chapter.topics.length];
  return {
    subject: chapter.subject,
    chapterId: chapter.id,
    chapterName: chapter.name,
    topicName: topic.name,
  };
}

// ----------------------------- Feasibility ---------------------------------

export interface DropRecommendation {
  chapter: Chapter;
  hours: number;
}

export interface Feasibility {
  /** true when Tier-1 + remaining Tier-2 fit inside the remaining capacity. */
  feasible: boolean;
  critical: boolean; // even Tier-1 alone overflows
  capacityHours: number;
  tier1Hours: number;
  tier2Hours: number;
  pendingHours: number;
  overflowHours: number;
  recommendedDrops: DropRecommendation[];
}

/** Remaining new-syllabus capacity between today and Phase-3 start. */
function remainingNewSyllabusCapacity(state: AppState, fromISO = todayISO()): number {
  const { settings } = state;
  const end = addDays(phase3StartISO(settings), -1);
  let total = 0;
  let cur = fromISO;
  let guard = 0;
  while (cur <= end && guard < 1200) {
    let cap = dailyCapacity(cur, settings);
    if (isEngLrDay(cur)) cap -= 0.75; // reserve the Eng/LR slot
    total += Math.max(0, cap);
    cur = addDays(cur, 1);
    guard++;
  }
  return total;
}

function pendingHoursByTier(state: AppState) {
  let tier1 = 0;
  let tier2 = 0;
  const tier2Chapters: Record<string, number> = {};
  for (const chapter of CHAPTERS) {
    if (chapter.subject === 'english') continue;
    if (isChapterDropped(state, chapter.id)) continue;
    let chHours = 0;
    for (const topic of chapter.topics) {
      if (unitState(state, topic.id) === 'green') continue;
      chHours += topic.estHours;
    }
    if (chHours === 0) continue;
    if (chapter.tier === 1) tier1 += chHours;
    else {
      tier2 += chHours;
      tier2Chapters[chapter.id] = chHours;
    }
  }
  return { tier1, tier2, tier2Chapters };
}

export function analyseFeasibility(state: AppState, fromISO = todayISO()): Feasibility {
  const capacityHours = remainingNewSyllabusCapacity(state, fromISO);
  const { tier1, tier2, tier2Chapters } = pendingHoursByTier(state);
  const pendingHours = tier1 + tier2;
  const overflow = pendingHours - capacityHours;

  if (overflow <= 0) {
    return {
      feasible: true,
      critical: false,
      capacityHours,
      tier1Hours: tier1,
      tier2Hours: tier2,
      pendingHours,
      overflowHours: 0,
      recommendedDrops: [],
    };
  }

  // Need to shed `overflow` hours. Drop Tier-2 chapters, lowest weight first.
  const droppable = Object.keys(tier2Chapters)
    .map((id) => TOPIC_INDEX[CHAPTERS.find((c) => c.id === id)!.topics[0].id].chapter)
    .map((c) => ({ chapter: c, hours: tier2Chapters[c.id] }))
    .sort((a, b) => a.chapter.weight - b.chapter.weight);

  const recommendedDrops: DropRecommendation[] = [];
  let shed = 0;
  for (const d of droppable) {
    if (shed >= overflow) break;
    recommendedDrops.push(d);
    shed += d.hours;
  }

  // Critical: even after dropping every Tier-2 chapter, Tier-1 still overflows.
  const critical = tier1 > capacityHours;

  return {
    feasible: false,
    critical,
    capacityHours,
    tier1Hours: tier1,
    tier2Hours: tier2,
    pendingHours,
    overflowHours: Math.round(overflow * 10) / 10,
    recommendedDrops,
  };
}

// ------------------------------ Progress -----------------------------------

export interface SubjectProgress {
  subject: SubjectKey;
  total: number;
  done: number;
  pct: number;
}

export function subjectProgress(state: AppState): SubjectProgress[] {
  const subjects: SubjectKey[] = ['math', 'physics', 'chemistry', 'english'];
  return subjects.map((subject) => {
    let total = 0;
    let done = 0;
    for (const chapter of CHAPTERS) {
      if (chapter.subject !== subject) continue;
      if (isChapterDropped(state, chapter.id)) continue;
      for (const topic of chapter.topics) {
        total++;
        if (unitState(state, topic.id) === 'green') done++;
      }
    }
    return {
      subject,
      total,
      done,
      pct: total ? Math.round((done / total) * 100) : 0,
    };
  });
}

export function overallProgress(state: AppState): number {
  const completed = ALL_TOPIC_IDS.filter((id) => unitState(state, id) === 'green').length;
  return Math.round((completed / ALL_TOPIC_IDS.length) * 100);
}

/** Units currently sitting in the backlog (RED) queue. */
export function backlogUnits(state: AppState): PendingUnit[] {
  const out: PendingUnit[] = [];
  for (const id of ALL_TOPIC_IDS) {
    if (unitState(state, id) === 'red') {
      const { topic, chapter } = TOPIC_INDEX[id];
      if (isChapterDropped(state, chapter.id)) continue;
      out.push({ topic, chapter, isBacklog: true });
    }
  }
  return out;
}

// --------------------------- Daily metrics ---------------------------------

export interface DailyMetrics {
  targetQuestions: number;
  solvedQuestions: number;
  blocksTotal: number;
  blocksDone: number;
}

export function dailyMetrics(state: AppState, blocks: ScheduledBlock[], date: string): DailyMetrics {
  const targetQuestions = blocks.reduce((s, b) => s + b.targetQuestions, 0);
  let solvedQuestions = 0;
  let blocksDone = 0;
  for (const b of blocks) {
    if (!b.unitId) continue;
    const e = state.entries[b.unitId];
    if (e && e.date === date) {
      solvedQuestions += e.actualQ;
      if (e.state !== 'pending') blocksDone++;
    }
  }
  // Also fold in any mock questions solved today (Phase 3).
  return {
    targetQuestions,
    solvedQuestions,
    blocksTotal: blocks.length,
    blocksDone,
  };
}

// --------------------------- Score projection ------------------------------

export const TARGET = {
  score: 330,
  outOf: 390,
  correct: 112,
  totalQ: 130,
  marksPerCorrect: 3,
  penaltyPerWrong: 1,
  accuracyFloor: 95,
  subjectQuestions: SUBJECT_META,
};

export function projectedScore(correct: number, wrong: number): number {
  return correct * TARGET.marksPerCorrect - wrong * TARGET.penaltyPerWrong;
}

export function accuracy(correct: number, attempted: number): number {
  if (!attempted) return 0;
  return Math.round((correct / attempted) * 1000) / 10;
}
