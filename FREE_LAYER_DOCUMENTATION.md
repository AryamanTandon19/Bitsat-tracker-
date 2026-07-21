# Society AI Watchdog — Free Layer Documentation

## Executive Summary

The **Free Layer** is the first-stage decision system that determines whether a moment is worth sending to Claude for expensive AI review. It uses lightweight detection signals (YOLO skeleton keypoints, frame differencing, and simple geometry) to identify suspicious vehicle activity in real time.

**Core Principle**: Errs toward firing (false trigger costs ₹1, missed incident costs everything). Claude does the precision filtering.

**Entry Point**: `CandidateTrigger.is_candidate(detections, ts, pose_signals, motion)` → `(bool, list[str])`

**Output**: Event objects with severity `HIGH` (break-in/theft) or `MEDIUM` (other suspicious activity) sent to clip saver and notifier.

---

## Architecture Overview

```
Frame Input
    ↓
YOLO Object Detection (detector.py)
    ├─ Persons: track_id, bbox [x1,y1,x2,y2], confidence
    └─ Vehicles: track_id, bbox, confidence
    ↓
    ├─→ Motion Scoring (motion.py: VehicleMotion.scores)
    │   └─ Frame differencing → motion dict {vehicle_tid: float}
    │
    ├─→ Pose Estimation (pose.py: PoseEstimator if near_vehicle)
    │   └─ 17 COCO keypoints → pose signals {person_tid: {signals}}
    │
    └─→ Trigger Logic (trigger.py: CandidateTrigger.is_candidate)
        ├─ Per-person checks: touch, dwell, zones, pose, night
        ├─ Per-vehicle checks: motion burst, theft chain
        └─ Escalation: break-in (HIGH), theft (HIGH), other (MEDIUM)
            ↓
        Rules Engine (rules.py: RulesEngine.update)
            └─ Event object: type, severity, description, track_ids
                ↓
            Clip Saver (clips.py)
                ├─ Buffer last 10s, save pre_event + post_event
                └─ On clip ready: trigger AI review
                    ↓
                Notifier (notify.py: TelegramNotifier)
                    └─ Send alert + clip + keyframes to Telegram
```

---

## Signal Types: The Four Layers

### Layer 1: Touch Signals (Person-Vehicle Overlap)

**Purpose**: Detect when a person is physically contacting or reaching into a vehicle.

#### Signal: `person_at_vehicle` (AT_VEHICLE)
- **Trigger**: IOU(person_bbox, vehicle_bbox) > 0.02
- **Meaning**: Person bounding box overlaps vehicle by >2% (touching, reaching in, working on)
- **Code Location**: `trigger.py:93-101`
- **Threshold**: `iou_threshold=0.02` (hard-coded, not configurable)
- **False Positives**: Owner opening door legitimately
- **Refractory**: Per-person refractory on ANY touch signal is 3 seconds (`touch_arm_s`)

#### Signal: `sustained_touch` (Armed Theft Chain)
- **Trigger**: `person_at_vehicle` **AND** touch duration ≥ `touch_arm_s` (default 3.0s)
- **Meaning**: Person has been physically in contact for 3+ seconds (not a quick door open)
- **Code Location**: `trigger.py:102-110`
- **Variables**:
  - `self._touch_since[person_tid]`: Timestamp when person first touched
  - `ts - self._touch_since[person_tid] >= touch_arm_s`: Duration check
- **Logic**:
  ```python
  if touching:  # IOU > 0.02 with a vehicle
      t0 = self._touch_since.setdefault(person_tid, ts)  # Record first touch
      if ts - t0 >= float(self.cfg.get("touch_arm_s", 3)):  # 3 seconds passed
          suspicious_for_p = sustained_touch = True  # Arms the theft chain
  else:
      self._touch_since.pop(person_tid, None)  # Reset if touch breaks
  ```
- **Effect**: Enables instant break-in escalation if person is also crouching or reaching

---

### Layer 2: Pose Signals (Skeleton Keypoint Analysis)

**Purpose**: Detect suspicious body postures that indicate breaking into a vehicle.

#### Signal: `pose_crouching`
- **Trigger**: Hip keypoint Y coordinate drops below shoulder/ankle midpoint
- **Formula**: `(ankle_y - hip_y) < crouch_ratio * person_height`
- **Default Threshold**: `crouch_ratio = 0.32`
  - Standing person: ratio ≈ 0.45-0.55 (ankles far below hips)
  - Crouching person: ratio ≈ 0.15-0.30 (ankles close to hips)
