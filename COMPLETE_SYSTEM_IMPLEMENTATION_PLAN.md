# Society AI Watchdog — Complete System Implementation Plan

## Executive Summary

Your free layer (YOLO + motion + pose) is complete. Now we build the complete system:
- **Claude AI screening tier** (Haiku + Opus)
- **Dashboard app** (multi-camera, multi-user)
- **Message routing** (Telegram, Email, SMS via n8n)
- **Camera integration** (RTSP, HTTP, cloud cameras)
- **User permissions** (guard, manager, admin roles)

**Total Effort**: 10 weeks with 2-3 developers
**Cost**: $50-200/month operational (most is clip storage, not AI)

---

## Architecture Overview

### Current State
✅ Free Layer: YOLO + motion + pose + trigger logic  
✅ Telegram notifications with feedback buttons  
✅ SQLite database with events  
✅ FastAPI dashboard with MJPEG viewer  

### What's Missing
⏳ Mobile app + redesigned web dashboard  
⏳ Email/SMS via n8n or make.com  
⏳ Multi-camera society management  
⏳ User authentication + permissions  
⏳ Analytics dashboard  
⏳ Cloud camera support  

---

## Critical Decision: Direct App vs Integration Platform

### Option A: Direct Integration (Current)
```
[Watchdog] → [Telegram] ✅ (fast, no infra)
          → [Database] ✅ (local)
          → [Dashboard] ✅ (FastAPI)
```
**Problem**: Adding email/SMS requires more infrastructure

### Option B: RECOMMENDED — Hybrid (Best of Both)
```
[Watchdog] → [Telegram] ✅ (direct, instant)
          → [Webhook] → [n8n/make.com] (email, SMS, Slack)
          → [REST API] → [Mobile App] (real-time alerts)
          → [SQLite] (local) + [Supabase] optional (cloud sync)
```

**Why hybrid:**
- Telegram: Instant, no rate limits, free → keep direct
- Email/SMS: Need template logic, retry, scheduling → defer to n8n
- App: Query local API (fast) + optional cloud backup
- Cost: Minimal (n8n free tier handles ~1000 alerts/month)

---

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    ON-PREMISE (Local Network)                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  [Camera 1: RTSP]  [Camera 2: HTTP]  [Camera 3: Cloud API]      │
│        │                  │                     │                │
│        └──────────────────┴─────────────────────┘                │
│                           │                                       │
│                    [YOLO Detection                               │
│                     + Pose + Motion]                             │
│                           │                                       │
│                    [CandidateTrigger                             │
│                     Free Layer]                                  │
│                           │                                       │
│        ┌──────────────────┼──────────────────┐                   │
│        │                  │                  │                   │
│   [Database]      [Event Bus]        [Clip Saver]               │
│   (SQLite)        (Pub/Sub)         (Pre/Post clips)            │
│        │                  │                  │                   │
│        └──────────────────┼──────────────────┘                   │
│                           │                                       │
│                    [Notification Router]                         │
│                           │                                       │
│        ┌──────────────────┼──────────────────┐                   │
│        │                  │                  │                   │
│   [Telegram]        [REST API]         [Webhook]                │
│   (Direct)          (Local)            (To n8n)                 │
│                           │                  │                   │
│                  [Dashboard + Mobile]    [n8n Workflows]        │
│                                             │                   │
└──────────────────────────────────────────────┼───────────────────┘
                                               │
                    ┌──────────────────────────┘
                    │
        ┌───────────┼───────────┐
        │           │           │
    [Email]      [SMS]      [Slack]
    (Twilio)   (Twilio)    (Webhooks)
    (Gmail)      (AWS)     (Discord)
```

---

## Phase 1: Event Distribution & API Foundation (Week 1-2)

### 1.1 Event Bus (Pub/Sub System)

**File**: `app/events_bus.py` (NEW)

```python
from collections import defaultdict
from typing import Callable, Dict, List

class EventBus:
    """Publish-subscribe system for decoupling components."""
    
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
    
    def subscribe(self, event_type: str, callback: Callable):
        """Register handler for event type.
        
        Usage:
            bus.subscribe("event.high_alert", on_high_alert)
            bus.subscribe("event.ai_reviewed", on_ai_reviewed)
        """
        self._subscribers[event_type].append(callback)
    
    def publish(self, event_type: str, data: dict):
        """Broadcast event to all subscribers."""
        for callback in self._subscribers[event_type]:
            try:
                callback(data)
            except Exception as e:
                log.exception(f"Callback failed for {event_type}: {e}")

# Event types to publish:
# - event.triggered        (raw free-layer trigger)
# - event.high_alert       (break-in/theft escalation)
# - event.ai_reviewed      (Haiku/Opus decision)
# - event.clipped          (clip ready for sending)
# - event.notified         (Telegram sent)
# - event.camera_offline   (RTSP connection lost)
```

**Usage in main.py**:
```python
# In AppContext.__init__:
self.bus = EventBus()

