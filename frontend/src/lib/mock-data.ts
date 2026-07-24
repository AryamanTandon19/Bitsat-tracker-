// Realistic mocks so the UI works without the FastAPI backend.

export type Severity = "HIGH" | "MEDIUM" | "LOW";

export interface Incident {
  index: number;
  start_s: number;
  end_s: number;
  severity: Severity;
  event_type: string;
  track_ids: number[];
  count: number;
  summary: string;
}
export interface AIFinding { time_s: number; activity: string; severity: Severity }
export interface EventLite {
  index: number; event_type: string; severity: Severity;
  video_time_s: number; plate: string | null; track_ids: number[];
  confidence: number; description: string;
}
export interface Job {
  id: string; filename: string;
  status: "queued" | "running" | "encoding" | "done" | "error";
  progress: number; message: string;
  events: EventLite[]; incidents: Incident[]; ai_findings: AIFinding[];
  ai_verdict: string; ai_note: string; error: string | null; video_ready: boolean;
}
export interface ArchiveEvent {
  id: number; ts: number; camera: string; event_type: string; severity: Severity;
  plate: string | null; track_ids: string; confidence: number; description: string;
  suppressed: number; clip_id: number | null; clip_path: string | null; clip_deleted: number;
  ai_summary: string | null;
}
export interface Vehicle {
  id: number; plate_number: string; owner_name: string; owner_phone: string;
  flat_number: string; telegram_chat_id: string; created_at: number;
}
export interface Costs {
  last_24h: { calls: number; cost_usd: number; input_tokens: number; output_tokens: number; cost_inr: number };
  last_30d: { calls: number; cost_usd: number; input_tokens: number; output_tokens: number; cost_inr: number };
  per_camera_30d: { camera: string; calls: number; cost_usd: number; cost_inr: number }[];
  ai_review_enabled: boolean;
}

export const cameras = ["gate", "parking-a", "parking-b", "lobby", "backlane", "rooftop"];

export function status() {
  const now = Date.now();
  return Object.fromEntries(
    cameras.map((c, i) => [c, { online: i !== 4, last_frame_age_s: 0.2 + (now % 1000) / 5000 + i * 0.1 }])
  );
}

// A small public sample video so the annotated player works on mocks.
export const SAMPLE_VIDEO =
  "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4";

