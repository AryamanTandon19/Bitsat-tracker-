// Input for the 3D game: WASD/arrows + a virtual joystick for movement,
// horizontal drag on the canvas to orbit the camera, short press = tap.
window.INPUT = (function () {
  const keys = {};
  let stick = { x: 0, y: 0, active: false, pid: null, cx: 0, cy: 0 };
  let tapCb = null, orbitCb = null;

  let jumpQueued = false;

  window.addEventListener("keydown", (e) => {
    const k = e.key.toLowerCase();
    if (k === " " || k.startsWith("arrow")) {
      if (e.target === document.body) e.preventDefault(); // don't scroll the page
    }
    if (k === " " && !keys[" "]) jumpQueued = true;
    keys[k] = true;
  });
  window.addEventListener("keyup", (e) => { keys[e.key.toLowerCase()] = false; });
  window.addEventListener("blur", () => { for (const k in keys) keys[k] = false; });

  function initJoystick() {
    const zone = document.getElementById("joystick");
    const knob = document.getElementById("joystick-knob");
    const R = 46;
    function setKnob() {
      knob.style.transform = "translate(" + stick.x * R + "px," + stick.y * R + "px)";
    }
    zone.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      SND.unlock();
      zone.setPointerCapture(e.pointerId);
      const r = zone.getBoundingClientRect();
      stick.active = true; stick.pid = e.pointerId;
      stick.cx = r.left + r.width / 2; stick.cy = r.top + r.height / 2;
      move(e);
    });
    function move(e) {
      if (!stick.active || e.pointerId !== stick.pid) return;
      let dx = (e.clientX - stick.cx) / R, dy = (e.clientY - stick.cy) / R;
      const d = Math.hypot(dx, dy);
      if (d > 1) { dx /= d; dy /= d; }
      stick.x = dx; stick.y = dy;
      setKnob();
    }
    zone.addEventListener("pointermove", move);
    const end = (e) => {
      if (e.pointerId !== stick.pid) return;
      stick.active = false; stick.x = stick.y = 0;
      setKnob();
    };
    zone.addEventListener("pointerup", end);
    zone.addEventListener("pointercancel", end);
  }

  function initCanvas(canvas) {
    let down = null, dragging = false;
    canvas.addEventListener("pointerdown", (e) => {
      SND.unlock();
      down = { x: e.clientX, y: e.clientY, t: performance.now(), id: e.pointerId };
      dragging = false;
      canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener("pointermove", (e) => {
      if (!down || e.pointerId !== down.id) return;
      const dx = e.clientX - down.x, dy = e.clientY - down.y;
      if (!dragging && Math.hypot(dx, dy) > 9) dragging = true;
      if (dragging && orbitCb) {
        orbitCb(e.movementX != null ? e.movementX : dx, e.movementY || 0);
      }
    });
    canvas.addEventListener("pointerup", (e) => {
      if (!down || e.pointerId !== down.id) return;
      const quick = performance.now() - down.t < 450;
      if (!dragging && quick && tapCb) {
        const r = canvas.getBoundingClientRect();
        tapCb(e.clientX - r.left, e.clientY - r.top);
      }
      down = null;
    });
    canvas.addEventListener("pointercancel", () => { down = null; });
  }

  function init(canvas) {
    initJoystick();
    initCanvas(canvas);
    const jb = document.getElementById("btn-jump");
    if (jb) jb.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      SND.unlock();
      jumpQueued = true;
    });
  }

  // movement vector in screen space: x = strafe right, y = forward
  function vec() {
    let x = stick.x, y = -stick.y;
    if (keys["a"] || keys["arrowleft"]) x -= 1;
    if (keys["d"] || keys["arrowright"]) x += 1;
    if (keys["w"] || keys["arrowup"]) y += 1;
    if (keys["s"] || keys["arrowdown"]) y -= 1;
    const d = Math.hypot(x, y);
    if (d > 1) { x /= d; y /= d; }
    return { x, y };
  }

  // camera orbit via q/e keys too
  function orbitKeys() {
    let o = 0;
    if (keys["q"]) o -= 1;
    if (keys["e"]) o += 1;
    return o;
  }

  return {
    init, vec, orbitKeys,
    jump() { const j = jumpQueued; jumpQueued = false; return j; },
    onTap(cb) { tapCb = cb; },
    onOrbit(cb) { orbitCb = cb; },
  };
})();