# Subscribe notifier to events
self.bus.subscribe("event.high_alert", 
                   lambda e: self.notifier.notify_high_alert(e))
self.bus.subscribe("event.clipped",
                   lambda e: self.reviewer.review_clip(e))

# In CameraPipeline._loop (when fire):
self.ctx.bus.publish("event.triggered", {
    "event": event,
    "reasons": reasons,
    "track_ids": self.trigger.last_involved,
    "severity": "HIGH" if theft_chain else "MEDIUM"
})
```

### 1.2 REST API Gateway

**File**: `app/api.py` (NEW)

```python
from fastapi import FastAPI, WebSocket, Depends, HTTPException
from fastapi_jwt_extended import JWTManager, create_access_token, get_jwt_identity
from contextlib import asynccontextmanager

app = FastAPI(title="Watchdog API", version="1.0")
jwt = JWTManager(app)

# Global context (injected)
ctx: AppContext = None

@app.post("/api/v1/auth/login")
def login(username: str, password: str):
    """JWT authentication."""
    user = ctx.db.verify_user(username, password)
    if not user:
        raise HTTPException(401, "Invalid credentials")
    
    access_token = create_access_token(identity=user.id)
    return {"access_token": access_token, "user": user.dict()}

@app.get("/api/v1/events")
def get_events(
    camera: str = None,
    severity: str = None,
    building: str = None,
    limit: int = 50,
    user = Depends(get_current_user)
):
    """List events, filtered by user's permitted buildings."""
    query = ctx.db.events()
    
    # Permission check
    if "*" not in user.buildings:
        query = query.filter(Event.building.in_(user.buildings))
    
    if camera:
        query = query.filter_by(camera=camera)
    if severity:
        query = query.filter_by(severity=severity)
    if building:
        query = query.filter_by(building=building)
    
    return query.order_by(Event.ts.desc()).limit(limit).all()

@app.get("/api/v1/cameras")
def get_cameras(user = Depends(get_current_user)):
    """List cameras accessible to user."""
    cameras = ctx.config["cameras"]
    if "*" not in user.buildings:
        cameras = [c for c in cameras if c["building"] in user.buildings]
    return cameras

@app.get("/api/v1/cameras/{name}/stream.mjpeg")
async def stream_camera(name: str, user = Depends(get_current_user)):
    """MJPEG stream for live view."""
    camera = next((c for c in ctx.config["cameras"] if c["name"] == name), None)
    if not camera:
        raise HTTPException(404, "Camera not found")
    
    if camera["building"] not in user.buildings:
        raise HTTPException(403, "Access denied")
    
    pipeline = ctx.pipelines.get(name)
    if not pipeline or pipeline.annotated_jpeg is None:
        raise HTTPException(503, "Camera not ready")
    
    async def generate():
        while True:
            jpeg = pipeline.annotated_jpeg
            if jpeg:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n'
                       b'Content-length: ' + str(len(jpeg)).encode() + b'\r\n\r\n'
                       + jpeg + b'\r\n')
            await asyncio.sleep(0.033)  # ~30 FPS
    
    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace")

@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket, user = Depends(get_current_user)):
    """Real-time event stream via WebSocket."""
    await websocket.accept()
    
    # Subscribe to all events
    def on_event(event_data):
        if event_data["building"] in user.buildings or "*" in user.buildings:
            asyncio.create_task(
                websocket.send_json({
                    "type": "event",
                    "data": event_data
                })
            )
    
    ctx.bus.subscribe("event.triggered", on_event)
    ctx.bus.subscribe("event.ai_reviewed", on_event)
    
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except:
        pass

@app.delete("/api/v1/clips/{clip_id}")
def delete_clip(clip_id: int, reason: str = "", user = Depends(get_current_user)):
    """Delete clip (audited)."""
    if not user.can_delete_clips:
        raise HTTPException(403, "Permission denied")
    
    clip = ctx.db.get_clip(clip_id)
    if clip.event.building not in user.buildings:
        raise HTTPException(403, "Access denied")
    
    # Delete file
    os.remove(clip.path)
    
    # Audit log
    ctx.db.audit_log(
        action="DELETE_CLIP",
        user_id=user.id,
        clip_id=clip_id,
        reason=reason,
        timestamp=time.time()
    )
    
    return {"status": "deleted"}

# Startup/shutdown
async def lifespan(app: FastAPI):
    global ctx
    # Will be injected before running
    yield
    # Cleanup

app = FastAPI(lifespan=lifespan)

def create_api_app(app_context: AppContext) -> FastAPI:
    """Factory function."""
    global ctx
    ctx = app_context
    return app
```

### 1.3 Enhanced Database Schema

**File**: `app/db.py` (enhance existing)

```python
# Add to schema:

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True)
    password_hash = Column(String)  # bcrypt
    email = Column(String)
    role = Column(String)  # admin | manager | guard | resident
    buildings = Column(String)  # JSON: ["A", "B"] or ["*"]
    can_acknowledge_alerts = Column(Boolean, default=True)
    can_delete_clips = Column(Boolean, default=False)
    can_export_data = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)

