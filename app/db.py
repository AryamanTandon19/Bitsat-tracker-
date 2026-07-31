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
CREATE TABLE IF NOT EXISTS ai_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id),
    camera TEXT NOT NULL,
    ts REAL NOT NULL,
    tier INTEGER NOT NULL,
    model TEXT NOT NULL,
    suspicious INTEGER NOT NULL,
    summary TEXT,
    findings_json TEXT
);
CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    camera TEXT NOT NULL,
    event_id INTEGER,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL
);
-- Automated gate register. One row per visit: a vehicle crossing the gate
-- opens a visit, the next crossing (after a debounce) closes it. Replaces the
-- handwritten visitor book, and works for residents too.
CREATE TABLE IF NOT EXISTS vehicle_visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plate TEXT NOT NULL,
    registered INTEGER NOT NULL DEFAULT 0,   -- 1 = in the vehicle registry
    owner_name TEXT,
    flat_number TEXT,
    entry_ts REAL NOT NULL,
    entry_camera TEXT,
    exit_ts REAL,                            -- NULL = still inside
    exit_camera TEXT,
    last_seen_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_visits_plate ON vehicle_visits(plate);
CREATE INDEX IF NOT EXISTS idx_visits_open ON vehicle_visits(exit_ts);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id),
    verdict TEXT NOT NULL,
    user_name TEXT,
    ts REAL NOT NULL
);
-- Committee/guard announcements to residents, written from the operator app.
CREATE TABLE IF NOT EXISTS notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    author TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    audience TEXT NOT NULL DEFAULT 'all',   -- 'all' | 'flat'
    flat_number TEXT,                       -- set when audience = 'flat'
    sent_ts REAL,                           -- NULL = not delivered yet
    recipients INTEGER NOT NULL DEFAULT 0
);
-- Operator accounts. Guards and committee members sign in to the operator app;
-- their identity is what gets written against a verdict or an announcement, so
-- it has to come from a session and not from a name someone typed.
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL,                      -- guard | committee | admin
    pw_hash TEXT NOT NULL,                   -- scrypt, salt embedded
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    last_login REAL
);
-- Server-side sessions. Only the hash of the token is stored, so a copy of
-- this database does not hand anyone a live session.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    user_agent TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
-- Parking slots: a drawn space, optionally assigned to a vehicle. This is the
-- knowledge a society has that a generic camera system never does.
CREATE TABLE IF NOT EXISTS parking_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera TEXT NOT NULL,
    label TEXT NOT NULL,                     -- "B-12", "Visitor 3"
    polygon_json TEXT NOT NULL,              -- [[x, y], ...] source pixels
    plate TEXT,                              -- the vehicle that belongs here
    flat_number TEXT,
    created_at REAL NOT NULL,
    UNIQUE (camera, label)
);
-- Every arrival and departure, so "when did my car leave?" has an answer
-- months later and a dispute has a record.
CREATE TABLE IF NOT EXISTS slot_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id INTEGER NOT NULL REFERENCES parking_slots(id),
    ts REAL NOT NULL,
    kind TEXT NOT NULL,                      -- occupied | vacated | intruder
    plate TEXT,
    notified INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_slot_activity ON slot_activity(slot_id, ts);
