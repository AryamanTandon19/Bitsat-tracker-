// Game orchestration: phases, roles, scoring, host logic, net sync, 3D loop.
//
// Host model: in net games the earliest-joined player is host and is the only
// one who advances phases, resolves accusations/orbs and broadcasts full
// `state` snapshots. Everyone else just streams their own `pos` and renders.
// In solo mode the local player is host of a private round vs an AI seeker.
window.GAME = (function () {
  const SETUP_MS = 45000, HUNT_MS = 100000, RESULTS_MS = 8000;
  const CONE_RANGE = 11, ACCUSE_RANGE = 4.4, ORB_PICK = 1.15, PAINT_RANGE = 4.5;
  const SPEED = { setup: 4.8, hunt: 1.55, seeker: 3.4, spec: 11 };
  const PTS = { orb: 10, survive: 25, catch: 30, wrong: -10, sweep: 20, dare: 5 };
  const BALLS_PER_ROUND = 3, BALL_RADIUS = 2.4, BALL_RANGE = 15;
  const TAG_SETUP_MS = 6000, TAG_HUNT_MS = 110000, TAG_RADIUS = 1.35, TAG_GRACE_MS = 2500;
  const TAG_PTS = { freeze: 15, rescue: 10 };
  const TAG_SPEED = { it: 4.35, runner: 3.8 };
  const TIPS = [
    "Watch for statues that twitch when your light passes…",
    "Wrong accusations cost 10 points. Stare first, tap second.",
    "Hiders must move to grab orbs. Patrol near them!",
    "Hiders can repaint mannequins to match themselves. Trust nothing.",
  ];

  let canvas, ui = {};
  let cb = { showGame: null, showLobby: null, showMenu: null };

  let S = null; // shared snapshot (host-authoritative)
  let L = null; // local state

  function freshLocal(mode, profile) {
    return {
      mode, profile,
      meId: mode === "solo" ? "me" : NET.state.myId,
      players: {},         // id -> {x,z,yaw,pose,paint,mv,lastMoveAt,...}
      rigs: {},            // id -> FIG3D rig (players and decoys)
      bots: {}, aiSeeker: null,
      camYaw: 0, freeCam: { x: 0, z: 0 },
      running: true,
      posDirty: false, paintDirty: false,
      sendAcc: 0, snapAcc: 0, keepAcc: 0, botAcc: 0, dpaintAcc: 0,
      lastAccuse: 0, lastSpotSnd: 0, msgT: 0, time: 0,
      paintTarget: null, pendingDpaint: null,
      aiming: false, fx: [], dangerSince: 0, appliedTheme: null,
      wasHost: false, tut: null,
    };
  }

  // =============== helpers ===============
  function now() { return Date.now(); }
  function me() { return L && L.players[L.meId]; }
  function isHost() { return L.mode === "solo" || NET.isHost(); }
  function humans(roster) { return roster.filter((p) => !p.bot); }
  function myRole() {
    if (!S || !L) return "spectator";
    if (S.seekerId === L.meId) return "seeker";
    const inRoster = S.roster.some((p) => p.id === L.meId);
    if (!inRoster) return "spectator";
    if (S.caught[L.meId] && !isTag()) return "spectator"; // frozen runners stay in play
    return "hider";
  }
  function seekerPos() {
    if (S.seekerId === "ai") return L.aiSeeker;
    return L.players[S.seekerId];
  }
  function hiderIds() {
    return S.roster.filter((p) => p.id !== S.seekerId).map((p) => p.id);
  }
  function dist2(a, b) { return Math.hypot(a.x - b.x, a.z - b.z); }
  function isTag() { return !!(S && S.gameMode === "tag"); }
  function showMsg(text, ms) {
    ui.msg.textContent = text;
    ui.msg.classList.remove("hidden");
    ui.msg.style.animation = "none";
    void ui.msg.offsetWidth; // restart the slam-in animation
    ui.msg.style.animation = "";
    L.msgT = L.time + (ms || 2.4);
  }
  function serializePose(p) {
    const o = {};
    for (const k in p) o[k] = Math.round(p[k] * 1000) / 1000;
    return o;
  }

  // =============== round lifecycle (host only) ===============
  function botRosterEntry(i) {
    const b = AI.createBot(i);
    const hat = Math.random() < 0.35 ? FIG3D.HATS[1 + ((Math.random() * (FIG3D.HATS.length - 1)) | 0)] : "none";
    return { id: b.id, name: b.name + " 🤖", color: b.color, bot: true, hat };
  }

  function buildRoster() {
    let roster;
    if (L.mode === "solo") {
      roster = [
        { id: "me", name: L.profile.name, color: L.profile.color, hat: L.profile.hat || "none" },
        { id: "ai", name: "Inspector Botto", color: "#c9b458", bot: true, aiSeeker: true },
      ];
      for (let i = 0; i < 3; i++) roster.push(botRosterEntry(i));
    } else {
      roster = NET.players().map((p) => ({ id: p.id, name: p.name, color: p.color, hat: p.hat || "none" }));
      const nBots = Math.max(0, 4 - roster.length);
      for (let i = 0; i < nBots; i++) roster.push(botRosterEntry(i));
    }
    return roster;
  }

  function hostStartMatch() {
    if (L) L.xpAwarded = false;
    const roster = buildRoster();
    S = {
      phase: "setup", endsAt: 0,
      gameMode: L.desiredMode || "mimic",
      theme: MU.choice(WORLD3D.THEME_NAMES),
      round: 0,
      totalRounds: L.mode === "solo" ? 3 : Math.min(6, Math.max(2, humans(roster).length)),
      seed: 1, seekerId: null, roster,
      decoys: [], cleared: {}, caught: {}, orbs: [],
      scores: {}, deltas: {},
    };
    for (const p of roster) S.scores[p.id] = 0;
    hostBeginSetup();
  }

  function hostBeginSetup() {
    S.round++;
    S.phase = "setup";
    S.endsAt = now() + (S.gameMode === "tag" ? TAG_SETUP_MS : SETUP_MS);
    S.seed = (Math.random() * 1e9) | 0;
    S.cleared = {}; S.caught = {}; S.orbs = []; S.deltas = {};
    S.balls = BALLS_PER_ROUND; S.nudges = {}; S.dareCd = {};

    if (L.mode !== "solo") {
      S.roster = buildRoster();
      for (const p of S.roster) if (S.scores[p.id] == null) S.scores[p.id] = 0;
      const hs = humans(S.roster);
      S.seekerId = hs[(S.round - 1) % hs.length].id;
    } else {
      S.seekerId = "ai";
    }

    S.grace = {};
    // scatter mannequins across the whole park's decoy spots (Mimic only)
    const rng = MU.seeded(S.seed);
    if (S.gameMode === "tag") {
      S.decoys = [];
    } else {
    const spots = WORLD3D.decoySpots.slice();
    for (let i = spots.length - 1; i > 0; i--) {
      const j = (rng() * (i + 1)) | 0;
      [spots[i], spots[j]] = [spots[j], spots[i]];
    }
    const n = Math.min(spots.length, 20);
    S.decoys = spots.slice(0, n).map((sp, i) => ({
      id: "d" + i,
      x: Math.round(sp.x * 10) / 10, z: Math.round(sp.z * 10) / 10,
      yaw: Math.round((sp.yaw + (rng() - 0.5) * 0.8) * 100) / 100,
      pose: serializePose(FIG.randomPose(rng)),
      paint: FIG3D.randomPaint(rng),
      hat: rng() < 0.3 ? FIG3D.HATS[1 + ((rng() * (FIG3D.HATS.length - 1)) | 0)] : "none",
    }));
    }

    // spawn bot hiders
    L.bots = {};
    for (const p of S.roster) {
      if (p.id === S.seekerId || !p.bot) continue;
      const b = AI.createBot(parseInt(p.id.slice(3), 10) || 0);
      b.id = p.id;
      L.bots[p.id] = b;
      L.players[p.id] = {
        x: b.x, z: b.z, yaw: b.yaw, pose: b.pose, paint: b.paint,
        mv: 0, lastMoveAt: 0,
      };
    }
    if (S.seekerId === "ai") {
      L.aiSeeker = AI.createSeeker();
      L.aiSeeker.range = CONE_RANGE;
      if (S.gameMode === "tag") L.aiSeeker.pause = TAG_SETUP_MS / 1000; // waits for GO
    }
    hostBroadcast();
    applyPhaseLocally("setup");
  }

  function hostBeginHunt() {
    S.phase = "hunt";
    S.endsAt = now() + (S.gameMode === "tag" ? TAG_HUNT_MS : HUNT_MS);
    hostBroadcast();
    applyPhaseLocally("hunt");
  }

  function hostEndHunt(sweep) {
    S.phase = "results";
    S.endsAt = now() + RESULTS_MS;
    for (const id of hiderIds()) {
      if (!S.caught[id]) {
        S.scores[id] += PTS.survive;
        S.deltas[id] = (S.deltas[id] || 0) + PTS.survive;
      }
    }
    if (sweep && S.seekerId !== "ai") {
      S.scores[S.seekerId] += PTS.sweep;
      S.deltas[S.seekerId] = (S.deltas[S.seekerId] || 0) + PTS.sweep;
    }
    hostBroadcast();
    applyPhaseLocally("results");
  }

  function hostNextOrFinal() {
    if (S.round >= S.totalRounds) {
      S.phase = "final";
      S.endsAt = 0;
      hostBroadcast();
      applyPhaseLocally("final");
    } else {
      hostBeginSetup();
    }
  }

  function hostResolveAccuse(seekerId, kind, id) {
    if (S.phase !== "hunt" || seekerId !== S.seekerId) return;
    if (kind === "decoy") {
      const d = S.decoys.find((d) => d.id === id);
      if (!d || S.cleared[id]) return;
      S.cleared[id] = true;
      S.scores[seekerId] += PTS.wrong;
      S.deltas[seekerId] = (S.deltas[seekerId] || 0) + PTS.wrong;
    } else {
      if (S.caught[id] || id === seekerId) return;
      if (!S.roster.some((p) => p.id === id)) return;
      S.caught[id] = true;
      S.scores[seekerId] += PTS.catch;
      S.deltas[seekerId] = (S.deltas[seekerId] || 0) + PTS.catch;
    }
    const payload = { kind, id, correct: kind === "player", seekerId };
    if (L.mode !== "solo") NET.send("verdict", payload);
    onVerdict(payload);
    const everyoneCaught = hiderIds().every((h) => S.caught[h]);
    if (everyoneCaught || (L.mode === "solo" && S.caught[L.meId])) {
      hostEndHunt(everyoneCaught);
    } else {
      hostBroadcast();
    }
  }

  function hostResolveOrb(playerId, orbId) {
    if (S.phase !== "hunt") return;
    const o = S.orbs.find((o) => o.id === orbId);
    const pl = L.players[playerId];
    if (!o || o.taken || !pl || S.caught[playerId]) return;
    if (dist2(pl, o) > 3) return;
    o.taken = true;
    S.scores[playerId] = (S.scores[playerId] || 0) + PTS.orb;
    S.deltas[playerId] = (S.deltas[playerId] || 0) + PTS.orb;
    if (L.mode !== "solo") NET.send("orbtaken", { id: orbId, by: playerId });
    onOrbTaken({ id: orbId, by: playerId });
    hostBroadcast();
  }

  function hostResolveTagFreeze(id) {
    if (!isTag() || S.phase !== "hunt" || S.caught[id]) return;
    if (S.grace[id] && now() < S.grace[id]) return;
    S.caught[id] = true;
    if (S.seekerId !== "ai") {
      S.scores[S.seekerId] += TAG_PTS.freeze;
      S.deltas[S.seekerId] = (S.deltas[S.seekerId] || 0) + TAG_PTS.freeze;
    }
    const payload = { id };
    if (L.mode !== "solo") NET.send("frozen", payload);
    onFrozen(payload);
    if (hiderIds().every((h) => S.caught[h])) hostEndHunt(true);
    else hostBroadcast();
  }

  function hostResolveThaw(id, by) {
    if (!isTag() || S.phase !== "hunt" || !S.caught[id]) return;
    S.caught[id] = false;
    S.grace[id] = now() + TAG_GRACE_MS;
    if (!String(by).startsWith("ai")) {
      S.scores[by] = (S.scores[by] || 0) + TAG_PTS.rescue;
      S.deltas[by] = (S.deltas[by] || 0) + TAG_PTS.rescue;
    }
    const payload = { id, by };
    if (L.mode !== "solo") NET.send("thaw", payload);
    onThaw(payload);
    hostBroadcast();
  }

  // contact checks: IT freezes runners, runners thaw frozen teammates
  function hostTagContacts() {
    const it = seekerPos();
    if (!it) return;
    const ids = hiderIds();
    for (const id of ids) {
      const pl = L.players[id];
      if (!pl || S.caught[id]) continue;
      if (dist2(pl, it) < TAG_RADIUS) { hostResolveTagFreeze(id); continue; }
    }
    for (const id of ids) {
      const pl = L.players[id];
      if (!pl || S.caught[id]) continue;
      for (const fid of ids) {
        if (fid === id || !S.caught[fid]) continue;
        const fpl = L.players[fid];
        if (fpl && dist2(pl, fpl) < TAG_RADIUS) hostResolveThaw(fid, id);
      }
    }
  }

  function hostResolveBall(throwerId, x0, z0, x1, z1) {
    if (S.phase !== "hunt" || throwerId !== S.seekerId || S.balls <= 0) return;
    S.balls--;
    const payload = { x0, z0, x1, z1, from: throwerId };
    if (L.mode !== "solo") NET.send("balln", payload);
    onBall(payload);
    // resolve hits when the ball lands (0.75s flight)
    setTimeout(() => {
      if (!S || S.phase !== "hunt") return;
      for (const id of hiderIds()) {
        const pl = L.players[id];
        if (!pl || S.caught[id]) continue;
        if (Math.hypot(pl.x - x1, pl.z - z1) < BALL_RADIUS) {
          const fl = { id };
          if (L.mode !== "solo") NET.send("flinch", fl);
          onFlinch(fl);
        }
      }
      hostBroadcast();
    }, 750);
  }

  function hostResolveDare(playerId) {
    if (S.phase !== "hunt" || S.caught[playerId]) return;
    const t = now();
    if (S.dareCd[playerId] && t - S.dareCd[playerId] < 10000) return;
    const pl = L.players[playerId];
    const sp = seekerPos();
    if (!pl || !sp || dist2(pl, sp) > CONE_RANGE + 2) return; // must actually be near
    S.dareCd[playerId] = t;
    S.scores[playerId] = (S.scores[playerId] || 0) + PTS.dare;
    S.deltas[playerId] = (S.deltas[playerId] || 0) + PTS.dare;
    if (L.mode !== "solo") NET.send("dared", { id: playerId });
    onDared({ id: playerId });
    hostBroadcast();
  }

  function hostResolveNudge(ghostId, decoyId) {
    if (S.phase !== "hunt") return;
    if (!S.caught[ghostId]) return;          // only ghosts may nudge
    if (S.nudges[ghostId]) return;           // once per round
    const d = S.decoys.find((d) => d.id === decoyId);
    if (!d || S.cleared[decoyId]) return;
    S.nudges[ghostId] = true;
    const payload = { id: decoyId };
    if (L.mode !== "solo") NET.send("nudge2", payload);
    onNudge(payload);
    hostBroadcast();
  }

  function hostApplyDecoyPaint(id, paint) {
    const d = S.decoys.find((d) => d.id === id);
    if (!d || S.phase !== "setup") return;
    d.paint = paint;
    // no immediate snapshot: the dpaint event already reached everyone
  }

  function hostTick() {
    if (!S || !isHost()) return;
    const t = now();
    if (S.phase === "setup" && t >= S.endsAt) hostBeginHunt();
    else if (S.phase === "hunt") {
      if (isTag()) hostTagContacts();
      if (t >= S.endsAt) hostEndHunt(false);
      else {
        const alive = S.orbs.filter((o) => !o.taken).length;
        if (alive < 3 && (!S.nextOrbAt || t >= S.nextOrbAt)) {
          if (S.nextOrbAt) {
            const sp = seekerPos();
            // spawn in the seeker's general area: reward = risk
            let p = WORLD3D.randomOpenSpot();
            if (sp) {
              for (let i = 0; i < 8; i++) {
                const a = Math.random() * Math.PI * 2, r = MU.rand(6, 13);
                const q = WORLD3D.clampPos({ x: sp.x + Math.cos(a) * r, z: sp.z + Math.sin(a) * r });
                if (dist2(q, sp) > 4) { p = q; break; }
              }
            }
            S.orbs.push({
              id: "o" + ((Math.random() * 1e6) | 0),
              x: Math.round(p.x * 10) / 10, z: Math.round(p.z * 10) / 10,
              taken: false,
            });
            hostBroadcast();
          }
          S.nextOrbAt = t + MU.rand(6000, 10000);
        }
      }
    } else if (S.phase === "results" && t >= S.endsAt) hostNextOrFinal();
  }

  function hostBroadcast() {
    if (L.mode === "solo") { syncUi(); return; }
    NET.send("state", { S });
    syncUi();
  }

  // =============== net event handling ===============
  function markConeMove(pl) {
    const sp = S && seekerPos();
    if (sp && AI.inCone(pl, { x: sp.x, z: sp.z, yaw: sp.yaw || 0, range: CONE_RANGE })) {
      pl.movedInConeAt = performance.now();
    }
  }

  function onNetEvent(t, p) {
    if (!L) return;
    if (t === "pos") {
      const id = p.id || p.from;
      if (id === L.meId) return;
      const pl = L.players[id] || (L.players[id] = {
        x: p.x, z: p.z, yaw: p.yaw || 0,
        pose: FIG.defaultPose(), paint: FIG3D.defaultPaint(), mv: 0, lastMoveAt: 0,
      });
      pl.x = p.x; pl.z = p.z; pl.yaw = p.yaw || 0; pl.mv = p.mv;
      pl.jy = p.y || 0;
      if (p.mv || pl.jy > 0) { pl.lastMoveAt = performance.now(); markConeMove(pl); }
      if (p.pose) pl.pose = p.pose;
      if (p.paint) pl.paint = p.paint;
    } else if (t === "state") {
      const prevPhase = S ? S.phase : null;
      S = p.S;
      if (S.phase !== prevPhase) applyPhaseLocally(S.phase);
      syncUi();
    } else if (t === "accuse") {
      if (isHost()) hostResolveAccuse(p.from, p.kind, p.id);
    } else if (t === "orb") {
      if (isHost()) hostResolveOrb(p.from, p.id);
    } else if (t === "verdict") {
      onVerdict(p);
    } else if (t === "orbtaken") {
      onOrbTaken(p);
    } else if (t === "ball") {
      if (isHost()) hostResolveBall(p.from, p.x0, p.z0, p.x1, p.z1);
    } else if (t === "balln") {
      onBall(p);
    } else if (t === "flinch") {
      onFlinch(p);
    } else if (t === "dare") {
      if (isHost()) hostResolveDare(p.from);
    } else if (t === "dared") {
      onDared(p);
    } else if (t === "nudge") {
      if (isHost()) hostResolveNudge(p.from, p.id);
    } else if (t === "nudge2") {
      onNudge(p);
    } else if (t === "frozen") {
      onFrozen(p);
    } else if (t === "thaw") {
      onThaw(p);
    } else if (t === "emote") {
      const rig = L.rigs[p.from];
      if (rig) rig.showEmote(p.e);
    } else if (t === "dpaint") {
      if (!S) return;
      const d = S.decoys.find((d) => d.id === p.id);
      if (d && S.phase === "setup") d.paint = p.paint;
      if (isHost()) hostApplyDecoyPaint(p.id, p.paint);
    }
  }

  function onPlayersChanged(list) {
    if (!L || L.mode === "solo" || !S) return;
    for (const id in L.players) {
      if (id.startsWith("bot")) continue;
      if (id !== L.meId && !list.some((p) => p.id === id)) {
        delete L.players[id];
        if (L.rigs[id]) { L.rigs[id].dispose(); delete L.rigs[id]; }
      }
    }
    if (!isHost()) return;
    if (S.phase === "setup" || S.phase === "hunt") {
      if (!list.some((p) => p.id === S.seekerId)) {
        showMsg("Seeker left — round over!");
        hostEndHunt(false);
        return;
      }
      for (const id of hiderIds()) {
        if (!id.startsWith("bot") && !list.some((p) => p.id === id) && !S.caught[id]) {
          S.caught[id] = true;
        }
      }
      hostBroadcast();
    }
  }

  function onVerdict(p) {
    if (!S) return;
    if (p.kind === "decoy") {
      S.cleared[p.id] = true;
      SND.wrong();
      const d = S.decoys.find((d) => d.id === p.id);
      if (d) {
        RENDER3D.burst(d.x, 1.1, d.z, 0x9aa0b4, 14, { spread: 1.8, up: 1.4, size: 0.1 });
        if (L.rigs[p.id]) L.rigs[p.id].wobble();
      }
      if (p.seekerId === L.meId) {
        RENDER3D.shake(0.25);
        showMsg("Nope, just a mannequin. " + PTS.wrong + " pts", 1.6);
      }
    } else {
      S.caught[p.id] = true;
      SND.catch_();
      const pl = L.players[p.id];
      if (pl) {
        RENDER3D.burst(pl.sx != null ? pl.sx : pl.x, 1.3, pl.sz != null ? pl.sz : pl.z, 0xffd166, 22, { spread: 2.6, up: 2.6, size: 0.13 });
        RENDER3D.burst(pl.sx != null ? pl.sx : pl.x, 1.0, pl.sz != null ? pl.sz : pl.z, 0xff5d6c, 14, { spread: 2.0, up: 2.0, size: 0.11 });
      }
      if (p.id === L.meId || p.seekerId === L.meId) RENDER3D.shake(0.55);
      const who = S.roster.find((r) => r.id === p.id);
      if (p.id === L.meId) showMsg("😱 You were CAUGHT!", 3);
      else showMsg((who ? who.name : "A hider") + " was caught!", 2);
    }
  }

  function onBall(p) {
    SND.throw_();
    L.fx.push({ type: "ball", x0: p.x0, z0: p.z0, x1: p.x1, z1: p.z1, t: 0 });
  }

  function onFlinch(p) {
    const rig = L.rigs[p.id];
    if (rig) rig.flinch();
    SND.pop();
    if (p.id === L.meId) showMsg("💥 The ball got you — you flinched!", 2);
  }

  function onDared(p) {
    if (p.id === L.meId) { SND.orb(); showMsg("😎 Nerves of steel! +" + PTS.dare, 1.6); }
  }

  function onNudge(p) {
    const rig = L.rigs[p.id];
    if (rig) rig.flinch();
    SND.pop();
  }

  function onFrozen(p) {
    if (!S) return;
    S.caught[p.id] = true;
    SND.catch_();
    const pl = L.players[p.id];
    if (pl) {
      const px = pl.sx != null ? pl.sx : pl.x, pz = pl.sz != null ? pl.sz : pl.z;
      RENDER3D.burst(px, 1.2, pz, 0x9fe4ff, 20, { spread: 2.2, up: 2.2, size: 0.13, grav: 3 });
    }
    const who = S.roster.find((r) => r.id === p.id);
    if (p.id === L.meId) {
      RENDER3D.shake(0.5);
      if (navigator.vibrate) navigator.vibrate([80, 60, 80]);
      showMsg("❄️ FROZEN! A teammate can thaw you out…", 3);
    } else if (S.seekerId === L.meId) {
      RENDER3D.shake(0.3);
      showMsg("❄️ Froze " + (who ? who.name : "a runner") + "! +" + TAG_PTS.freeze, 2);
    } else showMsg("❄️ " + (who ? who.name : "A runner") + " got frozen!", 1.6);
  }

  function onThaw(p) {
    if (!S) return;
    S.caught[p.id] = false;
    SND.orb();
    const pl = L.players[p.id];
    if (pl) {
      const px = pl.sx != null ? pl.sx : pl.x, pz = pl.sz != null ? pl.sz : pl.z;
      RENDER3D.burst(px, 1.2, pz, 0x8de04e, 16, { spread: 2, up: 2.2, size: 0.12, grav: 3 });
    }
    const who = S.roster.find((r) => r.id === p.id);
    const rescuer = S.roster.find((r) => r.id === p.by);
    if (p.id === L.meId) showMsg("🔥 Thawed by " + (rescuer ? rescuer.name : "a friend") + " — RUN!", 2.4);
    else if (p.by === L.meId) showMsg("🦸 You freed " + (who ? who.name : "a friend") + "! +" + TAG_PTS.rescue, 2);
  }

  function onOrbTaken(p) {
    if (!S) return;
    const o = S.orbs.find((o) => o.id === p.id);
    if (o) {
      o.taken = true;
      RENDER3D.burst(o.x, 1.0, o.z, 0x9dffe2, 18, { spread: 2.0, up: 2.4, size: 0.12, grav: 2.5 });
    }
    if (p.by === L.meId) { SND.orb(); showMsg("+" + PTS.orb + " ✨", 1.2); }
  }

  // =============== local phase transitions ===============
  function applyPhaseLocally(phase) {
    POSE_EDITOR.close();
    PAINT.close();
    ui.results.classList.add("hidden");
    ui.cover.classList.add("hidden");

    ui.danger.classList.remove("on");
    if (L) L.inDanger = false;

    if (phase === "setup") {
      SND.phase();
      SND.setAmbient("day");
      SND.setMusic("setup");
      if (S.theme && S.theme !== L.appliedTheme) {
        // new map theme: rebuild the world (rigs live outside the world group)
        RENDER3D.setTheme(S.theme);
        L.appliedTheme = S.theme;
      }
      RENDER3D.setMood("day");
      RENDER3D.clearOrbs();
      if (myRole() !== "spectator") {
        // spawn near a mannequin cluster so there's cover (and things to see)
        let p = WORLD3D.randomOpenSpot();
        if (S.decoys.length) {
          const d = MU.choice(S.decoys);
          p = WORLD3D.clampPos({ x: d.x + MU.rand(-4, 4), z: d.z + MU.rand(-4, 4) });
        }
        const meP = L.players[L.meId] = {
          x: p.x, z: p.z, yaw: Math.random() * 6.28,
          pose: FIG.defaultPose(),
          paint: (me() && me().paint) || FIG3D.defaultPaint(L.profile.color),
          mv: 0, lastMoveAt: 0,
        };
        L.posDirty = L.paintDirty = true;
      }
      if (isTag()) {
        showMsg(myRole() === "seeker"
          ? "🏃 You're IT! Freeze everyone by touching them!"
          : "🏃 RUN! Get tagged and you freeze — friends can thaw you", 4.5);
      } else if (myRole() === "seeker") {
        ui.cover.classList.remove("hidden");
        ui.coverTip.textContent = MU.choice(TIPS);
      } else if (myRole() === "hider") {
        showMsg("Find a spot, pose & paint yourself! Tap a mannequin to repaint it 🎨", 4);
      } else {
        showMsg("Spectating — you'll join next round", 3);
      }
      cb.showGame();
    } else if (phase === "hunt") {
      SND.phase();
      SND.setMusic("hunt");
      if (isTag()) {
        RENDER3D.setMood("day"); // bright daylight chase
        showMsg(myRole() === "seeker" ? "GO GO GO! 🏃💨" : "RUN!! The IT is loose 😱", 2.5);
      } else {
        SND.click(); // flashlight snaps on
        SND.setAmbient("dusk");
        RENDER3D.setMood("dusk");
        if (myRole() === "seeker") showMsg("🔦 Find the fakes! Tap a statue to accuse", 3);
        else if (myRole() === "hider") showMsg("FREEZE! Don't get caught 🤫", 3);
      }
      ui.cover.classList.add("hidden");
    } else if (phase === "results") {
      SND.setMusic(null);
      showResults(false);
    } else if (phase === "final") {
      SND.setMusic(null);
      SND.win();
      awardXp();
      showResults(true);
    } else if (phase === "lobby") {
      S = null;
      SND.setAmbient(null);
      SND.setMusic(null);
      disposeRigs();
      cb.showLobby();
    }
    syncUi();
  }

  function disposeRigs() {
    for (const id in L.rigs) L.rigs[id].dispose();
    L.rigs = {};
  }

  // =============== per-frame update ===============
  function update(dt) {
    if (!S || !L) return;
    L.time += dt;
    if (L.msgT && L.time > L.msgT) { ui.msg.classList.add("hidden"); L.msgT = 0; }

    // host migration: if we just inherited host duty, adopt the bots
    const hostNow = isHost();
    if (hostNow && !L.wasHost && L.mode !== "solo" && (S.phase === "setup" || S.phase === "hunt")) {
      for (const p of S.roster) {
        if (!p.bot || p.id === S.seekerId || L.bots[p.id]) continue;
        const b = AI.createBot(parseInt(p.id.slice(3), 10) || 0);
        b.id = p.id;
        const pl = L.players[p.id];
        if (pl) { b.x = pl.x; b.z = pl.z; b.yaw = pl.yaw; b.pose = pl.pose; b.paint = pl.paint; }
        else L.players[p.id] = { x: b.x, z: b.z, yaw: b.yaw, pose: b.pose, paint: b.paint, mv: 0, lastMoveAt: 0 };
        L.bots[p.id] = b;
      }
    }
    L.wasHost = hostNow;

    hostTick();
    const role = myRole();
    const meP = me();

    L.camYaw -= INPUT.orbitKeys() * dt * 2.2;

    // urgency ticks in the last 10 seconds of a phase
    if (S.endsAt && (S.phase === "setup" || S.phase === "hunt")) {
      const remain = S.endsAt - now();
      if (remain > 0 && remain < 10500) {
        const sec = Math.ceil(remain / 1000);
        if (L.lastTickSec !== sec) { L.lastTickSec = sec; SND.tick(); }
      }
    }

    // podium confetti
    if (S.phase === "final") {
      L.confettiAcc = (L.confettiAcc || 0) + dt;
      if (L.confettiAcc > 0.55) {
        L.confettiAcc = 0;
        const c = MU.choice([0xff5d6c, 0xffb43a, 0x4cd97b, 0x3ecbe8, 0xf068c0, 0xffe066]);
        RENDER3D.burst(
          L.freeCam.x + MU.rand(-5, 5), MU.rand(3, 6), L.freeCam.z + MU.rand(-5, 5),
          c, 14, { spread: 3, up: 1, size: 0.14, grav: 2 }
        );
      }
    }

    // ---- my movement (camera-relative) ----
    const v = INPUT.vec();
    const moving = Math.hypot(v.x, v.y) > 0.15;
    if (role === "spectator" || S.phase === "results" || S.phase === "final") {
      if (moving) {
        const s = SPEED.spec * dt;
        L.freeCam.x += (v.x * Math.cos(L.camYaw) + v.y * Math.sin(L.camYaw)) * s;
        L.freeCam.z += (-v.x * Math.sin(L.camYaw) + v.y * Math.cos(L.camYaw)) * s;
        L.freeCam.x = MU.clamp(L.freeCam.x, -45, 45);
        L.freeCam.z = MU.clamp(L.freeCam.z, -30, 30);
      }
    } else if (meP && S.phase !== "final") {
      let speed = 0;
      if (isTag()) {
        const frozenMe = !!S.caught[L.meId];
        if (S.phase === "setup") speed = role === "seeker" ? 0 : SPEED.setup; // IT waits for GO
        else if (S.phase === "hunt") speed = frozenMe ? 0 : role === "seeker" ? TAG_SPEED.it : TAG_SPEED.runner;
      } else {
        if (S.phase === "setup" && role === "hider") speed = SPEED.setup;
        else if (S.phase === "hunt" && role === "hider") speed = SPEED.hunt;
        else if (S.phase === "hunt" && role === "seeker") speed = SPEED.seeker;
      }
      if (moving && speed > 0) {
        const wx = v.x * Math.cos(L.camYaw) + v.y * Math.sin(L.camYaw);
        const wz = -v.x * Math.sin(L.camYaw) + v.y * Math.cos(L.camYaw);
        meP.x += wx * speed * dt;
        meP.z += wz * speed * dt;
        WORLD3D.clampPos(meP);
        meP.yaw = Math.atan2(wx, wz);
        meP.mv = 1;
        meP.lastMoveAt = performance.now();
        markConeMove(meP);
        L.posDirty = true;
        // footstep foley, cadence scales with speed
        L.stepAcc = (L.stepAcc || 0) + dt * speed;
        if (L.stepAcc > 1.35 && !(meP.jy > 0)) { L.stepAcc = 0; SND.step(); }
      } else {
        if (meP.mv) L.posDirty = true;
        meP.mv = 0;
      }
      RENDER3D.setMoving(moving && speed > 3);

      // jump! (visible, so jumping mid-hunt is a gamble)
      if (speed > 0) {
        meP.jy = meP.jy || 0;
        meP.vy = meP.vy || 0;
        if (INPUT.jump() && meP.jy === 0) {
          meP.vy = 4.6;
          SND.whoosh();
          meP.lastMoveAt = performance.now();
          markConeMove(meP);
        }
        if (meP.jy > 0 || meP.vy !== 0) {
          meP.vy -= 12.5 * dt;
          meP.jy = Math.max(0, meP.jy + meP.vy * dt);
          if (meP.jy === 0 && meP.vy < 0) {
            meP.vy = 0;
            SND.thud();
            RENDER3D.burst(meP.x, 0.15, meP.z, 0xcfc8bc, 8, { spread: 1.6, up: 0.7, size: 0.09 });
          }
          meP.lastMoveAt = performance.now();
          L.posDirty = true;
        }
      }

      // danger vignette + heartbeat: flashlight cone (Mimic) / IT closing in (Tag)
      if (role === "hider" && S.phase === "hunt") {
        const sp2 = seekerPos();
        const inDanger = sp2 && sp2 !== meP && (isTag()
          ? !S.caught[L.meId] && dist2(meP, sp2) < 7
          : AI.inCone(meP, { x: sp2.x, z: sp2.z, yaw: sp2.yaw || 0, range: CONE_RANGE }));
        if (inDanger !== L.inDanger) {
          L.inDanger = inDanger;
          ui.danger.classList.toggle("on", !!inDanger);
          if (inDanger) {
            L.dangerSince = L.time;
            if (navigator.vibrate) navigator.vibrate(80);
          } else if (!isTag() && L.time - L.dangerSince > 1.1 && !S.caught[L.meId]) {
            // survived the light: claim the close-call bonus
            if (isHost()) hostResolveDare(L.meId);
            else NET.send("dare", {});
          }
        }
        if (inDanger) {
          L.heartAcc = (L.heartAcc || 0) + dt;
          if (L.heartAcc > 0.95) { L.heartAcc = 0; SND.heart(); }
        }
      } else if (L.inDanger) {
        L.inDanger = false;
        ui.danger.classList.remove("on");
      }

      // camouflage hint during setup: how much do you stand out here?
      if (!isTag() && role === "hider" && S.phase === "setup") {
        L.blendAcc = (L.blendAcc || 0) + dt;
        if (L.blendAcc > 0.8) {
          L.blendAcc = 0;
          updateBlendHint(meP);
        }
      }

      // hider orb pickup
      if (S.phase === "hunt" && role === "hider") {
        for (const o of S.orbs) {
          if (!o.taken && dist2(o, meP) < ORB_PICK) {
            if (isHost()) hostResolveOrb(L.meId, o.id);
            else NET.send("orb", { id: o.id });
          }
        }
      }
    }

    // ---- host-side AI ----
    if (isHost() && (S.phase === "setup" || S.phase === "hunt")) {
      const sp = seekerPos();
      const seekerInfo = sp ? { x: sp.x, z: sp.z, yaw: sp.yaw || 0, range: CONE_RANGE } : null;
      for (const id in L.bots) {
        if (S.caught[id]) continue;
        const b = L.bots[id];
        if (isTag()) {
          if (S.phase === "hunt") {
            const runners = hiderIds().map((hid) => {
              const q = L.players[hid];
              return q ? { id: hid, x: q.x, z: q.z, frozen: !!S.caught[hid] } : null;
            }).filter(Boolean);
            AI.updateRunnerBot(b, dt, seekerInfo, runners, S.orbs);
          }
        } else AI.updateBot(b, dt, S.phase, seekerInfo, S.orbs);
        const pl = L.players[id];
        pl.x = b.x; pl.z = b.z; pl.yaw = b.yaw; pl.pose = b.pose; pl.paint = b.paint;
        if (b.moved) {
          pl.mv = 1;
          pl.lastMoveAt = performance.now();
          markConeMove(pl);
        } else pl.mv = 0;
        if (S.phase === "hunt") {
          for (const o of S.orbs) {
            if (!o.taken && dist2(o, b) < ORB_PICK) hostResolveOrb(id, o.id);
          }
        }
      }
      if (L.aiSeeker && S.phase === "hunt") {
        AI.updateSeeker(L.aiSeeker, dt, aiFigureList(), performance.now(), {
          accuse: (kind, id) => hostResolveAccuse("ai", kind, id),
        });
      }
    }

    // ---- seeker hears the twitch blip ----
    if (!isTag() && role === "seeker" && S.phase === "hunt") {
      const t = performance.now();
      for (const id of hiderIds()) {
        const pl = L.players[id];
        if (!pl || S.caught[id]) continue;
        if (pl.movedInConeAt && t - pl.movedInConeAt < 120 && t - L.lastSpotSnd > 600) {
          SND.spotted();
          L.lastSpotSnd = t;
        }
      }
    }

    tickBalls(dt);
    tickTutorial();
    netFlush(dt);
    syncRigs(dt);
    syncCamera(dt, role, meP);
    syncHud();
  }

  // lobbed ball projectiles (visual on every client)
  function tickBalls(dt) {
    for (let i = L.fx.length - 1; i >= 0; i--) {
      const f = L.fx[i];
      f.t += dt / 0.75;
      if (!f.mesh) {
        f.mesh = new THREE.Mesh(
          new THREE.SphereGeometry(0.14, 12, 10),
          new THREE.MeshStandardMaterial({ color: 0xffd166, roughness: 0.4 })
        );
        f.mesh.castShadow = true;
        RENDER3D.scene().add(f.mesh);
      }
      if (f.t >= 1) {
        RENDER3D.burst(f.x1, 0.3, f.z1, 0xffd166, 10, { spread: 1.8, up: 1.2, size: 0.1 });
        RENDER3D.scene().remove(f.mesh);
        f.mesh.geometry.dispose();
        L.fx.splice(i, 1);
        continue;
      }
      const t = f.t;
      f.mesh.position.set(
        MU.lerp(f.x0, f.x1, t),
        1.4 + Math.sin(t * Math.PI) * 3.2, // arc
        MU.lerp(f.z0, f.z1, t)
      );
    }
  }

  // "you stand out!" camouflage feedback vs the 3 nearest mannequins
  function updateBlendHint(meP) {
    const near = S.decoys
      .map((d) => ({ d, dist: dist2(d, meP) }))
      .sort((a, b) => a.dist - b.dist)
      .slice(0, 3);
    if (!near.length || near[0].dist > 9) {
      ui.blend.textContent = "🚶 No mannequins nearby";
      ui.blend.className = "hud-chip blend-bad";
      return;
    }
    const mine = meP.paint;
    const rgb = (hex) => [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];
    let diff = 0, cnt = 0;
    for (const { d } of near) {
      for (const part of ["torso", "head", "armL", "legL"]) {
        try {
          const a = rgb(mine[part]), b = rgb(d.paint[part]);
          diff += Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
          cnt++;
        } catch (e) {}
      }
    }
    const avg = cnt ? diff / cnt : 999;
    if (avg < 90) { ui.blend.textContent = "🦎 Blending in!"; ui.blend.className = "hud-chip blend-good"; }
    else if (avg < 190) { ui.blend.textContent = "🤔 Could blend better"; ui.blend.className = "hud-chip blend-mid"; }
    else { ui.blend.textContent = "⚠️ You stand out!"; ui.blend.className = "hud-chip blend-bad"; }
  }

  function aiFigureList() {
    const figs = [];
    for (const d of S.decoys) {
      figs.push({ kind: "decoy", id: d.id, x: d.x, z: d.z, cleared: !!S.cleared[d.id], caught: false });
    }
    for (const id of hiderIds()) {
      const pl = L.players[id];
      if (!pl) continue;
      figs.push({
        kind: "player", id, x: pl.x, z: pl.z,
        cleared: false, caught: !!S.caught[id],
        movedInConeAt: pl.movedInConeAt,
      });
    }
    return figs;
  }

  function netFlush(dt) {
    if (L.mode === "solo") return;
    L.sendAcc += dt; L.keepAcc += dt; L.snapAcc += dt; L.botAcc += dt;
    const meP = me();
    if (meP && (S.phase === "setup" || S.phase === "hunt") && myRole() !== "spectator") {
      if ((L.posDirty && L.sendAcc > 0.11) || L.keepAcc > 1.8) {
        const msg = {
          id: L.meId,
          x: Math.round(meP.x * 100) / 100, z: Math.round(meP.z * 100) / 100,
          yaw: Math.round(meP.yaw * 100) / 100, mv: meP.mv,
          y: Math.round((meP.jy || 0) * 100) / 100,
          pose: serializePose(meP.pose),
        };
        if (L.paintDirty || L.keepAcc > 1.8) { msg.paint = meP.paint; L.paintDirty = false; }
        NET.send("pos", msg);
        L.posDirty = false; L.sendAcc = 0; L.keepAcc = 0;
      }
    }
    // decoy repaint (throttled)
    L.dpaintAcc += dt;
    if (L.pendingDpaint && L.dpaintAcc > 0.3) {
      NET.send("dpaint", L.pendingDpaint);
      if (isHost()) hostApplyDecoyPaint(L.pendingDpaint.id, L.pendingDpaint.paint);
      L.pendingDpaint = null;
      L.dpaintAcc = 0;
    }
    if (isHost()) {
      if (L.botAcc > 0.16 && (S.phase === "setup" || S.phase === "hunt")) {
        L.botAcc = 0;
        for (const id in L.bots) {
          const pl = L.players[id];
          if (!pl || S.caught[id]) continue;
          if (pl.mv || Math.random() < 0.08) {
            NET.send("pos", {
              id, x: Math.round(pl.x * 100) / 100, z: Math.round(pl.z * 100) / 100,
              yaw: Math.round(pl.yaw * 100) / 100, mv: pl.mv,
              pose: serializePose(pl.pose), paint: pl.paint,
            });
          }
        }
      }
      if (L.snapAcc > 2.5) { NET.send("state", { S }); L.snapAcc = 0; }
    }
  }

  // =============== rig / camera sync ===============
  function ensureRig(id) {
    if (!L.rigs[id]) {
      const rig = FIG3D.create(RENDER3D.scene());
      rig.hitMesh.userData.figId = id;
      rig.pop(); // bouncy spawn-in
      L.rigs[id] = rig;
    }
    return L.rigs[id];
  }

  function paintSplash(rig, paint, x, z) {
    const t = performance.now();
    if (rig._lastSplash && t - rig._lastSplash < 350) return;
    rig._lastSplash = t;
    RENDER3D.burst(x, 1.25, z, paint.torso || "#ffffff", 12, { spread: 1.6, up: 1.6, size: 0.11, grav: 3 });
  }

  function walkCycle(pose, t) {
    const p = FIG.clone(pose);
    const s = Math.sin(t / 90);
    p.lL1 = Math.PI / 2 + s * 0.5; p.lL2 = Math.PI / 2 + s * 0.3;
    p.lR1 = Math.PI / 2 - s * 0.5; p.lR2 = Math.PI / 2 - s * 0.3;
    p.aL1 = Math.PI / 2 - s * 0.4; p.aR1 = Math.PI / 2 + s * 0.4;
    p.torso = 0.06; p.head = 0;
    return p;
  }

  function syncRigs(dt) {
    const role = myRole();
    const hunt = S.phase === "hunt";
    const reveal = S.phase === "results" || S.phase === "final";
    const tNow = performance.now();
    const sp = seekerPos();
    const seekerInfo = sp ? { x: sp.x, z: sp.z, yaw: sp.syaw != null ? sp.syaw : (sp.yaw || 0), range: CONE_RANGE } : null;
    const seen = {};

    for (const d of S.decoys) {
      seen[d.id] = true;
      const rig = ensureRig(d.id);
      rig.setPos(d.x, d.z, d.yaw);
      if (rig._pose !== d.pose) { rig.setPose(d.pose); rig._pose = d.pose; }
      if (rig._paint !== d.paint) {
        rig.setPaint(d.paint);
        if (rig._paint) paintSplash(rig, d.paint, d.x, d.z); // repainted live
        rig._paint = d.paint;
      }
      rig.setCleared(!!S.cleared[d.id]);
      rig.setHat(d.hat || "none");
      rig.setMode("frozen");
      rig.tick(L.time, dt);
    }

    for (const p of S.roster) {
      if (p.id === S.seekerId && p.id === "ai") continue; // AI seeker handled below
      const pl = L.players[p.id];
      if (!pl) continue;
      seen[p.id] = true;
      const rig = ensureRig(p.id);
      const caught = !!S.caught[p.id];
      const isMe = p.id === L.meId;
      const isSeekerFig = p.id === S.seekerId;

      // smooth remote motion
      if (pl.sx == null) { pl.sx = pl.x; pl.sz = pl.z; pl.syaw = pl.yaw; }
      const k = isMe ? 1 : Math.min(1, dt * 10);
      pl.sx += (pl.x - pl.sx) * k;
      pl.sz += (pl.z - pl.sz) * k;
      let dy = pl.yaw - pl.syaw;
      while (dy > Math.PI) dy -= 2 * Math.PI;
      while (dy < -Math.PI) dy += 2 * Math.PI;
      pl.syaw += dy * k;

      rig.setPos(pl.sx, pl.sz, pl.syaw, pl.jy || 0);
      rig.setFallen(caught && !isTag()); // frozen runners stand, iced over
      rig.setIce(caught && isTag());
      const moving = pl.mv && (isMe || tNow - pl.lastMoveAt < 300);
      if (moving && !caught) {
        rig.setPose(walkCycle(pl.pose, tNow), true); // snap: per-frame anim
        rig._pose = null;
      } else if (rig._pose !== pl.pose) {
        rig.setPose(pl.pose); // blend into the statue pose
        rig._pose = pl.pose;
      }
      if (rig._paint !== pl.paint) {
        rig.setPaint(pl.paint);
        if (rig._paint) paintSplash(rig, pl.paint, pl.sx, pl.sz);
        rig._paint = pl.paint;
      }
      // remote players landing from a jump puff dust too
      if (pl._prevJy > 0.05 && !(pl.jy > 0) && !isMe) {
        RENDER3D.burst(pl.sx, 0.15, pl.sz, 0xcfc8bc, 8, { spread: 1.6, up: 0.7, size: 0.09 });
      }
      pl._prevJy = pl.jy || 0;
      // statues hold perfectly still during the hunt; otherwise breathe
      rig.setMode(caught ? "frozen" : moving ? "walk" : (hunt && !isSeekerFig && !isTag()) ? "frozen" : "idle");
      rig.tick(L.time, dt);

      // labels: tag shows everyone always; Mimic hides names during the hunt
      const wantLabel = isTag() ? true : (!hunt && !caught) || isSeekerFig;
      const itIcon = isTag() ? "🏃 " : "🔦 ";
      const labelText = wantLabel
        ? (isSeekerFig ? itIcon + p.name + (isTag() ? " (IT)" : "") : p.name)
        : null;
      if (rig._label !== labelText) {
        rig.setLabel(labelText, isSeekerFig ? "#ffdd88" : p.color);
        rig._label = labelText;
      }

      rig.setMarker(isMe && !caught, p.color);
      rig.setHat(p.hat || "none");

      // twitch flash ring for seeker & spectators
      let flashOn = false;
      if (!isTag() && hunt && !caught && !isSeekerFig && (role === "seeker" || role === "spectator") && seekerInfo) {
        flashOn = AI.inCone(pl, seekerInfo) && pl.lastMoveAt && tNow - pl.lastMoveAt < 450;
      }
      if (!(caught && isTag())) rig.setFlash(flashOn);
    }

    // AI seeker rig (solo)
    if (S.seekerId === "ai" && L.aiSeeker && S.phase !== "setup") {
      seen["ai"] = true;
      const rig = ensureRig("ai");
      rig.setPos(L.aiSeeker.x, L.aiSeeker.z, L.aiSeeker.yaw);
      if (L.aiSeeker.mv) rig.setPose(walkCycle(FIG.defaultPose(), tNow), true);
      else if (rig._pose !== "idle") { rig.setPose(FIG.defaultPose()); rig._pose = "idle"; }
      if (L.aiSeeker.mv) rig._pose = null;
      rig.setMode(L.aiSeeker.mv ? "walk" : "idle");
      rig.tick(L.time, dt);
      if (!rig._painted) {
        rig.setPaint(FIG3D.defaultPaint("#c9b458"));
        rig.setLabel("🔦 Inspector Botto", "#ffdd88");
        rig._painted = true;
      }
    }
    if (S.seekerId === "ai" && S.phase === "setup" && L.rigs["ai"]) {
      L.rigs["ai"].setVisible(false);
    } else if (L.rigs["ai"]) {
      L.rigs["ai"].setVisible(true);
    }

    // remove rigs for gone entities
    for (const id in L.rigs) {
      if (!seen[id]) { L.rigs[id].dispose(); delete L.rigs[id]; }
    }

    // hide the human seeker's own... no: third person shows self. Hide hider
    // figures from the human seeker during setup? The cover overlay does that.

    // orbs + flashlight
    RENDER3D.syncOrbs(hunt ? S.orbs : []);
    RENDER3D.setFlashlight(
      hunt && seekerInfo && !isTag(), seekerInfo,
      seekerInfo ? seekerInfo.yaw : 0
    );
  }

  function syncCamera(dt, role, meP) {
    if (role === "spectator" || S.phase === "results" || S.phase === "final" || !meP) {
      RENDER3D.updateCamera(dt, L.freeCam, L.camYaw, { dist: 16, height: 12 });
    } else {
      RENDER3D.updateCamera(dt, { x: meP.sx != null ? meP.sx : meP.x, z: meP.sz != null ? meP.sz : meP.z }, L.camYaw, {
        dist: S.phase === "setup" ? 6.5 : 7.5, height: S.phase === "setup" ? 2.8 : 3.4,
      });
    }
  }

  // =============== taps: paint (setup) / accuse (hunt) ===============
  function onTap(sx, sy) {
    if (!S || isTag()) return; // tag is pure contact — no tap actions
    const role = myRole();
    const meP = me();

    // ghost mischief: caught players may nudge ONE mannequin per round
    if (S.phase === "hunt" && role === "spectator" && S.caught[L.meId] && !(S.nudges || {})[L.meId]) {
      const meshes = [];
      for (const d of S.decoys) {
        if (S.cleared[d.id]) continue;
        const rig = L.rigs[d.id];
        if (rig) meshes.push(rig.hitMesh);
      }
      const id = RENDER3D.pickFigure(sx, sy, meshes);
      if (id) {
        if (isHost()) hostResolveNudge(L.meId, id);
        else NET.send("nudge", { id });
        showMsg("👻 Nudged! Let the seeker wonder…", 2);
        return;
      }
    }

    // seeker aiming a ball throw: tap = target on the ground
    if (S.phase === "hunt" && role === "seeker" && L.aiming && meP) {
      const pt = RENDER3D.pickGround(sx, sy);
      L.aiming = false;
      ui.ballBtn.classList.remove("aiming");
      if (pt) {
        const d = Math.hypot(pt.x - meP.x, pt.z - meP.z);
        if (d > BALL_RANGE) {
          const k = BALL_RANGE / d;
          pt.x = meP.x + (pt.x - meP.x) * k;
          pt.z = meP.z + (pt.z - meP.z) * k;
        }
        const payload = { x0: meP.x, z0: meP.z, x1: Math.round(pt.x * 10) / 10, z1: Math.round(pt.z * 10) / 10 };
        if (isHost()) hostResolveBall(L.meId, payload.x0, payload.z0, payload.x1, payload.z1);
        else NET.send("ball", payload);
      }
      syncHud();
      return;
    }

    if (S.phase === "setup" && role === "hider" && meP) {
      const meshes = [];
      for (const d of S.decoys) {
        const rig = L.rigs[d.id];
        if (rig) meshes.push(rig.hitMesh);
      }
      if (L.rigs[L.meId]) meshes.push(L.rigs[L.meId].hitMesh);
      const id = RENDER3D.pickFigure(sx, sy, meshes);
      if (!id) return;
      if (id === L.meId) { openPaintSelf(); return; }
      const d = S.decoys.find((d) => d.id === id);
      if (d && dist2(d, meP) < PAINT_RANGE) {
        L.paintTarget = { type: "decoy", id: d.id };
        PAINT.show({ label: "a mannequin", paint: d.paint, canCopy: true });
      } else if (d) {
        showMsg("Get closer to repaint that one", 1.4);
      }
      return;
    }

    if (S.phase === "hunt" && role === "seeker" && meP) {
      const t = performance.now();
      if (t - L.lastAccuse < 800) return;
      const meshes = [];
      const lookup = {};
      for (const d of S.decoys) {
        if (S.cleared[d.id]) continue;
        const rig = L.rigs[d.id];
        if (rig) { meshes.push(rig.hitMesh); lookup[d.id] = d; }
      }
      for (const id of hiderIds()) {
        const pl = L.players[id];
        if (!pl || S.caught[id]) continue;
        const rig = L.rigs[id];
        if (rig) { meshes.push(rig.hitMesh); lookup[id] = pl; }
      }
      const id = RENDER3D.pickFigure(sx, sy, meshes);
      if (!id) return;
      if (dist2(lookup[id], meP) > ACCUSE_RANGE) {
        showMsg("Too far — get closer!", 1.2);
        return;
      }
      L.lastAccuse = t;
      const kind = S.decoys.some((d) => d.id === id) ? "decoy" : "player";
      if (isHost()) hostResolveAccuse(L.meId, kind, id);
      else NET.send("accuse", { kind, id });
    }
  }

  function openPaintSelf() {
    const meP = me();
    if (!meP || S.phase !== "setup" || myRole() !== "hider") return;
    L.paintTarget = { type: "self" };
    PAINT.show({ label: "yourself", paint: meP.paint, canCopy: false });
  }

  function onPaintChange(paint) {
    if (!L.paintTarget || !S || S.phase !== "setup") return;
    if (L.paintTarget.type === "self") {
      const meP = me();
      if (meP) {
        meP.paint = paint;
        L.paintDirty = L.posDirty = true;
        // PAINT mutates the object in place, so force the rig to re-read it
        if (L.rigs[L.meId]) L.rigs[L.meId]._paint = null;
      }
    } else {
      const d = S.decoys.find((d) => d.id === L.paintTarget.id);
      if (d) {
        d.paint = Object.assign({}, paint);
        L.pendingDpaint = { id: d.id, paint: d.paint };
      }
    }
  }

  // =============== HUD / overlays ===============
  function syncUi() { if (S) syncHud(); }

  function syncHud() {
    if (!S) return;
    const role = myRole();
    if (isTag()) {
      ui.role.textContent = role === "seeker" ? "🏃 IT"
        : role === "hider" ? (S.caught[L.meId] ? "❄️ FROZEN" : "🏃 RUNNER") : "👀 SPECTATOR";
    } else {
      ui.role.textContent = role === "seeker" ? "🔦 SEEKER" : role === "hider" ? "🗿 HIDER" : "👀 SPECTATOR";
    }
    const phaseNames = isTag()
      ? { setup: "GET READY", hunt: "RUN!", results: "RESULTS", final: "FINAL", lobby: "LOBBY" }
      : { setup: "HIDE & PAINT", hunt: "SEEK!", results: "RESULTS", final: "FINAL", lobby: "LOBBY" };
    ui.phase.textContent = phaseNames[S.phase] || S.phase;
    const remain = S.endsAt ? S.endsAt - now() : 0;
    ui.timer.textContent = MU.fmtTime(remain);
    ui.timer.classList.toggle("low", remain > 0 && remain < 11000);
    const myScore = S.scores[L.meId] || 0;
    if (L.shownScore != null && myScore !== L.shownScore) {
      ui.score.classList.remove("bump");
      void ui.score.offsetWidth;
      ui.score.classList.add("bump");
    }
    L.shownScore = myScore;
    ui.score.textContent = "⭐ " + myScore;
    ui.coverTimer.textContent = Math.max(0, Math.ceil(remain / 1000));

    const setupHider = S.phase === "setup" && role === "hider" && !isTag();
    const panelOpen = POSE_EDITOR.isOpen() || PAINT.isOpen();
    ui.poseBtn.classList.toggle("hidden", !setupHider || panelOpen);
    ui.paintBtn.classList.toggle("hidden", !setupHider || panelOpen);
    const canJump = role !== "spectator" && (S.phase === "setup" || S.phase === "hunt") &&
      !(role === "seeker" && S.phase === "setup" && !isTag()) &&
      !(isTag() && S.caught[L.meId]) && !panelOpen;
    ui.jumpBtn.classList.toggle("hidden", !canJump);
    ui.emoteCol.classList.toggle("hidden", !canJump);
    const showBall = role === "seeker" && S.phase === "hunt" && !isTag();
    ui.ballBtn.classList.toggle("hidden", !showBall);
    if (showBall) ui.ballBtn.textContent = "🎾 " + (S.balls != null ? S.balls : 0);
    if (S.balls === 0) ui.ballBtn.classList.remove("aiming");
    ui.blend.classList.toggle("hidden", !(role === "hider" && S.phase === "setup" && !isTag()));

    let hint = "";
    if (isTag() && S.phase === "hunt") {
      if (S.caught[L.meId]) hint = "❄️ Frozen! Wait for a teammate to touch you";
      else hint = role === "seeker" ? "Touch runners to freeze them!" : "Flee the IT · thaw frozen friends by touch";
    } else if (setupHider) hint = "Move 🕹 · drag to look · tap mannequins to repaint";
    else if (S.phase === "hunt" && role === "seeker") hint = "Tap a statue to accuse · 🎾 throws make real players flinch";
    else if (S.phase === "hunt" && role === "hider") hint = "Sneak when the light is off you. Grab ✨ orbs!";
    else if (role === "spectator" && S.phase === "hunt" && S.caught && S.caught[L.meId] && !(S.nudges || {})[L.meId])
      hint = "👻 Tap a mannequin to nudge it (once!) and mess with the seeker";
    else if (role === "spectator") hint = "🕹 to fly around · drag to orbit";
    ui.hint.textContent = hint;
  }

  function showResults(final) {
    const sorted = S.roster
      .map((p) => ({ ...p, pts: S.scores[p.id] || 0, delta: S.deltas[p.id] || 0 }))
      .sort((a, b) => b.pts - a.pts);
    ui.resultsTitle.textContent = final
      ? "🏆 " + (sorted[0] ? sorted[0].name + " wins!" : "Game over!")
      : "Round " + S.round + " / " + S.totalRounds + " over!";
    ui.resultsSub.textContent = final ? "Final standings" : "Scores so far";
    ui.resultsTable.innerHTML = "";
    sorted.forEach((p, i) => {
      const row = document.createElement("div");
      row.className = "res-row" + (i === 0 ? " first" : "");
      const medal = final ? ["🥇", "🥈", "🥉"][i] || "" : "";
      row.innerHTML =
        '<span class="dot" style="background:' + p.color + '"></span>' +
        "<span>" + medal + " " + escapeHtml(p.name) + (p.id === L.meId ? " (you)" : "") + "</span>" +
        '<span class="pts">' + p.pts + "</span>" +
        (p.delta ? '<span class="delta' + (p.delta < 0 ? " neg" : "") + '">' +
          (p.delta > 0 ? "+" : "") + p.delta + "</span>" : "");
      ui.resultsTable.appendChild(row);
    });
    ui.resultsNext.textContent = final ? "" : "Next round starting soon…";
    ui.resultsFinalBtns.classList.toggle("hidden", !final);
    ui.shareBtn.classList.toggle("hidden", !final);
    ui.btnAgain.classList.toggle("hidden", final && L.mode !== "solo" && !isHost());
    ui.btnAgain.textContent = L.mode === "solo" ? "↻ Play Again" : "↻ Back to Lobby";
    ui.results.classList.remove("hidden");
  }

  // ---- local progression: XP, level, stats (no account needed) ----
  function loadStats() {
    try { return JSON.parse(localStorage.getItem("mimic-stats")) || { xp: 0, games: 0, wins: 0 }; }
    catch (e) { return { xp: 0, games: 0, wins: 0 }; }
  }
  function saveStats(st) {
    try { localStorage.setItem("mimic-stats", JSON.stringify(st)); } catch (e) {}
  }
  function levelFor(xp) { return Math.floor(Math.sqrt(xp / 60)) + 1; }

  function awardXp() {
    if (L.xpAwarded) return;
    L.xpAwarded = true;
    const st = loadStats();
    const myScore = Math.max(0, S.scores[L.meId] || 0);
    const sorted = S.roster.slice().sort((a, b) => (S.scores[b.id] || 0) - (S.scores[a.id] || 0));
    const won = sorted[0] && sorted[0].id === L.meId;
    const before = levelFor(st.xp);
    st.xp += myScore + (won ? 30 : 10);
    st.games++;
    if (won) st.wins++;
    saveStats(st);
    const after = levelFor(st.xp);
    if (after > before) setTimeout(() => showMsg("🎉 LEVEL UP! You're now level " + after, 4), 1500);
    if (window.MIMIC_ON_STATS) window.MIMIC_ON_STATS(st);
  }

  // ---- shareable result card (canvas image -> share sheet / download) ----
  function buildShareCard() {
    const c = document.createElement("canvas");
    c.width = 1080; c.height = 1080;
    const x = c.getContext("2d");
    const bg = x.createLinearGradient(0, 0, 0, 1080);
    bg.addColorStop(0, "#2c2542");
    bg.addColorStop(1, "#16131f");
    x.fillStyle = bg;
    x.fillRect(0, 0, 1080, 1080);
    x.textAlign = "center";
    x.fillStyle = "#ffb43a";
    x.font = "900 130px 'Trebuchet MS', sans-serif";
    x.fillText("MIMIC!", 540, 170);
    x.fillStyle = "#9d93b8";
    x.font = "700 40px 'Trebuchet MS', sans-serif";
    x.fillText("pose · paint · freeze · survive", 540, 235);
    const sorted = S.roster
      .map((p) => ({ ...p, pts: S.scores[p.id] || 0 }))
      .sort((a, b) => b.pts - a.pts)
      .slice(0, 6);
    x.font = "900 56px 'Trebuchet MS', sans-serif";
    sorted.forEach((p, i) => {
      const y = 360 + i * 100;
      x.fillStyle = i === 0 ? "rgba(255,180,58,.16)" : "rgba(255,255,255,.05)";
      x.beginPath();
      x.roundRect(90, y - 62, 900, 86, 20);
      x.fill();
      x.fillStyle = p.color || "#fff";
      x.beginPath();
      x.arc(150, y - 20, 22, 0, Math.PI * 2);
      x.fill();
      x.fillStyle = "#f2eefc";
      x.textAlign = "left";
      const medal = ["🥇", "🥈", "🥉"][i] || "  ";
      x.fillText(medal + " " + p.name.slice(0, 14), 200, y);
      x.textAlign = "right";
      x.fillStyle = "#ffb43a";
      x.fillText(String(p.pts), 980, y);
      x.textAlign = "center";
    });
    x.fillStyle = "#7c5cff";
    x.font = "700 42px 'Trebuchet MS', sans-serif";
    x.fillText("Can you spot the fake statue?", 540, 1000);
    return c;
  }

  async function shareResult() {
    const c = buildShareCard();
    const blob = await new Promise((res) => c.toBlob(res, "image/png"));
    const file = new File([blob], "mimic-result.png", { type: "image/png" });
    if (navigator.share && navigator.canShare && navigator.canShare({ files: [file] })) {
      try { await navigator.share({ files: [file], title: "MIMIC!", text: "Can you spot the fake statue? 🗿" }); return; }
      catch (e) { /* user cancelled -> fall through to download */ }
    }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "mimic-result.png";
    a.click();
  }

  // ---- first-run tutorial (solo): step-by-step toasts ----
  function startTutorial() {
    if (localStorage.getItem("mimic-tut")) return;
    L.tut = { step: 0, t: 0 };
  }
  function tickTutorial() {
    if (!L.tut || !S) return;
    const meP = me();
    const t = L.tut;
    const steps = [
      { msg: "👋 Welcome! Drag the joystick (or WASD) to walk around", done: () => meP && meP.mv },
      { msg: "Nice! Now tap 🖐 Pose and strike a statue pose", done: () => POSE_EDITOR.isOpen() },
      { msg: "Tap 🎨 Paint to colour yourself like the mannequins", done: () => PAINT.isOpen() },
      { msg: "When dusk falls: FREEZE. Move only when the light is away. Good luck! 🤫", done: () => S.phase === "hunt" },
    ];
    if (t.step >= steps.length) {
      try { localStorage.setItem("mimic-tut", "1"); } catch (e) {}
      L.tut = null;
      return;
    }
    L.tutMsgAcc = (L.tutMsgAcc || 0) + 1;
    if (L.tutMsgAcc > 200 || t.shownStep !== t.step) {
      L.tutMsgAcc = 0;
      t.shownStep = t.step;
      if (!L.msgT) showMsg(steps[t.step].msg, 4);
    }
    if (steps[t.step].done()) t.step++;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // =============== main loop ===============
  let lastT = 0;
  function loop(t) {
    requestAnimationFrame(loop);
    const dt = Math.min(0.05, (t - lastT) / 1000 || 0.016);
    lastT = t;
    if (!L || !L.running) return;
    if (S && S.phase !== "lobby") {
      update(dt);
      RENDER3D.render(dt);
    }
  }

  // =============== public API ===============
  function init(canvasEl, callbacks) {
    canvas = canvasEl;
    cb = callbacks;
    RENDER3D.init(canvas);
    INPUT.init(canvas);
    INPUT.onTap(onTap);
    INPUT.onOrbit((dx) => { if (L) L.camYaw -= dx * 0.008; });
    POSE_EDITOR.init({
      onChange(pose) {
        const meP = me();
        if (!meP || !S || S.phase !== "setup" || myRole() !== "hider") return;
        meP.pose = pose;
        meP.lastMoveAt = performance.now();
        L.posDirty = true;
      },
      onDone() { syncHud(); },
    });
    PAINT.init({
      onChange: onPaintChange,
      onDone() { L.paintTarget = null; syncHud(); },
      onCopy() {
        // copy the tapped mannequin's paint onto yourself
        if (!L.paintTarget || L.paintTarget.type !== "decoy") return;
        const d = S.decoys.find((d) => d.id === L.paintTarget.id);
        const meP = me();
        if (d && meP) {
          meP.paint = Object.assign({}, d.paint);
          L.paintDirty = L.posDirty = true;
          showMsg("Copied! You now match that mannequin 🦎", 2);
        }
      },
    });
    ui = {
      role: document.getElementById("hud-role"),
      phase: document.getElementById("hud-phase"),
      timer: document.getElementById("hud-timer"),
      score: document.getElementById("hud-score"),
      msg: document.getElementById("hud-msg"),
      hint: document.getElementById("hud-hint"),
      poseBtn: document.getElementById("btn-pose"),
      paintBtn: document.getElementById("btn-paint"),
      jumpBtn: document.getElementById("btn-jump"),
      danger: document.getElementById("danger"),
      emoteCol: document.getElementById("emote-col"),
      ballBtn: document.getElementById("btn-ball"),
      blend: document.getElementById("blend-chip"),
      shareBtn: document.getElementById("btn-share"),
      cover: document.getElementById("cover"),
      coverTimer: document.getElementById("cover-timer"),
      coverTip: document.getElementById("cover-tip"),
      results: document.getElementById("results"),
      resultsTitle: document.getElementById("results-title"),
      resultsSub: document.getElementById("results-sub"),
      resultsTable: document.getElementById("results-table"),
      resultsNext: document.getElementById("results-next"),
      resultsFinalBtns: document.getElementById("results-final-btns"),
      btnAgain: document.getElementById("btn-again"),
    };
    ui.poseBtn.addEventListener("click", () => {
      const meP = me();
      if (meP) POSE_EDITOR.show(meP.pose, L.profile.color);
      syncHud();
    });
    ui.paintBtn.addEventListener("click", () => { openPaintSelf(); syncHud(); });
    ui.ballBtn.addEventListener("click", () => {
      if (!S || S.phase !== "hunt" || myRole() !== "seeker" || S.balls <= 0) return;
      L.aiming = !L.aiming;
      ui.ballBtn.classList.toggle("aiming", L.aiming);
      showMsg(L.aiming ? "🎾 Tap the ground to throw! (real players flinch)" : "Throw cancelled", 2);
    });
    ui.shareBtn.addEventListener("click", () => { shareResult(); });
    ui.emoteCol.querySelectorAll(".emote-btn").forEach((b) => {
      b.addEventListener("click", () => {
        const t = performance.now();
        if (L.lastEmote && t - L.lastEmote < 1500) return; // no spam
        L.lastEmote = t;
        const e = b.dataset.emote;
        const rig = L.rigs[L.meId];
        if (rig) rig.showEmote(e);
        if (L.mode !== "solo") NET.send("emote", { e });
      });
    });
    ui.btnAgain.addEventListener("click", () => {
      ui.results.classList.add("hidden");
      if (L.mode === "solo") hostStartMatch();
      else if (isHost()) {
        NET.send("state", { S: { phase: "lobby" } });
        applyPhaseLocally("lobby");
      }
    });
    document.getElementById("btn-exit").addEventListener("click", () => {
      stop();
      NET.leave();
      cb.showMenu();
    });
    setInterval(() => { if (document.hidden && L && L.running) hostTick(); }, 700);
    requestAnimationFrame(loop);
  }

  function startSolo(profile, mode) {
    L = freshLocal("solo", profile);
    L.desiredMode = mode || "mimic";
    if (L.desiredMode === "mimic") startTutorial();
    L.players["me"] = {
      x: 0, z: -8, yaw: 0, pose: FIG.defaultPose(),
      paint: FIG3D.defaultPaint(profile.color), mv: 0, lastMoveAt: 0,
    };
    hostStartMatch();
  }

  function startNetSession(profile) {
    L = freshLocal("net", profile);
    S = null;
    NET.onEvent(onNetEvent);
    NET.onPlayers(onPlayersChanged);
  }

  function netHostStart(mode) {
    if (!L) return;
    L.desiredMode = mode || "mimic";
    hostStartMatch();
  }

  function stop() {
    if (L) {
      L.running = false;
      disposeRigs();
    }
    L = null;
    S = null;
    SND.setAmbient(null);
    if (ui.danger) ui.danger.classList.remove("on");
    POSE_EDITOR.close();
    PAINT.close();
  }

  return {
    init, startSolo, startNetSession, netHostStart, stop,
    isActive: () => !!(L && L.running),
    inMatch: () => !!(S && S.phase && S.phase !== "lobby"),
    debug: () => ({ S, L }),
  };
})();
