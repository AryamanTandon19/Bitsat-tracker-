import React from 'react';

export function ProgressBar({
  pct,
  color = '#38bdf8',
  className = '',
  height = 10,
}: {
  pct: number;
  color?: string;
  className?: string;
  height?: number;
}) {
  return (
    <div
      className={`w-full overflow-hidden rounded-full bg-ink/70 ${className}`}
      style={{ height }}
    >
      <div
        className="h-full rounded-full transition-all duration-500"
        style={{ width: `${Math.min(100, Math.max(0, pct))}%`, background: color }}
      />
    </div>
  );
}

export function Stat({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  accent?: string;
}) {
  return (
    <div className="card">
      <div className="label">{label}</div>
      <div className="mt-1 text-3xl font-extrabold" style={accent ? { color: accent } : undefined}>
        {value}
      </div>
      {sub && <div className="mt-1 text-sm text-slate-400">{sub}</div>}
    </div>
  );
}

export function SectionTitle({
  children,
  right,
}: {
  children: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <h2 className="text-lg font-bold tracking-tight">{children}</h2>
      {right}
    </div>
  );
}

export function Pill({
  children,
  color = '#22304f',
  text = '#cbd5e1',
}: {
  children: React.ReactNode;
  color?: string;
  text?: string;
}) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-semibold"
      style={{ background: `${color}33`, color: text, border: `1px solid ${color}66` }}
    >
      {children}
    </span>
  );
}

export function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-edge bg-ink/30 p-6 text-center text-sm text-slate-400">
      {children}
    </div>
  );
}
