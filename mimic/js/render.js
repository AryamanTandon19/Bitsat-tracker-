// Canvas renderer: a cosy museum gallery. The game hands over a scene
// description each frame; this module owns all pixels.
window.RENDER = (function () {
  const VIEW_H = 430; // world units visible vertically
  let canvas, ctx;
  let m = { w: 0, h: 0, zoom: 1, floorY: 0, camX: 0 };
  let bgCache = null; // seeded painting layout

  const PAINT_COLORS = ["#b0543c", "#3c6e91", "#7a8a4f", "#8a5a8f", "#b08d3c", "#4f8a7a"];

  function init(c) {
    canvas = c;
    ctx = c.getContext("2d");
  }

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    m.w = w; m.h = h;
    m.floorY = h * 0.78;
    m.zoom = h / VIEW_H;
  }

  function wx(x) { return (x - m.camX) * m.zoom + m.w / 2; }
  function sx2wx(sx) { return (sx - m.w / 2) / m.zoom + m.camX; }

  function buildBg(seed, worldW) {
    const rng = MU.seeded(seed);
    const paintings = [];
    let x = 90;
    while (x < worldW - 120) {
      const pw = 60 + rng() * 90, ph = 55 + rng() * 70;
      paintings.push({ x, w: pw, h: ph, c: PAINT_COLORS[(rng() * PAINT_COLORS.length) | 0], s: rng() });
      x += pw + 60 + rng() * 160;
    }
    const boards = [];
    for (let bx = 0; bx < worldW; bx += 70) boards.push(bx);
    bgCache = { seed, worldW, paintings, boards };
  }

  function drawBg(seed, worldW, time) {
    if (!bgCache || bgCache.seed !== seed || bgCache.worldW !== worldW) buildBg(seed, worldW);

    // wall
    const wallG = ctx.createLinearGradient(0, 0, 0, m.floorY);
    wallG.addColorStop(0, "#241f36");
    wallG.addColorStop(1, "#39304f");
    ctx.fillStyle = wallG;
    ctx.fillRect(0, 0, m.w, m.floorY);

    // paintings
    for (const p of bgCache.paintings) {
      const px = wx(p.x);
      const pw = p.w * m.zoom;
      if (px + pw < -50 || px > m.w + 50) continue;
      const ph = p.h * m.zoom;
      const py = m.floorY - (200 + p.s * 60) * m.zoom;
      ctx.fillStyle = "#171226";
      ctx.fillRect(px - 4, py - 4, pw + 8, ph + 8);
      ctx.fillStyle = p.c;
      ctx.fillRect(px, py, pw, ph);
      ctx.fillStyle = "rgba(255,255,255,.14)";
      ctx.beginPath();
      ctx.arc(px + pw * (0.3 + p.s * 0.4), py + ph * 0.4, Math.min(pw, ph) * 0.25, 0, Math.PI * 2);
      ctx.fill();
    }

    // dado rail
    ctx.fillStyle = "#4a3f68";
    ctx.fillRect(0, m.floorY - 90 * m.zoom, m.w, 6 * m.zoom);

    // floor
    const floorG = ctx.createLinearGradient(0, m.floorY, 0, m.h);
    floorG.addColorStop(0, "#4d3b56");
    floorG.addColorStop(1, "#241b2b");
    ctx.fillStyle = floorG;
    ctx.fillRect(0, m.floorY, m.w, m.h - m.floorY);
    ctx.strokeStyle = "rgba(0,0,0,.25)";
    ctx.lineWidth = 1;
    for (const bx of bgCache.boards) {
      const px = wx(bx);
      if (px < -10 || px > m.w + 10) continue;
      ctx.beginPath();
      ctx.moveTo(px, m.floorY);
      ctx.lineTo(px - 30, m.h);
      ctx.stroke();
    }
  }

  function drawCone(cone) {
    if (!cone) return;
    const ex = wx(cone.x);
    const ey = m.floorY - 108 * m.zoom;
    const tip = wx(cone.x + cone.dir * cone.range);
    const g = ctx.createLinearGradient(ex, 0, tip, 0);
    g.addColorStop(0, "rgba(255,224,130,.34)");
    g.addColorStop(1, "rgba(255,224,130,.03)");
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.moveTo(ex, ey);
    ctx.lineTo(tip, m.floorY - 175 * m.zoom);
    ctx.lineTo(tip, m.floorY + 14 * m.zoom);
    ctx.closePath();
    ctx.fill();
  }

  function drawOrb(o, time) {
    if (o.taken) return;
    const px = wx(o.x);
    if (px < -40 || px > m.w + 40) return;
    const bob = Math.sin(time * 3 + o.x) * 5 * m.zoom;
    const py = m.floorY - 26 * m.zoom + bob;
    const r = 9 * m.zoom;
    const g = ctx.createRadialGradient(px, py, 1, px, py, r * 2.6);
    g.addColorStop(0, "rgba(120,255,214,.95)");
    g.addColorStop(1, "rgba(120,255,214,0)");
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(px, py, r * 2.6, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#aefce4";
    ctx.beginPath();
    ctx.arc(px, py, r * 0.55, 0, Math.PI * 2);
    ctx.fill();
  }

  function drawPedestal(px, w) {
    ctx.fillStyle = "rgba(0,0,0,.35)";
    ctx.beginPath();
    ctx.ellipse(px, m.floorY + 6 * m.zoom, w * 0.72, 5 * m.zoom, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#514668";
    ctx.fillRect(px - w / 2, m.floorY - 7 * m.zoom, w, 9 * m.zoom);
    ctx.fillStyle = "#665a82";
    ctx.fillRect(px - w / 2 - 3, m.floorY - 9 * m.zoom, w + 6, 4 * m.zoom);
  }

  function drawHat(px, floorY, fig, scale, dir, color) {
    const J = fig; // joints returned by FIG.draw
    const hx = px + J.headC.x * scale * dir;
    const hy = floorY + J.headC.y * scale;
    ctx.save();
    ctx.translate(hx, hy);
    ctx.fillStyle = color || "#2c2542";
    ctx.fillRect(-14 * scale, -14 * scale, 28 * scale, 4 * scale);
    ctx.fillRect(-9 * scale, -26 * scale, 18 * scale, 13 * scale);
    ctx.restore();
  }

  function drawLabel(text, px, y, color) {
    ctx.font = "700 13px 'Trebuchet MS', sans-serif";
    ctx.textAlign = "center";
    const w = ctx.measureText(text).width + 14;
    ctx.fillStyle = "rgba(15,12,24,.75)";
    ctx.beginPath();
    ctx.roundRect(px - w / 2, y - 15, w, 20, 8);
    ctx.fill();
    ctx.fillStyle = color || "#fff";
    ctx.fillText(text, px, y);
  }

  // scene: { camX, worldW, seed, time, cone, orbs, figures }
  // figure: { x, dir, pose, style, color, alpha, outline, fallen, cleared,
  //           label, labelColor, hat, noPedestal }
  function frame(scene) {
    resize();
    m.camX = MU.clamp(scene.camX, m.w / 2 / m.zoom, scene.worldW - m.w / 2 / m.zoom);
    if (scene.worldW < m.w / m.zoom) m.camX = scene.worldW / 2;

    drawBg(scene.seed, scene.worldW, scene.time);
    drawCone(scene.cone);
    for (const o of scene.orbs || []) drawOrb(o, scene.time);

    // draw figures back-to-front by x jitter irrelevant; just draw in order
    for (const f of scene.figures) {
      const px = wx(f.x);
      if (px < -80 || px > m.w + 80) continue;
      if (!f.noPedestal) drawPedestal(px, 60 * m.zoom);
      const J = FIG.draw(ctx, px, m.floorY - 7 * m.zoom, {
        scale: m.zoom, dir: f.dir, pose: f.pose, style: f.style,
        color: f.color, alpha: f.alpha, outline: f.outline,
        fallen: f.fallen, cleared: f.cleared,
      });
      if (f.hat && !f.fallen) drawHat(px, m.floorY - 7 * m.zoom, J, m.zoom, f.dir, f.hatColor);
      if (f.label) drawLabel(f.label, px, m.floorY - (FIG.L.TOP + 26) * m.zoom, f.labelColor);
    }

    // vignette
    const v = ctx.createRadialGradient(m.w / 2, m.h / 2, m.h * 0.4, m.w / 2, m.h / 2, m.h);
    v.addColorStop(0, "rgba(0,0,0,0)");
    v.addColorStop(1, "rgba(0,0,0,.42)");
    ctx.fillStyle = v;
    ctx.fillRect(0, 0, m.w, m.h);
  }

  return {
    init, frame,
    metrics: () => m,
    screenToWorld: sx2wx,
    floorY: () => m.floorY,
    zoom: () => m.zoom,
  };
})();
