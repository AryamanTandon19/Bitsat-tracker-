// App shell: menu ⇄ lobby ⇄ game screen wiring.
(function () {
  const COLORS = [
    "#ff5d6c", "#ffb43a", "#4cd97b", "#3ecbe8",
    "#7c5cff", "#f068c0", "#a3e048", "#ff8b4a",
  ];
  const $ = (id) => document.getElementById(id);
  const screens = {
    menu: $("screen-menu"),
    lobby: $("screen-lobby"),
    game: $("screen-game"),
  };
  let myColor = COLORS[Math.floor(Math.random() * COLORS.length)];
  let myHat = localStorage.getItem("mimic-hat") || "none";
  let myMode = localStorage.getItem("mimic-mode") || "mimic";
  let inLobby = false;

  const HAT_ICONS = { none: "🚫", cap: "🧢", party: "🥳", tophat: "🎩", crown: "👑", halo: "😇" };
  const HAT_LEVEL = { none: 1, cap: 1, party: 2, tophat: 3, crown: 4, halo: 5 };

  function stats() {
    try { return JSON.parse(localStorage.getItem("mimic-stats")) || { xp: 0, games: 0, wins: 0 }; }
    catch (e) { return { xp: 0, games: 0, wins: 0 }; }
  }
  function myLevel() { return Math.floor(Math.sqrt(stats().xp / 60)) + 1; }

  function renderHats() {
    const row = $("hat-row");
    row.innerHTML = "";
    const lvl = myLevel();
    $("level-tag").textContent = "· Level " + lvl + " (" + stats().xp + " xp)";
    for (const h of FIG3D.HATS) {
      const d = document.createElement("div");
      const locked = lvl < HAT_LEVEL[h];
      d.className = "hat-dot" + (h === myHat ? " sel" : "") + (locked ? " locked" : "");
      d.textContent = HAT_ICONS[h] || "🎩";
      d.title = locked ? "Unlocks at level " + HAT_LEVEL[h] : h;
      d.addEventListener("click", () => {
        if (locked) return;
        myHat = h;
        localStorage.setItem("mimic-hat", h);
        renderHats();
      });
      row.appendChild(d);
    }
  }
  window.MIMIC_ON_STATS = () => renderHats(); // refresh after each match

  function renderModes() {
    document.querySelectorAll(".mode-chip").forEach((b) => {
      b.classList.toggle("sel", b.dataset.mode === myMode);
      b.onclick = () => {
        myMode = b.dataset.mode;
        localStorage.setItem("mimic-mode", myMode);
        renderModes();
      };
    });
  }
  renderModes();

  function show(name) {
    for (const k in screens) screens[k].classList.toggle("hidden", k !== name);
    inLobby = name === "lobby";
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // ---------- profile ----------
  const nameInput = $("inp-name");
  nameInput.value = localStorage.getItem("mimic-name") || "";
  const colorRow = $("color-row");
  COLORS.forEach((c) => {
    const d = document.createElement("div");
    d.className = "color-dot" + (c === myColor ? " sel" : "");
    d.style.background = c;
    d.addEventListener("click", () => {
      myColor = c;
      colorRow.querySelectorAll(".color-dot").forEach((el) => el.classList.remove("sel"));
      d.classList.add("sel");
    });
    colorRow.appendChild(d);
  });

  function profile() {
    let name = nameInput.value.trim().slice(0, 12);
    if (!name) name = "Statue" + Math.floor(Math.random() * 900 + 100);
    localStorage.setItem("mimic-name", name);
    return { name, color: myColor, hat: myHat };
  }
  renderHats();

  // ---------- online status ----------
  const netStatus = $("net-status");
  const online = NET.init();
  if (online) {
    netStatus.textContent = "🟢 online — rooms & quick match available";
    netStatus.classList.add("ok");
  } else {
    netStatus.textContent = "🔴 offline — solo practice only";
    netStatus.classList.add("err");
    $("btn-create").disabled = $("btn-quick").disabled = $("btn-join").disabled = true;
  }

  // ---------- lobby rendering ----------
  function renderLobby() {
    const list = NET.players();
    const box = $("lobby-players");
    box.innerHTML = "";
    list.forEach((p, i) => {
      const row = document.createElement("div");
      row.className = "lobby-player";
      row.innerHTML =
        '<span class="dot" style="background:' + p.color + '"></span>' +
        "<span>" + escapeHtml(p.name) + (p.id === NET.state.myId ? " (you)" : "") + "</span>" +
        (i === 0 ? '<span class="host-tag">HOST</span>' : "");
      box.appendChild(row);
    });
    const host = NET.isHost();
    const enough = list.length >= 2;
    $("btn-start").classList.toggle("hidden", !host || !enough);
    $("lobby-wait").classList.toggle("hidden", host);
    $("lobby-need").classList.toggle("hidden", !host || enough);
  }

  NET.onPlayers(() => { if (inLobby) renderLobby(); });

  async function enterRoom(code, isJoin) {
    const btns = ["btn-create", "btn-quick", "btn-join", "btn-solo"].map($);
    btns.forEach((b) => (b.disabled = true));
    netStatus.textContent = "joining " + code + "…";
    try {
      await NET.join(code, profile());
      GAME.startNetSession(profile());
      try { localStorage.setItem("mimic-last", JSON.stringify({ code, t: Date.now() })); } catch (err) {}
      $("lobby-code").textContent = code;
      show("lobby");
      renderLobby();
    } catch (e) {
      netStatus.textContent = "⚠ couldn't join (" + e.message + ") — try again";
    }
    btns.forEach((b) => (b.disabled = false));
  }

  // ---------- menu buttons ----------
  $("btn-solo").addEventListener("click", () => {
    SND.unlock();
    show("game");
    GAME.startSolo(profile(), myMode);
  });
  $("btn-create").addEventListener("click", () => {
    SND.unlock();
    enterRoom(NET.makeCode(), false);
  });
  $("btn-quick").addEventListener("click", async () => {
    SND.unlock();
    // shard the public plaza: try rooms until one has space
    const base = MIMIC_CONFIG.QUICK_MATCH_CODE.slice(0, 3);
    for (let i = 1; i <= 4; i++) {
      await enterRoom(base + i, true);
      if (NET.state.code) {
        if (NET.players().length <= 8) return; // found a seat
        NET.leave();
        GAME.stop();
        show("menu");
      } else return; // join failed entirely (offline etc.)
    }
  });

  // rejoin the last room after a disconnect / reload
  (function () {
    try {
      const last = JSON.parse(localStorage.getItem("mimic-last"));
      if (last && Date.now() - last.t < 10 * 60 * 1000) {
        const b = $("btn-rejoin");
        b.textContent = "↩ Rejoin room " + last.code;
        b.classList.remove("hidden");
        b.addEventListener("click", () => {
          SND.unlock();
          enterRoom(last.code, true);
        });
      }
    } catch (e) {}
  })();
  $("btn-join").addEventListener("click", () => {
    SND.unlock();
    const code = $("inp-code").value.trim().toUpperCase();
    if (code.length !== 4) {
      netStatus.textContent = "⚠ room codes are 4 letters";
      return;
    }
    enterRoom(code, true);
  });
  $("inp-code").addEventListener("input", (e) => {
    e.target.value = e.target.value.toUpperCase().replace(/[^A-Z]/g, "");
  });

  // ---------- lobby buttons ----------
  $("btn-start").addEventListener("click", () => {
    if (NET.isHost() && NET.players().length >= 2) GAME.netHostStart(myMode);
  });
  $("btn-leave").addEventListener("click", () => {
    GAME.stop();
    NET.leave();
    show("menu");
    netStatus.textContent = online ? "🟢 online — rooms & quick match available" : netStatus.textContent;
  });

  // ---------- game init ----------
  GAME.init($("game-canvas"), {
    showGame: () => show("game"),
    showLobby: () => { show("lobby"); renderLobby(); },
    showMenu: () => show("menu"),
  });

  // leaving the page = leaving the room
  window.addEventListener("beforeunload", () => NET.leave());
})();
