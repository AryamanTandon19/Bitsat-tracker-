import type { AppState, Settings } from '../types';
import { toISO } from './dates';

const STORAGE_KEY = 'bitsat-330-tracker:v1';
const STATE_VERSION = 1;

/** Sensible defaults derived from "today". */
export function defaultSettings(): Settings {
  const now = new Date();
  const year = now.getFullYear();

  // College (VIT-AP) resumes Sept 1 of the current academic cycle.
  // If we're already past Sept 1, roll to next year's Sept 1.
  const collegeYear = now.getMonth() >= 8 ? year + 1 : year;
  const collegeStart = new Date(collegeYear, 8, 1); // Sept = month 8

  // BITSAT is held around late May; target the next exam window after college.
  const examYear = collegeYear + 1;
  const exam = new Date(examYear, 4, 20); // May = month 4

  return {
    examDate: toISO(exam),
    collegeStartDate: toISO(collegeStart),
    studyStartHour: 6.5, // 6:30 AM
  };
}

export function defaultState(): AppState {
  return {
    version: STATE_VERSION,
    settings: defaultSettings(),
    entries: {},
    mocks: [],
    errors: [],
    droppedChapters: [],
    lastRebalance: Date.now(),
  };
}

export function loadState(): AppState {
  if (typeof window === 'undefined') return defaultState();
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaultState();
    const parsed = JSON.parse(raw) as Partial<AppState>;
    const base = defaultState();
    return {
      ...base,
      ...parsed,
      version: STATE_VERSION,
      settings: { ...base.settings, ...(parsed.settings ?? {}) },
      entries: parsed.entries ?? {},
      mocks: parsed.mocks ?? [],
      errors: parsed.errors ?? [],
      droppedChapters: parsed.droppedChapters ?? [],
    };
  } catch (err) {
    console.warn('Failed to load saved state, starting fresh.', err);
    return defaultState();
  }
}

export function saveState(state: AppState): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch (err) {
    console.warn('Failed to persist state.', err);
  }
}

export function clearState(): void {
  if (typeof window === 'undefined') return;
  window.localStorage.removeItem(STORAGE_KEY);
}
