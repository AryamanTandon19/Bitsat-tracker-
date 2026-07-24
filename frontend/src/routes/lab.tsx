import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Job } from "@/lib/mock-data";
import { Button, Card, Chip, EmptyState, SeverityChip, Switch, ThreatBanner } from "@/components/ui-prims";

export const Route = createFileRoute("/lab")({
  head: () => ({
    meta: [
      { title: "Forensic Lab · VisionGuard" },
      { name: "description", content: "Upload a clip and receive a Claude-verified verdict with jump-to-evidence." },
      { property: "og:title", content: "Forensic Lab · VisionGuard" },
      { property: "og:description", content: "Upload a clip and receive a Claude-verified verdict with jump-to-evidence." },
    ],
  }),
  component: LabView,
});

function fmt(t: number) {
  const m = Math.floor(t / 60).toString().padStart(2, "0");
  const s = Math.floor(t % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

function LabView() {
  const [cameras, setCameras] = useState<string[]>([]);
  const [aiReview, setAiReview] = useState(true);
  const [zonesFrom, setZonesFrom] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { api.cameras().then(setCameras).catch(() => {}); }, []);

  // Poll while active
  useEffect(() => {
    if (!jobId) return;
    let alive = true;
    const tick = async () => {
      try {
        const j = await api.job(jobId);
        if (!alive) return;
        setJob(j);
        if (j.status !== "done" && j.status !== "error") setTimeout(tick, 1000);
      } catch (e) {
        if (alive) setJob((prev) => prev && { ...prev, status: "error", error: String(e) });
      }
    };
    tick();
    return () => { alive = false; };
  }, [jobId]);

  const start = async () => {
    if (!file) return;
    setJob(null);
    const { job_id } = await api.startAnalyze(file, { aiReview, zonesFrom });
    setJobId(job_id);
  };

  const seekTo = (t: number) => {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = t;
    v.play().catch(() => {});
    v.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const dropHandlers = useMemo(() => ({
    onDragOver: (e: React.DragEvent) => { e.preventDefault(); setDragging(true); },
    onDragLeave: () => setDragging(false),
    onDrop: (e: React.DragEvent) => {
      e.preventDefault(); setDragging(false);
      const f = e.dataTransfer.files?.[0];
      if (f) setFile(f);
    },
  }), []);

  const busy = job && job.status !== "done" && job.status !== "error";
  const progress = job?.progress ?? 0;

  return (
    <div className="space-y-6">
      <header>
        <div className="label-hud text-cyan">Forensic Lab</div>
        <h1 className="mt-1 text-2xl font-bold tracking-tight text-text">Upload · analyze · verdict</h1>
        <p className="text-sm text-muted">Drop a clip. We'll run the free-layer detectors and — if Smart AI Review is on — hand it to Claude.</p>
      </header>

      <Card className="p-5">
        <div
          {...dropHandlers}
          onClick={() => inputRef.current?.click()}
          className={`relative flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed p-10 text-center transition-all ${
            dragging ? "border-[color:var(--color-cyan)] bg-[color-mix(in_oklab,var(--color-cyan)_8%,transparent)]" : "border-border-strong hover:border-[color:var(--color-cyan)] hover:bg-black/[0.02]"
          }`}
        >
          <div className="flex h-12 w-12 items-center justify-center rounded-full"
            style={{ background: "color-mix(in oklab, var(--color-cyan) 14%, white)" }}>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-cyan">
              <path d="M12 3v12m0-12 4 4m-4-4-4 4M5 15v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4" />
            </svg>
          </div>
          <div className="text-sm font-semibold text-text">{file ? file.name : "Drop a video or click to browse"}</div>
          <div className="text-xs text-muted">mp4 · avi · mov · mkv · dav</div>
          <input ref={inputRef} type="file" className="hidden" accept="video/mp4,video/x-msvideo,video/quicktime,video/x-matroska,.dav"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </div>

        <div className="mt-5 grid gap-4 sm:grid-cols-[1fr_auto_auto] sm:items-end">
          <div>
            <div className="label-hud mb-1.5">Zones from</div>
            <select
              value={zonesFrom}
              onChange={(e) => setZonesFrom(e.target.value)}
              className="w-full rounded-lg bg-[color:var(--color-surface)] px-3 py-2 text-sm text-text ring-1 ring-inset ring-[color:var(--color-border-strong)] focus:outline-none focus:ring-2 focus:ring-[color:var(--color-cyan)]"
            >
              <option value="">— none (analyze whole frame) —</option>
              {cameras.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-3 rounded-lg bg-[color:var(--color-surface-2)] px-4 py-2.5 ring-1 ring-inset ring-[color:var(--color-border)]">
            <Switch checked={aiReview} onChange={setAiReview} />
            <div>
              <div className="text-sm font-semibold text-text">Smart AI Review</div>
              <div className="text-[11px] text-muted">Claude verifies flagged moments</div>
            </div>
          </div>
          <Button variant="grad" disabled={!file || !!busy} onClick={start}>
            {busy ? "Analyzing…" : "Analyze"}
          </Button>
        </div>

        {job && (
          <div className="mt-5 space-y-2">
            <div className="flex items-center justify-between text-xs">
              <div className="flex items-center gap-2">
                <Chip tone={job.status === "done" ? "green" : job.status === "error" ? "red" : "cyan"}>{job.status}</Chip>
                <span className="text-muted">{job.message}</span>
              </div>
              <span className="mono text-muted tabular-nums">{Math.round(progress * 100)}%</span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-[color:var(--color-surface-2)]">
              <div className="h-full rounded-full transition-[width] duration-500"
                style={{ width: `${progress * 100}%`, background: "var(--color-cyan)" }} />
            </div>
          </div>
        )}
      </Card>

      {!job && <EmptyState title="No analysis yet" hint="Upload a video above to get started." />}

      {job?.status === "done" && (
        <div className="space-y-6">
          {job.ai_verdict && <ThreatBanner text={job.ai_verdict} />}
          {!job.ai_verdict && job.ai_note && (
            <Card accent="purple" className="p-4">
              <div className="label-hud text-purple-lt">AI note</div>
              <div className="mt-1 text-sm text-text">{job.ai_note}</div>
            </Card>
          )}

          {job.incidents.length > 0 && (
            <section>
              <SectionTitle label="Incidents detected" count={job.incidents.length} />
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                {job.incidents.map((inc) => (
                  <button key={inc.index} onClick={() => seekTo(inc.start_s)}
                    className="rise text-left">
                    <Card accent={inc.severity === "HIGH" ? "red" : inc.severity === "MEDIUM" ? "amber" : "cyan"}
                      className="p-4 transition-transform hover:-translate-y-0.5">
                      <div className="flex items-center justify-between">
                        <span className="label-hud">Incident {inc.index + 1}</span>
                        <SeverityChip sev={inc.severity} />
                      </div>
                      <div className="mt-2 text-base font-semibold leading-snug text-text">{inc.summary}</div>
                      <div className="mono mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted tabular-nums">
                        <span>{fmt(inc.start_s)} → {fmt(inc.end_s)}</span>
                        <span>tracks: {inc.track_ids.join(",")}</span>
                        <span>{inc.count} alert{inc.count === 1 ? "" : "s"}</span>
                      </div>
                    </Card>
                  </button>
                ))}
              </div>
            </section>
          )}

          {job.video_ready && (
            <section>
              <SectionTitle label="Annotated playback" />
              <Card className="mt-3 overflow-hidden">
                <video ref={videoRef} controls src={api.jobVideoUrl(job.id)} className="w-full bg-black" style={{ maxHeight: 520 }} />
                {/* timeline markers */}
                <div className="relative h-8 border-t border-border bg-[color:var(--color-surface-2)] px-3">
                  <div className="absolute inset-x-3 top-1/2 h-px bg-[color:var(--color-border)]" />
                  {[...job.incidents.map((i) => ({ t: i.start_s, sev: i.severity, k: `i${i.index}` })),
                    ...job.ai_findings.map((f, k) => ({ t: f.time_s, sev: f.severity, k: `a${k}` }))].map((m) => {
                    const est = Math.max(60, Math.max(...job.incidents.map((i) => i.end_s), ...job.ai_findings.map((f) => f.time_s), 60));
                    const pct = Math.min(100, (m.t / est) * 100);
                    const color = m.sev === "HIGH" ? "var(--color-red-deep)" : m.sev === "MEDIUM" ? "var(--color-amber)" : "var(--color-cyan)";
                    return (
                      <button key={m.k} onClick={() => seekTo(m.t)}
                        title={`Jump to ${fmt(m.t)}`}
                        className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-white hover:scale-125 transition-transform"
                        style={{ left: `${pct}%`, background: color }} />
                    );
                  })}
                </div>
              </Card>
            </section>
          )}

          {job.ai_findings.length > 0 && (
            <section>
              <SectionTitle label="AI Scene Review" count={job.ai_findings.length} />
              <Card className="mt-3 overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-[color:var(--color-surface-2)] text-left label-hud">
                      <th className="px-4 py-3">Time</th><th className="px-4 py-3">Activity</th><th className="px-4 py-3">Sev</th>
                    </tr>
                  </thead>
                  <tbody>
                    {job.ai_findings.map((f, i) => (
                      <tr key={i} className="border-t border-border hover:bg-black/[0.03]">
                        <td className="px-4 py-3 mono tabular-nums">
                          <button onClick={() => seekTo(f.time_s)} className="text-cyan hover:underline">{fmt(f.time_s)}</button>
                        </td>
                        <td className="px-4 py-3 text-text">{f.activity}</td>
                        <td className="px-4 py-3"><SeverityChip sev={f.severity} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
            </section>
          )}

          {job.events.length > 0 && (
            <section>
              <SectionTitle label="Rule-based checks" count={job.events.length} />
              <Card className="mt-3 overflow-hidden">
                <div className="overflow-x-auto scrollbar-thin">
                  <table className="w-full min-w-[640px] text-sm">
                    <thead>
                      <tr className="bg-[color:var(--color-surface-2)] text-left label-hud">
                        <th className="px-4 py-3">Sev</th><th className="px-4 py-3">Time</th><th className="px-4 py-3">Type</th>
                        <th className="px-4 py-3">Plate</th><th className="px-4 py-3">Description</th>
                      </tr>
                    </thead>
                    <tbody>
                      {job.events.map((e) => (
                        <tr key={e.index} className="border-t border-border hover:bg-black/[0.03]">
                          <td className="px-4 py-3"><SeverityChip sev={e.severity} /></td>
                          <td className="px-4 py-3 mono tabular-nums">
                            <button onClick={() => seekTo(e.video_time_s)} className="text-cyan hover:underline">{fmt(e.video_time_s)}</button>
                          </td>
                          <td className="px-4 py-3 text-text">{e.event_type}</td>
                          <td className="px-4 py-3 mono text-purple-lt">{e.plate ?? "—"}</td>
                          <td className="px-4 py-3 text-muted">{e.description}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </section>
          )}
        </div>
      )}

      {job?.status === "error" && (
        <Card accent="red" className="p-4">
          <div className="label-hud text-red">Error</div>
          <div className="mt-1 text-sm text-text">{job.error ?? "Analysis failed."}</div>
        </Card>
      )}
    </div>
  );
}

function SectionTitle({ label, count }: { label: string; count?: number }) {
  return (
    <div className="flex items-center gap-3">
      <div className="label-hud text-cyan">{label}</div>
      {count != null && <span className="mono text-xs text-muted">({count})</span>}
      <div className="ml-2 h-px flex-1 bg-[color:var(--color-border)]" />
    </div>
  );
}
