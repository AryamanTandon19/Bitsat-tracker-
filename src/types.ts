// ---------------------------------------------------------------------------
// Core domain types for the BITSAT 330+ Adaptive Tracker
// ---------------------------------------------------------------------------

export type SubjectKey = 'math' | 'physics' | 'chemistry' | 'english';

export type Tier = 1 | 2;
export type Klass = 11 | 12;

/** A single micro-topic. This is the atomic unit the engine schedules. */
export interface Topic {
  id: string;
  name: string;
  /** Estimated focused study hours required to clear this micro-topic. */
  estHours: number;
  /** Default number of practice questions to solve for this micro-topic. */
  targetQuestions: number;
}

/** A chapter groups micro-topics and carries weightage metadata. */
export interface Chapter {
  id: string;
  subject: SubjectKey;
  name: string;
  klass: Klass;
  tier: Tier;
  /** Relative weightage used for prioritisation & drop ordering (higher = keep). */
  weight: number;
  topics: Topic[];
}

/** The binary outcome of a study block. */
export type BlockState = 'pending' | 'green' | 'red';

/** Persisted outcome for a single unit (micro-topic). */
export interface UnitEntry {
  unitId: string;
  date: string; // ISO yyyy-mm-dd of last attempt
  targetQ: number;
  actualQ: number;
  state: BlockState;
}

export type BlockKind =
  | 'study'
  | 'englr'
  | 'mock'
  | 'erroranalysis'
  | 'revision'
  | 'formula'
  | 'drill';

/** A concrete, time-bounded block placed on a calendar day by the engine. */
export interface ScheduledBlock {
  /** Stable id for this block on this day. */
  id: string;
  index: number;
  kind: BlockKind;
  subject: SubjectKey | 'mixed';
  chapterId?: string;
  chapterName?: string;
  unitId?: string;
  topicName: string;
  durationHours: number;
  targetQuestions: number;
  startLabel: string;
  endLabel: string;
}

export type ErrorTag = 'calculation' | 'concept' | 'time';

export interface MockEntry {
  id: string;
  date: string;
  score: number;
  attempted: number;
  correct: number;
  incorrect: number;
}

export interface ErrorLogEntry {
  id: string;
  date: string;
  subject: SubjectKey;
  topic: string;
  tag: ErrorTag;
  note: string;
}

/** University Semester Exam Blackout window (mid-terms / term-end). */
export interface ExamBlackout {
  startDate: string;
  endDate: string;
  label: string;
  /**
   * Set true once the post-exam Iterative Re-balancing Engine has auto-fired
   * on reaching the End Date, so it only triggers once.
   */
  resolved: boolean;
}

export interface Settings {
  /** ISO date of the BITSAT exam. */
  examDate: string;
  /** ISO date college (VIT-AP) resumes — Phase 1 -> Phase 2 boundary. */
  collegeStartDate: string;
  /** Hour of day (0-23) the study schedule starts. */
  studyStartHour: number;
  /** Active University Semester Exam Blackout, or null when none scheduled. */
  examBlackout: ExamBlackout | null;
}

export interface AppState {
  version: number;
  settings: Settings;
  /** Keyed by unitId. */
  entries: Record<string, UnitEntry>;
  mocks: MockEntry[];
  errors: ErrorLogEntry[];
  /** Chapter ids the user has explicitly de-prioritised / dropped. */
  droppedChapters: string[];
  /** Timestamp of last manual re-balance, for UI feedback. */
  lastRebalance: number;
}
