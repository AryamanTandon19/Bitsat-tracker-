// Paint studio bottom sheet: pick a body part, tap palette colours, and
// doodle freehand on the torso. Works on yourself AND on decoy mannequins
// (tap one during Hide & Pose) — camouflage by paint, chameleon-style.
window.PAINT = (function () {
  let panel, partsEl, palEl, doodleCanvas, dctx, titleEl;
  let paint = null, part = "torso", brush = "#ff5d6c";
  let onChange = null, onDone = null, onCopy = null;
  let open = false, targetLabel = "";
  let drawing = false, last = null;

  const PART_LABELS = {
    torso: "Body", head: "Head", armL: "L Arm", armR: "R Arm", legL: "L Leg", legR: "R Leg",
  };

  function fire() { if (onChange) onChange(paint); }

  function renderChips() {
    partsEl.innerHTML = "";
    for (const k in PART_LABELS) {
      const b = document.createElement("button");
      b.className = "btn paint-chip" + (part === k ? " sel" : "");
      b.textContent = PART_LABELS[k];
      b.style.borderBottom = "4px solid " + paint[k];
      b.addEventListener("click", () => { part = k; renderChips(); });
      partsEl.appendChild(b);
    }
  }

  function renderPalette() {
    palEl.innerHTML = "";
    for (const c of FIG3D.PALETTE) {
      const d = document.createElement("div");
      d.className = "color-dot pal-dot" + (c === brush ? " sel" : "");
      d.style.background = c;
      d.addEventListener("click", () => {
        brush = c;
        paint[part] = c;
        if (part === "torso") redrawDoodleBase();
        renderChips(); renderPalette();
        fire();
      });
      palEl.appendChild(d);
    }
  }

  function redrawDoodleBase() {
    dctx.fillStyle = paint.torso;
    dctx.fillRect(0, 0, 128, 128);
    if (paint.doodle) {
      const img = new Image();
      img.onload = () => dctx.drawImage(img, 0, 0, 128, 128);
      img.src = paint.doodle;
    }
  }

  function saveDoodle() {
    // store only the strokes over the base colour (whole canvas is fine)
    paint.doodle = doodleCanvas.toDataURL("image/png");
    fire();
  }

  function pos(e) {
    const r = doodleCanvas.getBoundingClientRect();
    return {
      x: ((e.clientX - r.left) / r.width) * 128,
      y: ((e.clientY - r.top) / r.height) * 128,
    };
  }

  function init(opts) {
    onChange = opts.onChange;
    onDone = opts.onDone;
    onCopy = opts.onCopy;
    panel = document.getElementById("paint-panel");
    partsEl = document.getElementById("paint-parts");
    palEl = document.getElementById("paint-palette");
    titleEl = document.getElementById("paint-title");
    doodleCanvas = document.getElementById("paint-doodle");
    dctx = doodleCanvas.getContext("2d");

    doodleCanvas.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      doodleCanvas.setPointerCapture(e.pointerId);
      drawing = true;
      last = pos(e);
    });
    doodleCanvas.addEventListener("pointermove", (e) => {
      if (!drawing) return;
      const p = pos(e);
      dctx.strokeStyle = brush;
      dctx.lineWidth = 7;
      dctx.lineCap = "round";
      dctx.beginPath();
      dctx.moveTo(last.x, last.y);
      dctx.lineTo(p.x, p.y);
      dctx.stroke();
      last = p;
    });
    const stop = () => { if (drawing) { drawing = false; saveDoodle(); } };
    doodleCanvas.addEventListener("pointerup", stop);
    doodleCanvas.addEventListener("pointercancel", stop);

    document.getElementById("btn-paint-clear").addEventListener("click", () => {
      paint.doodle = null;
      redrawDoodleBase();
      fire();
    });
    document.getElementById("btn-paint-random").addEventListener("click", () => {
      const d = paint.doodle;
      paint = Object.assign(paint, FIG3D.randomPaint());
      paint.doodle = d;
      redrawDoodleBase(); renderChips();
      fire();
    });
    document.getElementById("btn-paint-copy").addEventListener("click", () => {
      if (onCopy) onCopy();
    });
    document.getElementById("btn-paint-done").addEventListener("click", () => {
      close();
      if (onDone) onDone();
    });
  }

  // target: {label, paint, canCopy} — edits the paint object via onChange
  function show(opts) {
    paint = opts.paint;
    targetLabel = opts.label || "yourself";
    titleEl.textContent = "🎨 Painting " + targetLabel;
    document.getElementById("btn-paint-copy").classList.toggle("hidden", !opts.canCopy);
    open = true;
    panel.classList.remove("hidden");
    redrawDoodleBase();
    renderChips();
    renderPalette();
  }

  function close() {
    open = false;
    panel.classList.add("hidden");
  }

  return { init, show, close, isOpen: () => open };
})();
