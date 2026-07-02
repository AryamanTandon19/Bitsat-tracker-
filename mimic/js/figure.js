// Ragdoll stick-figure model. Poses are absolute segment angles in a
// face-right local space: 0 = forward (+x), PI/2 = down, -PI/2 = up.
// The figure root (hip) sits above the floor; the whole body auto-drops
// so the lowest foot always touches the pedestal (bent legs = crouch).
window.FIG = (function () {
  const L = {
    TORSO: 42, NECK: 15, HEAD_R: 11,
    A1: 25, A2: 23,      // upper arm / forearm
    L1: 30, L2: 28,      // thigh / shin
    HIP: -58,            // hip height above floor when standing
    W: 9,                // limb thickness
    TOP: 150,            // rough total height, for hit boxes
  };
  const D = Math.PI / 180;

  function P(torso, head, aL1, aL2, aR1, aR2, lL1, lL2, lR1, lR2) {
    return {
      torso: torso * D, head: head * D,
      aL1: aL1 * D, aL2: aL2 * D, aR1: aR1 * D, aR2: aR2 * D,
      lL1: lL1 * D, lL2: lL2 * D, lR1: lR1 * D, lR2: lR2 * D,
    };
  }

  const PRESETS = {
    Attention: P(0, 0, 100, 95, 80, 85, 97, 95, 83, 85),
    "T-Pose":  P(0, 0, 180, 180, 0, 0, 95, 93, 85, 87),
    Wave:      P(0, 8, 100, 95, -50, -100, 95, 93, 85, 87),
    Point:     P(5, 0, 100, 100, 0, -5, 85, 88, 95, 92),
    Thinker:   P(25, 30, 110, 100, 45, -60, 70, 110, 105, 80),
    Hero:      P(-6, -5, 140, 60, 40, 120, 110, 95, 70, 85),
    Ballet:    P(-4, -8, -135, -110, -45, -70, 90, 90, 20, 60),
    Dab:       P(12, 40, -120, -170, -45, -45, 80, 95, 100, 85),
    Zombie:    P(8, 15, 5, 0, -5, 5, 88, 92, 84, 92),
    Flamingo:  P(0, -4, 180, 175, 0, 5, 130, 25, 90, 90),
  };
  const PRESET_NAMES = Object.keys(PRESETS);

  function clone(p) { return Object.assign({}, p); }
  function defaultPose() { return clone(PRESETS.Attention); }

  function randomPose(rng) {
    rng = rng || Math.random;
    const base = PRESETS[PRESET_NAMES[Math.floor(rng() * PRESET_NAMES.length)]];
    const p = clone(base);
    const j = 8 * D;
    for (const k in p) p[k] += (rng() * 2 - 1) * (k === "torso" ? j * 0.5 : j);
    return p;
  }

  function mirror(p) {
    const m = (a) => Math.PI - a;
    return {
      torso: -p.torso, head: -p.head,
      aL1: m(p.aR1), aL2: m(p.aR2), aR1: m(p.aL1), aR2: m(p.aL2),
      lL1: m(p.lR1), lL2: m(p.lR2), lR1: m(p.lL1), lR2: m(p.lL2),
    };
  }

  function unit(a) { return { x: Math.cos(a), y: Math.sin(a) }; }
  function add(p, u, len) { return { x: p.x + u.x * len, y: p.y + u.y * len }; }

  // Compute joint positions in face-right local coords (hip root, y down).
  function joints(p) {
    const hip = { x: 0, y: L.HIP };
    const tDir = -Math.PI / 2 + p.torso;
    const chest = add(hip, unit(tDir), L.TORSO);
    const hDir = tDir + p.head;
    const headC = add(chest, unit(hDir), L.NECK);
    const elbL = add(chest, unit(p.aL1), L.A1);
    const handL = add(elbL, unit(p.aL2), L.A2);
    const elbR = add(chest, unit(p.aR1), L.A1);
    const handR = add(elbR, unit(p.aR2), L.A2);
    const kneeL = add(hip, unit(p.lL1), L.L1);
    const footL = add(kneeL, unit(p.lL2), L.L2);
    const kneeR = add(hip, unit(p.lR1), L.L1);
    const footR = add(kneeR, unit(p.lR2), L.L2);
    // drop the body so the lowest foot touches the floor (y = 0)
    const dy = -Math.max(footL.y, footR.y);
    const all = { hip, chest, headC, elbL, handL, elbR, handR, kneeL, footL, kneeR, footR };
    for (const k in all) all[k] = { x: all[k].x, y: all[k].y + dy };
    return all;
  }

  // 2-bone IK: absolute angles so the chain from `origin` reaches `target`.
  function ik(origin, target, l1, l2, bend) {
    let dx = target.x - origin.x, dy = target.y - origin.y;
    let d = Math.hypot(dx, dy);
    d = Math.min(Math.max(d, 2), l1 + l2 - 0.5);
    const base = Math.atan2(dy, dx);
    const cosA = (l1 * l1 + d * d - l2 * l2) / (2 * l1 * d);
    const a1 = base - bend * Math.acos(Math.max(-1, Math.min(1, cosA)));
    const mid = add(origin, unit(a1), l1);
    const a2 = Math.atan2(target.y - mid.y, target.x - mid.x);
    return [a1, a2];
  }

  function shade(hex, f) {
    const n = parseInt(hex.slice(1), 16);
    const r = Math.min(255, ((n >> 16) & 255) * f) | 0;
    const g = Math.min(255, ((n >> 8) & 255) * f) | 0;
    const b = Math.min(255, (n & 255) * f) | 0;
    return "rgb(" + r + "," + g + "," + b + ")";
  }

  // Draw a figure standing at world-screen position (sx, floorY).
  // opts: { scale, dir, pose, style:'mannequin'|'color', color, alpha,
  //         outline, fallen, cleared }
  function draw(ctx, sx, floorY, opts) {
    const s = opts.scale || 1;
    const dir = opts.dir || 1;
    const p = opts.pose || defaultPose();
    const J = joints(p);

    ctx.save();
    ctx.translate(sx, floorY);
    if (opts.fallen) {
      ctx.rotate(dir * Math.PI * 0.47);
      ctx.translate(0, -4 * s);
    }
    ctx.scale(s * dir, s);
    ctx.globalAlpha = opts.alpha != null ? opts.alpha : 1;

    const mann = opts.style !== "color";
    const cMain = mann ? "#e9e4d6" : opts.color || "#7c5cff";
    const cBack = mann ? "#c9c2b0" : shade(opts.color || "#7c5cff", 0.62);
    const cHead = mann ? "#efeadd" : shade(opts.color || "#7c5cff", 1.18);

    if (opts.outline) {
      ctx.shadowColor = opts.outline;
      ctx.shadowBlur = 14;
    }

    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    function limb(a, b, c, w) {
      ctx.strokeStyle = c;
      ctx.lineWidth = w || L.W;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }

    // far side (left) limbs first, darker
    limb(J.hip, J.kneeL, cBack); limb(J.kneeL, J.footL, cBack);
    limb(J.chest, J.elbL, cBack, L.W - 1.5); limb(J.elbL, J.handL, cBack, L.W - 1.5);
    // torso
    limb(J.hip, J.chest, cMain, L.W + 3);
    // near side (right) limbs
    limb(J.hip, J.kneeR, cMain); limb(J.kneeR, J.footR, cMain);
    limb(J.chest, J.elbR, cMain, L.W - 1.5); limb(J.elbR, J.handR, cMain, L.W - 1.5);
    // head
    ctx.fillStyle = cHead;
    ctx.beginPath();
    ctx.arc(J.headC.x, J.headC.y, L.HEAD_R, 0, Math.PI * 2);
    ctx.fill();

    ctx.shadowBlur = 0;

    if (opts.cleared) {
      // seeker already checked this one — stamp a faint X on the chest
      ctx.strokeStyle = "rgba(255,93,108,.8)";
      ctx.lineWidth = 4;
      const c = J.chest, r = 12;
      ctx.beginPath();
      ctx.moveTo(c.x - r, c.y - r); ctx.lineTo(c.x + r, c.y + r);
      ctx.moveTo(c.x + r, c.y - r); ctx.lineTo(c.x - r, c.y + r);
      ctx.stroke();
    }
    ctx.restore();
    return J;
  }

  function hitTest(px, py, sx, floorY, scale) {
    return (
      px > sx - 36 * scale && px < sx + 36 * scale &&
      py > floorY - L.TOP * scale && py < floorY + 10 * scale
    );
  }

  return {
    L, PRESETS, PRESET_NAMES,
    defaultPose, randomPose, clone, mirror,
    joints, ik, draw, hitTest, shade,
  };
})();
