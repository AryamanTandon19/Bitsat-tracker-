// Multiplayer over Supabase Realtime. One channel per room; player roster
// comes from channel presence, gameplay messages travel over broadcast.
// The earliest-joined player acts as host (authoritative for phases/scoring);
// if the host leaves, the next-earliest player takes over automatically.
window.NET = (function () {
  const CODE_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ"; // no I/L/O — less misreading
  let client = null;
  let channel = null;
  let roster = [];
  const evCbs = [], playersCbs = [];

  // stable id across page reloads (per tab!) so a dropped player can rejoin
  // as themselves; sessionStorage keeps two tabs from colliding as one player
  function persistentId() {
    try {
      let id = sessionStorage.getItem("mimic-id");
      if (!id) { id = MU.uid(); sessionStorage.setItem("mimic-id", id); }
      return id;
    } catch (e) { return MU.uid(); }
  }

  const self = {
    online: false,
    code: null,
    myId: persistentId(),
    profile: null,
  };

  function init() {
    try {
      if (!window.supabase) return false;
      client = window.supabase.createClient(
        MIMIC_CONFIG.SUPABASE_URL,
        MIMIC_CONFIG.SUPABASE_KEY,
        { realtime: { params: { eventsPerSecond: 20 } } }
      );
      self.online = true;
    } catch (e) {
      console.warn("supabase init failed", e);
      self.online = false;
    }
    return self.online;
  }

  function makeCode() {
    let c = "";
    for (let i = 0; i < 4; i++) c += CODE_CHARS[Math.floor(Math.random() * CODE_CHARS.length)];
    return c;
  }

  function rebuildRoster() {
    if (!channel) { roster = []; return; }
    const state = channel.presenceState();
    const list = [];
    for (const key in state) {
      const meta = state[key][0];
      if (meta) list.push({ id: key, name: meta.name, color: meta.color, hat: meta.hat, joined: meta.joined });
    }
    list.sort((a, b) => (a.joined - b.joined) || (a.id < b.id ? -1 : 1));
    roster = list;
    for (const cb of playersCbs) cb(roster);
  }

  function join(code, profile) {
    self.profile = profile;
    return new Promise((resolve, reject) => {
      if (!client) return reject(new Error("offline"));
      leave();
      self.code = code.toUpperCase();
      channel = client.channel("mimic-" + self.code, {
        config: { presence: { key: self.myId }, broadcast: { self: false } },
      });
      channel.on("presence", { event: "sync" }, rebuildRoster);
      channel.on("broadcast", { event: "msg" }, (m) => {
        if (m.payload) for (const cb of evCbs) cb(m.payload.t, m.payload);
      });
      let settled = false;
      channel.subscribe(async (status) => {
        if (status === "SUBSCRIBED") {
          await channel.track({
            name: profile.name,
            color: profile.color,
            hat: profile.hat || "none",
            joined: Date.now(),
          });
          if (!settled) { settled = true; resolve(self.code); }
        } else if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") {
          if (!settled) { settled = true; reject(new Error(status)); }
        }
      });
      setTimeout(() => {
        if (!settled) { settled = true; reject(new Error("timeout")); }
      }, 10000);
    });
  }

  function leave() {
    if (channel) {
      try { client.removeChannel(channel); } catch (e) {}
      channel = null;
    }
    roster = [];
    self.code = null;
  }

  function send(t, payload) {
    if (!channel) return;
    channel.send({
      type: "broadcast",
      event: "msg",
      payload: Object.assign({ t, from: self.myId }, payload),
    });
  }

  function players() { return roster; }
  function isHost() { return roster.length > 0 && roster[0].id === self.myId; }

  return {
    state: self,
    init, join, leave, send, players, isHost, makeCode,
    onEvent(cb) { if (!evCbs.includes(cb)) evCbs.push(cb); },
    onPlayers(cb) { if (!playersCbs.includes(cb)) playersCbs.push(cb); },
  };
})();
