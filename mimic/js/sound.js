// Procedural WebAudio: UI blips, movement foley and ambient soundscapes.
// No audio assets — everything is synthesized.
window.SND = (function () {
  let ctx = null;
  let ambientTimer = null, ambientMode = null;

  function ac() {
    if (!ctx) {
      try { ctx = new (window.AudioContext || window.webkitAudioContext)(); }
      catch (e) { return null; }
    }
    if (ctx.state === "suspended") ctx.resume();
    return ctx;
  }

  function tone(freq, dur, type, vol, slide, delay) {
    const a = ac();
    if (!a) return;
    const t0 = a.currentTime + (delay || 0);
    const o = a.createOscillator();
    const g = a.createGain();
    o.type = type || "sine";
    o.frequency.setValueAtTime(freq, t0);
    if (slide) o.frequency.exponentialRampToValueAtTime(slide, t0 + dur);
    g.gain.setValueAtTime(vol || 0.08, t0);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.connect(g).connect(a.destination);
    o.start(t0);
    o.stop(t0 + dur);
  }

  // short filtered-noise burst (steps, thuds, whooshes)
  function noise(dur, vol, freq, q, slideTo) {
    const a = ac();
    if (!a) return;
    const n = Math.floor(a.sampleRate * dur);
    const buf = a.createBuffer(1, n, a.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < n; i++) d[i] = Math.random() * 2 - 1;
    const src = a.createBufferSource();
    src.buffer = buf;
    const f = a.createBiquadFilter();
    f.type = "bandpass";
    f.frequency.setValueAtTime(freq, a.currentTime);
    if (slideTo) f.frequency.exponentialRampToValueAtTime(slideTo, a.currentTime + dur);
    f.Q.value = q || 1;
    const g = a.createGain();
    g.gain.setValueAtTime(vol, a.currentTime);
    g.gain.exponentialRampToValueAtTime(0.0001, a.currentTime + dur);
    src.connect(f).connect(g).connect(a.destination);
    src.start();
  }

  function chirp() {
    // a little 2-3 note bird melody
    const base = 2200 + Math.random() * 1400;
    const notes = 2 + (Math.random() * 2 | 0);
    for (let i = 0; i < notes; i++) {
      tone(base * (1 + Math.random() * 0.3), 0.09, "sine", 0.022, base * 1.4, i * 0.12 + Math.random() * 0.04);
    }
  }
  function cricket() {
    for (let i = 0; i < 3; i++) tone(4200 + Math.random() * 400, 0.03, "sine", 0.012, null, i * 0.07);
  }

  // ---- lightweight generative music ----
  let musicTimer = null, musicMode = null, beat = 0;
  const PENTA = [220, 261.6, 293.7, 329.6, 392, 440, 523.3];
  function setMusic(mode) {
    if (mode === musicMode) return;
    musicMode = mode;
    beat = 0;
    if (musicTimer) { clearInterval(musicTimer); musicTimer = null; }
    if (!mode) return;
    musicTimer = setInterval(() => {
      if (!ctx || ctx.state !== "running" || document.hidden) return;
      beat++;
      if (musicMode === "setup") {
        // relaxed marimba-ish plucks
        if (beat % 2 === 0 && Math.random() < 0.75) {
          tone(PENTA[(Math.random() * PENTA.length) | 0], 0.28, "triangle", 0.028);
        }
        if (beat % 8 === 0) tone(110, 0.6, "sine", 0.03);
      } else if (musicMode === "hunt") {
        // tense low pulse
        if (beat % 4 === 0) tone(55, 0.3, "sine", 0.05, 50);
        if (beat % 4 === 2) tone(58.3, 0.22, "sine", 0.035);
        if (beat % 16 === 7) tone(466, 0.5, "sine", 0.018, 440);
      }
    }, 240);
  }

  function setAmbient(mode) {
    if (mode === ambientMode) return;
    ambientMode = mode;
    if (ambientTimer) { clearInterval(ambientTimer); ambientTimer = null; }
    if (!mode) return;
    ambientTimer = setInterval(() => {
      if (!ctx || ctx.state !== "running" || document.hidden) return;
      if (ambientMode === "day") { if (Math.random() < 0.55) chirp(); }
      else if (ambientMode === "dusk") { if (Math.random() < 0.8) cricket(); }
    }, ambientMode === "day" ? 2600 : 1300);
  }

  return {
    unlock() { ac(); },
    // UI / events
    orb()     { tone(880, 0.15, "sine", 0.09, 1320); },
    wrong()   { tone(160, 0.3, "sawtooth", 0.07, 90); },
    catch_()  { tone(520, 0.12, "square", 0.08); setTimeout(() => tone(700, 0.18, "square", 0.08), 110); },
    spotted() { tone(1100, 0.09, "triangle", 0.07); },
    phase()   { tone(440, 0.12, "sine", 0.08); setTimeout(() => tone(660, 0.18, "sine", 0.08), 120); },
    win()     { [523, 659, 784, 1047].forEach((f, i) => setTimeout(() => tone(f, 0.22, "triangle", 0.09), i * 140)); },
    click()   { noise(0.05, 0.12, 2400, 4); },
    tick()    { tone(1500, 0.05, "square", 0.045); },
    // movement foley
    step()    { noise(0.07, 0.055, 300 + Math.random() * 150, 1.2); },
    whoosh()  { noise(0.22, 0.07, 500, 0.8, 1600); },
    thud()    { noise(0.12, 0.1, 160, 1); tone(90, 0.12, "sine", 0.08, 55); },
    heart()   { tone(58, 0.12, "sine", 0.14, 42); setTimeout(() => tone(52, 0.14, "sine", 0.11, 38), 160); },
    throw_()  { noise(0.16, 0.06, 900, 0.9, 300); },
    pop()     { tone(720, 0.07, "square", 0.06, 900); },
    // ambient soundscape + music
    setAmbient, setMusic,
  };
})();
