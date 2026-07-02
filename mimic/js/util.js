window.MU = (function () {
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function rand(a, b) { return a + Math.random() * (b - a); }
  function randInt(a, b) { return Math.floor(rand(a, b + 1)); }
  function choice(arr) { return arr[Math.floor(Math.random() * arr.length)]; }
  function uid() {
    return "p" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  }
  // deterministic PRNG so every client draws the same background
  function seeded(seed) {
    let s = seed >>> 0;
    return function () {
      s = (s * 1664525 + 1013904223) >>> 0;
      return s / 4294967296;
    };
  }
  function fmtTime(ms) {
    const s = Math.max(0, Math.ceil(ms / 1000));
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }
  return { clamp, lerp, rand, randInt, choice, uid, seeded, fmtTime };
})();