- **Meaning**: Person is kneeling/crouching at or near the vehicle
- **Code Location**: `pose.py` (called from `main.py:58` and `analyze.py:~200`)
- **Variables**:
  - COCO keypoint 11: left_hip (x, y, confidence)
  - COCO keypoint 12: right_hip (x, y, confidence)
  - COCO keypoint 15: left_ankle (x, y, confidence)
  - COCO keypoint 16: right_ankle (x, y, confidence)
  - `self.cfg["crouch_ratio"]` from config.yaml
- **Effect**: Arming signal for break-in. If crouching + at vehicle + arm swing → HIGH escalation
- **Noise**: False positives if person sits naturally (leaning into car to pick up object)

#### Signal: `pose_reaching`
- **Trigger**: Wrist raised above shoulder **OR** wrist extended far sideways
- **Formula**: 
  - Vertical: `wrist_y < shoulder_y` (wrist above shoulder)
  - Horizontal: `|wrist_x - shoulder_x| > reach_x_ratio * person_width`
- **Default Threshold**: `reach_x_ratio = 0.5`
  - Sideways reach >50% of body width = suspicious
- **Meaning**: Person's arm is extended in a posture consistent with reaching into a vehicle
- **Code Location**: `pose.py` (called from `main.py:58` and `analyze.py:~200`)
- **Variables**:
  - COCO keypoint 6: left_shoulder (x, y, confidence)
  - COCO keypoint 7: right_shoulder (x, y, confidence)
  - COCO keypoint 9: left_wrist (x, y, confidence)
  - COCO keypoint 10: right_wrist (x, y, confidence)
  - `self.cfg["reach_x_ratio"]` from config.yaml
  - `person_bbox` for width calculation
- **Effect**: Arming signal for break-in. If reaching + sustained touch (3s) → HIGH escalation
- **Noise**: False positives if person stretches arm naturally

#### Signal: `pose_arm_swing`
- **Trigger**: Wrist velocity exceeds threshold
- **Formula**: `wrist_distance_traveled / time_interval > swing_speed_ratio * person_height`
- **Default Threshold**: `swing_speed_ratio = 1.1`
  - Threshold ≈ 1.1 × person_height per second
  - Example: 1.5m tall person → swing speed > 1.65 m/s = strike
- **Meaning**: Person's arm is moving at strike velocity (consistent with breaking glass)
- **Code Location**: `pose.py` (tracks wrist position frame-to-frame)
- **Variables**:
  - `self._prev_wrists`: dict mapping person_tid → [(wrist_x, wrist_y), ...]
  - `ts`: current timestamp
  - Euclidean distance between previous and current wrist position
  - `person_height` computed from bbox
- **Effect**: Instant HIGH break-in escalation if + at vehicle
- **Noise**: False positives on ballistic gestures (throwing, pointing, wild hand movements)

#### Integration: Pose Model Runtime
- **Controlled by**: `cfg["on_pose"]` (default true)
- **Optimization**: Only runs if `analyze_mod._pose_worth_running(trig_cfg, detections)` returns true
- **Worth Running Check**:
  - If `pose_only_near_vehicle=True` (default): Run pose only if any person within 180px of a vehicle
  - If `pose_only_near_vehicle=False`: Always run pose
- **Code Location**: `main.py:126-128`, `analyze.py:~200`
- **Effect**: Saves 30-50% of inference time by skipping pose model when no threats present

---

### Layer 3: Motion Signals (Frame Differencing)

**Purpose**: Detect violent pixel bursts ON a parked vehicle (glass shattering without seeing the arm).

#### Signal: `vehicle_disturbance`
- **Trigger**: Motion score in vehicle bounding box exceeds threshold
- **Formula**: `mean(abs(current_frame[vehicle_box] - prev_frame[vehicle_box])) >= disturb_thresh`
- **Default Threshold**: `disturb_thresh = 16.0` (on 0-255 grayscale scale)
  - Score 0: no motion (identical frames)
  - Score 10-30: normal motion (car shifting, shadows)
  - Score 40+: violent motion (glass breaking, struck surface)
- **Meaning**: A sudden, intense motion event happened ON the vehicle
- **Code Location**: `motion.py` (called every frame)
- **Implementation**:
  ```python
  # motion.py:VehicleMotion.scores()
  gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
  prev, self._prev_gray = self._prev_gray, gray
  for vehicle in vehicle_dets:
      x1, y1, x2, y2 = vehicle.xyxy
      diff = cv2.absdiff(gray[y1:y2, x1:x2], prev[y1:y2, x1:x2])
      motion_score = float(diff.mean())  # Mean pixel intensity change
  ```
- **Variables**:
  - `self._prev_gray`: Grayscale of previous frame (persisted in motion.py)
  - `detections[i].track_id`: Vehicle identifier
  - `detections[i].xyxy`: Bounding box [x1, y1, x2, y2]
