// AI actors on the 2D ground plane (x,z). The AI seeker runs solo practice;
// AI hider bots pad out small lobbies and panic-run for orbs.
window.AI = (function () {
  const BOT_NAMES = ["Bolt", "Pebbles", "Waldo", "Gnocchi", "Sir Still"];

  function dist(a, b) { return Math.hypot(a.x - b.x, a.z - b.z); }
  function stepToward(e, tx, tz, speed, dt) {
    const dx = tx - e.x, dz = tz - e.z;
    const d = Math.hypot(dx, dz);
    if (d < 0.05) return true;
    const s = Math.min(d, speed * dt);
    e.x += (dx / d) * s;
    e.z += (dz / d) * s;
    e.yaw = Math.atan2(dx, dz);
    WORLD3D.clampPos(e);
    return d < 0.4;
  }

  // ---------------- seeker ----------------
  function createSeeker() {
    const p = WORLD3D.randomOpenSpot();
    return {
      x: p.x, z: p.z, yaw: 0, speed: 3.4,
      mode: "patrol", target: WORLD3D.randomOpenSpot(),
      pause: 1.4, inspected: {}, suspect: null, mv: 0,
    };
  }

  function updateSeeker(ai, dt, figures, now, cb) {
    ai.mv = 0;
    const live = figures.filter((f) => !f.cleared && !f.caught);

    for (const f of live) {
      if (f.kind === "player" && f.movedInConeAt && now - f.movedInConeAt < 500 &&
          ai.suspect !== f.id) {
        ai.suspect = f.id;
        ai.mode = "inspect";
        ai.target = { x: f.x, z: f.z };
        ai.pause = 0.55;
      }
    }

    if (ai.pause > 0) { ai.pause -= dt; return; }

    if (ai.mode === "patrol") {
      const arrived = stepToward(ai, ai.target.x, ai.target.z, ai.speed, dt);
      ai.mv = arrived ? 0 : 1;
      if (arrived) {
        const near = live
          .filter((f) => !ai.inspected[f.kind + f.id])
          .sort((a, b) => dist(a, ai) - dist(b, ai))[0];
        if (near && dist(near, ai) < 18 && Math.random() < 0.75) {
          ai.mode = "inspect";
          ai.target = { x: near.x, z: near.z };
          ai.suspect = null;
        } else {
          ai.target = WORLD3D.randomOpenSpot();
          ai.pause = 0.5 + Math.random() * 1.2;
        }
      }
    } else if (ai.mode === "inspect") {
      const closeEnough = dist(ai, ai.target) < 2.6;
      if (!closeEnough) {
        stepToward(ai, ai.target.x, ai.target.z, ai.speed, dt);
        ai.mv = 1;
      } else {
        ai.pause = 0.7 + Math.random() * 0.8;
        const f = live.sort((a, b) => dist(a, ai) - dist(b, ai))[0];
        ai.mode = "patrol";
        if (f && dist(f, ai) < 4) {
          ai.inspected[f.kind + f.id] = true;
          const sawIt = ai.suspect && f.kind === "player" && f.id === ai.suspect;
          const p = sawIt ? 0.95 : 0.22;
          if (Math.random() < p) cb.accuse(f.kind, f.id);
        }
        ai.suspect = null;
      }
    }
  }

  // ---------------- hider bots ----------------
  function createBot(i) {
    const p = WORLD3D.randomOpenSpot();
    return {
      id: "bot" + i,
      name: BOT_NAMES[i % BOT_NAMES.length],
      color: ["#9aa0b4", "#c9b458", "#7d9c86", "#9a7f9e", "#6f8ea5"][i % 5],
      bot: true,
      x: p.x, z: p.z, yaw: Math.random() * 6.28,
      pose: FIG.randomPose(),
      paint: FIG3D.randomPaint(),
      mode: "idle", target: null, think: 2 + Math.random() * 6,
      moved: false,
    };
  }

  function inCone(b, seeker) {
    if (!seeker) return false;
    const dx = b.x - seeker.x, dz = b.z - seeker.z;
    const d = Math.hypot(dx, dz);
    if (d > seeker.range) return false;
    const ang = Math.atan2(dx, dz);
    let diff = ang - seeker.yaw;
    while (diff > Math.PI) diff -= 2 * Math.PI;
    while (diff < -Math.PI) diff += 2 * Math.PI;
    return Math.abs(diff) < 0.55;
  }

  function updateBot(b, dt, phase, seeker, orbs) {
    b.moved = false;
    if (phase === "setup") {
      if (b.mode === "idle") {
        b.think -= dt;
        if (b.think <= 0) {
          b.mode = "walk";
          const p = WORLD3D.randomOpenSpot();
          b.target = p;
        }
      } else {
        const arrived = stepToward(b, b.target.x, b.target.z, 3.4, dt);
        b.moved = !arrived;
        if (arrived) { b.mode = "idle"; b.think = 99; b.pose = FIG.randomPose(); }
      }
      return;
    }
    if (phase !== "hunt") return;

    const lit = inCone(b, seeker);
    if (b.mode === "idle") {
      b.think -= dt;
      const twitchy = lit && Math.random() < 0.0008;
      if ((b.think <= 0 && !lit) || twitchy) {
        const orb = orbs.filter((o) => !o.taken)
          .sort((a, c) => dist(a, b) - dist(c, b))[0];
        b.target = orb && dist(orb, b) < 20
          ? { x: orb.x, z: orb.z }
          : { x: b.x + MU.rand(-3, 3), z: b.z + MU.rand(-3, 3) };
        b.mode = "walk";
      }
    } else {
      if (lit && Math.random() < 0.9) { b.mode = "idle"; b.think = 3 + Math.random() * 5; return; }
      const arrived = stepToward(b, b.target.x, b.target.z, 1.4, dt);
      b.moved = !arrived;
      if (arrived) { b.mode = "idle"; b.think = 5 + Math.random() * 8; }
    }
  }

  // ---------------- Freeze Tag brains ----------------
  // The IT chases the nearest unfrozen runner at full sprint.
  function updateChaser(ai, dt, runners, cb) {
    ai.mv = 0;
    const live = runners.filter((r) => !r.frozen);
    if (!live.length) return;
    const target = live.sort((a, b) => dist(a, ai) - dist(b, ai))[0];
    if (ai.pause > 0) { ai.pause -= dt; return; }
    const arrived = stepToward(ai, target.x, target.z, 4.1, dt);
    ai.mv = arrived ? 0 : 1;
    if (dist(target, ai) < 1.25) {
      cb.tag(target.id);
      ai.pause = 1.2; // celebrate, then move on — no camping the frozen victim
    }
    // brief stumble sometimes so humans can juke it
    if (Math.random() < 0.002) ai.pause = 0.5;
  }

  // Runner bots flee the IT, rescue frozen friends, and snack on orbs.
  function updateRunnerBot(b, dt, it, runners, orbs) {
    b.moved = false;
    if (b.frozenSelf) return;
    const itDist = it ? dist(b, it) : 99;
    let tx = null, tz = null, speed = 3.4;
    if (it && itDist < 8) {
      // flee directly away, with a little jitter
      const away = Math.atan2(b.x - it.x, b.z - it.z) + (Math.random() - 0.5) * 0.5;
      tx = b.x + Math.sin(away) * 6;
      tz = b.z + Math.cos(away) * 6;
      speed = 3.7;
    } else {
      const frozenMate = runners
        .filter((r) => r.frozen && r.id !== b.id)
        .sort((a, c) => dist(a, b) - dist(c, b))[0];
      const orb = orbs.filter((o) => !o.taken).sort((a, c) => dist(a, b) - dist(c, b))[0];
      if (frozenMate && dist(frozenMate, b) < 22 && (!it || dist(frozenMate, it) > 9)) {
        tx = frozenMate.x; tz = frozenMate.z;
      } else if (orb && dist(orb, b) < 18) {
        tx = orb.x; tz = orb.z;
      } else {
        if (!b.target || dist(b.target, b) < 1) b.target = WORLD3D.randomOpenSpot();
        tx = b.target.x; tz = b.target.z;
        speed = 2.6;
      }
    }
    const arrived = stepToward(b, tx, tz, speed, dt);
    b.moved = !arrived;
  }

  return { createSeeker, updateSeeker, createBot, updateBot, inCone,
           updateChaser, updateRunnerBot, BOT_NAMES };
})();
