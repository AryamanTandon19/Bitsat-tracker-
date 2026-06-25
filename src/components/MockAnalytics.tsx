import React, { useState } from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useApp } from '../context/AppContext';
import { SUBJECT_META } from '../data/syllabus';
import type { ErrorLogEntry, ErrorTag, MockEntry, SubjectKey } from '../types';
import { TARGET, accuracy, projectedScore } from '../lib/engine';
import { todayISO } from '../lib/dates';
import { SectionTitle, Pill, EmptyState, Stat } from './ui';

function uid() {
  return Math.random().toString(36).slice(2, 10);
}

const TAG_META: Record<ErrorTag, { label: string; color: string }> = {
  calculation: { label: 'Calculation Error', color: '#f59e0b' },
  concept: { label: 'Concept Missing', color: '#ef4444' },
  time: { label: 'Time Panic', color: '#a78bfa' },
};

function MockForm() {
  const { dispatch } = useApp();
  const [date, setDate] = useState(todayISO());
  const [attempted, setAttempted] = useState(0);
  const [correct, setCorrect] = useState(0);
  const [incorrect, setIncorrect] = useState(0);

  const autoScore = projectedScore(correct, incorrect);
  const acc = accuracy(correct, attempted);
  const valid = attempted > 0 && correct + incorrect <= attempted;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    const mock: MockEntry = {
      id: uid(),
      date,
      score: autoScore,
      attempted,
      correct,
      incorrect,
    };
    dispatch({ type: 'ADD_MOCK', mock });
    setAttempted(0);
    setCorrect(0);
    setIncorrect(0);
  };

  return (
    <form className="card" onSubmit={submit}>
      <SectionTitle>Log a Full Mock Test</SectionTitle>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <label className="block">
          <span className="label">Date</span>
          <input
            type="date"
            className="input mt-1"
            value={date}
            onChange={(e) => setDate(e.target.value)}
          />
        </label>
        <label className="block">
          <span className="label">Attempted</span>
          <input
            type="number"
            min={0}
            max={TARGET.totalQ}
            className="input mt-1"
            value={attempted}
            onChange={(e) => setAttempted(Math.max(0, Number(e.target.value)))}
          />
        </label>
        <label className="block">
          <span className="label">Correct</span>
          <input
            type="number"
            min={0}
            className="input mt-1"
            value={correct}
            onChange={(e) => setCorrect(Math.max(0, Number(e.target.value)))}
          />
        </label>
        <label className="block">
          <span className="label">Incorrect</span>
          <input
            type="number"
            min={0}
            className="input mt-1"
            value={incorrect}
            onChange={(e) => setIncorrect(Math.max(0, Number(e.target.value)))}
          />
        </label>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-4">
        <div className="text-sm">
          Auto Score:{' '}
          <span className="text-lg font-extrabold text-brand">{autoScore}</span>
          <span className="text-slate-400"> /{TARGET.outOf}</span>
        </div>
        <div className="text-sm">
          Accuracy:{' '}
          <span
            className="text-lg font-extrabold"
            style={{ color: acc >= TARGET.accuracyFloor ? '#22c55e' : '#ef4444' }}
          >
            {acc}%
          </span>
        </div>
        <button className="btn-brand ml-auto" disabled={!valid}>
          Save Mock
        </button>
      </div>
      {!valid && (attempted > 0 || correct > 0 || incorrect > 0) && (
        <div className="mt-2 text-xs text-bad">
          Correct + Incorrect cannot exceed Attempted, and Attempted must be &gt; 0.
        </div>
      )}
    </form>
  );
}