- **Effect**: Fires `vehicle_disturbance` when combined with gate conditions
- **Refractory**: Must sustain for `disturb_frames=2` (default 2 analyzed frames = 1/3 second at 6 FPS)

#### Integration: Motion-Based Disturbance Detection
- **Gate 1**: Vehicle must be parked (moved < 20% of width in recent frames)
  - Checked in `trigger.py:159`: `st["departed"] == False`
- **Gate 2**: At least one person nearby (within `near_vehicle_px=180` default)
  - Checked in `trigger.py:161-162`: `people_near = veh_near_people.get(vid, set())`
- **Gate 3**: Score must exceed threshold for `disturb_frames` consecutive analyzed frames
  - Code: `trigger.py:163-169` tracks `self._disturb_streak[vehicle_tid]`
- **Code Path**: `main.py:129-133` computes motion scores, passes to trigger
- **Effect Without Gates**: Excessive false positives (wind, rain, shadows on moving cars)

---

### Layer 4: Zone & Time Signals

#### Signal: `person_in_[zone]` (restricted/parking/entry)
- **Trigger**: Person footpoint (center-bottom of bbox) inside polygon zone
- **Zones Configured in**: `config.yaml` cameras.zones
- **Meaning**: Person has entered a predefined restricted area
- **Code Location**: `trigger.py:115-119`
- **Implementation**:
  ```python
  for zname in ("restricted", "parking", "entry"):
      if point_in_polygon(person_foot_point, self.zones.get(zname)):
          reasons.add(f"person_in_{zname}")
          fired_for_p = True
          if zname == "restricted":
              suspicious_for_p = True  # Arms theft chain
  ```
- **Variables**:
  - `self.zones`: dict of zone_name → [(x,y), (x,y), ...] polygons
  - `person.foot_point`: (center_x, y2) of person bbox (where feet would be)

#### Signal: `person_at_night` (AT_NIGHT)
- **Trigger**: Current time within night_hours range **AND** any person detected
- **Default Range**: 23:00 to 05:00
- **Meaning**: Unexpected person presence during off-hours (higher suspicion)
- **Code Location**: `trigger.py:122-124`
- **Variables**:
  - `self.cfg["night_hours"]`: dict with "start" and "end" time strings
  - `self.localtime_fn()`: Injected function to get current time (default datetime.now)
- **Effect**: Any person at night = automatic trigger (+ arms theft chain)
- **Use Case**: Parking lot typically empty at 2 AM; any person is suspicious

---

## State Machines

### State Machine 1: Per-Person Dwell Timer

**Purpose**: Track how long each detected person has been present (uninterrupted).

**Variables in CandidateTrigger**:
- `self._person_since: dict[int, float]` — Timestamp when person first entered view
- `self._last_seen: dict[int, float]` — Timestamp of most recent frame with this person
- `self._touch_since: dict[int, float]` — Timestamp when person first touched a vehicle

**State Diagram**:
```
Person detected for first time
    ↓ (record timestamp in _person_since[tid])
Person continuously visible
    ↓ (update _last_seen[tid] every frame)
Dwell timer starts: ts - _person_since[tid]
    ↓ (when dwell >= dwell_s = 8 seconds)
Fire "person_lingering" signal
    ↓ (if person leaves view)
Person absent for > 3 seconds
    ↓ (expire from _person_since, reset dwell)
(if person re-enters, dwell timer restarts)
```

**Code Location**: `trigger.py:64-72`
```python
# Maintain per-person dwell timers (expire after a short absence)
seen = {p.track_id for p in persons}
for tid in list(self._person_since):
    if tid not in seen and ts - self._last_seen.get(tid, ts) > 3.0:
        self._person_since.pop(tid, None)  # Forget this person
        self._last_seen.pop(tid, None)
for p in persons:
    self._person_since.setdefault(p.track_id, ts)  # Record first sight
    self._last_seen[p.track_id] = ts  # Update last sight
```

**Configuration**:
- `dwell_s: 8` (default) — Lingering threshold
- `3.0` second expiry (hard-coded) — If absent >3s, timer resets

**Fire Condition**: `ts - self._person_since[p.track_id] >= dwell_s`

**Effect**: Enables "person_lingering" signal (armed for theft chain)

---

### State Machine 2: Per-Vehicle Parking Anchor

**Purpose**: Track whether each vehicle is parked, where it's parked, and when suspicious activity occurred near it.

