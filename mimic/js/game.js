// Game orchestration: phases, roles, scoring, host logic, net sync, render loop.
//
// Host model: in net games the earliest-joined player is host and is the only
// one who advances phases, resolves accusations/orbs and broadcasts full
// `state` snapshots. Everyone else just streams their own `pos` and renders.
// In solo mode the local player is host of a private round vs an AI seeker.
window.GAME = (function () {
  const WORLD_W = 2200;
  const SETUP_MS = 40000, HUNT_MS = 100000, RESULTS_MS = 8000;
  const CONE_RANGE = 340, ACCUSE_RANGE = 170, ORB_PICK = 34;
  const SPEED = { setup: 230, hunt: 68, seeker: 165, spec: 330 };
  const PTS = { orb: 10, survive: 25, catch: 30, wrong: -10, sweep: 20 };
  const TIPS = [
    "Watch for statues that twitch when your light passes…",
    "Wrong accusations cost 10 points. Stare first, tap second.",
    "Hiders must move to grab orbs. Patrol near them!",
    "Mannequins never blink. Neither should you.",
  ];

  let canvas, ui = {};
  let cb = { showGame: null, showLobby: null, showMenu: null };

  // ---- shared snapshot (host-authoritative) ----
  let S = null;
  // ---- local state ----
  let L = null;

  function freshLocal(mode, profile) {
    return {
      mode, profile,
      meId: mode === "solo" ? "me" : NET.state.myId,
      players: {},          // id -> {x,dir,pose,mv,lastMoveAt}
      bots: {},             // host-side bot brains
      aiSeeker: null,
      camX: WORLD_W / 2,
      running: true,
      posDirty: false, poseDirty: false,
      sendAcc: 0, snapAcc: 0, keepAcc: 0,
      lastAccuse: 0, lastSpotSnd: 0,
      msgT: 0,
      resultsShown: false,
      time: 0,
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

  function spreadPositions(count, rng, margin, gap) {
    const xs = [];
    let guard = 0;
    while (xs.length < count && guard++ < 900) {
      const x = margin + rng() * (WORLD_W - margin * 2);
      if (xs.every((v) => Math.abs(v - x) > gap)) xs.push(x);
    }
    return xs;
  }

  // =============== round lifecycle (host only) ===============
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

  function botRosterEntry(i) {
    const b = AI.createBot(i, WORLD_W);
    return { id: b.id, name: b.name + " 🤖", color: b.color, bot: true };
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
      // refresh roster so joiners/leavers are handled between rounds
      S.roster = buildRoster();
      for (const p of S.roster) if (S.scores[p.id] == null) S.scores[p.id] = 0;
      const hs = humans(S.roster);
      S.seekerId = hs[(S.round - 1) % hs.length].id;
    } else {
      S.seekerId = "ai";
    }

    const rng = MU.seeded(S.seed);
    const n = 12 + ((rng() * 4) | 0);
    S.decoys = spreadPositions(n, rng, 70, 78).map((x, i) => ({
      id: "d" + i, x: Math.round(x),
      pose: serializePose(FIG.randomPose(rng)),
      dir: rng() < 0.5 ? -1 : 1,
    }));

    // spawn actors
    const spots = spreadPositions(S.roster.length + 2, rng, 110, 60);
    let si = 0;
    L.bots = {};
    for (const p of S.roster) {
      if (p.id === S.seekerId) continue;
      const x = spots[si++] || MU.rand(100, WORLD_W - 100);
      if (p.bot) {
        const b = AI.createBot(parseInt(p.id.slice(3), 10) || 0, WORLD_W);
        b.id = p.id; b.x = x;
        L.bots[p.id] = b;
        L.players[p.id] = { x, dir: b.dir, pose: b.pose, mv: 0, lastMoveAt: 0 };
      }
    }
    if (S.seekerId === "ai") {
      L.aiSeeker = AI.createSeeker(WORLD_W);
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
    // survival bonuses
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
    let correct = false;
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
      correct = true;
      S.scores[seekerId] += PTS.catch;
      S.deltas[seekerId] = (S.deltas[seekerId] || 0) + PTS.catch;
    }
    const payload = { kind, id, correct, seekerId };
    if (L.mode !== "solo") NET.send("verdict", payload);
    onVerdict(payload);
    // solo practice: round is over once YOU are caught
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
    if (Math.abs(pl.x - o.x) > 70) return;
    o.taken = true;
    S.scores[playerId] = (S.scores[playerId] || 0) + PTS.orb;
    S.deltas[playerId] = (S.deltas[playerId] || 0) + PTS.orb;
    if (L.mode !== "solo") NET.send("orbtaken", { id: orbId, by: playerId });
    onOrbTaken({ id: orbId, by: playerId });
    hostBroadcast();
  }

  function hostTick() {
    if (!S || !isHost()) return;
    const t = now();
    if (S.phase === "setup" && t >= S.endsAt) hostBeginHunt();
    else if (S.phase === "hunt") {
      if (t >= S.endsAt) hostEndHunt(false);
      else {
        // orb spawner: keep temptation alive
        const alive = S.orbs.filter((o) => !o.taken).length;
        if (alive < 3 && (!S.nextOrbAt || t >= S.nextOrbAt)) {
          if (S.nextOrbAt) {
            const sp = seekerPos();
            let x = MU.rand(120, WORLD_W - 120);
            if (sp && Math.abs(x - sp.x) < 200) x = (x + WORLD_W / 2) % WORLD_W;
            S.orbs.push({ id: "o" + ((Math.random() * 1e6) | 0), x: Math.round(x), taken: false });
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
  function onNetEvent(t, p) {
    if (!L) return;
    if (t === "pos") {
      const id = p.id || p.from;
      if (id === L.meId) return;
      const pl = L.players[id] || (L.players[id] = { x: p.x, dir: 1, pose: FIG.defaultPose(), mv: 0, lastMoveAt: 0 });
      if (p.mv) {
        pl.lastMoveAt = performance.now();
        // remember whether the move happened in the seeker's light
        const sp = S && seekerPos();
        if (sp && (pl.x - sp.x) * sp.dir > 0 && Math.abs(pl.x - sp.x) < CONE_RANGE) {
          pl.movedInConeAt = performance.now();
        }
      }
      pl.x = p.x; pl.dir = p.dir; pl.mv = p.mv;
      if (p.pose) pl.pose = p.pose;
    } else if (t === "state") {
      const prevPhase = S ? S.phase : null;
      const wasHost = false;
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
    }
  }

  function onPlayersChanged(list) {
    if (!L || L.mode === "solo" || !S) return;
    // drop live positions of players who left
    for (const id in L.players) {
      if (id.startsWith("bot")) continue;
      if (id !== L.meId && !list.some((p) => p.id === id)) delete L.players[id];
    }
    if (!isHost()) return;
    // host duties on roster change mid-round
    if (S.phase === "setup" || S.phase === "hunt") {
      if (!list.some((p) => p.id === S.seekerId)) {
        showMsg("Seeker left — round over!");
        hostEndHunt(false);
        return;
      }
      for (const id of hiderIds()) {
        if (!id.startsWith("bot") && !list.some((p) => p.id === id) && !S.caught[id]) {
          S.caught[id] = true; // leavers count as caught
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
    ui.results.classList.add("hidden");
    ui.cover.classList.add("hidden");
    L.resultsShown = false;

    if (phase === "setup") {
      SND.phase();
      // (re)spawn my figure
      if (myRole() !== "spectator") {
        const isSeeker = myRole() === "seeker";
        L.players[L.meId] = {
          x: isSeeker ? 90 : MU.rand(140, WORLD_W - 140),
          dir: 1,
          pose: FIG.defaultPose(),
          mv: 0, lastMoveAt: 0,
        };
        L.posDirty = L.poseDirty = true;
      }
      if (myRole() === "seeker") {
        ui.cover.classList.remove("hidden");
        ui.coverTip.textContent = MU.choice(TIPS);
      } else if (myRole() === "hider") {
        showMsg("Find a spot & strike a pose! 🗿", 3);
        POSE_EDITOR.show(L.players[L.meId].pose, L.profile.color);
      } else {
        showMsg("Spectating — you'll join next round", 3);
      }
      cb.showGame();
    } else if (phase === "hunt") {
      SND.phase();
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
      cb.showLobby();
    }
    syncUi();
  }

  // =============== per-frame update ===============
  function update(dt) {
    if (!S || !L) return;
    L.time += dt;
    if (L.msgT && L.time > L.msgT) { ui.msg.classList.add("hidden"); L.msgT = 0; }

    hostTick();
    const role = myRole();
    const meP = me();

    // ---- my movement ----
    const mv = INPUT.move();
    if (role === "spectator" || S.phase === "results" || S.phase === "final") {
      L.camX += mv * SPEED.spec * dt;
      L.camX = MU.clamp(L.camX, 0, WORLD_W);
    } else if (meP && S.phase !== "final") {
      let speed = 0;
      if (S.phase === "setup" && role === "hider") speed = SPEED.setup;
      else if (S.phase === "hunt" && role === "hider") speed = SPEED.hunt;
      else if (S.phase === "hunt" && role === "seeker") speed = SPEED.seeker;
      if (mv !== 0 && speed > 0) {
        meP.x = MU.clamp(meP.x + mv * speed * dt, 60, WORLD_W - 60);
        meP.dir = mv > 0 ? 1 : -1;
        meP.mv = 1;
        meP.lastMoveAt = performance.now();
        // the AI seeker (solo) reads this to notice you twitching in its light
        const sp0 = S.phase === "hunt" ? seekerPos() : null;
        if (sp0 && sp0 !== meP && (meP.x - sp0.x) * (sp0.dir || 1) > 0 &&
            Math.abs(meP.x - sp0.x) < CONE_RANGE) {
          meP.movedInConeAt = performance.now();
        }
        L.posDirty = true;
      } else {
        if (meP.mv) L.posDirty = true;
        meP.mv = 0;
      }
      L.camX = MU.lerp(L.camX, meP.x, Math.min(1, dt * 5));

      // hider orb pickup
      if (S.phase === "hunt" && role === "hider") {
        for (const o of S.orbs) {
          if (!o.taken && Math.abs(o.x - meP.x) < ORB_PICK) {
            if (isHost()) hostResolveOrb(L.meId, o.id);
            else NET.send("orb", { id: o.id });
          }
        }
      }
    }

    // ---- host-side AI ----
    if (isHost() && (S.phase === "setup" || S.phase === "hunt")) {
      const sp = seekerPos();
      const seekerInfo = sp ? { x: sp.x, dir: sp.dir, range: CONE_RANGE } : null;
      for (const id in L.bots) {
        if (S.caught[id]) continue;
        const b = L.bots[id];
        AI.updateBot(b, dt, S.phase, seekerInfo, S.orbs, WORLD_W);
        const pl = L.players[id];
        pl.x = b.x; pl.dir = b.dir; pl.pose = b.pose;
        if (b.moved) {
          pl.mv = 1;
          pl.lastMoveAt = performance.now();
          if (seekerInfo && (pl.x - seekerInfo.x) * seekerInfo.dir > 0 &&
              Math.abs(pl.x - seekerInfo.x) < CONE_RANGE) {
            pl.movedInConeAt = performance.now();
          }
        } else pl.mv = 0;
        // bots grab orbs too
        if (S.phase === "hunt") {
          for (const o of S.orbs) {
            if (!o.taken && Math.abs(o.x - b.x) < ORB_PICK) hostResolveOrb(id, o.id);
          }
        }
      }
      if (L.aiSeeker && S.phase === "hunt") {
        const figs = aiFigureList();
        const beforeX = L.aiSeeker.x;
        AI.updateSeeker(L.aiSeeker, dt, figs, performance.now(), {
          accuse: (kind, id) => hostResolveAccuse("ai", kind, id),
        });
        L.aiSeeker.mv = Math.abs(L.aiSeeker.x - beforeX) > 0.1 ? 1 : 0;
      }
    }

    // ---- seeker hears the twitch blip ----
    if (role === "seeker" && S.phase === "hunt") {
      const sp = meP;
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
    syncHud();
  }

  function aiFigureList() {
    const t = performance.now();
    const figs = [];
    for (const d of S.decoys) {
      figs.push({ kind: "decoy", id: d.id, x: d.x, cleared: !!S.cleared[d.id], caught: false });
    }
    for (const id of hiderIds()) {
      const pl = L.players[id];
      if (!pl) continue;
      figs.push({
        kind: "player", id, x: pl.x,
        cleared: false, caught: !!S.caught[id],
        movedInConeAt: pl.movedInConeAt,
      });
    }
    return figs;
  }

  function netFlush(dt) {
    if (L.mode === "solo") return;
    L.sendAcc += dt; L.keepAcc += dt; L.snapAcc += dt;
    const meP = me();
    if (meP && (S.phase === "setup" || S.phase === "hunt") && myRole() !== "spectator") {
      if ((L.posDirty && L.sendAcc > 0.11) || L.keepAcc > 1.8) {
        NET.send("pos", {
          id: L.meId,
          x: Math.round(meP.x), dir: meP.dir, mv: meP.mv,
          pose: serializePose(meP.pose),
        });
        L.posDirty = false; L.sendAcc = 0; L.keepAcc = 0;
      }
    }
    if (isHost()) {
      // stream bot movement + periodic snapshot for late joiners
      L.botAcc = (L.botAcc || 0) + dt;
      if (L.botAcc > 0.16 && (S.phase === "setup" || S.phase === "hunt")) {
        L.botAcc = 0;
        for (const id in L.bots) {
          const pl = L.players[id];
          if (!pl || S.caught[id]) continue;
          if (pl.mv || Math.random() < 0.08) {
            NET.send("pos", { id, x: Math.round(pl.x), dir: pl.dir, mv: pl.mv, pose: serializePose(pl.pose) });
          }
        }
      }
      if (L.snapAcc > 2.5) { NET.send("state", { S }); L.snapAcc = 0; }
    }
  }

  // =============== seeker tap-to-accuse ===============
  function onTap(sx, sy) {
    if (!S || S.phase !== "hunt" || myRole() !== "seeker") return;
    const t = performance.now();
    if (t - L.lastAccuse < 800) return;
    const meP = me();
    const zoom = RENDER.zoom(), floorY = RENDER.floorY();
    const wxTap = RENDER.screenToWorld(sx);

    // nearest accusable figure under the tap
    let best = null, bd = 1e9;
    const consider = (kind, id, x) => {
      const px = (x - wxTap);
      const scrX = sx + px * zoom; // screen x of figure
      if (!FIG.hitTest(sx, sy, scrX, floorY - 7 * zoom, zoom)) return;
      const d = Math.abs(x - meP.x);
      if (d < bd) { bd = d; best = { kind, id, x }; }
    };
    for (const d of S.decoys) if (!S.cleared[d.id]) consider("decoy", d.id, d.x);
    for (const id of hiderIds()) {
      const pl = L.players[id];
      if (pl && !S.caught[id]) consider("player", id, pl.x);
    }
    if (!best) return;
    if (Math.abs(best.x - meP.x) > ACCUSE_RANGE) {
      showMsg("Too far — get closer!", 1.2);
      return;
    }
    L.lastAccuse = t;
    if (isHost()) hostResolveAccuse(L.meId, best.kind, best.id);
    else NET.send("accuse", { kind: best.kind, id: best.id });
  }

  // =============== render ===============
  function buildScene() {
    const role = myRole();
    const hunt = S.phase === "hunt";
    const tNow = performance.now();
    const sp = S.phase !== "setup" ? seekerPos() : null;
    const figures = [];

    for (const d of S.decoys) {
      figures.push({
        x: d.x, dir: d.dir || 1, pose: d.pose,
        style: "mannequin", cleared: !!S.cleared[d.id],
      });
    }

    for (const p of S.roster) {
      if (p.id === S.seekerId) continue;
      const pl = L.players[p.id];
      if (!pl) continue;
      const caught = !!S.caught[p.id];
      const isMe = p.id === L.meId;
      const f = {
        x: pl.x, dir: pl.dir || 1, pose: pl.pose,
        style: hunt || S.phase === "results" || S.phase === "final" ? "mannequin" : "color",
        color: p.color,
        fallen: caught,
        alpha: caught ? 0.75 : 1,
      };
      if (!hunt && !caught) f.label = p.name;
      if (!hunt) f.labelColor = p.color;
      if (isMe && !caught) f.outline = p.color; // always know which statue is you
      if (hunt && role === "spectator" && !caught) f.outline = p.color;
      // twitch flash: seeker (and spectators) see movers in the light glow red
      if (hunt && !caught && (role === "seeker" || role === "spectator") && sp) {
        const inCone = (pl.x - sp.x) * sp.dir > 0 && Math.abs(pl.x - sp.x) < CONE_RANGE;
        if (inCone && pl.lastMoveAt && tNow - pl.lastMoveAt < 450) f.outline = "#ff4455";
      }
      figures.push(f);
    }

    // the seeker figure
    if (sp && S.phase !== "setup") {
      const seekerEntry = S.roster.find((r) => r.id === S.seekerId);
      figures.push({
        x: sp.x, dir: sp.dir || 1,
        pose: sp.walkPose || walkingPose(sp, tNow),
        style: "color",
        color: seekerEntry ? seekerEntry.color : "#c9b458",
        label: seekerEntry ? "🔦 " + seekerEntry.name : "🔦 Seeker",
        labelColor: "#ffdd88",
        hat: true, hatColor: "#1c1826",
        noPedestal: true,
      });
    }

    return {
      camX: L.camX, worldW: WORLD_W, seed: S.seed, time: L.time,
      cone: sp && hunt ? { x: sp.x, dir: sp.dir || 1, range: CONE_RANGE } : null,
      orbs: hunt ? S.orbs : [],
      figures,
    };
  }

  // simple procedural walk/idle pose for the seeker
  function walkingPose(sp, tNow) {
    const p = FIG.defaultPose();
    if (sp.mv || sp.modeMoving) {
      const s = Math.sin(tNow / 90);
      p.lL1 = Math.PI / 2 + s * 0.5; p.lL2 = Math.PI / 2 + s * 0.3;
      p.lR1 = Math.PI / 2 - s * 0.5; p.lR2 = Math.PI / 2 - s * 0.3;
      p.aL1 = Math.PI / 2 - s * 0.4; p.aR1 = Math.PI / 2 + s * 0.4;
      p.torso = 0.06;
    } else {
      p.aR1 = 0.35; p.aR2 = -0.5; // holding up the flashlight
    }
    return p;
  }

  // =============== HUD / overlays ===============
  function syncUi() {
    if (!S) return;
    syncHud();
  }

  function syncHud() {
    if (!S) return;
    const role = myRole();
    ui.role.textContent = role === "seeker" ? "🔦 SEEKER" : role === "hider" ? "🗿 HIDER" : "👀 SPECTATOR";
    const phaseNames = { setup: "HIDE & POSE", hunt: "SEEK!", results: "RESULTS", final: "FINAL", lobby: "LOBBY" };
    ui.phase.textContent = phaseNames[S.phase] || S.phase;
    const remain = S.endsAt ? S.endsAt - now() : 0;
    ui.timer.textContent = MU.fmtTime(remain);
    ui.timer.classList.toggle("low", remain > 0 && remain < 11000);
    ui.score.textContent = "⭐ " + (S.scores[L.meId] || 0);
    ui.coverTimer.textContent = Math.max(0, Math.ceil(remain / 1000));

    const showPoseBtn = S.phase === "setup" && role === "hider" && !POSE_EDITOR.isOpen();
    ui.poseBtn.classList.toggle("hidden", !showPoseBtn);

    let hint = "";
    if (S.phase === "setup" && role === "hider") hint = "Walk ◀ ▶ then pose. Blend in!";
    else if (S.phase === "hunt" && role === "seeker") hint = "Tap a statue to accuse it";
    else if (S.phase === "hunt" && role === "hider") hint = "Sneak when the light is off you. Grab ✨ orbs!";
    else if (role === "spectator") hint = "◀ ▶ to pan around";
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
    ui.btnAgain.classList.toggle(
      "hidden",
      final && L.mode !== "solo" && !isHost()
    );
    ui.btnAgain.textContent = L.mode === "solo" ? "↻ Play Again" : "↻ Back to Lobby";
    ui.results.classList.remove("hidden");
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // =============== main loop ===============
  let rafId = 0, lastT = 0;
  function loop(t) {
    rafId = requestAnimationFrame(loop);
    const dt = Math.min(0.05, (t - lastT) / 1000 || 0.016);
    lastT = t;
    if (!L || !L.running) return;
    if (S && S.phase !== "lobby") {
      update(dt);
      RENDER.frame(buildScene());
    }
  }

  // =============== public API ===============
  function init(canvasEl, callbacks) {
    canvas = canvasEl;
    cb = callbacks;
    RENDER.init(canvas);
    INPUT.init(canvas);
    INPUT.onTap(onTap);
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
    ui = {
      role: document.getElementById("hud-role"),
      phase: document.getElementById("hud-phase"),
      timer: document.getElementById("hud-timer"),
      score: document.getElementById("hud-score"),
      msg: document.getElementById("hud-msg"),
      hint: document.getElementById("hud-hint"),
      poseBtn: document.getElementById("btn-pose"),
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
    // keep host logic ticking while the tab is hidden (mobile screen off etc.)
    setInterval(() => { if (document.hidden && L && L.running) hostTick(); }, 700);
    requestAnimationFrame(loop);
  }

  function startSolo(profile) {
    L = freshLocal("solo", profile);
    L.players["me"] = { x: WORLD_W / 2, dir: 1, pose: FIG.defaultPose(), mv: 0, lastMoveAt: 0 };
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
    if (L) L.running = false;
    L = null;
    S = null;
    POSE_EDITOR.close();
  }

  return {
    init, startSolo, startNetSession, netHostStart, stop,
    isActive: () => !!(L && L.running),
    inMatch: () => !!(S && S.phase && S.phase !== "lobby"),
    // read-only peek for debugging/tests
    debug: () => ({ S, L }),
  };
})();