class Event(Base):  # Enhance existing
    __tablename__ = "events"
    
    id = Column(Integer, primary_key=True)
    ts = Column(Float)
    camera = Column(String)
    building = Column(String)  # NEW: derive from camera config
    event_type = Column(String)
    severity = Column(String)  # HIGH | MEDIUM | LOW
    description = Column(String)
    plate = Column(String, nullable=True)
    track_ids = Column(String)  # JSON
    confidence = Column(Float)
    ai_reviewed = Column(Boolean, default=False)
    ai_verdict = Column(String, nullable=True)  # theft | break_in | false_alarm
    feedback = Column(String, nullable=True)  # From telegram buttons
    created_at = Column(DateTime, default=datetime.now)

class AuditLog(Base):  # NEW
    __tablename__ = "audit_log"
    
    id = Column(Integer, primary_key=True)
    action = Column(String)  # DELETE_CLIP, MODIFY_REGISTRY, CHANGE_CONFIG
    user_id = Column(Integer, ForeignKey("users.id"))
    target_id = Column(Integer, nullable=True)  # clip_id or event_id
    old_value = Column(String, nullable=True)  # JSON
    new_value = Column(String, nullable=True)  # JSON
    reason = Column(String, nullable=True)
    timestamp = Column(DateTime, default=datetime.now)
    # Hash chain for tamper detection
    prev_hash = Column(String, nullable=True)
    this_hash = Column(String)  # SHA256(prev_hash + json(this_record))
```

### 1.4 Updated config.yaml Schema

**File**: `config.yaml`

```yaml
# Enhanced to support multi-building setup

cameras:
  - name: "building_a_gate"
    building: "A"              # NEW
    url: "rtsp://..."
    zones:
      entry: [[x,y], ...]
      parking: []
      restricted: []

  - name: "building_b_parking"
    building: "B"              # NEW
    url: "rtsp://..."

buildings:                      # NEW section
  - name: "Main Building"
    code: "A"
    contact_phone: "+91-9876543210"
    manager_chat_id: "123456789"
    
  - name: "Annex"
    code: "B"
    contact_phone: "+91-9876543211"
    manager_chat_id: "987654321"

auth:                           # NEW section
  jwt_secret: "your-secret-key-min-32-chars"
  token_expire_hours: 24
  allow_registration: false     # Manual user creation only

webhooks:                       # NEW section
  enabled: true
  url: "https://your-n8n.com/webhook/watchdog"
  retry_count: 3
  retry_delay_s: 5
```

---

## Phase 2: Dashboard & Mobile App (Weeks 3-5)

### 2.1 Web Dashboard Architecture

**Structure**:
```
/dashboard
  ├─ /live            (camera grid + live MJPEG)
  ├─ /events          (searchable table)
  ├─ /clips           (gallery view)
  ├─ /registry        (vehicle database)
  ├─ /users           (permissions management)
  ├─ /settings        (alert rules, webhooks)
  ├─ /analytics       (incidents/day, false alarm rate)
  └─ /audit-log       (immutable event history)