"""

# columns added after first release — applied with ALTER TABLE on startup so
# existing databases upgrade in place
MIGRATIONS = [
    "ALTER TABLE events ADD COLUMN incident_id INTEGER",
    # the scoring layer: the number and the words behind it, so an alert can
    # always answer "why?" long after the frame is gone
    "ALTER TABLE events ADD COLUMN score REAL NOT NULL DEFAULT 0",
    "ALTER TABLE events ADD COLUMN score_why TEXT",
]


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
            for mig in MIGRATIONS:
                try:
                    self._conn.execute(mig)
                except sqlite3.OperationalError:
                    pass  # column already exists
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
    # -- visitor log (automated gate register) ----------------------------
    def record_gate_crossing(self, plate: str, camera: str, ts: float,
                             debounce_s: float = 60.0,
                             min_visit_s: float = 30.0) -> dict | None:
        """Record a vehicle crossing the gate and return the affected visit.

        Crossings alternate: the first opens a visit, the next closes it. A
        vehicle dwelling in view produces many sightings, so `debounce_s`
        collapses them into one crossing, and `min_visit_s` stops a car that
        pauses at the gate from being logged as an instant in-and-out.

        Returns {"action": "entry"|"exit"|"ignored", "visit": {...}}.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM vehicle_visits WHERE plate = ? AND exit_ts IS NULL"
                " ORDER BY id DESC LIMIT 1", (plate,)).fetchone()

            if row is not None:
                # Same pass still in view, or too soon to be a real exit.
                if ts - row["last_seen_ts"] < debounce_s or \
                        ts - row["entry_ts"] < min_visit_s:
                    self._conn.execute(
                        "UPDATE vehicle_visits SET last_seen_ts = ? WHERE id = ?",
                        (ts, row["id"]))
                    self._conn.commit()
                    return {"action": "ignored",
                            "visit": dict(row) | {"last_seen_ts": ts}}
                self._conn.execute(
                    "UPDATE vehicle_visits SET exit_ts = ?, exit_camera = ?,"
                    " last_seen_ts = ? WHERE id = ?", (ts, camera, ts, row["id"]))
                self._conn.commit()
                out = self._conn.execute(
                    "SELECT * FROM vehicle_visits WHERE id = ?",
                    (row["id"],)).fetchone()
                return {"action": "exit", "visit": dict(out)}

            veh = self._conn.execute(
                "SELECT owner_name, flat_number FROM vehicles WHERE plate_number = ?",
                (plate,)).fetchone()
            cur = self._conn.execute(
                "INSERT INTO vehicle_visits (plate, registered, owner_name,"
                " flat_number, entry_ts, entry_camera, last_seen_ts)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (plate, int(veh is not None),
                 veh["owner_name"] if veh else None,
                 veh["flat_number"] if veh else None, ts, camera, ts))
            self._conn.commit()
            new = self._conn.execute(
                "SELECT * FROM vehicle_visits WHERE id = ?",
                (cur.lastrowid,)).fetchone()
            return {"action": "entry", "visit": dict(new)}

    def open_visits(self) -> list[dict]:
        """Vehicles currently inside — the 'who is in the society now' view."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM vehicle_visits WHERE exit_ts IS NULL"
                " ORDER BY entry_ts DESC").fetchall()
            return [dict(r) for r in rows]

    def recent_visits(self, limit: int = 200, plate: str | None = None,
                      registered: bool | None = None) -> list[dict]:
        """The gate register, newest first. Optionally filter by plate or by
        whether the vehicle is a known resident."""
        sql = "SELECT * FROM vehicle_visits"
        where, args = [], []
        if plate:
            where.append("plate LIKE ?")
            args.append(f"%{plate}%")
        if registered is not None:
            where.append("registered = ?")
            args.append(int(registered))
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY entry_ts DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
            return [dict(r) for r in rows]

    def overstaying_visits(self, hours: float = 12.0) -> list[dict]:
        """Unregistered vehicles still inside after `hours` — the flag a guard
        actually acts on."""
        cutoff = time.time() - hours * 3600
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM vehicle_visits WHERE exit_ts IS NULL"
                " AND registered = 0 AND entry_ts < ?"
                " ORDER BY entry_ts ASC", (cutoff,)).fetchall()
            return [dict(r) for r in rows]

    def insert_event(self, ts: float, camera: str, event_type: str, severity: str,
                     plate: str | None, track_ids: list[int], confidence: float,
                     description: str, suppressed: bool = False,
                     incident_window_s: float = 120.0,
                     score: float = 0.0, score_why: str = "") -> int:
        """Events on the same camera within incident_window_s are grouped
        into ONE incident (incident_id = id of the incident's first event)."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT incident_id, id FROM events WHERE camera = ? AND ts > ?"
                " ORDER BY id DESC LIMIT 1", (camera, ts - incident_window_s))
            row = cur.fetchone()
            prior_incident = (row["incident_id"] or row["id"]) if row else None
            cur = self._conn.execute(
                "INSERT INTO events (ts, camera, event_type, severity, plate,"
                " track_ids, confidence, description, suppressed, incident_id,"
                " score, score_why)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, camera, event_type, severity, plate,
                 json.dumps(track_ids), confidence, description,
                 int(suppressed), prior_incident, float(score), score_why))
            event_id = cur.lastrowid
            if prior_incident is None:
                self._conn.execute(
                    "UPDATE events SET incident_id = ? WHERE id = ?",
                    (event_id, event_id))
            self._conn.commit()
            return event_id

    def get_event(self, event_id: int) -> dict | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM events WHERE id = ?",
                                     (event_id,))
            r = cur.fetchone()
            return dict(r) if r else None

    # -- guard feedback (✅/❌ on Telegram alerts) ---------------------------
    def insert_feedback(self, event_id: int, verdict: str,
                        user_name: str = "") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO feedback (event_id, verdict, user_name, ts)"
                " VALUES (?, ?, ?, ?)",
                (event_id, verdict, user_name, time.time()))
            self._conn.commit()
            fid = cur.lastrowid
        self.append_audit(user_name or "telegram", "FEEDBACK",
                          {"event_id": event_id, "verdict": verdict})
        return fid

    def feedback_summary(self) -> dict:
        with self._lock:
            cur = self._conn.execute(
                "SELECT verdict, COUNT(*) AS n FROM feedback GROUP BY verdict")
            return {r["verdict"]: r["n"] for r in cur.fetchall()}

    def verdict_rates(self, min_samples: int = 5,
                      days: float = 60.0) -> dict[tuple[str, str], dict]:
        """How often each (camera, event_type) turned out to be nothing.

        This is what the guards' taps are *for*: an alert a site keeps
        dismissing should get quieter at that site. Only the latest verdict per
        event counts, and a pairing needs `min_samples` before it is allowed to
        move anything — two dismissals are an opinion, not a pattern.
        """
        since = time.time() - days * 86400
        with self._lock:
            rows = self._conn.execute(
                "SELECT e.camera, e.event_type, f.verdict FROM events e"
                " JOIN feedback f ON f.id = (SELECT id FROM feedback"
                "   WHERE event_id = e.id ORDER BY id DESC LIMIT 1)"
                " WHERE e.ts >= ?", (since,)).fetchall()
        tally: dict[tuple[str, str], dict] = {}
        for r in rows:
            k = (r["camera"], r["event_type"])
            t = tally.setdefault(k, {"n": 0, "false": 0, "real": 0})
            t["n"] += 1
            t["false" if r["verdict"] == "false_alarm" else "real"] += 1
        return {k: {**t,
                    "false_alarm_rate": t["false"] / t["n"],
                    "confirmed_rate": t["real"] / t["n"]}
                for k, t in tally.items() if t["n"] >= min_samples}

    def event_verdicts(self, event_ids: list[int]) -> dict[int, dict]:
        """Latest verdict per event — what the operator app shows as 'already
        triaged' so two guards don't work the same alert twice."""
        if not event_ids:
            return {}
        marks = ",".join("?" * len(event_ids))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT event_id, verdict, user_name, ts FROM feedback"
                f" WHERE event_id IN ({marks}) ORDER BY id ASC",
                event_ids).fetchall()
        # ascending, so the last write for an event wins
        return {r["event_id"]: dict(r) for r in rows}

    # -- operator accounts & sessions ----------------------------------------
    def add_user(self, username: str, display_name: str, role: str,
                 password: str, actor: str = "system") -> int:
        from .auth import ROLES, hash_password
        username = username.strip().lower()
        if not username:
            raise ValueError("username must not be empty")
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO users (username, display_name, role, pw_hash,"
                " active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
                (username, display_name.strip() or username, role,
                 hash_password(password), time.time()))
            self._conn.commit()
            uid = cur.lastrowid
        # the password never reaches the audit log
        self.append_audit(actor, "USER_ADD",
                          {"username": username, "role": role})
        return uid

    def get_user(self, username: str) -> dict | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM users WHERE username = ?",
                                   (username.strip().lower(),)).fetchone()
            return dict(r) if r else None

    def list_users(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, username, display_name, role, active, created_at,"
                " last_login FROM users ORDER BY username").fetchall()
            return [dict(r) for r in rows]

    def set_user_password(self, username: str, password: str,
                          actor: str = "system") -> bool:
        from .auth import hash_password
        with self._lock:
            cur = self._conn.execute(
                "UPDATE users SET pw_hash = ? WHERE username = ?",
                (hash_password(password), username.strip().lower()))
            self._conn.commit()
            changed = cur.rowcount > 0
        if changed:
            # every existing session for that account dies with the password
            self.drop_sessions_for(username)
            self.append_audit(actor, "USER_PASSWORD", {"username": username})
        return changed

    def set_user_active(self, username: str, active: bool,
                        actor: str = "system") -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE users SET active = ? WHERE username = ?",
                (int(active), username.strip().lower()))
            self._conn.commit()
            changed = cur.rowcount > 0
        if changed:
            if not active:
                self.drop_sessions_for(username)
            self.append_audit(actor, "USER_ACTIVE",
                              {"username": username, "active": bool(active)})
        return changed

    def authenticate(self, username: str, password: str) -> dict | None:
        """Return the user on a correct password for an active account."""
        from .auth import verify_password
        user = self.get_user(username)
        if user is None:
            # spend the time anyway: returning instantly for an unknown user
            # tells an attacker which usernames exist
            verify_password(password, "scrypt$32768$8$1$" + "00" * 16 +
                            "$" + "00" * 32)
            return None
        if not verify_password(password, user["pw_hash"]) or not user["active"]:
            return None
        with self._lock:
            self._conn.execute("UPDATE users SET last_login = ? WHERE id = ?",
                               (time.time(), user["id"]))
            self._conn.commit()
        return user

    def create_session(self, user_id: int, token_hash: str, expires_at: float,
                       user_agent: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at,"
                " expires_at, user_agent) VALUES (?, ?, ?, ?, ?)",
                (token_hash, user_id, time.time(), expires_at, user_agent[:200]))
            self._conn.commit()

    def session_user(self, token_hash: str, now: float | None = None) -> dict | None:
        """The account behind a session token, or None if it is unknown,
        expired, or the account has since been deactivated."""
        now = time.time() if now is None else now
        with self._lock:
            r = self._conn.execute(
                "SELECT u.*, s.expires_at FROM sessions s JOIN users u"
                " ON u.id = s.user_id WHERE s.token_hash = ?",
                (token_hash,)).fetchone()
        if r is None or r["expires_at"] <= now or not r["active"]:
            return None
        return dict(r)

    def touch_session(self, token_hash: str, expires_at: float) -> None:
        """Slide the expiry forward so an active shift is not interrupted."""
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
                (expires_at, token_hash))
            self._conn.commit()

    def drop_session(self, token_hash: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE token_hash = ?",
                               (token_hash,))
            self._conn.commit()

    def drop_sessions_for(self, username: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM sessions WHERE user_id IN"
                " (SELECT id FROM users WHERE username = ?)",
                (username.strip().lower(),))
            self._conn.commit()
            return cur.rowcount

    def purge_expired_sessions(self, now: float | None = None) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE expires_at <= ?",
                                     (time.time() if now is None else now,))
            self._conn.commit()
            return cur.rowcount

    # -- parking slots -------------------------------------------------------
    def add_slot(self, camera: str, label: str, polygon: list,
                 plate: str | None = None, flat_number: str = "",
                 actor: str = "system") -> int:
        if not polygon or len(polygon) < 3:
            raise ValueError("a slot needs at least three points")
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR REPLACE INTO parking_slots (camera, label,"
                " polygon_json, plate, flat_number, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (camera, label, json.dumps(polygon), plate or None,
                 flat_number or None, time.time()))
            self._conn.commit()
            sid = cur.lastrowid
        self.append_audit(actor, "SLOT_CHANGE",
                          {"op": "add", "camera": camera, "label": label,
                           "plate": plate})
        return sid

    def list_slots(self, camera: str | None = None) -> list[dict]:
        sql = ("SELECT s.*, v.owner_name FROM parking_slots s"
               " LEFT JOIN vehicles v ON v.plate_number = s.plate")
        args: list = []
        if camera:
            sql += " WHERE s.camera = ?"
            args.append(camera)
        sql += " ORDER BY s.camera, s.label"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [{**dict(r), "polygon": json.loads(r["polygon_json"])}
                for r in rows]

    def remove_slot(self, slot_id: int, actor: str = "system") -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM parking_slots WHERE id = ?",
                                     (slot_id,))
            self._conn.commit()
            gone = cur.rowcount > 0
        if gone:
            self.append_audit(actor, "SLOT_CHANGE", {"op": "remove",
                                                     "slot_id": slot_id})
        return gone

    def record_slot_activity(self, slot_id: int, kind: str, plate: str | None,
                             ts: float, notified: bool = False) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO slot_activity (slot_id, ts, kind, plate, notified)"
                " VALUES (?, ?, ?, ?, ?)",
                (slot_id, ts, kind, plate, int(notified)))
            self._conn.commit()
            return cur.lastrowid

    def slot_activity(self, limit: int = 200, slot_id: int | None = None,
                      plate: str | None = None) -> list[dict]:
        sql = ("SELECT a.*, s.label, s.camera, s.flat_number"
               " FROM slot_activity a JOIN parking_slots s ON s.id = a.slot_id")
        where, args = [], []
        if slot_id:
            where.append("a.slot_id = ?")
            args.append(slot_id)
        if plate:
            where.append("a.plate = ?")
            args.append(plate)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY a.id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    # -- notices (committee -> members) --------------------------------------
    def add_notice(self, title: str, body: str, author: str,
                   audience: str = "all", flat_number: str = "") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO notices (ts, author, title, body, audience,"
                " flat_number) VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), author, title, body, audience, flat_number))
            self._conn.commit()
            nid = cur.lastrowid
        self.append_audit(author, "NOTICE", {"id": nid, "title": title,
                                             "audience": audience})
        return nid

    def recent_notices(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM notices ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
            return [dict(r) for r in rows]

    def mark_notice_sent(self, notice_id: int, recipients: int):
        with self._lock:
            self._conn.execute(
                "UPDATE notices SET sent_ts = ?, recipients = ? WHERE id = ?",
                (time.time(), recipients, notice_id))
            self._conn.commit()

    def notifications_for_incident(self, incident_id: int) -> int:
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) AS n FROM notifications n JOIN events e"
                " ON e.id = n.event_id WHERE e.incident_id = ?"
                " AND n.status IN ('sent', 'sent-no-clip')", (incident_id,))
            return cur.fetchone()["n"]

    def recent_events(self, limit: int = 100) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT e.*, c.id AS clip_id, c.path AS clip_path, c.deleted AS clip_deleted,"
                " (SELECT ar.summary FROM ai_reviews ar WHERE ar.event_id = e.id"
                "  ORDER BY ar.tier DESC, ar.id DESC LIMIT 1) AS ai_summary"
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

    # -- AI reviews & cost tracking ----------------------------------------
    def insert_ai_review(self, event_id: int, camera: str, tier: int,
                         model: str, suspicious: bool, summary: str,
                         findings: list) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO ai_reviews (event_id, camera, ts, tier, model,"
                " suspicious, summary, findings_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, camera, time.time(), tier, model, int(suspicious),
                 summary, json.dumps(findings)))
            self._conn.commit()
            return cur.lastrowid

    def insert_ai_usage(self, camera: str, event_id: int | None, model: str,
                        input_tokens: int, output_tokens: int,
                        cost_usd: float) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO ai_usage (ts, camera, event_id, model,"
                " input_tokens, output_tokens, cost_usd)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (time.time(), camera, event_id, model, input_tokens,
                 output_tokens, cost_usd))
            self._conn.commit()
            return cur.lastrowid

    def ai_reviews_last_24h(self, camera: str) -> int:
        cutoff = time.time() - 86400
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) AS n FROM ai_reviews"
                " WHERE camera = ? AND ts > ? AND tier = 1", (camera, cutoff))
            return cur.fetchone()["n"]

    def ai_cost_summary(self) -> dict:
        """Spend + call counts for the dashboard cost meter."""
        now = time.time()
        out = {}
        with self._lock:
            for label, cutoff in (("last_24h", now - 86400),
                                  ("last_30d", now - 30 * 86400)):
                cur = self._conn.execute(
                    "SELECT COUNT(*) AS calls,"
                    " COALESCE(SUM(cost_usd), 0) AS cost_usd,"
                    " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
                    " COALESCE(SUM(output_tokens), 0) AS output_tokens"
                    " FROM ai_usage WHERE ts > ?", (cutoff,))
                out[label] = dict(cur.fetchone())
            cur = self._conn.execute(
                "SELECT camera, COUNT(*) AS calls,"
                " COALESCE(SUM(cost_usd), 0) AS cost_usd"
                " FROM ai_usage WHERE ts > ? GROUP BY camera"
                " ORDER BY cost_usd DESC", (now - 30 * 86400,))
            out["per_camera_30d"] = [dict(r) for r in cur.fetchall()]
        return out

    def notifications_last_hour(self, camera: str) -> int:
        cutoff = time.time() - 3600
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) AS n FROM notifications n"
                " JOIN events e ON e.id = n.event_id"
                " WHERE e.camera = ? AND n.sent_at > ? AND n.status = 'sent'",
                (camera, cutoff))
            return cur.fetchone()["n"]