function AccuracyChart({ mocks }: { mocks: MockEntry[] }) {
  const data = [...mocks]
    .sort((a, b) => a.date.localeCompare(b.date))
    .map((m) => ({
      date: m.date.slice(5),
      accuracy: accuracy(m.correct, m.attempted),
      score: m.score,
    }));

  if (data.length === 0) {
    return <EmptyState>Log a mock to start plotting accuracy and score trends.</EmptyState>;
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="card">
        <div className="label mb-2">Accuracy % over time (floor 95%)</div>
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={data} margin={{ top: 5, right: 10, left: -20, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#22304f" />
            <XAxis dataKey="date" stroke="#64748b" fontSize={12} />
            <YAxis domain={[60, 100]} stroke="#64748b" fontSize={12} />
            <Tooltip
              contentStyle={{ background: '#111a2e', border: '1px solid #22304f', borderRadius: 12 }}
            />
            <ReferenceLine y={95} stroke="#22c55e" strokeDasharray="5 5" label={{ value: '95%', fill: '#22c55e', fontSize: 11 }} />
            <Line type="monotone" dataKey="accuracy" stroke="#38bdf8" strokeWidth={2.5} dot={{ r: 3 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="card">
        <div className="label mb-2">Score over time (target 330)</div>
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={data} margin={{ top: 5, right: 10, left: -20, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#22304f" />
            <XAxis dataKey="date" stroke="#64748b" fontSize={12} />
            <YAxis domain={[0, 390]} stroke="#64748b" fontSize={12} />
            <Tooltip
              contentStyle={{ background: '#111a2e', border: '1px solid #22304f', borderRadius: 12 }}
            />
            <ReferenceLine y={330} stroke="#f59e0b" strokeDasharray="5 5" label={{ value: '330', fill: '#f59e0b', fontSize: 11 }} />
            <Line type="monotone" dataKey="score" stroke="#22c55e" strokeWidth={2.5} dot={{ r: 3 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function MockTable({ mocks }: { mocks: MockEntry[] }) {
  const { dispatch } = useApp();
  if (mocks.length === 0) return null;
  return (
    <div className="card overflow-x-auto">
      <SectionTitle>Mock History</SectionTitle>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-slate-400">
            <th className="py-2">Date</th>
            <th>Score</th>
            <th>Attempted</th>
            <th>Correct</th>
            <th>Incorrect</th>
            <th>Accuracy</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {mocks.map((m) => {
            const acc = accuracy(m.correct, m.attempted);
            return (
              <tr key={m.id} className="border-t border-edge">
                <td className="py-2">{m.date}</td>
                <td className="font-semibold">{m.score}</td>
                <td>{m.attempted}</td>
                <td className="text-good">{m.correct}</td>
                <td className="text-bad">{m.incorrect}</td>
                <td>
                  <span style={{ color: acc >= TARGET.accuracyFloor ? '#22c55e' : '#ef4444' }}>
                    {acc}%
                  </span>
                </td>
                <td className="text-right">
                  <button
                    className="text-xs text-slate-400 hover:text-bad"
                    onClick={() => dispatch({ type: 'DELETE_MOCK', id: m.id })}
                  >
                    delete
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ErrorLog({ errors }: { errors: ErrorLogEntry[] }) {
  const { dispatch } = useApp();
  const [topic, setTopic] = useState('');
  const [subject, setSubject] = useState<SubjectKey>('math');
  const [tag, setTag] = useState<ErrorTag>('calculation');
  const [note, setNote] = useState('');

  const add = (e: React.FormEvent) => {
    e.preventDefault();
    if (!topic.trim()) return;
    const entry: ErrorLogEntry = {
      id: uid(),
      date: todayISO(),
      subject,
      topic: topic.trim(),
      tag,
      note: note.trim(),
    };
    dispatch({ type: 'ADD_ERROR', error: entry });
    setTopic('');
    setNote('');
  };

  const counts: Record<ErrorTag, number> = { calculation: 0, concept: 0, time: 0 };
  for (const e of errors) counts[e.tag]++;

  return (
    <div className="card">
      <SectionTitle>Error Log & Mistake Categorisation</SectionTitle>
      <div className="mb-3 flex flex-wrap gap-2">
        {(Object.keys(TAG_META) as ErrorTag[]).map((t) => (
          <Pill key={t} color={TAG_META[t].color} text={TAG_META[t].color}>
            {TAG_META[t].label}: {counts[t]}
          </Pill>
        ))}
      </div>
      <form className="grid grid-cols-1 gap-3 sm:grid-cols-12" onSubmit={add}>
        <select
          className="input sm:col-span-3"
          value={subject}
          onChange={(e) => setSubject(e.target.value as SubjectKey)}
        >
          {(Object.keys(SUBJECT_META) as SubjectKey[]).map((s) => (
            <option key={s} value={s}>
              {SUBJECT_META[s].short}
            </option>
          ))}
        </select>
        <input
          className="input sm:col-span-3"
          placeholder="Topic (e.g. Rotational MoI)"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
        />
        <select
          className="input sm:col-span-3"
          value={tag}
          onChange={(e) => setTag(e.target.value as ErrorTag)}
        >
          {(Object.keys(TAG_META) as ErrorTag[]).map((t) => (
            <option key={t} value={t}>
              {TAG_META[t].label}
            </option>
          ))}
        </select>
        <input
          className="input sm:col-span-3"
          placeholder="Note (optional)"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <button className="btn-brand sm:col-span-12">Add Error</button>
      </form>

      <div className="mt-4 overflow-x-auto">
        {errors.length === 0 ? (
          <EmptyState>No logged errors yet — categorise mistakes after every mock.</EmptyState>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-slate-400">
                <th className="py-2">Date</th>
                <th>Subject</th>
                <th>Topic</th>
                <th>Tag</th>
                <th>Note</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {errors.map((e) => (
                <tr key={e.id} className="border-t border-edge">
                  <td className="py-2">{e.date}</td>
                  <td>{SUBJECT_META[e.subject].short}</td>
                  <td className="font-medium">{e.topic}</td>
                  <td>
                    <Pill color={TAG_META[e.tag].color} text={TAG_META[e.tag].color}>
                      {TAG_META[e.tag].label}
                    </Pill>
                  </td>
                  <td className="text-slate-400">{e.note || '—'}</td>
                  <td className="text-right">
                    <button
                      className="text-xs text-slate-400 hover:text-bad"
                      onClick={() => dispatch({ type: 'DELETE_ERROR', id: e.id })}
                    >
                      delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export default function MockAnalytics() {
  const { state } = useApp();
  const mocks = state.mocks;
  const best = mocks.reduce((m, x) => (x.score > m ? x.score : m), 0);
  const avgAcc =
    mocks.length === 0
      ? 0
      : Math.round(
          (mocks.reduce((s, m) => s + accuracy(m.correct, m.attempted), 0) / mocks.length) * 10
        ) / 10;

  return (
    <div className="space-y-5">
      <SectionTitle>Mock Test & Accuracy Analytics</SectionTitle>

      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Mocks Logged" value={mocks.length} />
        <Stat label="Best Score" value={best || '—'} sub={`Target ${TARGET.score}/${TARGET.outOf}`} accent="#38bdf8" />
        <Stat
          label="Average Accuracy"
          value={mocks.length ? `${avgAcc}%` : '—'}
          sub={`Floor > ${TARGET.accuracyFloor}%`}
          accent={avgAcc >= TARGET.accuracyFloor ? '#22c55e' : '#ef4444'}
        />
      </div>

      <MockForm />
      <AccuracyChart mocks={mocks} />
      <MockTable mocks={mocks} />
      <ErrorLog errors={state.errors} />
    </div>
  );
}