**Variables in CandidateTrigger**:
- `self._veh: dict[int, dict]` — Parking state per vehicle
  - Keys: vehicle track_id
  - Value: `{ts0: float, cx: float, cy: float, w: float, last: float, departed: bool}`
    - `ts0`: Timestamp when vehicle first parked at current location
    - `cx, cy`: Center position of vehicle bbox
    - `w`: Width of vehicle bbox (used to measure departure distance)
    - `last`: Most recent timestamp vehicle was visible
    - `departed`: Boolean flag indicating vehicle has left this parking spot

- `self._veh_activity: dict[int, float]` — Last suspicious activity timestamp per vehicle
  - Key: vehicle track_id
  - Value: Timestamp of most recent suspicious person action near this vehicle
  - Used to link theft chain: "activity at T, departure at T+10s" → HIGH alert

- `self._veh_people: dict[int, set[int]]` — Persons involved with each vehicle
  - Key: vehicle track_id
  - Value: Set of person track_ids who acted suspiciously near this vehicle
  - Used to identify culprits in HIGH events

- `self._disturb_streak: dict[int, int]` — Consecutive motion frames per vehicle
  - Key: vehicle track_id
  - Value: Number of consecutive analyzed frames with motion_score >= disturb_thresh
  - Reset if motion drops below threshold or vehicle moves

**State Diagram**:
```
Vehicle detected for first time
    ↓ (create anchor: ts0=now, cx/cy=position, departed=False)
Vehicle remains in view
    ↓ (update _last_seen[tid]; measure distance moved)
    ├─ Distance < 25% of width (drifting <parked_min): Update anchor, stay parked
    │  └─ (allows for small micro-motions, wind-induced sway)
    │
    ├─ Distance >= 25% of width AND time_parked < parked_min: Update anchor, still drifting
    │  └─ (vehicle never fully parked; follow its motion)
    │
    ├─ Distance >= 60% of width AND time_parked >= parked_min: DEPARTURE!
    │  └─ (vehicle has moved significantly after sitting still >= 6s)
    │  ├─ If suspicious_activity_recent (within link_s=600s):
    │  │  └─ Fire THEFT CHAIN (HIGH severity)
    │  │  └─ Record departure_info: gap_s, people involved
    │  │
    │  └─ Else: Silent (owner just driving away)
    │
    └─ Parked again (distance < 20% of width, parked_min elapsed):
       └─ Re-arm departure detection (can fire again)

Vehicle not seen for > 5 seconds
    ↓ (expire from tracking)
```

**Code Location**: `trigger.py:185-229` (_update_vehicles method)

**Key Constants**:
- `parked_min_s: 6` (default) — Vehicle must sit still ≥6s to be "parked"
- `depart_frac: 0.6` (default) — Departure detected if moved 60% of vehicle width
- `link_s: 600` (default) — Theft chain window (10 minutes)

**Departure Logic**:
```python
dist = sqrt((cx - prev_cx)^2 + (cy - prev_cy)^2)  # Center displacement
parked_for = ts - ts0  # How long at current position
if dist >= depart_frac * vehicle_width:  # Moved 60%+
    if parked_for >= parked_min AND not departed:
        departed = True
        if vehicle_id in _veh_activity and ts - _veh_activity[vehicle_id] <= link_s:
            # Suspicious activity within 600s → THEFT
            return {"vehicle": vehicle_id, "gap_s": ts - activity_ts, "people": involved_set}
```

**Fire Condition**: Vehicle departed AND suspicious activity within last 600s

**Effect**: Enables "vehicle_departure_after_activity" signal (HIGH severity) + sends departure info to event description

---

### State Machine 3: Per-Vehicle Motion Burst Counter

**Purpose**: Require motion disturbance to sustain for multiple frames before firing (eliminate noise spikes).

**Variables in CandidateTrigger**:
- `self._disturb_streak: dict[int, int]` — Count of consecutive frames with motion_score >= disturb_thresh
  - Key: vehicle track_id
  - Value: Integer count of analyzed frames

**State Diagram**:
```
Frame N: motion_score[vehicle_id] = 18 (> threshold 16)
    ↓ (increment streak counter)
Frame N+1: motion_score[vehicle_id] = 20
    ↓ (streak == 2, meets disturb_frames threshold)
Fire "vehicle_disturbance" + "possible_break_in" (HIGH severity)
    ↓
Frame N+2: motion_score[vehicle_id] = 3 (< threshold)
    ↓ (reset streak to 0)
```

**Code Location**: `trigger.py:154-172`

**Configuration**:
- `disturb_thresh: 16.0` — Motion score threshold
- `disturb_frames: 2` — Minimum consecutive frames to trigger

**Logic**:
```python
need = int(cfg.get("disturb_frames", 2))
for vid, score in motion.items():
    if score >= thresh:
        self._disturb_streak[vid] = self._disturb_streak.get(vid, 0) + 1
        if self._disturb_streak[vid] >= need:
            reasons.add(BREAK_IN)  # Escalate to HIGH
    else:
        self._disturb_streak.pop(vid, None)  # Reset if below threshold
```

