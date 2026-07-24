import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ArchiveEvent } from "@/lib/mock-data";
import { Card, Chip, SeverityChip } from "@/components/ui-prims";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Overview · VisionGuard" },
      { name: "description", content: "A calm, at-a-glance view of your society's cameras and recent events." },
      { property: "og:title", content: "Overview · VisionGuard" },
      { property: "og:description", content: "A calm, at-a-glance view of your society's cameras and recent events." },
    ],
  }),
  component: OverviewView,
});

function timeAgo(ts: number) {
  const s = Math.max(1, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function OverviewView() {
  const [cameras, setCameras] = useState<string[]>([]);
  const [status, setStatus] = useState<Record<string, { online: boolean; last_frame_age_s: number }>>({});
  const [events, setEvents] = useState<ArchiveEvent[]>([]);

  useEffect(() => {
    api.cameras().then(setCameras).catch(() => {});
    api.events(6).then(setEvents).catch(() => {});
    const tick = () => api.status().then(setStatus).catch(() => {});
    tick();
    const t = setInterval(tick, 5000);
    return () => clearInterval(t);
  }, []);

  const onlineCount = Object.values(status).filter((s) => s.online).length;
  const total = cameras.length;
  const allGood = total > 0 && onlineCount === total;
  const highToday = events.filter((e) => e.severity === "HIGH" && Date.now() / 1000 - e.ts < 86400).length;
  const topEvent = events[0];

  return (
    <div className="space-y-8">
      {/* Greeting */}
      <header>
        <div className="label-hud">Overview</div>
        <h1 className="mt-1 font-display text-3xl font-bold tracking-tight text-text md:text-4xl">
          Everything looks {allGood ? "calm" : "worth a look"}.
        </h1>
        <p className="mt-1 text-sm text-muted">
          A quick, plain-language summary of your building right now.
        </p>
      </header>

      {/* Bento grid */}
      <div className="grid gap-4 md:grid-cols-6">
        {/* Status hero — spans wide */}
        <Card className="p-6 md:col-span-4">
          <div className="flex items-start gap-4">
            <div
              className="mt-1 h-3 w-3 shrink-0 rounded-full"
              style={{
                background: allGood ? "var(--color-green)" : "var(--color-amber)",
                boxShadow: `0 0 0 6px color-mix(in oklab, ${allGood ? "var(--color-green)" : "var(--color-amber)"} 20%, transparent)`,
                animation: "pulse-dot 2.4s ease-in-out infinite",
              }}
            />
            <div className="flex-1">
              <div className="text-lg font-semibold text-text">
                {total === 0 ? "Waiting for cameras" : allGood ? "All cameras are online" : `${total - onlineCount} camera${total - onlineCount === 1 ? "" : "s"} need attention`}
              </div>
              <div className="mt-1 text-sm text-muted">
                {onlineCount}/{total || "—"} live · last checked a moment ago
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <Link to="/view" className="rounded-full bg-[color:var(--color-cyan)] px-4 py-1.5 text-sm font-semibold text-white hover:bg-[color:var(--color-cyan-dim)]">
                  Open live view
                </Link>
                <Link to="/lab" className="rounded-full border border-border bg-white px-4 py-1.5 text-sm font-medium text-text hover:border-border-strong">
                  Review a clip
                </Link>
                <Link to="/events" className="rounded-full border border-border bg-white px-4 py-1.5 text-sm font-medium text-text hover:border-border-strong">
                  See history
                </Link>
              </div>
            </div>
          </div>
        </Card>

        {/* Alerts today */}
        <Card className="p-6 md:col-span-2">
          <div className="label-hud">Flagged today</div>
          <div className="mt-2 flex items-baseline gap-2">
            <div className="font-display text-4xl font-bold text-text tabular-nums">{events.length}</div>
            <div className="text-sm text-muted">moments</div>
          </div>
          <div className="mt-3 flex items-center gap-2 text-xs">
            {highToday > 0 ? (
              <>
                <span className="h-1.5 w-1.5 rounded-full" style={{ background: "var(--color-red)" }} />
                <span className="text-text">{highToday} needs your attention</span>
              </>
            ) : (
              <>
                <span className="h-1.5 w-1.5 rounded-full" style={{ background: "var(--color-green)" }} />
                <span className="text-muted">Nothing urgent</span>
              </>
            )}
          </div>
        </Card>

        {/* Cameras tile */}
        <Card className="p-6 md:col-span-3">
          <div className="label-hud">Cameras</div>
          <div className="mt-2 flex items-baseline gap-2">
            <div className="font-display text-4xl font-bold text-text tabular-nums">{onlineCount}</div>
            <div className="text-sm text-muted">of {total || "—"} live</div>
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {cameras.slice(0, 8).map((c) => (
              <span
                key={c}
                title={c}
                className="h-2 w-6 rounded-full"
                style={{ background: status[c]?.online ? "var(--color-green)" : "var(--color-border-strong)" }}
              />
            ))}
          </div>
        </Card>

        {/* Top incident */}
        <Card className="p-6 md:col-span-3">
          <div className="label-hud">Latest flag</div>
          {topEvent ? (
            <>
              <div className="mt-2 flex items-center gap-2">
                <SeverityChip sev={topEvent.severity} />
                <span className="text-xs text-muted">{timeAgo(topEvent.ts)}</span>
              </div>
              <div className="mt-2 text-sm font-medium leading-snug text-text line-clamp-2">
                {topEvent.description}
              </div>
              <div className="mt-2 text-xs text-muted">at {topEvent.camera}</div>
            </>
          ) : (
            <div className="mt-2 text-sm text-muted">Nothing yet today.</div>
          )}
        </Card>

        {/* Live cameras strip — wide */}
        <Card className="p-6 md:col-span-6">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <div className="label-hud">Live view</div>
              <div className="mt-0.5 text-sm font-semibold text-text">Cameras right now</div>
            </div>
            <Link to="/view" className="text-xs font-semibold text-[color:var(--color-cyan)] hover:underline">Open live view →</Link>
          </div>
          {cameras.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted">
              No cameras configured yet.
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {cameras.slice(0, 6).map((c) => {
                const s = status[c];
                const online = s?.online ?? false;
                return (
                  <Link key={c} to="/view" className="soft-panel overflow-hidden">
                    <div className="relative" style={{ aspectRatio: "16 / 9" }}>
                      {online ? (
                        <img src={api.streamUrl(c)} alt={`${c} live`} className="h-full w-full object-cover" />
                      ) : (
                        <div className="flex h-full w-full items-center justify-center bg-[color:var(--color-surface-2)]">
                          <div className="text-center">
                            <div className="text-xs font-semibold text-muted">Reconnecting…</div>
                          </div>
                        </div>
                      )}
                      <div className="absolute left-2.5 top-2.5">
                        <Chip tone={online ? "green" : "red"} dot>
                          {online ? "Live" : "Offline"}
                        </Chip>
                      </div>
                    </div>
                    <div className="flex items-center justify-between px-3.5 py-2.5">
                      <div className="text-sm font-medium capitalize text-text">{c.replace(/-/g, " ")}</div>
                      <span className="text-[10px] text-muted-soft">{online ? "recording" : "—"}</span>
                    </div>
                  </Link>
                );
              })}
            </div>
          )}
        </Card>

        {/* Recent activity list */}
        <Card className="p-6 md:col-span-6">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <div className="label-hud">Recent activity</div>
              <div className="mt-0.5 text-sm font-semibold text-text">Latest flagged moments</div>
            </div>
            <Link to="/events" className="text-xs font-semibold text-[color:var(--color-cyan)] hover:underline">See all →</Link>
          </div>
          {events.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted">
              Nothing flagged in the last while.
            </div>
          ) : (
            <ul className="divide-y divide-[color:var(--color-border)]">
              {events.slice(0, 5).map((e) => (
                <li key={e.id} className="flex items-start gap-4 py-3 first:pt-0 last:pb-0">
                  <SeverityChip sev={e.severity} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm text-text">{e.description}</div>
                    <div className="mt-0.5 text-xs text-muted">
                      {e.camera} · {timeAgo(e.ts)}{e.plate ? ` · ${e.plate}` : ""}
                    </div>
                  </div>
                  {e.ai_summary && (
                    <div className="hidden max-w-[240px] text-xs italic text-muted md:block">
                      "{e.ai_summary}"
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
