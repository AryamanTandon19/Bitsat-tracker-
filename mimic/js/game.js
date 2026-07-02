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
  const PTS = { orb: 10, survive: 25, catch: 30, wrong: -10, sweep: 20 };
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
    if (!inRoster || S.caught[L.meId]) return "spectator";
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
  function showMsg(text, ms) {
    ui.msg.textContent = text;
    ui.msg.classList.remove("hidden");
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
    return { id: b.id, name: b.name + " 🤖", color: b.color, bot: true };
  }

  function buildRoster() {
    let roster;
    if (L.mode === "solo") {
      roster = [
        { id: "me", name: L.profile.name, color: L.profile.color },
        { id: "ai", name: "Inspector Botto", color: "#c9b458", bot: true, aiSeeker: true },
      ];
      for (let i = 0; i < 3; i++) roster.push(botRosterEntry(i));
    } else {
      roster = NET.players().map((p) => ({ id: p.id, name: p.name, color: p.color }));
      const nBots = Math.max(0, 4 - roster.length);
      for (let i = 0; i < nBots; i++) roster.push(botRosterEntry(i));
    }
    return roster;
  }

  function hostStartMatch() {
    const roster = buildRoster();
    S = {
      phase: "setup", endsAt: 0,
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
    S.endsAt = now() + SETUP_MS;
    S.seed = (Math.random() * 1e9) | 0;
    S.cleared = {}; S.caught = {}; S.orbs = []; S.deltas = {};

    if (L.mode !== "solo") {
      S.roster = buildRoster();
      for (const p of S.roster) if (S.scores[p.id] == null) S.scores[p.id] = 0;
      const hs = humans(S.roster);
      S.seekerId = hs[(S.round - 1) % hs.length].id;
    } else {
      S.seekerId = "ai";
    }

    // scatter mannequins across the whole park's decoy spots
    const rng = MU.seeded(S.seed);
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
    }));

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
    }
    hostBroadcast();
    applyPhaseLocally("setup");
  }

  function hostBeginHunt() {
    S.phase = "hunt";
    S.endsAt = now() + HUNT_MS;
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
      if (t >= S.endsAt) hostEndHunt(false);
      else {
        const alive = S.orbs.filter((o) => !o.taken).length;
        if (alive < 3 && (!S.nextOrbAt || t >= S.nextOrbAt)) {
          if (S.nextOrbAt) {
            const sp = seekerPos();
            let p = WORLD3D.randomOpenSpot();
            if (sp && dist2(p, sp) < 8) p = WORLD3D.randomOpenSpot();
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
      if (p.mv) { pl.lastMoveAt = performance.now(); markConeMove(pl); }
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
      if (p.seekerId === L.meId) showMsg("Nope, just a mannequin. " + PTS.wrong + " pts", 1.6);
    } else {
      S.caught[p.id] = true;
      SND.catch_();
      const who = S.roster.find((r) => r.id === p.id);
      if (p.id === L.meId) showMsg("😱 You were CAUGHT!", 3);
      else showMsg((who ? who.name : "A hider") + " was caught!", 2);
    }
  }

  function onOrbTaken(p) {
    if (!S) return;
    const o = S.orbs.find((o) => o.id === p.id);
    if (o) o.taken = true;
    if (p.by === L.meId) { SND.orb(); showMsg("+" + PTS.orb + " ✨", 1.2); }
  }

  // =============== local phase transitions ===============
  function applyPhaseLocally(phase) {
    POSE_EDITOR.close();
    PAINT.close();
    ui.results.classList.add("hidden");
    ui.cover.classList.add("hidden");

    if (phase === "setup") {
      SND.phase();
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
      if (myRole() === "seeker") {
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
      RENDER3D.setMood("dusk");
      ui.cover.classList.add("hidden");
      if (myRole() === "seeker") showMsg("🔦 Find the fakes! Tap a statue to accuse", 3);
      else if (myRole() === "hider") showMsg("FREEZE! Don't get caught 🤫", 3);
    } else if (phase === "results") {
      showResults(false);
    } else if (phase === "final") {
      SND.win();
      showResults(true);
    } else if (phase === "lobby") {
      S = null;
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

    hostTick();
    const role = myRole();
    const meP = me();

    L.camYaw -= INPUT.orbitKeys() * dt * 2.2;

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
      if (S.phase === "setup" && role === "hider") speed = SPEED.setup;
      else if (S.phase === "hunt" && role === "hider") speed = SPEED.hunt;
      else if (S.phase === "hunt" && role === "seeker") speed = SPEED.seeker;
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
      } else {
        if (meP.mv) L.posDirty = true;
        meP.mv = 0;
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
        AI.updateBot(b, dt, S.phase, seekerInfo, S.orbs);
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
    if (role === "seeker" && S.phase === "hunt") {
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

    netFlush(dt);
    syncRigs(dt);
    syncCamera(dt, role, meP);
    syncHud();
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
      L.rigs[id] = rig;
    }
    return L.rigs[id];
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
      if (rig._paint !== d.paint) { rig.setPaint(d.paint); rig._paint = d.paint; }
      rig.setCleared(!!S.cleared[d.id]);
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

      rig.setPos(pl.sx, pl.sz, pl.syaw);
      rig.setFallen(caught);
      const moving = pl.mv && (isMe || tNow - pl.lastMoveAt < 300);
      if (moving && !caught) {
        rig.setPose(walkCycle(pl.pose, tNow));
        rig._pose = null;
      } else if (rig._pose !== pl.pose) {
        rig.setPose(pl.pose);
        rig._pose = pl.pose;
      }
      if (rig._paint !== pl.paint) { rig.setPaint(pl.paint); rig._paint = pl.paint; }

      // labels: setup + reveal show names; seeker always labelled during hunt
      const wantLabel = (!hunt && !caught) || isSeekerFig;
      const labelText = wantLabel ? (isSeekerFig ? "🔦 " + p.name : p.name) : null;
      if (rig._label !== labelText) {
        rig.setLabel(labelText, isSeekerFig ? "#ffdd88" : p.color);
        rig._label = labelText;
      }

      rig.setMarker(isMe && !caught, p.color);

      // twitch flash ring for seeker & spectators
      let flashOn = false;
      if (hunt && !caught && !isSeekerFig && (role === "seeker" || role === "spectator") && seekerInfo) {
        flashOn = AI.inCone(pl, seekerInfo) && pl.lastMoveAt && tNow - pl.lastMoveAt < 450;
      }
      rig.setFlash(flashOn);
    }

    // AI seeker rig (solo)
    if (S.seekerId === "ai" && L.aiSeeker && S.phase !== "setup") {
      seen["ai"] = true;
      const rig = ensureRig("ai");
      rig.setPos(L.aiSeeker.x, L.aiSeeker.z, L.aiSeeker.yaw);
      rig.setPose(L.aiSeeker.mv ? walkCycle(FIG.defaultPose(), tNow) : FIG.defaultPose());
      rig._pose = null;
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
      hunt && seekerInfo, seekerInfo,
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
    if (!S) return;
    const role = myRole();
    const meP = me();

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
    ui.role.textContent = role === "seeker" ? "🔦 SEEKER" : role === "hider" ? "🗿 HIDER" : "👀 SPECTATOR";
    const phaseNames = { setup: "HIDE & PAINT", hunt: "SEEK!", results: "RESULTS", final: "FINAL", lobby: "LOBBY" };
    ui.phase.textContent = phaseNames[S.phase] || S.phase;
    const remain = S.endsAt ? S.endsAt - now() : 0;
    ui.timer.textContent = MU.fmtTime(remain);
    ui.timer.classList.toggle("low", remain > 0 && remain < 11000);
    ui.score.textContent = "⭐ " + (S.scores[L.meId] || 0);
    ui.coverTimer.textContent = Math.max(0, Math.ceil(remain / 1000));

    const setupHider = S.phase === "setup" && role === "hider";
    const panelOpen = POSE_EDITOR.isOpen() || PAINT.isOpen();
    ui.poseBtn.classList.toggle("hidden", !setupHider || panelOpen);
    ui.paintBtn.classList.toggle("hidden", !setupHider || panelOpen);

    let hint = "";
    if (setupHider) hint = "Move 🕹 · drag to look · tap mannequins to repaint";
    else if (S.phase === "hunt" && role === "seeker") hint = "Tap a statue to accuse it";
    else if (S.phase === "hunt" && role === "hider") hint = "Sneak when the light is off you. Grab ✨ orbs!";
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
    ui.btnAgain.classList.toggle("hidden", final && L.mode !== "solo" && !isHost());
    ui.btnAgain.textContent = L.mode === "solo" ? "↻ Play Again" : "↻ Back to Lobby";
    ui.results.classList.remove("hidden");
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

  function startSolo(profile) {
    L = freshLocal("solo", profile);
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

  function netHostStart() {
    if (!L) return;
    hostStartMatch();
  }

  function stop() {
    if (L) {
      L.running = false;
      disposeRigs();
    }
    L = null;
    S = null;
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
