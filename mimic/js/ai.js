// AI actors. The AI seeker runs solo-practice rounds; AI hider bots pad out
// small lobbies (and are comedy gold when they panic-run for orbs).
window.AI = (function () {
  const BOT_NAMES = ["Bolt", "Pebbles", "Waldo", "Gnocchi", "Sir Still"];

  // ---------------- seeker ----------------
  function createSeeker(worldW) {
    return {
      x: 120, dir: 1, speed: 150,
      mode: "patrol", targetX: worldW * 0.4,
      pause: 1.2, inspected: {}, suspect: null,
      worldW,
    };
  }

  // figures: [{kind:'decoy'|'player', id, x, cleared, caught, movedInConeAt}]
  // cb.accuse(kind, id) is called when the AI commits to a guess.
  function updateSeeker(ai, dt, figures, now, cb) {
    const live = figures.filter((f) => !f.cleared && !f.caught);

    // spotted someone moving in the light? go get 'em
    for (const f of live) {
      if (f.kind === "player" && f.movedInConeAt && now - f.movedInConeAt < 500 &&
          ai.suspect !== f.id) {
        ai.suspect = f.id;
        ai.mode = "inspect";
        ai.targetX = f.x;
        ai.pause = 0.55; // reaction time
      }
    }

    if (ai.pause > 0) { ai.pause -= dt; return; }

    if (ai.mode === "patrol") {
      const d = ai.targetX - ai.x;
      if (Math.abs(d) < 12) {
        // arrived: maybe inspect the nearest unvisited figure
        const near = live
          .filter((f) => !ai.inspected[f.kind + f.id])
          .sort((a, b) => Math.abs(a.x - ai.x) - Math.abs(b.x - ai.x))[0];
        if (near && Math.abs(near.x - ai.x) < 420 && Math.random() < 0.75) {
          ai.mode = "inspect";
          ai.targetX = near.x;
          ai.suspect = null;
        } else {
          ai.targetX = 80 + Math.random() * (ai.worldW - 160);
          ai.pause = 0.5 + Math.random() * 1.2;
        }
      } else {
        ai.dir = d > 0 ? 1 : -1;
        ai.x += ai.dir * ai.speed * dt;
      }
    } else if (ai.mode === "inspect") {
      const d = ai.targetX - ai.x;
      if (Math.abs(d) < 60) {
        // stare at it, then decide
        ai.pause = 0.7 + Math.random() * 0.8;
        const f = live.sort((a, b) => Math.abs(a.x - ai.x) - Math.abs(b.x - ai.x))[0];
        ai.mode = "patrol";
        if (f) {
          ai.inspected[f.kind + f.id] = true;
          const sawIt = ai.suspect && f.kind === "player" && f.id === ai.suspect;
          const p = sawIt ? 0.95 : 0.22;
          if (Math.random() < p) cb.accuse(f.kind, f.id);
        }
        ai.suspect = null;
      } else {
        ai.dir = d > 0 ? 1 : -1;
        ai.x += ai.dir * ai.speed * dt;
      }
    }
  }

  // ---------------- hider bots ----------------
  function createBot(i, worldW) {
    return {
      id: "bot" + i,
      name: BOT_NAMES[i % BOT_NAMES.length],
      color: ["#8a8f98", "#a58e6f", "#7d9c86", "#9a7f9e", "#6f8ea5"][i % 5],
      bot: true,
      x: 100 + Math.random() * (worldW - 200),
      dir: Math.random() < 0.5 ? -1 : 1,
      pose: FIG.randomPose(),
      mode: "idle", targetX: 0, think: 2 + Math.random() * 6,
      moved: false,
    };
  }

  // seekerX/seekerDir/coneRange let bots know when the light is on them.
  function updateBot(b, dt, phase, seeker, orbs, worldW) {
    b.moved = false;
    if (phase === "setup") {
      // wander to a fresh spot, then settle into a pose
      if (b.mode === "idle") {
        b.think -= dt;
        if (b.think <= 0) {
          b.mode = "walk";
          b.targetX = MU.clamp(b.x + MU.rand(-300, 300), 80, worldW - 80);
        }
      } else {
        const d = b.targetX - b.x;
        if (Math.abs(d) < 8) { b.mode = "idle"; b.think = 99; b.pose = FIG.randomPose(); }
        else { b.dir = d > 0 ? 1 : -1; b.x += b.dir * 160 * dt; b.moved = true; }
      }
      return;
    }
    if (phase !== "hunt") return;

    const inCone = seeker &&
      (b.x - seeker.x) * seeker.dir > 0 &&
      Math.abs(b.x - seeker.x) < seeker.range;

    if (b.mode === "idle") {
      b.think -= dt;
      const twitchy = inCone && Math.random() < 0.0008; // rare panic twitch
      if ((b.think <= 0 && !inCone) || twitchy) {
        // go for a nearby orb, or nervously shuffle
        const orb = orbs.filter((o) => !o.taken)
          .sort((a, c) => Math.abs(a.x - b.x) - Math.abs(c.x - b.x))[0];
        b.targetX = orb && Math.abs(orb.x - b.x) < 500
          ? orb.x
          : MU.clamp(b.x + MU.rand(-70, 70), 80, worldW - 80);
        b.mode = "walk";
      }
    } else {
      if (inCone && Math.random() < 0.9) { b.mode = "idle"; b.think = 3 + Math.random() * 5; return; }
      const d = b.targetX - b.x;
      if (Math.abs(d) < 6) { b.mode = "idle"; b.think = 5 + Math.random() * 8; }
      else { b.dir = d > 0 ? 1 : -1; b.x += b.dir * 65 * dt; b.moved = true; }
    }
  }

  return { createSeeker, updateSeeker, createBot, updateBot, BOT_NAMES };
})();