---

## Complete Variable Reference

### CandidateTrigger.__init__

| Variable | Type | Purpose | Default |
|----------|------|---------|---------|
| `self.zones` | dict[str, list[tuple]] | Polygon zones: "restricted", "parking", "entry" | `{}` per zone |
| `self.cfg` | dict | Config dict with all thresholds | Passed in |
| `self.localtime_fn` | callable | Function to get current time (injected for testing) | `datetime.now` |
| `self._person_since` | dict[int, float] | track_id → first-seen timestamp | `{}` |
| `self._last_seen` | dict[int, float] | track_id → most-recent timestamp | `{}` |
| `self.last_involved` | set[int] | Person track_ids behind last fire | `set()` |
| `self._veh` | dict[int, dict] | Vehicle track_id → anchor state dict | `{}` |
| `self._veh_activity` | dict[int, float] | Vehicle track_id → last suspicious ts | `{}` |
| `self._veh_people` | dict[int, set] | Vehicle track_id → set of person tids | `{}` |
| `self._touch_since` | dict[int, float] | Person track_id → touch start ts | `{}` |
| `self._disturb_streak` | dict[int, int] | Vehicle track_id → burst frame count | `{}` |
| `self.last_departure` | dict or None | Info about last theft chain fire | `None` |

### config.yaml: Trigger Section

```yaml
trigger:
  # Enable/disable each signal type
  on_near_vehicle: true          # Person close to vehicle (main theft signal)
  on_loiter: true                # Person dwelling (dwell_s threshold)
  on_zone: true                  # Person in polygon zones
  on_night_person: true          # Any person during night hours
  on_pose: true                  # Stick-figure signals
  on_touch: true                 # Person overlapping vehicle
  on_departure: true             # Theft chain enabled
  on_break_in: true              # Instant HIGH escalation enabled
  on_disturb: true               # Motion-burst detector enabled
  
  # Proximity and dwell
  near_vehicle_px: 180           # "Near vehicle" radius (pixels)
  dwell_s: 8                     # Lingering threshold (seconds)
  touch_arm_s: 3                 # Sustained touch threshold (seconds)
  
  # Vehicle state tracking
  parked_min_s: 6                # Minimum time to be "parked" (seconds)
  depart_frac: 0.6               # Departure distance (fraction of width)
  
  # Theft chain linking
  link_s: 600                    # Activity → departure window (seconds, 10 min)
  
  # Motion-burst detection
  disturb_thresh: 16             # Motion score threshold (0-255)
  disturb_frames: 2              # Minimum consecutive frames
  
  # Pose model optimization
  pose_only_near_vehicle: true   # Skip pose if no person near vehicle
  
  # Night hours
  night_hours:
    start: "23:00"
    end: "05:00"
  
  # Pose thresholds
  crouch_ratio: 0.32             # Crouch detection (hip-ankle distance)
  reach_x_ratio: 0.5             # Sideways reach (fraction of body width)
  swing_speed_ratio: 1.1         # Arm strike velocity (body-heights/second)
```

### Rules Engine Integration

**Location**: `main.py:49`, `trigger.py` is called inside `CameraPipeline._loop`

**Connection**:
```python
# main.py:119-133
detections = self.detector.track(frame)  # YOLO detections

# Pose signals (optional, optimized)
pose_signals = {}
if self.pose is not None and persons and analyze_mod._pose_worth_running(...):
    pose_signals = self.pose.analyze_frame(frame, persons, ts)

# Motion scores (always computed)
motion_scores = self.motion.scores(frame, vehicles)

# Trigger decision
fire, reasons = self.trigger.is_candidate(detections, ts, pose_signals, motion_scores)

if fire:
    # Create and dispatch event
    events.append(Event(ts=ts, camera=name, event_type=SUSPICIOUS_ACTIVITY, 
                       severity="HIGH" if break_in else "MEDIUM", ...))
```

---

## Escalation Logic

### Severity Assignment

| Condition | Severity | Description | Code Location |
|-----------|----------|-------------|----------------|
| `pose_arm_swing` + at/near vehicle | HIGH | POSSIBLE BREAK-IN: arm strike detected | `trigger.py:135-140` |
| `sustained_touch` (3s+) + reaching | HIGH | POSSIBLE BREAK-IN: sustained reach | `trigger.py:135-140` |
| `sustained_touch` + crouching | HIGH | POSSIBLE BREAK-IN: crouch while touching | `trigger.py:135-140` |
| `motion_burst` sustained (2+ frames) + person nearby | HIGH | POSSIBLE BREAK-IN: motion burst | `trigger.py:169-170` |
| Vehicle departed within 600s of suspicious activity | HIGH | POSSIBLE VEHICLE THEFT: stole car after break-in | `trigger.py:178` |
| Other signals fire (lingering, near vehicle, etc.) | MEDIUM | Suspicious activity (not instant threat) | `main.py:147-163` |

