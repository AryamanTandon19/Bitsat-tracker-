// VisionGuard API client. Points at VITE_API_BASE if set, otherwise uses local mocks
// so the UI is fully explorable without the FastAPI backend.
import * as mock from "./mock-data";

const BASE = (import.meta as any).env?.VITE_API_BASE as string | undefined;
export const USING_MOCKS = !BASE;

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { credentials: "include", ...init });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? res.statusText);
  return res.json();
}

// Sleep for realism on mocks
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export const api = {
  async cameras(): Promise<string[]> {
    if (USING_MOCKS) return mock.cameras;
    return j("/api/cameras");
  },
  async status(): Promise<Record<string, { online: boolean; last_frame_age_s: number }>> {
    if (USING_MOCKS) return mock.status();
    return j("/api/status");
  },
  async events(limit = 100): Promise<mock.ArchiveEvent[]> {
    if (USING_MOCKS) return mock.events.slice(0, limit);
    return j(`/api/events?limit=${limit}`);
  },
  async registry(): Promise<mock.Vehicle[]> {
    if (USING_MOCKS) return mock.getRegistry();
    return j("/api/registry");
  },
  async addVehicle(form: Record<string, string>): Promise<{ ok: boolean; plate: string }> {
    if (USING_MOCKS) return mock.addVehicle(form);
    const body = new URLSearchParams(form);
    return j("/api/registry", { method: "POST", body });
  },
  async deleteVehicle(plate: string) {
    if (USING_MOCKS) return mock.deleteVehicle(plate);
    return j(`/api/registry/${encodeURIComponent(plate)}`, { method: "DELETE" });
  },
  async costs(): Promise<mock.Costs> {
    if (USING_MOCKS) return mock.costs;
    return j("/api/costs");
  },
  async startAnalyze(file: File, opts: { aiReview: boolean; zonesFrom: string }) {
    if (USING_MOCKS) return mock.startAnalyze(file, opts);
    const fd = new FormData();
    fd.append("file", file);
    fd.append("ai_review", String(opts.aiReview));
    fd.append("zones_from", opts.zonesFrom);
    return j<{ job_id: string }>("/api/analyze", { method: "POST", body: fd });
  },
  async job(jobId: string): Promise<mock.Job> {
    if (USING_MOCKS) return mock.pollJob(jobId);
    return j(`/api/analyze/${jobId}`);
  },
  jobVideoUrl(jobId: string): string {
    if (USING_MOCKS) return mock.SAMPLE_VIDEO;
    return `${BASE}/api/analyze/${jobId}/video`;
  },
  streamUrl(camera: string): string {
    if (USING_MOCKS) return mock.streamPoster(camera);
    return `${BASE}/stream/${camera}`;
  },
  async deleteClip(clipId: number, name: string, reason: string) {
    if (USING_MOCKS) {
      await sleep(400);
      return { ok: true };
    }
    const body = new URLSearchParams({ name, reason });
    return j(`/api/clips/${clipId}/delete`, { method: "POST", body });
  },
};
