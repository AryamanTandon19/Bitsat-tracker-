import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ArchiveEvent } from "@/lib/mock-data";
import { Button, Card, EmptyState, SeverityChip } from "@/components/ui-prims";

export const Route = createFileRoute("/events")({
  head: () => ({
    meta: [
      { title: "Events · VisionGuard" },
      { name: "description", content: "Archive of every anomaly across all cameras with Claude summaries and audited clip actions." },
      { property: "og:title", content: "Events · VisionGuard" },
      { property: "og:description", content: "Archive of every anomaly across all cameras with Claude summaries and audited clip actions." },
    ],
  }),
  component: EventsView,
});

function timeAgo(ts: number) {
  const s = Math.max(1, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function EventsView() {
  const [events, setEvents] = useState<ArchiveEvent[]>([]);
  const [filter, setFilter] = useState<"all" | "HIGH" | "MEDIUM" | "LOW">("all");
  const [deleting, setDeleting] = useState<ArchiveEvent | null>(null);

  useEffect(() => {
    const tick = () => api.events(100).then(setEvents).catch(() => {});
    tick();
    const t = setInterval(tick, 8000);
    return () => clearInterval(t);
  }, []);

  const filtered = filter === "all" ? events : events.filter((e) => e.severity === filter);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="label-hud text-cyan">Events</div>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-text">Anomaly archive</h1>
          <p className="text-sm text-muted">Every flagged moment across every camera.</p>
        </div>
        <div className="flex gap-1 rounded-lg bg-[color:var(--color-surface-2)] p-1 ring-1 ring-inset ring-[color:var(--color-border)]">
          {(["all", "HIGH", "MEDIUM", "LOW"] as const).map((f) => (
            <button key={f} onClick={() => setFilter(f)}
              className={`rounded-md px-3 py-1.5 text-xs font-semibold uppercase tracking-wider ${filter === f ? "bg-cyan text-white" : "text-muted hover:text-text"}`}>
              {f}
            </button>
          ))}
        </div>
      </header>

      {filtered.length === 0 ? (
        <EmptyState title="No events" hint="When cameras flag activity, it will appear here." />
      ) : (
        <Card className="overflow-hidden">
          <div className="overflow-x-auto scrollbar-thin">
            <table className="w-full min-w-[900px] text-sm">
              <thead>
                <tr className="bg-[color:var(--color-surface-2)] text-left label-hud">
                  <th className="px-4 py-3">Sev</th>
                  <th className="px-4 py-3">#</th>
                  <th className="px-4 py-3">Time</th>
                  <th className="px-4 py-3">Camera</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Plate</th>
                  <th className="px-4 py-3">Description</th>
                  <th className="px-4 py-3">AI says</th>
                  <th className="px-4 py-3">Clip</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((e) => (
                  <tr key={e.id} className="border-t border-border hover:bg-black/[0.03]">
                    <td className="px-4 py-3"><SeverityChip sev={e.severity} /></td>
                    <td className="px-4 py-3 mono text-muted">#{e.id}</td>
                    <td className="px-4 py-3">
                      <div className="text-text">{timeAgo(e.ts)}</div>
                      <div className="mono text-[10px] text-muted">{new Date(e.ts * 1000).toLocaleString()}</div>
                    </td>
                    <td className="px-4 py-3 text-text">{e.camera}</td>
                    <td className="px-4 py-3 text-muted">{e.event_type}</td>
                    <td className="px-4 py-3 mono text-purple-lt">{e.plate ?? "—"}</td>
                    <td className="px-4 py-3 max-w-[300px] text-muted">{e.description}</td>
                    <td className="px-4 py-3">
                      {e.ai_summary ? <span className="text-cyan">"{e.ai_summary}"</span> : <span className="text-muted">—</span>}
                    </td>
                    <td className="px-4 py-3">
                      {e.clip_id && !e.clip_deleted ? (
                        <div className="flex items-center gap-2">
                          <a href={`/clips/${e.clip_id}`} className="text-cyan hover:underline text-xs">play</a>
                          <button onClick={() => setDeleting(e)} className="text-red hover:underline text-xs">delete</button>
                        </div>
                      ) : e.clip_deleted ? (
                        <span className="text-[11px] text-muted italic">deleted</span>
                      ) : (
                        <span className="text-[11px] text-muted">no clip</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {deleting && (
        <DeleteClipModal
          event={deleting}
          onClose={() => setDeleting(null)}
          onDone={() => {
            setEvents((evts) => evts.map((x) => x.id === deleting.id ? { ...x, clip_deleted: 1 } : x));
            setDeleting(null);
          }}
        />
      )}
    </div>
  );
}

function DeleteClipModal({ event, onClose, onDone }: { event: ArchiveEvent; onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    if (!name.trim() || !reason.trim()) { setErr("Name and reason are required."); return; }
    setBusy(true);
    try { await api.deleteClip(event.clip_id!, name.trim(), reason.trim()); onDone(); }
    catch (e) { setErr(String(e)); }
    finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm" onClick={onClose}>
      <div className="glass w-full max-w-lg p-6" onClick={(e) => e.stopPropagation()}>
        <div className="label-hud text-red">Delete clip · audited</div>
        <h2 className="mt-1 text-lg font-bold text-text">Clip #{event.clip_id} — {event.camera}</h2>
        <p className="mt-2 text-sm text-muted">
          This action is <strong className="text-text">permanent</strong> and is written to the tamper-evident audit chain
          along with your name and reason. It cannot be undone.
        </p>
        <div className="mt-4 space-y-3">
          <Field label="Your name">
            <input value={name} onChange={(e) => setName(e.target.value)} className="w-full rounded-lg bg-[color:var(--color-surface)] px-3 py-2 text-sm text-text ring-1 ring-inset ring-[color:var(--color-border-strong)] focus:outline-none focus:ring-2 focus:ring-[color:var(--color-cyan)]" />
          </Field>
          <Field label="Reason">
            <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3}
              className="w-full rounded-lg bg-[color:var(--color-surface)] px-3 py-2 text-sm text-text ring-1 ring-inset ring-[color:var(--color-border-strong)] focus:outline-none focus:ring-2 focus:ring-[color:var(--color-cyan)]" />
          </Field>
          {err && <div className="text-xs text-red">{err}</div>}
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant="danger" onClick={submit} disabled={busy}>{busy ? "Deleting…" : "Delete permanently"}</Button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="label-hud mb-1.5">{label}</div>
      {children}
    </label>
  );
}