### Refractory Windows

**In Real-Time Live Cameras** (`main.py`):
- `SUSPICIOUS_REFRACTORY_S = 60` — One suspicious event per camera per 60s
- `ESCALATION_REFRACTORY_S = 30` — One HIGH alert per camera per 30s (break-in + theft)

**In Offline Video Analysis** (`analyze.py`):
- `ESCALATION_REFRACTORY_S = 30` — Same as live (tunable for batch processing)

**Purpose**: Prevent duplicate alerts for same incident (same person, same vehicle).

**Code**:
```python
# main.py:142-145
if escalate or ts - self._last_suspicious_ts >= SUSPICIOUS_REFRACTORY_S:
    self._last_suspicious_ts = ts
    if escalate:
        self._last_escalation_ts = ts
```

---

## Debug Overlay (For Video Analysis)

**Purpose**: Visualize what the free layer is seeing frame-by-frame.

**Enable**: Set `analyze.debug_overlay: true` in config.yaml

**Content Burned into Output Video**:
```
FREE LAYER t=12.3s  pose:on
veh#1 motion=42.5! (thr 16.0) parked=yes
veh#3 motion=8.2  (thr 16.0) parked=no
person#7 dwell=8.1s pose=pose_reaching,pose_crouching
person#12 dwell=0.3s touch_time=0.0s
Nearby: person#7→veh#1 (80px), person#12→veh#3 (200px)
Fired: possible_break_in, vehicle_disturbance
Last involved: [7, 12]
```

**Interpretation Guide**:
- `motion=42.5! (thr 16.0)`: Motion score 42.5 exceeds threshold 16 (!)
- `parked=yes/no`: Vehicle departed flag state
- `dwell=8.1s`: Person visible for 8.1 seconds
- `pose=...`: Signals detected this frame
- `touch_time=3.2s`: How long person has been touching this vehicle
- `Nearby: person#7→veh#1 (80px)`: Person 80 pixels from vehicle
- `Fired: ...`: Signals that triggered this frame

**Code Location**: `analyze.py:432-466` (_overlay_lines and _draw_overlay methods)

---

## System Integration Points

### Integration 1: CameraPipeline (Live Alerts)

**File**: `main.py:38-187` (CameraPipeline class)

**Flow**:
1. Worker reads frame from RTSP/file (class CameraWorker)
2. Detector runs YOLO (returns detections with track_id)
3. Motion scorer computes frame difference (returns motion dict)
4. Pose estimator runs on persons near vehicles (returns pose_signals dict)
5. Trigger.is_candidate() evaluates (returns fire bool + reasons list)
6. If fire:
   - Event object created with severity, description, track_ids
   - Event passed to _handle_event()
7. _handle_event():
   - Inserts event into database (db.insert_event)
   - Triggers clip saver (clip_saver.save_async)
   - Clip saver collects pre_event_s (10s default) + post_event_s (20s default) frames
   - On clip ready: triggers AI review (reviewer.review_clip or vlm.describe)
   - Finally: sends Telegram notification with clip + description

**Timing**: Pipeline processes at `process_fps` (default 6 FPS) = analyzed frame every ~167ms
- YOLO detection: ~80-150ms
- Pose model (near vehicle): ~50-100ms
- Trigger logic: <1ms
- Total: ~150-250ms per frame (can slip frames to catch up)

### Integration 2: VideoAnalyzer (Offline Analysis)

**File**: `app/analyze.py` (AnalyzeJob class)

**Flow**:
1. User uploads video (up to 300MB default)
2. VideoAnalyzer opens video file with OpenCV
3. Reads frames at input_fps or skips to reach `analyze_fps` (default 6)
4. For each frame:
   - Detector runs YOLO
   - Motion scorer computes frame difference
   - Pose estimator runs on persons near vehicles
   - Trigger.is_candidate() evaluates
   - If fire: record timestamp + reasons
5. Post-processing:
   - Merge fired timestamps into temporal windows (gap_s=3, pad_s=2)
   - For each window: extract keyframes, run AI review if enabled
6. Output video:
   - If full_fps_video=False (default): write only at analyze_fps (~6 FPS, 4x smaller)
   - If full_fps_video=True: interpolate + write at input_fps (slower, smoother)
   - If debug_overlay=True: burn free layer state into each frame

