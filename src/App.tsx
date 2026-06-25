import { useState } from 'react';
import Dashboard from './components/Dashboard';
import BlockExecution from './components/BlockExecution';
import SyllabusManager from './components/SyllabusManager';
import MockAnalytics from './components/MockAnalytics';
import SettingsPanel from './components/SettingsPanel';
import { useApp } from './context/AppContext';
import { phaseInfo } from './lib/dates';

type Page = 'dashboard' | 'blocks' | 'syllabus' | 'mocks' | 'settings';

const NAV: { key: Page; label: string; icon: string }[] = [
  { key: 'dashboard', label: 'Dashboard', icon: '◉' },
  { key: 'blocks', label: 'Block Execution', icon: '▤' },
  { key: 'syllabus', label: 'Syllabus & Backlog', icon: '☰' },
  { key: 'mocks', label: 'Mocks & Analytics', icon: '📈' },
  { key: 'settings', label: 'Settings', icon: '⚙' },
];

export default function App() {
  const { state, derived } = useApp();
  const [page, setPage] = useState<Page>('dashboard');
  const info = phaseInfo(derived.today, state.settings);
  const phaseColor = info.phase === 1 ? '#22c55e' : info.phase === 2 ? '#f59e0b' : '#ef4444';

  return (
    <div className="mx-auto flex min-h-screen max-w-7xl flex-col px-4 pb-16">
      <header className="sticky top-0 z-20 -mx-4 mb-5 border-b border-edge bg-ink/80 px-4 py-3 backdrop-blur">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div
              className="grid h-9 w-9 place-items-center rounded-xl font-extrabold text-ink"
              style={{ background: '#38bdf8' }}
            >
              B
            </div>
            <div>
              <div className="text-base font-extrabold leading-tight">BITSAT 330+ Tracker</div>
              <div className="text-xs text-slate-400">
                Adaptive prep engine · CSE @ VIT-AP partial-dropper
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span
              className="chip"
              style={{ borderColor: `${phaseColor}66`, color: phaseColor }}
            >
              ● {info.label.replace(/^Phase \d: /, '')} · {info.capacity}h
            </span>
            {derived.backlog.length > 0 && (
              <span className="chip" style={{ borderColor: '#ef444466', color: '#fca5a5' }}>
                {derived.backlog.length} backlog
              </span>
            )}
          </div>
        </div>

        <nav className="mt-3 flex gap-1 overflow-x-auto">
          {NAV.map((n) => (
            <button
              key={n.key}
              onClick={() => setPage(n.key)}
              className={`btn whitespace-nowrap px-3 py-1.5 text-sm ${
                page === n.key
                  ? 'bg-brand text-ink'
                  : 'border border-transparent text-slate-300 hover:bg-panel2'
              }`}
            >
              <span className="opacity-80">{n.icon}</span> {n.label}
            </button>
          ))}
        </nav>
      </header>

      <main className="flex-1">
        {page === 'dashboard' && <Dashboard goTo={(p) => setPage(p as Page)} />}
        {page === 'blocks' && <BlockExecution />}
        {page === 'syllabus' && <SyllabusManager />}
        {page === 'mocks' && <MockAnalytics />}
        {page === 'settings' && <SettingsPanel />}
      </main>

      <footer className="mt-10 border-t border-edge pt-4 text-center text-xs text-slate-500">
        Local-first · all logs persist in your browser. Score = (112 × 3) − (6 × 1) = 330 / 390.
      </footer>
    </div>
  );
}
