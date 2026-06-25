import type { Settings } from '../types';

export type Phase = 1 | 2 | 3;

export const MS_PER_DAY = 24 * 60 * 60 * 1000;

/** ISO yyyy-mm-dd for a Date in local time. */
export function toISO(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

export function parseISO(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}

export function todayISO(): string {
  return toISO(new Date());
}

/** Whole-day difference (b - a), floored. */
export function daysBetween(aISO: string, bISO: string): number {
  const a = parseISO(aISO).getTime();
  const b = parseISO(bISO).getTime();
  return Math.round((b - a) / MS_PER_DAY);
}

export function addDays(iso: string, n: number): string {
  const d = parseISO(iso);
  d.setDate(d.getDate() + n);
  return toISO(d);
}

/** Phase 3 begins this many days before the exam. */
export const TERMINAL_REVISION_DAYS = 60;

export function phase3StartISO(settings: Settings): string {
  return addDays(settings.examDate, -TERMINAL_REVISION_DAYS);
}

/** Determine the phase for a given calendar date. */
export function getPhase(iso: string, settings: Settings): Phase {
  if (iso >= phase3StartISO(settings)) return 3;
  if (iso >= settings.collegeStartDate) return 2;
  return 1;
}

export const PHASE_CAPACITY: Record<Phase, number> = {
  1: 8.5,
  2: 5.0,
  3: 5.0,
};

/** Strict maintenance boundary during a University Exam Blackout (1.0–1.5h). */
export const MAINTENANCE_CAP = 1.5;

/** Is this calendar date inside an active exam-blackout window? */
export function isBlackoutDay(iso: string, settings: Settings): boolean {
  const bo = settings.examBlackout;
  if (!bo) return false;
  return iso >= bo.startDate && iso <= bo.endDate;
}

export function dailyCapacity(iso: string, settings: Settings): number {
  if (isBlackoutDay(iso, settings)) return MAINTENANCE_CAP;
  return PHASE_CAPACITY[getPhase(iso, settings)];
}

export interface PhaseInfo {
  phase: Phase;
  capacity: number;
  label: string;
  description: string;
  /** True when an exam blackout overrides the normal phase budget. */
  blackout: boolean;
}

export function phaseInfo(iso: string, settings: Settings): PhaseInfo {
  const phase = getPhase(iso, settings);
  if (isBlackoutDay(iso, settings)) {
    return {
      phase,
      capacity: MAINTENANCE_CAP,
      label: 'Exam Blackout: Maintenance Mode',
      description: `${MAINTENANCE_CAP}h Maintenance Boundary`,
      blackout: true,
    };
  }
  if (phase === 1) {
    return {
      phase,
      capacity: 8.5,
      label: 'Phase 1: Pre-College',
      description: '8.5h Budget',
      blackout: false,
    };
  }
  if (phase === 2) {
    return {
      phase,
      capacity: 5.0,
      label: 'Phase 2: VIT-AP Balance',
      description: '5h Budget',
      blackout: false,
    };
  }
  return {
    phase,
    capacity: 5.0,
    label: 'Phase 3: Terminal Revision',
    description: 'Mocks + Weak-Link Drilling',
    blackout: false,
  };
}

const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

export function dayOfWeek(iso: string): number {
  return parseISO(iso).getDay();
}

export function dayName(iso: string): string {
  return DOW[dayOfWeek(iso)];
}

export function prettyDate(iso: string): string {
  const d = parseISO(iso);
  return d.toLocaleDateString(undefined, {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  });
}

/** English & LR baseline slot — 4x a week (Mon, Wed, Fri, Sun). */
export function isEngLrDay(iso: string): boolean {
  const dow = dayOfWeek(iso);
  return dow === 1 || dow === 3 || dow === 5 || dow === 0;
}

export interface Countdown {
  days: number;
  hours: number;
  minutes: number;
  totalMs: number;
}

export function countdownTo(targetISO: string): Countdown {
  const target = parseISO(targetISO).getTime();
  const now = Date.now();
  const totalMs = Math.max(0, target - now);
  const days = Math.floor(totalMs / MS_PER_DAY);
  const hours = Math.floor((totalMs % MS_PER_DAY) / (60 * 60 * 1000));
  const minutes = Math.floor((totalMs % (60 * 60 * 1000)) / (60 * 1000));
  return { days, hours, minutes, totalMs };
}

/** Format a fractional-hour clock label, e.g. 6.5 -> "6:30 AM". */
export function clockLabel(hourFloat: number): string {
  const totalMinutes = Math.round(hourFloat * 60) % (24 * 60);
  let h = Math.floor(totalMinutes / 60);
  const m = totalMinutes % 60;
  const ampm = h >= 12 ? 'PM' : 'AM';
  h = h % 12;
  if (h === 0) h = 12;
  return `${h}:${String(m).padStart(2, '0')} ${ampm}`;
}