export function streamPoster(camera: string) {
  // Warm neutral placeholder for demo (real feeds replace this).
  const bg = camera === "backlane" ? "#efeae1" : "#e8e2d6";
  const label = camera.replace(/-/g, " ");
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='640' height='360'>
    <rect width='100%' height='100%' fill='${bg}'/>
    <g fill='none' stroke='rgba(26,22,20,0.06)' stroke-width='1'>
      ${Array.from({ length: 12 }, (_, i) => `<line x1='0' y1='${i * 30}' x2='640' y2='${i * 30}'/>`).join("")}
      ${Array.from({ length: 22 }, (_, i) => `<line x1='${i * 30}' y1='0' x2='${i * 30}' y2='360'/>`).join("")}
    </g>
    <text x='24' y='40' fill='#1a1614' font-family='Outfit, sans-serif' font-size='18' font-weight='600'>${label}</text>
    <text x='24' y='340' fill='#8f8a83' font-family='monospace' font-size='11'>${new Date().toISOString().slice(11,19)}</text>
  </svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

export const events: ArchiveEvent[] = [
  {
    id: 42, ts: Date.now() / 1000 - 3600, camera: "gate", event_type: "suspicious_activity",
    severity: "HIGH", plate: "DL3CAB1234", track_ids: "[7]", confidence: 0.82,
    description: "Person lingering at vehicle for 48s before vehicle departed",
    suppressed: 0, clip_id: 5, clip_path: "/clips/5.mp4", clip_deleted: 0,
    ai_summary: "car driven away — likely theft",
  },
  {
    id: 41, ts: Date.now() / 1000 - 7200, camera: "parking-a", event_type: "unauthorized_vehicle",
    severity: "MEDIUM", plate: "UP16XY9911", track_ids: "[3]", confidence: 0.71,
    description: "Plate not in registry entered parking-a",
    suppressed: 0, clip_id: 4, clip_path: "/clips/4.mp4", clip_deleted: 0,
    ai_summary: "delivery vehicle, no clear intent",
  },
  {
    id: 40, ts: Date.now() / 1000 - 9000, camera: "backlane", event_type: "loitering",
    severity: "LOW", plate: null, track_ids: "[11]", confidence: 0.44,
    description: "Person detected in restricted zone",
    suppressed: 0, clip_id: 3, clip_path: "/clips/3.mp4", clip_deleted: 0, ai_summary: null,
  },
  {
    id: 39, ts: Date.now() / 1000 - 12000, camera: "lobby", event_type: "tailgating",
    severity: "MEDIUM", plate: null, track_ids: "[9,10]", confidence: 0.66,
    description: "Two people entered on one badge scan",
    suppressed: 0, clip_id: 2, clip_path: "/clips/2.mp4", clip_deleted: 1, ai_summary: null,
  },
  {
    id: 38, ts: Date.now() / 1000 - 20000, camera: "rooftop", event_type: "motion",
    severity: "LOW", plate: null, track_ids: "[1]", confidence: 0.31,
    description: "Ambient motion — bird detected",
    suppressed: 1, clip_id: null, clip_path: null, clip_deleted: 0, ai_summary: null,
  },
];

let registry: Vehicle[] = [
  { id: 1, plate_number: "DL3CAB1234", owner_name: "R. Sharma", owner_phone: "+91 98100 22222", flat_number: "B-402", telegram_chat_id: "111", created_at: Date.now() / 1000 - 86400 * 30 },
  { id: 2, plate_number: "HR26AB9090", owner_name: "N. Iyer", owner_phone: "+91 99999 11111", flat_number: "A-101", telegram_chat_id: "112", created_at: Date.now() / 1000 - 86400 * 12 },
  { id: 3, plate_number: "MH12CD4567", owner_name: "P. Kulkarni", owner_phone: "+91 98202 33344", flat_number: "C-703", telegram_chat_id: "", created_at: Date.now() / 1000 - 86400 * 3 },
];
let nextId = 100;
export function getRegistry() { return registry.slice(); }
export function addVehicle(form: Record<string, string>) {
  const plate = (form.plate || "").toUpperCase().trim();
  if (!/^[A-Z0-9-]{4,12}$/.test(plate)) throw new Error("Invalid plate");
  registry = [
    { id: nextId++, plate_number: plate, owner_name: form.owner_name || "", owner_phone: form.owner_phone || "", flat_number: form.flat_number || "", telegram_chat_id: form.telegram_chat_id || "", created_at: Date.now() / 1000 },
    ...registry,
  ];
  return { ok: true, plate };
}
export function deleteVehicle(plate: string) {
  registry = registry.filter((v) => v.plate_number !== plate);
  return { ok: true };
}

export const costs: Costs = {
  last_24h: { calls: 12, cost_usd: 0.34, input_tokens: 40000, output_tokens: 3000, cost_inr: 30.6 },
  last_30d: { calls: 300, cost_usd: 8.1, input_tokens: 1_000_000, output_tokens: 80_000, cost_inr: 729.0 },
  per_camera_30d: [
    { camera: "gate", calls: 120, cost_usd: 3.2, cost_inr: 288.0 },
    { camera: "parking-a", calls: 90, cost_usd: 2.4, cost_inr: 216.0 },
    { camera: "parking-b", calls: 55, cost_usd: 1.5, cost_inr: 135.0 },
    { camera: "lobby", calls: 25, cost_usd: 0.6, cost_inr: 54.0 },
    { camera: "backlane", calls: 10, cost_usd: 0.4, cost_inr: 36.0 },
  ],
  ai_review_enabled: true,
};

// --- Analyze job simulation ---
const jobs = new Map<string, { started: number; opts: { aiReview: boolean; zonesFrom: string }; filename: string }>();

export function startAnalyze(file: File, opts: { aiReview: boolean; zonesFrom: string }) {
  const id = Math.random().toString(16).slice(2, 14);
  jobs.set(id, { started: Date.now(), opts, filename: file.name });
  return Promise.resolve({ job_id: id });
}

export async function pollJob(id: string): Promise<Job> {
  const j = jobs.get(id);
  if (!j) throw new Error("job not found");
  const elapsed = (Date.now() - j.started) / 1000;
  const base: Job = {
    id, filename: j.filename, status: "queued", progress: 0, message: "queued",
    events: [], incidents: [], ai_findings: [], ai_verdict: "", ai_note: "",
    error: null, video_ready: false,
  };
  if (elapsed < 1) return { ...base, status: "queued", message: "waiting in queue…" };
  if (elapsed < 5) return { ...base, status: "running", progress: Math.min(0.85, (elapsed - 1) / 5), message: "scanning frames…" };
  if (elapsed < 6.5) return { ...base, status: "encoding", progress: 0.92, message: "encoding annotated video…" };

  const events: EventLite[] = [
    { index: 0, event_type: "motion", severity: "LOW", video_time_s: 4.2, plate: null, track_ids: [7], confidence: 0.45, description: "Motion near vehicle" },
    { index: 1, event_type: "suspicious_activity", severity: "MEDIUM", video_time_s: 12.3, plate: null, track_ids: [7], confidence: 0.62, description: "Person at vehicle >20s" },
    { index: 2, event_type: "suspicious_activity", severity: "MEDIUM", video_time_s: 34.1, plate: null, track_ids: [7], confidence: 0.7, description: "Repeated approach" },
    { index: 3, event_type: "vehicle_departed", severity: "HIGH", video_time_s: 60.0, plate: "DL3CAB1234", track_ids: [7], confidence: 0.88, description: "Vehicle drove away after activity" },
  ];
  const incidents: Incident[] = [
    { index: 0, start_s: 11, end_s: 60, severity: "HIGH", event_type: "suspicious_activity",
      track_ids: [7], count: 4, summary: "POSSIBLE VEHICLE THEFT: vehicle drove away 48s after unusual activity around it" },
  ];
  const ai_findings: AIFinding[] = j.opts.aiReview ? [
    { time_s: 12.3, activity: "person inspecting driver-side window", severity: "MEDIUM" },
    { time_s: 34.1, activity: "second approach, blocking camera line of sight", severity: "MEDIUM" },
    { time_s: 60.0, activity: "car driven away — likely theft", severity: "HIGH" },
  ] : [];
  return {
    ...base,
    status: "done", progress: 1,
    message: `${incidents.length} incident(s), ${events.length} alerts, ${ai_findings.length} AI findings`,
    events, incidents, ai_findings,
    ai_verdict: j.opts.aiReview ? "HIGH at 60s — car driven away after 48s of unusual activity, likely theft" : "",
    ai_note: j.opts.aiReview ? "" : "AI review disabled for this job.",
    video_ready: true,
  };
}
