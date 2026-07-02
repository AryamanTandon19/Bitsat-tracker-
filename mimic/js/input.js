// Keyboard (A/D, arrows) + on-screen hold buttons + canvas taps.
window.INPUT = (function () {
  const keys = {};
  let btnL = false, btnR = false;
  let tapCb = null;

  window.addEventListener("keydown", (e) => {
    keys[e.key.toLowerCase()] = true;
  });
  window.addEventListener("keyup", (e) => {
    keys[e.key.toLowerCase()] = false;
  });
  window.addEventListener("blur", () => {
    for (const k in keys) keys[k] = false;
  });

  function hold(el, set) {
    const on = (e) => { e.preventDefault(); set(true); SND.unlock(); };
    const off = (e) => { e.preventDefault(); set(false); };
    el.addEventListener("pointerdown", on);
    el.addEventListener("pointerup", off);
    el.addEventListener("pointerleave", off);
    el.addEventListener("pointercancel", off);
  }

  function init(canvas) {
    hold(document.getElementById("btn-left"), (v) => (btnL = v));
    hold(document.getElementById("btn-right"), (v) => (btnR = v));
    canvas.addEventListener("pointerdown", (e) => {
      SND.unlock();
      if (tapCb) {
        const r = canvas.getBoundingClientRect();
        tapCb(e.clientX - r.left, e.clientY - r.top);
      }
    });
  }

  function move() {
    let m = 0;
    if (keys["a"] || keys["arrowleft"] || btnL) m -= 1;
    if (keys["d"] || keys["arrowright"] || btnR) m += 1;
    return m;
  }

  return {
    init, move,
    onTap(cb) { tapCb = cb; },
  };
})();
