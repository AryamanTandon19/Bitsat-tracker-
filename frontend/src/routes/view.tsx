import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Card, Chip } from "@/components/ui-prims";

export const Route = createFileRoute("/view")({
  head: () => ({
    meta: [
      { title: "Live view · VisionGuard" },
      { name: "description", content: "See every camera at once. Tap any tile to enlarge and look closer." },
      { property: "og:title", content: "Live view · VisionGuard" },
      { property: "og:description", content: "See every camera at once. Tap any tile to enlarge and look closer." },
    ],
  }),
  component: LiveView,
});

function LiveView() {
  const [cameras, setCameras] = useState<string[]>([]);
  const [status, setStatus] = useState<Record<string, { online: boolean; last_frame_age_s: number }>>({});
  const [focus, setFocus] = useState<string | null>(null);

  useEffect(() => {
    api.cameras().then(setCameras).catch(() => {});
    const tick = () => api.status().then(setStatus).catch(() => {});
    tick();
    const t = setInterval(tick, 5000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!focus) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setFocus(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [focus]);

  const onlineCount = Object.values(status).filter((s) => s.online).length;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="label-hud">Live view</div>
          <h1 className="mt-1 font-display text-2xl font-bold tracking-tight text-text md:text-3xl">
            Cameras right now
          </h1>
          <p className="mt-1 text-sm text-muted">
            Tap any tile to enlarge. Press Esc to close.
          </p>
        </div>
        <div className="text-xs text-muted">
          <span className="mono font-semibold text-text">{onlineCount}</span> of{" "}
          <span className="mono font-semibold text-text">{cameras.length || "—"}</span> live · refreshes every 5s
        </div>
      </header>

      {cameras.length === 0 ? (
        <Card className="p-10 text-center text-sm text-muted">No cameras configured yet.</Card>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {cameras.map((c) => {
            const online = status[c]?.online ?? false;
            return (
              <button
                key={c}
                onClick={() => setFocus(c)}
                className="soft-panel group overflow-hidden text-left transition-transform hover:-translate-y-0.5 hover:shadow-md focus:outline-none focus:ring-2 focus:ring-[color:var(--color-cyan)]"
              >
                <div className="relative" style={{ aspectRatio: "16 / 9" }}>
                  {online ? (
                    <img src={api.streamUrl(c)} alt={`${c} live`} className="h-full w-full object-cover" />
                  ) : (
                    <div className="flex h-full w-full items-center justify-center bg-[color:var(--color-surface-2)]">
                      <div className="text-xs font-semibold text-muted">Reconnecting…</div>
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
                  <span className="text-[10px] text-muted-soft group-hover:text-[color:var(--color-cyan)]">
                    tap to enlarge →
                  </span>
                </div>
              </button>
            );
          })}
        </div>
      )}

      {focus && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
          onClick={() => setFocus(null)}
        >
          <div
            className="soft-panel w-full max-w-5xl overflow-hidden bg-[color:var(--color-surface)]"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="relative" style={{ aspectRatio: "16 / 9" }}>
              {status[focus]?.online ? (
                <img src={api.streamUrl(focus)} alt={`${focus} live enlarged`} className="h-full w-full object-cover" />
              ) : (
                <div className="flex h-full w-full items-center justify-center bg-[color:var(--color-surface-2)]">
                  <div className="text-sm font-semibold text-muted">Reconnecting…</div>
                </div>
              )}
              <div className="absolute left-3 top-3">
                <Chip tone={status[focus]?.online ? "green" : "red"} dot>
                  {status[focus]?.online ? "Live" : "Offline"}
                </Chip>
              </div>
              <button
                onClick={() => setFocus(null)}
                className="absolute right-3 top-3 rounded-full bg-white/90 px-3 py-1 text-xs font-semibold text-text hover:bg-white"
              >
                Close ✕
              </button>
            </div>
            <div className="flex items-center justify-between px-5 py-3">
              <div className="text-base font-semibold capitalize text-text">{focus.replace(/-/g, " ")}</div>
              <span className="text-xs text-muted">enlarged view · Esc to close</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
