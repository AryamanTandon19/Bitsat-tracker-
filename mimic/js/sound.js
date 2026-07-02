// Tiny WebAudio blips — no assets needed.
window.SND = (function () {
  let ctx = null;
  function ac() {
    if (!ctx) {
      try { ctx = new (window.AudioContext || window.webkitAudioContext)(); }
      catch (e) { return null; }
    }
    if (ctx.state === "suspended") ctx.resume();
    return ctx;
  }
  function tone(freq, dur, type, vol, slide) {
    const a = ac();
    if (!a) return;
    const o = a.createOscillator();
    const g = a.createGain();
    o.type = type || "sine";
    o.frequency.value = freq;
    if (slide) o.frequency.exponentialRampToValueAtTime(slide, a.currentTime + dur);
    g.gain.value = vol || 0.08;
    g.gain.exponentialRampToValueAtTime(0.0001, a.currentTime + dur);
    o.connect(g).connect(a.destination);
    o.start();
    o.stop(a.currentTime + dur);
  }
  return {
    unlock() { ac(); },
    orb()    { tone(880, 0.15, "sine", 0.09, 1320); },
    wrong()  { tone(160, 0.3, "sawtooth", 0.07, 90); },
    catch_() { tone(520, 0.12, "square", 0.08); setTimeout(() => tone(700, 0.18, "square", 0.08), 110); },
    spotted(){ tone(1100, 0.09, "triangle", 0.07); },
    phase()  { tone(440, 0.12, "sine", 0.08); setTimeout(() => tone(660, 0.18, "sine", 0.08), 120); },
    win()    { [523, 659, 784, 1047].forEach((f, i) => setTimeout(() => tone(f, 0.22, "triangle", 0.09), i * 140)); },
  };
})();