```

**Tech Stack**:
- Frontend: React 18 + Vite + TypeScript
- State: Zustand or Redux
- Styling: Tailwind CSS
- UI Components: shadcn/ui (pre-built, Tailwind-based)
- Real-time: Socket.IO (WebSocket) or React Query (polling)
- Maps: Leaflet or Google Maps (optional, for multi-building view)

**Example Component: EventsTable.jsx**

```jsx
import React, { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { API } from '../api/client';

export function EventsTable({ building, camera }) {
  const [filters, setFilters] = useState({ building, camera, severity: null });
  
  const { data: events, isLoading } = useQuery({
    queryKey: ['events', filters],
    queryFn: () => API.getEvents(filters),
    refetchInterval: 5000  // Poll every 5s
  });
  
  const handleDelete = async (eventId) => {
    if (window.confirm('Delete clip? This is audited.')) {
      await API.deleteClip(eventId, 'User request');
      // Refetch
    }
  };
  
  return (
    <div className="p-4">
      <h2 className="text-2xl font-bold mb-4">Alerts</h2>
      
      <div className="flex gap-4 mb-4">
        <select value={filters.severity} onChange={e => setFilters({...filters, severity: e.target.value})}>
          <option value="">All Severities</option>
          <option value="HIGH">HIGH</option>
          <option value="MEDIUM">MEDIUM</option>
        </select>
      </div>
      
      {isLoading ? (
        <p>Loading...</p>
      ) : (
        <table className="w-full border-collapse">
          <thead>
            <tr className="bg-gray-200">
              <th className="border p-2">Time</th>
              <th className="border p-2">Camera</th>
              <th className="border p-2">Severity</th>
              <th className="border p-2">Description</th>
              <th className="border p-2">Actions</th>
            </tr>
          </thead>
          <tbody>
            {events?.map(event => (
              <tr key={event.id} className="hover:bg-gray-100">
                <td className="border p-2">{new Date(event.ts * 1000).toLocaleString()}</td>
                <td className="border p-2">{event.camera}</td>
                <td className="border p-2">
                  <span className={`px-2 py-1 rounded ${event.severity === 'HIGH' ? 'bg-red-200' : 'bg-yellow-200'}`}>
                    {event.severity}
                  </span>
                </td>
                <td className="border p-2">{event.description}</td>
                <td className="border p-2">
                  <button onClick={() => handleDelete(event.id)} className="text-red-500 hover:underline">
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
```

### 2.2 Mobile App (React Native + Expo)

**Structure**:
```
mobile/
├── src/
│   ├── screens/
│   │   ├─ AlertsList.js          (real-time events)
│   │   ├─ CameraLive.js          (MJPEG stream)
│   │   ├─ ClipPlayer.js          (play + share)
│   │   ├─ Registry.js            (search vehicle)
│   │   └─ Settings.js            (notifications, logout)
│   ├── navigation/
│   │   └─ RootNavigator.js       (tab navigator)
│   ├── store/
│   │   ├─ redux/
│   │   │   ├─ alertsSlice.js
│   │   │   └─ userSlice.js
│   │   └─ configureStore.js
│   ├── api/
│   │   └─ client.js              (Fetch + JWT)
│   ├── components/
│   │   ├─ AlertCard.js
│   │   └─ CameraPreview.js
│   └── App.js                    (Root)
├── app.json
├── eas.json                      (Expo build config)
└── package.json
```

**Key Features**:
- Push notifications via Firebase Cloud Messaging (FCM)
- Offline cache (AsyncStorage + SQLite)
- Biometric authentication (FaceID/TouchID)
- Share clip to WhatsApp/Telegram
- Call manager via built-in dialer

---

## Phase 3: Message Templates & Webhooks (Weeks 4-6)

### 3.1 Alert Template System

**File**: `app/templates.py` (NEW)

```python
from jinja2 import Environment, BaseLoader
from dataclasses import dataclass

ALERT_TEMPLATES = {
    "telegram": {
        "break_in": """
🚨 <b>BREAK-IN ALERT [{{ severity }}]</b>
━━━━━━━━━━━━━━━━━
📍 Camera: <code>{{ camera }}</code>
🏢 Building: <code>{{ building }}</code>
⏰ Time: <code>{{ time }}</code>
📝 Description: {{ description }}
{% if confidence %}✅ Confidence: {{ (confidence*100)|int }}%{% endif %}
        """,
        
        "theft": """
🚗 <b>VEHICLE THEFT ALERT [{{ severity }}]</b>
━━━━━━━━━━━━━━━━━
📍 Camera: <code>{{ camera }}</code>
🏢 Building: <code>{{ building }}</code>
⏰ Activity Time: <code>{{ activity_time }}</code>
🚗 Departed: <code>{{ departure_time }}</code> (+{{ gap_s }}s)
📝 {{ description }}
        """,
        
        "unauthorized": """
⚠️ <b>UNAUTHORIZED VEHICLE</b>
━━━━━━━━━━━━━━━━━
🏷️ Plate: <code>{{ plate }}</code>
📍 Camera: <code>{{ camera }}</code>
⏰ Time: <code>{{ time }}</code>
        """
    },
    
    "email": {
        "break_in": """
        <h2>🚨 Break-In Alert</h2>
        <p><strong>Building:</strong> {{ building }}</p>
        <p><strong>Camera:</strong> {{ camera }}</p>
        <p><strong>Time:</strong> {{ time }}</p>
        <p><strong>Description:</strong> {{ description }}</p>
        <p><a href="{{ clip_url }}" class="btn btn-primary">View Clip</a></p>
        """,
        
        "daily_digest": """
        <h2>Daily Alert Summary — {{ date }}</h2>
        <table>
        {% for building, count in incidents.items() %}
            <tr>
                <td>{{ building }}</td>
                <td>{{ count }} alerts</td>
            </tr>
        {% endfor %}
        </table>
        <p>False alarm rate: {{ false_alarm_rate }}%</p>
        <p><a href="{{ dashboard_url }}">View Dashboard</a></p>
        """
    },
    
    "sms": {
        "break_in": "Alert: Break-in at {{ camera }} ({{ building }}). Time: {{ time }}. Check app.",
        "theft": "Alert: Vehicle stolen from {{ camera }} ({{ building }}). Departed {{ gap_s }}s after activity.",
    },
    
    "slack": {
        "break_in": """
        {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "🚨 Break-In Alert [{{ severity }}]",
                        "emoji": true
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*Building:* {{ building }}\n*Camera:* {{ camera }}\n*Time:* {{ time }}\n*Description:* {{ description }}"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "View Clip"
                            },
                            "url": "{{ clip_url }}"
                        }
                    ]
                }
            ]
        }
        """
    }
}

class TemplateManager:
    def __init__(self):
        self.env = Environment(loader=BaseLoader())
    
    def render(self, channel: str, template_name: str, context: dict) -> str:
        """Render template for notification channel."""
        try:
            template_str = ALERT_TEMPLATES[channel][template_name]
            template = self.env.from_string(template_str)
            return template.render(**context)
        except KeyError:
            log.warning(f"Template not found: {channel}/{template_name}")
            return f"Alert: {context.get('description', 'Incident detected')}"
    
    def get_context(self, event: Event, clip_url: str = None) -> dict:
        """Extract context dict from event."""
        return {
            "camera": event.camera,
            "building": event.building,
            "severity": event.severity,
            "time": format_time(event.ts),
            "description": event.description,
            "plate": event.plate,
            "confidence": event.confidence,
            "clip_url": clip_url or f"/clips/{event.id}",
            "dashboard_url": "https://yourserver.com/dashboard"
        }
```

### 3.2 Webhook Integration

**File**: `app/webhooks.py` (NEW)

```python
import requests
import time
from typing import List

class WebhookRouter:
    """Send events to n8n or make.com workflows."""
    
    def __init__(self, config: dict, db):
        self.config = config
        self.db = db
        self.enabled = config.get("enabled", True)
        self.webhook_url = config.get("url")
        self.retry_count = config.get("retry_count", 3)
        self.retry_delay = config.get("retry_delay_s", 5)
    
    def send_event(self, event: Event, clip_url: str = None):
        """POST event to webhook endpoint."""
        if not self.enabled or not self.webhook_url:
            return
        
        payload = {
            "event_id": event.id,
            "event_type": event.event_type,
            "severity": event.severity,
            "camera": event.camera,
            "building": event.building,
            "timestamp": event.ts,
            "description": event.description,
            "plate": event.plate,
            "confidence": event.confidence,
            "clip_url": clip_url,
            "track_ids": event.track_ids
        }
        
        # Retry logic
        for attempt in range(self.retry_count):
            try:
                resp = requests.post(
                    self.webhook_url,
                    json=payload,
                    timeout=10,
                    headers={"Content-Type": "application/json"}
                )
                if resp.status_code == 200:
                    log.info(f"Webhook sent: event {event.id}")
                    return True
            except requests.RequestException as e:
                log.warning(f"Webhook attempt {attempt+1} failed: {e}")
                if attempt < self.retry_count - 1:
                    time.sleep(self.retry_delay * (2 ** attempt))  # Exponential backoff
        
        log.error(f"Webhook failed after {self.retry_count} attempts for event {event.id}")
        return False
```

### 3.3 n8n Workflow Template

**Export as JSON** from n8n:
```json
{
  "name": "Watchdog Alert Router",
  "nodes": [
    {
      "parameters": {
        "path": "webhook/watchdog",
        "responseMode": "onReceived",
        "options": {}
      },
      "id": "webhook_in",
      "name": "Webhook: Watchdog",
      "type": "n8n-nodes-base.webhook",
      "typeVersion": 1,
      "position": [250, 300]
    },
    {
      "parameters": {
        "conditions": {
          "numberOperator": "equals",
          "numberValue1": "HIGH",
          "pass": "='{{ $json.severity }}'='HIGH'"
        }
      },
      "id": "switch",
      "name": "Route by Severity",
      "type": "n8n-nodes-base.switch",
      "typeVersion": 1,
      "position": [450, 300]
    },
    {
      "parameters": {
        "authentication": "oAuth2",
        "toEmail": "={{ $json.manager_email }}",
        "subject": "Alert: {{ $json.event_type }}",
        "textOnly": false,
        "htmlEmail": "<h2>{{ $json.description }}</h2><p><a href='{{ $json.clip_url }}'>View Clip</a></p>"
      },
      "id": "send_email",
      "name": "Gmail: Send Email",
      "type": "n8n-nodes-base.gmail",
      "typeVersion": 2,
      "position": [650, 200]
    },
    {
      "parameters": {
        "fromField": "messageSid",
        "authentication": "twilio",
        "toField": "phoneNumber",
        "messageField": "textMessage"
      },
      "id": "send_sms",
      "name": "Twilio: Send SMS",
      "type": "n8n-nodes-base.twilio",
      "typeVersion": 1,
      "position": [650, 400]
    }
  ],
  "connections": {
    "webhook_in": {
      "main": [[{"node": "switch", "type": "main", "index": 0}]]
    },
    "switch": {
      "main": [
        [{"node": "send_email", "type": "main", "index": 0}],
        [{"node": "send_sms", "type": "main", "index": 0}]
      ]
    }
  }
}
```

**How to use**:
1. Self-host n8n or use n8n.cloud free tier
2. Create new workflow
3. Copy JSON template above
4. Add Twilio + Gmail credentials
5. Set webhook URL in config.yaml: `webhooks.url: "https://your-n8n.com/webhook/watchdog"`
6. Deploy!

---

## Phase 4: Camera System Integration (Weeks 5-7)

### 4.1 Camera Adapter Pattern

**File**: `app/camera_adapters.py` (NEW)

```python
from abc import ABC, abstractmethod
import cv2
import numpy as np
import requests
import time

class CameraAdapter(ABC):
    """Base class for all camera types."""
    
    @abstractmethod
    def connect(self) -> bool:
        """Test connectivity. Return True if successful."""
        pass
    
    @abstractmethod
    def read_frame(self) -> tuple[np.ndarray, float]:
        """Return (frame, timestamp)."""
        pass
    
    @abstractmethod
    def disconnect(self):
        """Cleanup resources."""
        pass

class RTSPCamera(CameraAdapter):
    """Network cameras: Hikvision, Dahua, etc."""
    
    def __init__(self, url: str, timeout_s: int = 30):
        self.url = url
        self.timeout_s = timeout_s
        self.cap = None
    
    def connect(self) -> bool:
        self.cap = cv2.VideoCapture(self.url)
        # Try to read one frame to verify
        ret, _ = self.cap.read()
        return ret
    
    def read_frame(self) -> tuple[np.ndarray, float]:
        if self.cap is None:
            return None, 0.0
        ret, frame = self.cap.read()
        if ret:
            return frame, time.time()
        return None, 0.0
    
    def disconnect(self):
        if self.cap:
            self.cap.release()

class HTTPCamera(CameraAdapter):
    """HTTP MJPEG or snapshot cameras."""
    
    def __init__(self, url: str, user: str = None, password: str = None):
        self.url = url
        self.auth = (user, password) if user else None
        self.cap = None
    
    def connect(self) -> bool:
        # For MJPEG streams
        self.cap = cv2.VideoCapture(self.url)
        ret, _ = self.cap.read()
        return ret
    
    def read_frame(self) -> tuple[np.ndarray, float]:
        ret, frame = self.cap.read()
        if ret:
            return frame, time.time()
        return None, 0.0
    
    def disconnect(self):
        if self.cap:
            self.cap.release()

class HTTPSnapshotCamera(CameraAdapter):
    """HTTP snapshot camera (polling-based)."""
    
    def __init__(self, snapshot_url: str, user: str = None, password: str = None, interval_s: float = 1.0):
        self.snapshot_url = snapshot_url
        self.auth = (user, password) if user else None
        self.interval_s = interval_s
        self.last_frame_time = 0.0
    
    def connect(self) -> bool:
        try:
            resp = requests.get(self.snapshot_url, auth=self.auth, timeout=5)
            return resp.status_code == 200
        except:
            return False
    
    def read_frame(self) -> tuple[np.ndarray, float]:
        now = time.time()
        if now - self.last_frame_time < self.interval_s:
            return None, 0.0  # Not time yet
        
        try:
            resp = requests.get(self.snapshot_url, auth=self.auth, timeout=5)
            img = cv2.imdecode(np.frombuffer(resp.content, np.uint8), cv2.IMREAD_COLOR)
            self.last_frame_time = now
            return img, now
        except:
            return None, 0.0
    
    def disconnect(self):
        pass

class CameraFactory:
    """Create appropriate adapter based on URL scheme."""
    
    ADAPTERS = {
        "rtsp": RTSPCamera,
        "http": HTTPCamera,
        "https": HTTPCamera,
    }
    
    @staticmethod
    def create(url: str, user: str = None, password: str = None) -> CameraAdapter:
        scheme = url.split("://")[0].lower()
        
        if scheme in CameraFactory.ADAPTERS:
            cls = CameraFactory.ADAPTERS[scheme]
            return cls(url, user, password)
        else:
            raise ValueError(f"Unsupported camera scheme: {scheme}")
```

### 4.2 Camera Discovery Service

**File**: `app/camera_discovery.py` (enhance existing)

```python
import ipaddress
import requests
from threading import Thread, Lock

VENDOR_PATTERNS = {
    "hikvision": [
        "rtsp://{user}:{password}@{ip}:554/Streaming/Channels/101",
        "rtsp://{user}:{password}@{ip}:554/Streaming/Channels/102",
        "http://{user}:{password}@{ip}/ISAPI/Streaming/channels/101/preview",
    ],
    "dahua": [
        "rtsp://{user}:{password}@{ip}:554/cam/realmonitor?channel=1&subtype=1",
        "rtsp://{user}:{password}@{ip}:554/cam/realmonitor?channel=2&subtype=1",
    ],
    "uniview": [
        "rtsp://{user}:{password}@{ip}:554/av0_0",
        "rtsp://{user}:{password}@{ip}:554/av1_0",
    ]
}

class CameraDiscovery:
    def __init__(self):
        self.results = []
        self.lock = Lock()
    
    def scan_network(self, cidr: str, user: str = "admin", password: str = "12345"):
        """Scan CIDR range for cameras."""
        network = ipaddress.IPv4Network(cidr, strict=False)
        threads = []
        
        for ip in network.hosts():
            t = Thread(target=self._test_ip, args=(str(ip), user, password))
            t.start()
            threads.append(t)
        
        for t in threads:
            t.join()
        
        return self.results
    
    def _test_ip(self, ip: str, user: str, password: str):
        """Try all known patterns for this IP."""
        for vendor, patterns in VENDOR_PATTERNS.items():
            for pattern in patterns:
                url = pattern.format(ip=ip, user=user, password=password)
                try:
                    # Test RTSP connection
                    import cv2
                    cap = cv2.VideoCapture(url)
                    ret, _ = cap.read()
                    cap.release()
                    
                    if ret:
                        with self.lock:
                            self.results.append({
                                "ip": ip,
                                "vendor": vendor,
                                "url": url,
                                "user": user,
                                "timestamp": time.time()
                            })
                        return
                except:
                    pass
```

---

## Phase 5: User Permissions & Auth (Weeks 7-9)

### 5.1 Permission Model

**File**: `app/auth.py` (NEW)

```python
from pydantic import BaseModel
from typing import List, Literal
from fastapi import Depends, HTTPException, status
from fastapi_jwt_extended import get_jwt_identity

class User(BaseModel):
    id: int
    username: str
    email: str
    role: Literal["admin", "manager", "guard", "resident"]
    buildings: List[str]  # ["A", "B"] or ["*"]
    can_acknowledge_alerts: bool = True
    can_delete_clips: bool = False
    can_export_data: bool = False
    can_modify_registry: bool = False

ROLE_PERMISSIONS = {
    "admin": {
        "can_delete_clips": True,
        "can_export_data": True,
        "can_modify_registry": True,
        "can_modify_config": True,
        "can_manage_users": True,
        "buildings": ["*"]
    },
    "manager": {
        "can_delete_clips": True,
        "can_export_data": True,
        "can_modify_registry": True,
        "can_acknowledge_alerts": True,
        "buildings": ["*"]  # Or specific buildings
    },
    "guard": {
        "can_acknowledge_alerts": True,
        "buildings": ["*"]  # Or assigned buildings
    },
    "resident": {
        "can_acknowledge_alerts": False,
        "buildings": ["assigned"]  # Only own building
    }
}

async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    """Extract user from JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    user = db.get_user(int(user_id))
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    
    return user

def check_permission(user: User, permission: str) -> bool:
    """Check if user has permission."""
    return getattr(user, permission, False)

def check_building_access(user: User, building: str) -> bool:
    """Check if user can access building."""
    return "*" in user.buildings or building in user.buildings
```

### 5.2 Settings UI Flow

**Dashboard → Settings Flow**:

```
┌─ Alert Rules (if role >= manager)
│  ├─ Enable/disable anomaly types
│  │  ├─ on_near_vehicle
│  │  ├─ on_pose
│  │  ├─ on_loiter
│  │  └─ on_motion_burst
│  │
│  ├─ Thresholds
│  │  ├─ Motion sensitivity (disturb_thresh: 10-30)
│  │  ├─ Dwell time (dwell_s: 5-30s)
│  │  └─ Near vehicle radius (near_vehicle_px: 100-300)
│  │
│  ├─ Escalation
│  │  ├─ Refractory windows
│  │  └─ High alert triggers
│  │
│  └─ Time-based rules
│     ├─ Night hours
│     └─ Quiet hours
│
├─ Notifications (per user)
│  ├─ Telegram: enabled/disabled
│  ├─ Email: address + frequency
│  │  ├─ Real-time
│  │  └─ Digest (daily/weekly)
│  ├─ SMS: phone + enabled
│  ├─ Slack: webhook URL
│  └─ Quiet hours: 22:00-06:00
│
├─ Webhooks (if role >= admin)
│  ├─ n8n webhook URL
│  ├─ Test button (send test event)
│  └─ Retry policy
│
└─ Audit (if role >= admin)
   ├─ View tamper-proof event chain
   ├─ Export audit log
   └─ Verify hash signatures
```

---

## Phase 6: Analytics Dashboard (Weeks 9-10)

### 6.1 Metrics to Track

```python
# app/analytics.py

class Analytics:
    def daily_summary(self, date: str, building: str = None):
        """Daily report."""
        return {
            "date": date,
            "total_alerts": count_events(date, building),
            "high_severity": count_events(date, building, severity="HIGH"),
            "medium_severity": count_events(date, building, severity="MEDIUM"),
            "false_alarms": count_feedback(date, building, verdict="false_alarm"),
            "false_alarm_rate": ...,
            "cameras_online_avg": ...,
            "top_cameras": [(camera, count), ...],
            "ai_cost_inr": ...
        }
    
    def cost_breakdown(self, start_date, end_date, building: str = None):
        """Claude API cost analysis."""
        haiku_reviews = count_reviews_by_model(start_date, end_date, "haiku")
        opus_reviews = count_reviews_by_model(start_date, end_date, "opus")
        
        return {
            "period": f"{start_date} to {end_date}",
            "haiku_screens": {
                "count": haiku_reviews,
                "cost_inr": haiku_reviews * 0.50
            },
            "opus_reviews": {
                "count": opus_reviews,
                "cost_inr": opus_reviews * 4.0
            },
            "total_cost_inr": (haiku_reviews * 0.50) + (opus_reviews * 4.0),
            "cost_per_alert": total_cost / total_alerts,
            "daily_average": total_cost / num_days
        }
    
    def false_alarm_analysis(self, days: int = 30):
        """Analyze false alarm patterns."""
        feedback = db.query(Feedback).filter(...).all()
        
        false_alarms = [f for f in feedback if f.verdict == "false_alarm"]
        
        return {
            "total_alerts": len(feedback),
            "false_alarms": len(false_alarms),
            "false_alarm_rate": len(false_alarms) / len(feedback),
            "by_camera": group_by_camera(false_alarms),
            "by_hour": group_by_hour(false_alarms),
            "common_reasons": [...]
        }
```

---

## Complete Technology Stack

### Backend
```
Framework:      FastAPI (async, WebSocket-ready)
Database:       SQLite (on-premise) + Supabase (optional cloud)
Cache:          Redis (real-time events, optional)
Auth:           PyJWT + python-jose
Task Queue:     Celery + Redis (async processing, optional)
Container:      Docker + docker-compose
```

### Frontend
```
Dashboard:      React 18 + Vite + TypeScript
State Mgmt:     Zustand (lightweight) or Redux (complex)
Styling:        Tailwind CSS + shadcn/ui
Real-time:      Socket.IO (WebSocket wrapper)
Data Fetch:     TanStack Query (React Query)
Maps:           Leaflet (optional, multi-site view)
```

### Mobile
```
Framework:      React Native + Expo
State Mgmt:     Redux (shared with dashboard)
Local Storage:  AsyncStorage + SQLite
Push Notif:     Firebase Cloud Messaging (FCM)
Biometric:      expo-local-authentication
```

### External Services
```
Notifications:  Telegram (direct), n8n (email/SMS/Slack)
SMS Gateway:    Twilio or AWS SNS
Email:          Gmail API or Sendgrid
Cloud (opt):    Supabase (PostgreSQL + Auth)
CI/CD:          GitHub Actions
Hosting (opt):  Railway, Render, or self-hosted
```

---

## Implementation Checklist

### Week 1-2: Foundation
- [ ] Event bus pub/sub system
- [ ] REST API gateway (cameras, events, auth)
- [ ] Database schema (users, permissions, audit log)
- [ ] JWT authentication + password hashing

### Week 3-5: Frontend
- [ ] React dashboard (events table, live MJPEG)
- [ ] React Native mobile app shell
- [ ] Real-time updates (WebSocket integration)
- [ ] Permission checks on all routes

### Week 4-6: Messages
- [ ] Template engine (Jinja2)
- [ ] Webhook router to n8n
- [ ] n8n workflow (email, SMS, Slack)
- [ ] Test with real alerts

### Week 5-7: Cameras
- [ ] Camera adapter pattern
- [ ] RTSP + HTTP snapshot support
- [ ] Discovery service
- [ ] Multi-camera config in dashboard

### Week 7-9: Permissions
- [ ] User management UI (admin only)
- [ ] Role-based access control (RBAC)
- [ ] Building-scoped dashboards
- [ ] Audit log viewer

### Week 9-10: Analytics
- [ ] Daily summary metrics
- [ ] Cost tracking dashboard
- [ ] False alarm analysis
- [ ] Export to CSV

---

## Cost Breakdown

| Component | Cost | Notes |
|-----------|------|-------|
| Infrastructure | $0-50/mo | Self-hosted or $5-50 cloud |
| Clip Storage | $10-200/mo | Depends on incident volume |
| Claude API | $5-100/mo | Haiku + Opus, tunable via daily_cap |
| n8n (self) | $0/mo | Free tier, 1000 tasks/mo |
| Twilio (SMS) | $0-50/mo | $0.01-0.05 per SMS |
| **Total** | **$15-400/mo** | Scales with usage |

---

## What's Next?

**You now have:**
1. ✅ Free layer (YOLO + motion + pose)
2. ✅ Free-layer documentation
3. ✅ **Complete implementation strategy** (this doc)

**To proceed:**
1. Choose your stack (recommended: React + FastAPI)
2. Set up repository structure
3. Start Phase 1 (Event Bus + API)
4. Build in parallel (dashboard team + mobile team)
5. Integrate n8n workflows as you build

Would you like me to help with:
- [ ] Setting up the React dashboard starter?
- [ ] Building the FastAPI API gateway?
- [ ] Creating n8n workflow templates?
- [ ] UX/UI mockups for different roles?