**Timing**: ~150-250ms per analyzed frame (same as live)
- Batch processing: ~1 minute of video → ~5-10 minutes analysis time (30-60x slowdown)
- Full 300MB video → ~20-40 minutes

### Integration 3: Event Object & Description

**File**: `rules.py` (Event dataclass)

**Event Fields**:
- `ts: float` — Timestamp of trigger moment (from trigger.is_candidate)
- `camera: str` — Camera name
- `event_type: str` — "SUSPICIOUS_ACTIVITY" (hard-coded, only type currently)
- `severity: str` — "HIGH" or "MEDIUM" (set in CameraPipeline._loop)
- `description: str` — Human-readable text describing what happened
- `track_ids: list[int]` — Culprit person track_ids
- `confidence: float` — 0.8 (theft chain) or 0.5 (other)
- `plate: str or None` — License plate if read (filled by rules engine)

**Description Template** (in `main.py:146-157`):
- MEDIUM: `"Suspicious activity: person_near_vehicle, person_lingering, ..."`
- HIGH (break-in): `"POSSIBLE BREAK-IN AT VEHICLE: strike/reach detected at the car (pose_arm_swing, ...)"`
- HIGH (theft): `"POSSIBLE VEHICLE THEFT: vehicle drove away 10.2s after suspicious activity around it"`

---

## Configuration Checklist

**Before Deployment**, tune these in config.yaml:

1. **Zone Polygons**:
   - Run `python zones.py --camera gate` to draw zones interactively
   - Mark restricted areas (VIP parking, admin zones)
   - Mark parking areas (for loitering detection)
   - Mark entry points (for unauthorized access detection)

2. **Motion Threshold** (`trigger.disturb_thresh`):
   - Default 16.0 works for 1920x1080 video
   - If false positives (wind, rain, shadows): increase to 20-25
   - If misses (compression artifacts, bright sun): decrease to 12-14
   - **Debug**: Enable `analyze.debug_overlay: true` to see actual scores

3. **Pose Thresholds** (`trigger.crouch_ratio`, `reach_x_ratio`, `swing_speed_ratio`):
   - Default values tuned for typical human proportions
   - Only adjust if seeing consistent false positives/negatives with specific persons

4. **Night Hours** (`trigger.night_hours`):
   - Set to actual site hours (e.g., 22:00 to 06:00 for 24/7 unattended lot)
   - Any person at night automatically fires (high suspicion)

5. **Refractory Windows** (in `main.py`):
   - `SUSPICIOUS_REFRACTORY_S = 60` — Tune for site activity level
   - `ESCALATION_REFRACTORY_S = 30` — Tune to prevent alert spam

---

## Testing & Validation

### Test Suite Location
`tests/test_trigger.py` — 116 passing tests covering:
- Person near vehicle fires
- Lingering detection (dwell timer)
- Zone detection (restricted/parking/entry)
- Theft chain (activity + departure)
- Owner quick-pickup (silent)
- Sustained touch (3s threshold)
- Pose-based break-in (swing + crouching)
- Motion-based disturbance (frame differencing)
- Refractory windows (duplicate suppression)

### Running Tests
```bash
pytest tests/test_trigger.py -v
```

### Validating on Real Video
```bash
# Enable debug overlay
nano config.yaml  # Set analyze.debug_overlay: true

# Analyze video with free layer visualization
python -m app.analyze tests/sample_gate.mp4

# Open output video: clips/uploads/[timestamp]_analyzed.mp4
# Pause at suspicious moments and read overlay to see what fired/why
```

---

## Known Limitations & Future Improvements

### Current Limitations of Free Layer

1. **Pose Model Dependency**:
   - If YOLO pose model fails to load or outputs low-confidence keypoints, arm swing may not fire
   - Mitigation: Motion-based disturbance detector now provides fallback

2. **Motion Threshold Tuning**:
   - Threshold is global (one value for all cameras)
   - Bright sun + compression artifacts cause false positives
   - Mitigation: Video quality matters; use IP cameras with H.264 codec, enable debug overlay to tune per-site

3. **Partial Visibility**:
   - If thief is partially hidden (behind fence, window), YOLO may not detect them
   - Mitigation: Place cameras to minimize obstructions; use multiple overlapping cameras

4. **Touch-Based False Positives**:
   - If owner's briefcase touches car while opening door, may flag as suspicious
   - Mitigation: Sustained touch threshold (3s) and pose validation (reach/crouch required)

5. **No Audio**:
   - Glass breaking is loud but not detected from video alone
   - Mitigation: Consider YAMNet audio event detection for independent backup

### Planned Improvements

