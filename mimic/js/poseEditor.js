// Bottom-sheet pose editor: drag circle handles on your figure; hands and
// feet use 2-bone IK so limbs follow naturally, like posing an action figure.
window.POSE_EDITOR = (function () {
  let panel, canvas, ctx, presetsEl;
  let pose = null, color = "#7c5cff";
  let onChange = null, onDone = null;
  let dragging = null;
  let open = false;

  const HANDLES = ["headC", "chest", "handL", "handR", "footL", "footR"];

  function metrics() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    return { w, h, cx: w * 0.5, floorY: h * 0.9, scale: (h * 0.9) / (FIG.L.TOP + 18) };
  }

  function toScreen(j, m) { return { x: m.cx + j.x * m.scale, y: m.floorY + j.y * m.scale }; }
  function toLocal(sx, sy, m) { return { x: (sx - m.cx) / m.scale, y: (sy - m.floorY) / m.scale }; }

  function drawPanel() {
    if (!open) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const m = metrics();

    // floor + pedestal
    ctx.fillStyle = "#1d1830";
    ctx.fillRect(0, m.floorY, w, h - m.floorY);
    ctx.fillStyle = "#3a3455";
    ctx.fillRect(m.cx - 46, m.floorY - 6, 92, 8);

    FIG.draw(ctx, m.cx, m.floorY - 6, { scale: m.scale, dir: 1, pose, style: "color", color });

    // handles
    const J = FIG.joints(pose);
    for (const hname of HANDLES) {
      const p = toScreen(J[hname], m);
      ctx.beginPath();
      ctx.arc(p.x, p.y, 11, 0, Math.PI * 2);
      ctx.strokeStyle = dragging === hname ? "#ffb43a" : "rgba(255,255,255,.85)";
      ctx.lineWidth = 2.5;
      ctx.stroke();
      ctx.fillStyle = dragging === hname ? "rgba(255,180,58,.25)" : "rgba(255,255,255,.08)";
      ctx.fill();
    }
  }

  function pickHandle(sx, sy) {
    const m = metrics();
    const J = FIG.joints(pose);
    let best = null, bd = 26;
    for (const hname of HANDLES) {
      const p = toScreen(J[hname], m);
      const d = Math.hypot(p.x - sx, p.y - sy);
      if (d < bd) { bd = d; best = hname; }
    }
    return best;
  }

  function applyDrag(hname, sx, sy) {
    const m = metrics();
    const t = toLocal(sx, sy, m);
    const J = FIG.joints(pose);
    // joints() shifts the body so feet touch the floor; undo that shift so
    // IK math happens in unshifted root space.
    const rawHipY = FIG.L.HIP;
    const shift = J.hip.y - rawHipY;
    t.y -= shift;
    const chest = { x: J.chest.x, y: J.chest.y - shift };
    const hip = { x: 0, y: rawHipY };

    if (hname === "chest") {
      pose.torso = MU.clamp(
        Math.atan2(t.y - hip.y, t.x - hip.x) + Math.PI / 2,
        -1.0, 1.0
      );
    } else if (hname === "headC") {
      const tDir = -Math.PI / 2 + pose.torso;
      pose.head = MU.clamp(Math.atan2(t.y - chest.y, t.x - chest.x) - tDir, -1.2, 1.2);
    } else if (hname === "handL" || hname === "handR") {
      const bend = t.x >= chest.x ? -1 : 1; // elbow trails the reach
      const [a1, a2] = FIG.ik(chest, t, FIG.L.A1, FIG.L.A2, bend);
      if (hname === "handL") { pose.aL1 = a1; pose.aL2 = a2; }
      else { pose.aR1 = a1; pose.aR2 = a2; }
    } else if (hname === "footL" || hname === "footR") {
      const [a1, a2] = FIG.ik(hip, t, FIG.L.L1, FIG.L.L2, 1);
      if (hname === "footL") { pose.lL1 = a1; pose.lL2 = a2; }
      else { pose.lR1 = a1; pose.lR2 = a2; }
    }
    if (onChange) onChange(pose);
  }

  function pointerPos(e) {
    const r = canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  function setPose(p) {
    pose = FIG.clone(p);
    if (onChange) onChange(pose);
    drawPanel();
  }

  function init(opts) {
    panel = document.getElementById("pose-panel");
    canvas = document.getElementById("pose-canvas");
    ctx = canvas.getContext("2d");
    presetsEl = document.getElementById("pose-presets");
    onChange = opts.onChange;
    onDone = opts.onDone;

    for (const name of FIG.PRESET_NAMES) {
      const b = document.createElement("button");
      b.className = "btn";
      b.textContent = name;
      b.addEventListener("click", () => setPose(FIG.PRESETS[name]));
      presetsEl.appendChild(b);
    }
    document.getElementById("btn-pose-mirror").addEventListener("click", () => setPose(FIG.mirror(pose)));
    document.getElementById("btn-pose-random").addEventListener("click", () => setPose(FIG.randomPose()));
    document.getElementById("btn-pose-reset").addEventListener("click", () => setPose(FIG.defaultPose()));
    document.getElementById("btn-pose-done").addEventListener("click", () => {
      close();
      if (onDone) onDone();
    });

    canvas.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      canvas.setPointerCapture(e.pointerId);
      const [x, y] = pointerPos(e);
      dragging = pickHandle(x, y);
      if (dragging) applyDrag(dragging, x, y);
      drawPanel();
    });
    canvas.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      e.preventDefault();
      const [x, y] = pointerPos(e);
      applyDrag(dragging, x, y);
      drawPanel();
    });
    const stop = () => { dragging = null; drawPanel(); };
    canvas.addEventListener("pointerup", stop);
    canvas.addEventListener("pointercancel", stop);
  }

  function show(p, c) {
    color = c || color;
    pose = FIG.clone(p);
    open = true;
    panel.classList.remove("hidden");
    drawPanel();
  }

  function close() {
    open = false;
    panel.classList.add("hidden");
  }

  return {
    init, show, close,
    isOpen: () => open,
    getPose: () => pose,
    redraw: drawPanel,
  };
})();
