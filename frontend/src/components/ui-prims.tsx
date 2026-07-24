import type { ReactNode } from "react";
import type { Severity } from "@/lib/mock-data";

export function Chip({
  children,
  tone = "cyan",
  dot = true,
}: {
  children: ReactNode;
  tone?: "cyan" | "red" | "amber" | "green" | "purple" | "muted";
  dot?: boolean;
}) {
  const map = {
    cyan:   { fg: "var(--color-cyan)",     bg: "color-mix(in oklab, var(--color-cyan) 12%, white)",   border: "color-mix(in oklab, var(--color-cyan) 30%, transparent)" },
    red:    { fg: "var(--color-red-deep)", bg: "color-mix(in oklab, var(--color-red) 16%, white)",    border: "color-mix(in oklab, var(--color-red) 35%, transparent)" },
    amber:  { fg: "#8a5a0f",               bg: "color-mix(in oklab, var(--color-amber) 20%, white)", border: "color-mix(in oklab, var(--color-amber) 40%, transparent)" },
    green:  { fg: "#2f5c3d",               bg: "color-mix(in oklab, var(--color-green) 18%, white)", border: "color-mix(in oklab, var(--color-green) 40%, transparent)" },
    purple: { fg: "var(--color-purple)",   bg: "color-mix(in oklab, var(--color-purple) 8%, white)", border: "color-mix(in oklab, var(--color-purple) 20%, transparent)" },
    muted:  { fg: "var(--color-muted)",    bg: "var(--color-surface-2)",                             border: "var(--color-border)" },
  }[tone];
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-semibold"
      style={{ background: map.bg, color: map.fg, boxShadow: `inset 0 0 0 1px ${map.border}` }}
    >
      {dot && (
        <span
          className="h-1.5 w-1.5 rounded-full"
          style={{ background: map.fg, animation: "pulse-dot 2.4s ease-in-out infinite" }}
        />
      )}
      {children}
    </span>
  );
}

export function SeverityChip({ sev }: { sev: Severity }) {
  const tone = sev === "HIGH" ? "red" : sev === "MEDIUM" ? "amber" : "green";
  const label = sev === "HIGH" ? "Urgent" : sev === "MEDIUM" ? "Review" : "Info";
  return <Chip tone={tone as any}>{label}</Chip>;
}

export function Card({
  children,
  className = "",
  accent,
}: {
  children: ReactNode;
  className?: string;
  accent?: "cyan" | "red" | "amber" | "purple";
}) {
  const accentColor = accent && {
    cyan: "var(--color-cyan)",
    red: "var(--color-red-deep)",
    amber: "var(--color-amber)",
    purple: "var(--color-purple)",
  }[accent];
  return (
    <div className={`glass relative overflow-hidden ${className}`}>
      {accent && <div className="absolute inset-x-0 top-0 h-[3px]" style={{ background: accentColor }} />}
      {children}
    </div>
  );
}

export function StatCard({
  label, value, sub, tone = "cyan",
}: { label: string; value: ReactNode; sub?: string; tone?: "cyan" | "red" | "purple" | "amber" }) {
  return (
    <Card accent={tone} className="p-5">
      <div className="label-hud">{label}</div>
      <div className="mt-2 font-display text-3xl font-bold tracking-tight text-text tabular-nums">{value}</div>
      {sub && <div className="mt-1 text-xs text-muted">{sub}</div>}
    </Card>
  );
}

export function Button({
  variant = "solid", className = "", children, ...rest
}: {
  variant?: "solid" | "grad" | "ghost" | "danger";
  className?: string;
  children: ReactNode;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-full px-4 py-2 text-sm font-semibold transition-all disabled:opacity-40 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-cyan)]/40";
  const v = {
    solid: "bg-[color:var(--color-cyan)] text-white hover:bg-[color:var(--color-cyan-dim)]",
    grad:  "bg-[color:var(--color-cyan)] text-white hover:bg-[color:var(--color-cyan-dim)] shadow-[var(--shadow-glow-cyan)]",
    ghost: "bg-white text-text border border-border hover:border-border-strong",
    danger:"bg-[color-mix(in_oklab,var(--color-red)_14%,white)] text-[color:var(--color-red-deep)] border border-[color-mix(in_oklab,var(--color-red)_35%,transparent)] hover:bg-[color-mix(in_oklab,var(--color-red)_22%,white)]",
  }[variant];
  return (
    <button className={`${base} ${v} ${className}`} {...rest}>
      {children}
    </button>
  );
}

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-3 select-none">
      <span
        onClick={() => onChange(!checked)}
        role="switch"
        aria-checked={checked}
        className={`relative h-6 w-11 rounded-full transition-colors ${checked ? "bg-[color:var(--color-cyan)]" : "bg-[color:var(--color-surface-2)] ring-1 ring-inset ring-[color:var(--color-border-strong)]"}`}
      >
        <span
          className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${checked ? "left-[22px]" : "left-0.5"}`}
          style={{ boxShadow: "0 1px 3px rgba(0,0,0,.25)" }}
        />
      </span>
      {label && <span className="text-sm text-text">{label}</span>}
    </label>
  );
}

export function ThreatBanner({ text }: { text: string }) {
  return (
    <div className="rise glass relative overflow-hidden p-5"
      style={{ background: "color-mix(in oklab, var(--color-red) 8%, white)", borderColor: "color-mix(in oklab, var(--color-red) 30%, transparent)" }}>
      <div className="absolute inset-x-0 top-0 h-[3px]" style={{ background: "var(--color-red-deep)" }} />
      <div className="flex items-start gap-4">
        <div className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-full"
          style={{ background: "color-mix(in oklab, var(--color-red) 20%, white)" }}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" style={{ color: "var(--color-red-deep)" }}>
            <path d="M12 9v4M12 17h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" />
          </svg>
        </div>
        <div className="min-w-0 flex-1">
          <div className="label-hud" style={{ color: "var(--color-red-deep)" }}>AI verdict</div>
          <div className="mt-1 font-display text-lg font-semibold leading-snug text-text">{text}</div>
        </div>
      </div>
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="glass flex flex-col items-center justify-center gap-2 p-10 text-center">
      <div className="text-sm font-semibold text-text">{title}</div>
      {hint && <div className="text-xs text-muted">{hint}</div>}
    </div>
  );
}
