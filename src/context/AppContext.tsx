import React, {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useReducer,
} from 'react';
import type {
  AppState,
  BlockState,
  ErrorLogEntry,
  MockEntry,
  Settings,
} from '../types';
import { defaultState, loadState, saveState } from '../lib/storage';
import { todayISO } from '../lib/dates';
import {
  analyseFeasibility,
  backlogUnits,
  buildSchedule,
  overallProgress,
  subjectProgress,
} from '../lib/engine';

type Action =
  | { type: 'RESOLVE_BLOCK'; unitId: string; state: BlockState; targetQ: number; actualQ: number }
  | { type: 'RESET_BLOCK'; unitId: string }
  | { type: 'DROP_CHAPTER'; chapterId: string }
  | { type: 'RESTORE_CHAPTER'; chapterId: string }
  | { type: 'APPLY_DROPS'; chapterIds: string[] }
  | { type: 'REBALANCE' }
  | { type: 'ADD_MOCK'; mock: MockEntry }
  | { type: 'DELETE_MOCK'; id: string }
  | { type: 'ADD_ERROR'; error: ErrorLogEntry }
  | { type: 'DELETE_ERROR'; id: string }
  | { type: 'UPDATE_SETTINGS'; settings: Partial<Settings> }
  | { type: 'RESET_ALL' }
  | { type: 'IMPORT'; state: AppState };

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'RESOLVE_BLOCK':
      return {
        ...state,
        entries: {
          ...state.entries,
          [action.unitId]: {
            unitId: action.unitId,
            date: todayISO(),
            targetQ: action.targetQ,
            actualQ: action.actualQ,
            state: action.state,
          },
        },
      };
    case 'RESET_BLOCK': {
      const next = { ...state.entries };
      delete next[action.unitId];
      return { ...state, entries: next };
    }
    case 'DROP_CHAPTER':
      if (state.droppedChapters.includes(action.chapterId)) return state;
      return {
        ...state,
        droppedChapters: [...state.droppedChapters, action.chapterId],
        lastRebalance: Date.now(),
      };
    case 'RESTORE_CHAPTER':
      return {
        ...state,
        droppedChapters: state.droppedChapters.filter((id) => id !== action.chapterId),
        lastRebalance: Date.now(),
      };
    case 'APPLY_DROPS': {
      const set = new Set([...state.droppedChapters, ...action.chapterIds]);
      return { ...state, droppedChapters: [...set], lastRebalance: Date.now() };
    }
    case 'REBALANCE':
      return { ...state, lastRebalance: Date.now() };
    case 'ADD_MOCK':
      return { ...state, mocks: [action.mock, ...state.mocks] };
    case 'DELETE_MOCK':
      return { ...state, mocks: state.mocks.filter((m) => m.id !== action.id) };
    case 'ADD_ERROR':
      return { ...state, errors: [action.error, ...state.errors] };
    case 'DELETE_ERROR':
      return { ...state, errors: state.errors.filter((e) => e.id !== action.id) };
    case 'UPDATE_SETTINGS':
      return { ...state, settings: { ...state.settings, ...action.settings } };
    case 'RESET_ALL':
      return defaultState();
    case 'IMPORT':
      return action.state;
    default:
      return state;
  }
}

interface AppContextValue {
  state: AppState;
  dispatch: React.Dispatch<Action>;
  derived: {
    today: string;
    schedule: ReturnType<typeof buildSchedule>;
    todayBlocks: ReturnType<typeof buildSchedule>['byDate'] extends Map<string, infer V> ? V : never;
    feasibility: ReturnType<typeof analyseFeasibility>;
    backlog: ReturnType<typeof backlogUnits>;
    progress: ReturnType<typeof subjectProgress>;
    overall: number;
  };
}

const AppContext = createContext<AppContextValue | null>(null);

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(reducer, undefined, loadState);

  useEffect(() => {
    saveState(state);
  }, [state]);

  const derived = useMemo(() => {
    const today = todayISO();
    const schedule = buildSchedule(state, today);
    return {
      today,
      schedule,
      todayBlocks: schedule.byDate.get(today) ?? [],
      feasibility: analyseFeasibility(state, today),
      backlog: backlogUnits(state),
      progress: subjectProgress(state),
      overall: overallProgress(state),
    };
    // Re-derive whenever entries, drops, settings, or a manual rebalance change.
  }, [state.entries, state.droppedChapters, state.settings, state.lastRebalance]);

  const value: AppContextValue = { state, dispatch, derived };
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useApp must be used within AppProvider');
  return ctx;
}
