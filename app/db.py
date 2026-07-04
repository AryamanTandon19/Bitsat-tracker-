"""SQLite storage + append-only, hash-chained audit log.

Single-file DB, safe for use from multiple threads (one connection guarded by
a lock — traffic is light enough that this is simpler and safer than a pool).
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

GENESIS_HASH = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plate_number TEXT NOT NULL UNIQUE,
    owner_name TEXT,
    owner_phone TEXT,
    flat_number TEXT,
    telegram_chat_id TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    flat_number TEXT,
    notes TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    camera TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    plate TEXT,
    track_ids TEXT,
    confidence REAL,
    description TEXT,
    suppressed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id),
    path TEXT NOT NULL,
    sidecar_path TEXT,
    start_ts REAL,
    end_ts REAL,
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id),
    chat_id TEXT NOT NULL,
    sent_at REAL NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    details_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL
);
"""


def _row_payload(ts: float, actor: str, action: str, details_json: str) -> str:
    # Canonical serialization: repr of the float keeps full precision and is
    # stable across platforms.
    return f"{ts!r}|{actor}|{action}|{details_json}"


def compute_row_hash(prev_hash: str, ts: float, actor: str, action: str,
                     details_json: str) -> str:
    payload = prev_hash + _row_payload(ts, actor, action, details_json)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Database:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    # -- audit (append-only hash chain) ------------------------------------
    def append_audit(self, actor: str, action: str, details: dict) -> int:
        details_json = json.dumps(details, sort_keys=True, ensure_ascii=False)
        ts = time.time()
        with self._lock:
            cur = self._conn.execute(
                "SELECT row_hash FROM audit_log ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            prev_hash = row["row_hash"] if row else GENESIS_HASH
            row_hash = compute_row_hash(prev_hash, ts, actor, action, details_json)
            cur = self._conn.execute(
                "INSERT INTO audit_log (ts, actor, action, details_json, prev_hash, row_hash)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (ts, actor, action, details_json, prev_hash, row_hash))
            self._conn.commit()
            return cur.lastrowid

    def audit_rows(self) -> list[dict]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM audit_log ORDER BY id")
            return [dict(r) for r in cur.fetchall()]

    def verify_audit_chain(self) -> tuple[bool, list[str]]:
        """Walk the chain; returns (ok, list of problems)."""
        problems = []
        prev = GENESIS_HASH
        for r in self.audit_rows():
            expect = compute_row_hash(prev, r["ts"], r["actor"], r["action"],
                                      r["details_json"])
            if r["prev_hash"] != prev:
                problems.append(f"row {r['id']}: prev_hash mismatch")
            if r["row_hash"] != expect:
                problems.append(f"row {r['id']}: row_hash mismatch (content tampered)")
            prev = r["row_hash"]
        return (not problems, problems)

    # -- vehicle registry ---------------------------------------------------
    def add_vehicle(self, plate: str, owner_name: str = "", owner_phone: str = "",
                    flat_number: str = "", telegram_chat_id: str = "",
                    actor: str = "system") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO vehicles"
                " (plate_number, owner_name, owner_phone, flat_number, telegram_chat_id, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (plate, owner_name, owner_phone, flat_number, telegram_chat_id, time.time()))
            self._conn.commit()
        self.append_audit(actor, "REGISTRY_CHANGE",
                          {"op": "add", "plate": plate, "flat": flat_number})

    def remove_vehicle(self, plate: str, actor: str = "system") -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM vehicles WHERE plate_number = ?", (plate,))
            self._conn.commit()
            removed = cur.rowcount > 0
        if removed:
            self.append_audit(actor, "REGISTRY_CHANGE", {"op": "remove", "plate": plate})
        return removed

    def list_vehicles(self) -> list[dict]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM vehicles ORDER BY plate_number")
            return [dict(r) for r in cur.fetchall()]

    def vehicle_by_plate(self, plate: str) -> dict | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM vehicles WHERE plate_number = ?", (plate,))
            r = cur.fetchone()
            return dict(r) if r else None

    def registry_plates(self) -> list[str]:
        with self._lock:
            cur = self._conn.execute("SELECT plate_number FROM vehicles")
            return [r["plate_number"] for r in cur.fetchall()]

    def seed_registry_from_csv(self, csv_path: str) -> int:
        """Import registry.csv. Only runs rows whose plate isn't present yet."""
        p = Path(csv_path)
        if not p.exists():
            return 0
        existing = set(self.registry_plates())
        count = 0
        with p.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                plate = (row.get("plate_number") or "").strip().upper()
                if not plate or plate in existing:
                    continue
                self.add_vehicle(plate,
                                 row.get("owner_name", "").strip(),
                                 row.get("owner_phone", "").strip(),
                                 row.get("flat_number", "").strip(),
                                 row.get("telegram_chat_id", "").strip(),
                                 actor="csv-import")
                count += 1
        return count

    # -- events / clips / notifications --------------------------------------
    def insert_event(self, ts: float, camera: str, event_type: str, severity: str,
                     plate: str | None, track_ids: list[int], confidence: float,
                     description: str, suppressed: bool = False) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (ts, camera, event_type, severity, plate,"
                " track_ids, confidence, description, suppressed)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, camera, event_type, severity, plate,
                 json.dumps(track_ids), confidence, description, int(suppressed)))
            self._conn.commit()
            return cur.lastrowid

    def recent_events(self, limit: int = 100) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT e.*, c.id AS clip_id, c.path AS clip_path, c.deleted AS clip_deleted"
                " FROM events e LEFT JOIN clips c ON c.event_id = e.id"
                " ORDER BY e.id DESC LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]

    def insert_clip(self, event_id: int, path: str, sidecar_path: str,
                    start_ts: float, end_ts: float) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO clips (event_id, path, sidecar_path, start_ts, end_ts, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (event_id, path, sidecar_path, start_ts, end_ts, time.time()))
            self._conn.commit()
            clip_id = cur.lastrowid
        self.append_audit("system", "CLIP_SAVED",
                          {"clip_id": clip_id, "event_id": event_id, "path": path})
        return clip_id

    def get_clip(self, clip_id: int) -> dict | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,))
            r = cur.fetchone()
            return dict(r) if r else None

    def mark_clip_deleted(self, clip_id: int, actor: str, reason: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE clips SET deleted = 1 WHERE id = ?", (clip_id,))
            self._conn.commit()
        self.append_audit(actor, "CLIP_DELETED",
                          {"clip_id": clip_id, "reason": reason})

    def insert_notification(self, event_id: int, chat_id: str, status: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO notifications (event_id, chat_id, sent_at, status)"
                " VALUES (?, ?, ?, ?)", (event_id, chat_id, time.time(), status))
            self._conn.commit()
            nid = cur.lastrowid
        self.append_audit("system", "NOTIFICATION_SENT",
                          {"notification_id": nid, "event_id": event_id,
                           "chat_id": chat_id, "status": status})
        return nid

    def notifications_last_hour(self, camera: str) -> int:
        cutoff = time.time() - 3600
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) AS n FROM notifications n"
                " JOIN events e ON e.id = n.event_id"
                " WHERE e.camera = ? AND n.sent_at > ? AND n.status = 'sent'",
                (camera, cutoff))
            return cur.fetchone()["n"]