1. **Audio-Based Glass Breaking Detection**:
   - Integrate YAMNet (TensorFlow Lite model trained on AudioSet)
   - Detects "glass_break" event class independently
   - Secondary channel: if audio fires, escalate immediately

2. **Advanced Skeleton-Based Action Recognition**:
   - ST-GCN (Spatial-Temporal Graph Convolutional Networks) or PoseC3D
   - Learned action patterns: "breaking glass", "reaching into car", "prying door"
   - Better than hand-crafted pose thresholds, but requires training data

3. **Zero-Shot Video Anomaly Detection**:
   - VadCLIP or LAVADA (caption-and-score pipeline)
   - Learns "what is theft" from text descriptions without labeled video data
   - Can detect novel attack methods not in training

4. **Motion Burst Refinement**:
   - Current: mean pixel change in bounding box
   - Future: Localized motion heatmap (detect change ON glass area vs interior)
   - Prevents false positives from car interior motion (people shifting inside)

---

## Summary: Free Layer Decision Flow

```
GIVEN: Frame with detections (persons, vehicles) + timestamp

STEP 1: Motion Scoring (VehicleMotion.scores)
  → For each vehicle: compute mean pixel change in bbox
  → Output: {vehicle_tid: float}

STEP 2: Pose Estimation (PoseEstimator.analyze_frame, if near_vehicle)
  → For each person: extract skeleton keypoints
  → Compute: crouch_ratio, reach_x_ratio, swing_speed_ratio
  → Output: {person_tid: {pose_crouching, pose_reaching, pose_arm_swing, ...}}

STEP 3: Trigger Logic (CandidateTrigger.is_candidate)
  → Loop over persons:
      Check touch (IOU > 0.02)
      Check sustained touch (> 3s)
      Check dwell (> 8s)
      Check zones (foot_point in polygon)
      Check night hours
      Check pose signals
      Check break-in escalation (pose + touch)
      → Record: which persons "fired"
  
  → Loop over vehicles:
      Check motion burst (score > 16, 2+ frames, parked, person nearby)
      Check theft chain (activity within 600s, then departure)
      → Record: which vehicles "fired"
  
  → Return: (any_fired, sorted_list_of_reasons)

STEP 4: Event Creation (if fired)
  → Determine severity:
      break_in or theft_chain → HIGH
      other → MEDIUM
  → Create Event object with description
  → Pass to clip saver + notifier

STEP 5: Notifications (Telegram, with AI review optional)
  → Send clip + keyframes + description to Telegram group
  → (Optional) Run Claude Haiku screener (tier1) or Claude Opus (tier2)
```

---

## Glossary

| Term | Definition |
|------|-----------|
| **Candidate Trigger** | Free-layer decision gate: cheap check before expensive AI review |
| **Track ID** | Unique identifier assigned by YOLO tracker to each person/vehicle across frames |
| **IOU** | Intersection-over-Union: overlap ratio of two bounding boxes (0.0-1.0) |
| **Pose Model** | YOLO11n-pose.pt: skeleton extractor that outputs 17 COCO keypoints (joints) |
| **Motion Score** | Mean absolute pixel intensity change inside vehicle bounding box (0-255) |
| **Disturbance** | Motion score >= threshold, sustained for 2+ frames, vehicle parked, person nearby |
| **Dwell** | How long a person has continuously occupied the scene |
| **Sustained Touch** | Person in physical contact with vehicle for >= 3 seconds |
| **Parked Anchor** | Reference position + timestamp for a vehicle's parking location |
| **Departure** | Vehicle moves >= 60% of its width after being parked >= 6 seconds |
| **Theft Chain** | Link between suspicious activity around a vehicle and that vehicle's subsequent departure |
| **Refractory Window** | Minimum time gap between duplicate alerts (prevents alert spam) |
| **Zone** | Polygon area drawn for restricted/parking/entry detection |
| **Escalation** | Jump from MEDIUM to HIGH severity (instant alert, not deferred) |

---

## Contact & Feedback

For questions about the free layer or to report missed detections:

1. **Check Debug Overlay**: Enable `analyze.debug_overlay: true`, re-analyze the video, and look for what signals fired/didn't fire.

2. **Tune Thresholds**: Adjust `trigger.disturb_thresh`, `crouch_ratio`, `reach_x_ratio`, `swing_speed_ratio` based on debug output.

3. **Report Missing Detections**: Provide:
   - Video file + timestamp of miss
   - Debug overlay screenshot showing what free layer saw
   - Expected signal (pose_arm_swing? motion_burst? touch?)

---

**Document Version**: 1.0  
**Last Updated**: 2026-07-21  
**Free Layer Release**: With motion-based disturbance detector and debug overlay  
**Status**: All 116 tests passing, ready for field deployment
