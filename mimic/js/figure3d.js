// 3D poseable figure. Reuses FIG.joints() (the 2D skeleton) mapped into the
// figure's local Y-up/Z-forward plane, with arms/legs offset sideways for
// depth. Limbs are smooth capsules with sphere joints; each body part has a
// paintable material and the torso carries a canvas texture for doodles.
window.FIG3D = (function () {
  const S = 0.0135; // px -> meters (figure ≈ 1.7m tall)
  const RAD = { torso: 0.145, limb: 0.058, head: 0.165 };
  const ARM_X = 0.26, LEG_X = 0.145;
  const UP = new THREE.Vector3(0, 1, 0);

  const PALETTE = [
    "#e9e4d6", "#f2f2f2", "#2f2f38", "#ff5d6c", "#ffb43a", "#ffe066",
    "#4cd97b", "#2e8b57", "#3ecbe8", "#3a6ea5", "#7c5cff", "#f068c0",
    "#a3e048", "#ff8b4a", "#8a5a3c", "#c9b458", "#9aa0b4", "#5b4030",
  ];

  function defaultPaint(base) {
    return { head: base || "#e9e4d6", torso: base || "#e9e4d6",
             armL: base || "#e9e4d6", armR: base || "#e9e4d6",
             legL: base || "#e9e4d6", legR: base || "#e9e4d6", doodle: null };
  }

  function randomPaint(rng) {
    rng = rng || Math.random;
    const pick = () => PALETTE[(rng() * PALETTE.length) | 0];
    const base = pick();
    const p = defaultPaint(base);
    if (rng() < 0.7) p.torso = pick();
    if (rng() < 0.4) { p.armL = p.armR = pick(); }
    if (rng() < 0.35) { p.legL = p.legR = pick(); }
    if (rng() < 0.25) p.head = pick();
    return p;
  }

  function std(opts) {
    return new THREE.MeshStandardMaterial(Object.assign({ roughness: 0.65, metalness: 0.05 }, opts));
  }

  // fixed-length smooth capsule (limb lengths never change, only rotate)
  function capsule(mat, r, lenPx) {
    const m = new THREE.Mesh(new THREE.CapsuleGeometry(r, lenPx * S, 6, 14), mat);
    m.castShadow = true;
    return m;
  }
  function ball(mat, r, w, h) {
    const m = new THREE.Mesh(new THREE.SphereGeometry(r, w || 20, h || 16), mat);
    m.castShadow = true;
    return m;
  }

  function makeLabel(text, color) {
    const c = document.createElement("canvas");
    c.width = 256; c.height = 64;
    const x = c.getContext("2d");
    x.font = "700 30px 'Trebuchet MS', sans-serif";
    x.textAlign = "center";
    const w = Math.min(240, x.measureText(text).width + 26);
    x.fillStyle = "rgba(10,8,16,.72)";
    x.beginPath(); x.roundRect(128 - w / 2, 8, w, 46, 14); x.fill();
    x.fillStyle = color || "#fff";
    x.fillText(text, 128, 40);
    const tex = new THREE.CanvasTexture(c);
    if (THREE.SRGBColorSpace) tex.colorSpace = THREE.SRGBColorSpace;
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false }));
    sp.scale.set(1.35, 0.34, 1);
    return sp;
  }

  function create(scene) {
    const group = new THREE.Group();
    scene.add(group);

    // torso doodle texture: base colour + player drawing composited
    const texCanvas = document.createElement("canvas");
    texCanvas.width = texCanvas.height = 128;
    const texCtx = texCanvas.getContext("2d");
    const torsoTex = new THREE.CanvasTexture(texCanvas);
    if (THREE.SRGBColorSpace) torsoTex.colorSpace = THREE.SRGBColorSpace;

    const mats = {
      head: std({ color: "#e9e4d6" }),
      torso: std({ map: torsoTex }),
      armL: std({ color: "#e9e4d6" }),
      armR: std({ color: "#e9e4d6" }),
      legL: std({ color: "#e9e4d6" }),
      legR: std({ color: "#e9e4d6" }),
    };
    const LEN = FIG.L;
    const parts = {
      torso: capsule(mats.torso, RAD.torso, LEN.TORSO),
      head: ball(mats.head, RAD.head, 24, 18),
      armL1: capsule(mats.armL, RAD.limb, LEN.A1), armL2: capsule(mats.armL, RAD.limb, LEN.A2),
      armR1: capsule(mats.armR, RAD.limb, LEN.A1), armR2: capsule(mats.armR, RAD.limb, LEN.A2),
      legL1: capsule(mats.legL, RAD.limb * 1.2, LEN.L1), legL2: capsule(mats.legL, RAD.limb * 1.15, LEN.L2),
      legR1: capsule(mats.legR, RAD.limb * 1.2, LEN.L1), legR2: capsule(mats.legR, RAD.limb * 1.15, LEN.L2),
      hip: ball(mats.torso, RAD.torso * 0.9, 18, 14),
      shoulders: ball(mats.torso, RAD.torso * 0.8, 18, 14),
      handL: ball(mats.armL, RAD.limb * 1.25, 12, 10), handR: ball(mats.armR, RAD.limb * 1.25, 12, 10),
      footL: ball(mats.legL, RAD.limb * 1.35, 12, 10), footR: ball(mats.legR, RAD.limb * 1.35, 12, 10),
    };
    for (const k in parts) group.add(parts[k]);

    // a gentle mannequin face (everyone gets the same one — no tells)
    const eyeMat = std({ color: "#26222e", roughness: 0.35 });
    const eyeL = ball(eyeMat, 0.021, 8, 6), eyeR = ball(eyeMat, 0.021, 8, 6);
    eyeL.castShadow = eyeR.castShadow = false;
    parts.head.add(eyeL); parts.head.add(eyeR);
    eyeL.position.set(-0.055, 0.025, RAD.head - 0.02);
    eyeR.position.set(0.055, 0.025, RAD.head - 0.02);

    // invisible hit capsule for tap raycasting
    const hit = new THREE.Mesh(
      new THREE.CylinderGeometry(0.52, 0.52, 1.9, 6),
      new THREE.MeshBasicMaterial({ visible: false })
    );
    hit.position.y = 0.95;
    group.add(hit);

    // "this is you" marker + spotted flash ring
    const marker = new THREE.Mesh(
      new THREE.ConeGeometry(0.16, 0.3, 16),
      new THREE.MeshBasicMaterial({ color: 0xffffff })
    );
    marker.rotation.x = Math.PI;
    marker.visible = false;
    group.add(marker);

    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(0.58, 0.045, 10, 36),
      new THREE.MeshBasicMaterial({ color: 0xff4455 })
    );
    ring.rotation.x = Math.PI / 2;
    ring.position.y = 0.1;
    ring.visible = false;
    group.add(ring);

    // cleared "X" sprite
    const xc = document.createElement("canvas");
    xc.width = xc.height = 64;
    const xx = xc.getContext("2d");
    xx.strokeStyle = "#ff5d6c"; xx.lineWidth = 10; xx.lineCap = "round";
    xx.beginPath(); xx.moveTo(14, 14); xx.lineTo(50, 50); xx.moveTo(50, 14); xx.lineTo(14, 50); xx.stroke();
    const clearedSp = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(xc), depthTest: false }));
    clearedSp.scale.set(0.5, 0.5, 1);
    clearedSp.position.y = 2.1;
    clearedSp.visible = false;
    group.add(clearedSp);

    let labelSprite = null;
    let topY = 1.8;
    let paint = defaultPaint();
    let emoteSprite = null, emoteT = 0;
    let hatGroup = null, hatName = "none";
    // pose blending (walk -> statue transitions)
    let curPose = null, blendFrom = null, blendTo = null, blendT = 1;
    // animation state: composed every tick() so effects stack cleanly
    const A = {
      px: 0, pz: 0, py: 0, pyaw: 0,
      mode: "idle",            // idle | walk | frozen
      pop: 1,                  // spawn pop-in progress (1 = done)
      wobble: 0,               // wrong-accusation shake timer
      fall: 0, fallTarget: 0,  // caught fall-over progress
      phase: Math.random() * 9,
      headBaseY: 0, headBaseZ: 0,
    };

    function easeOutBack(t) {
      const c = 1.70158;
      return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2);
    }
    function easeOutBounce(t) {
      const n1 = 7.5625, d1 = 2.75;
      if (t < 1 / d1) return n1 * t * t;
      if (t < 2 / d1) return n1 * (t -= 1.5 / d1) * t + 0.75;
      if (t < 2.5 / d1) return n1 * (t -= 2.25 / d1) * t + 0.9375;
      return n1 * (t -= 2.625 / d1) * t + 0.984375;
    }

    const _dir = new THREE.Vector3();
    function place(mesh, a, b) {
      _dir.set(b.x - a.x, b.y - a.y, b.z - a.z);
      const len = Math.max(0.001, _dir.length());
      mesh.position.set((a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2);
      mesh.quaternion.setFromUnitVectors(UP, _dir.multiplyScalar(1 / len));
    }

    function lerpAngle(a, b, t) {
      let d = b - a;
      while (d > Math.PI) d -= 2 * Math.PI;
      while (d < -Math.PI) d += 2 * Math.PI;
      return a + d * t;
    }
    function lerpPose(p1, p2, t) {
      const o = {};
      for (const k in p2) o[k] = lerpAngle(p1[k] != null ? p1[k] : p2[k], p2[k], t);
      return o;
    }

    const rig = {
      group, mats,
      // snap=true applies instantly (walk cycle frames); otherwise blends
      setPose(pose, snap) {
        if (snap || !curPose) {
          curPose = pose;
          blendT = 1;
          applyPose(pose);
        } else {
          blendFrom = curPose;
          blendTo = pose;
          blendT = 0;
        }
      },
      applyPose,
    };

    function applyPose(pose) {
        curPose = pose;
        const J = FIG.joints(pose);
        // 2D (x right, y down) -> local 3D (z forward, y up), uniform scale
        const j = {};
        for (const k in J) j[k] = { y: -J[k].y * S, z: J[k].x * S };
        const P = (k, x) => new THREE.Vector3(x, j[k].y, j[k].z);
        place(parts.torso, P("hip", 0), P("chest", 0));
        parts.head.position.set(0, j.headC.y + 0.03, j.headC.z);
        // face the head along the head tilt a bit
        parts.head.rotation.x = Math.atan2(j.headC.z - j.chest.z, j.headC.y - j.chest.y) * 0.8;
        parts.hip.position.set(0, j.hip.y, j.hip.z);
        parts.shoulders.position.set(0, j.chest.y, j.chest.z);
        place(parts.armL1, P("chest", -ARM_X), P("elbL", -ARM_X));
        place(parts.armL2, P("elbL", -ARM_X), P("handL", -ARM_X));
        place(parts.armR1, P("chest", ARM_X), P("elbR", ARM_X));
        place(parts.armR2, P("elbR", ARM_X), P("handR", ARM_X));
        place(parts.legL1, P("hip", -LEG_X), P("kneeL", -LEG_X));
        place(parts.legL2, P("kneeL", -LEG_X), P("footL", -LEG_X));
        place(parts.legR1, P("hip", LEG_X), P("kneeR", LEG_X));
        place(parts.legR2, P("kneeR", LEG_X), P("footR", LEG_X));
        parts.handL.position.set(-ARM_X, j.handL.y, j.handL.z);
        parts.handR.position.set(ARM_X, j.handR.y, j.handR.z);
        parts.footL.position.set(-LEG_X, j.footL.y + 0.02, j.footL.z);
        parts.footR.position.set(LEG_X, j.footR.y + 0.02, j.footR.z);
        topY = Math.max(j.headC.y + 0.26, 1.3);
        marker.position.set(0, topY + 0.25, 0);
        A.headBaseY = parts.head.position.y;
        A.headBaseZ = parts.head.position.z;
        if (labelSprite) labelSprite.position.y = topY + 0.6;
        if (hatGroup) {
          hatGroup.position.copy(parts.head.position);
          hatGroup.rotation.copy(parts.head.rotation);
        }
    }

    Object.assign(rig, {
      setPos(x, z, yaw, y) {
        A.px = x; A.pz = z; A.pyaw = yaw || 0; A.py = y || 0;
        group.position.x = x;
        group.position.z = z;
        group.rotation.y = A.pyaw;
      },
      setMode(m) { A.mode = m; },
      pop() { A.pop = 0; },
      wobble() { A.wobble = 0.6; },
      // compose all animation layers; call once per frame
      tick(t, dt) {
        // pose transition blending (walk -> statue etc.)
        if (blendT < 1 && blendTo) {
          blendT = Math.min(1, blendT + dt / 0.22);
          applyPose(blendT >= 1 ? blendTo : lerpPose(blendFrom, blendTo, blendT));
        }
        // flinch hop physics
        if (A.fvy || A.fy) {
          A.fvy = (A.fvy || 0) - 11 * dt;
          A.fy = Math.max(0, (A.fy || 0) + A.fvy * dt);
          if (A.fy === 0 && A.fvy < 0) A.fvy = 0;
        }
        // spawn pop-in with overshoot
        if (A.pop < 1) {
          A.pop = Math.min(1, A.pop + dt * 2.4);
          const s = 0.25 + 0.75 * easeOutBack(A.pop);
          group.scale.setScalar(Math.max(0.05, s));
        }
        // fall over (caught) with a bounce; snap back up instantly on reset
        if (A.fall < A.fallTarget) A.fall = Math.min(1, A.fall + dt * 1.9);
        else if (A.fall > A.fallTarget) A.fall = 0;
        const falling = A.fall > 0;
        const fallRot = falling ? -Math.PI * 0.47 * easeOutBounce(A.fall) : 0;

        // walk bounce-hop / idle breathing / statue stillness
        let hopY = 0, lean = 0;
        if (!falling) {
          if (A.mode === "walk") {
            hopY = Math.abs(Math.sin(t * 9 + A.phase)) * 0.075;
            lean = 0.07;
          } else if (A.mode === "idle") {
            parts.head.position.y = A.headBaseY + Math.sin(t * 2.1 + A.phase) * 0.012;
            hopY = Math.sin(t * 2.1 + A.phase) * 0.006;
          }
        }
        if (A.mode !== "idle") parts.head.position.y = A.headBaseY;

        // wrong-accusation wobble
        let wob = 0;
        if (A.wobble > 0) {
          A.wobble = Math.max(0, A.wobble - dt);
          wob = Math.sin(A.wobble * 34) * 0.16 * A.wobble;
        }

        group.position.y = (falling ? 0.42 * A.fall : 0) + A.py + hopY + (A.fy || 0);
        group.rotation.x = fallRot + lean;
        group.rotation.z = wob;

        // spotted ring pulse
        if (ring.visible) {
          const p = 1 + Math.sin(t * 10) * 0.12;
          ring.scale.set(p, p, 1);
        }
        // emote bubble: pop in, hover, fade out
        if (emoteSprite) {
          emoteT -= dt;
          const age = 2.2 - emoteT;
          const sIn = Math.min(1, age * 5);
          const sOut = Math.min(1, Math.max(0, emoteT) * 3);
          const sc = 0.9 * easeOutBack(sIn) * sOut;
          emoteSprite.scale.set(sc, sc, 1);
          emoteSprite.position.y = topY + 0.95 + Math.sin(t * 3) * 0.05;
          if (emoteT <= 0) { group.remove(emoteSprite); emoteSprite = null; }
        }
      },
      showEmote(char) {
        if (emoteSprite) group.remove(emoteSprite);
        const c = document.createElement("canvas");
        c.width = c.height = 96;
        const x = c.getContext("2d");
        x.beginPath();
        x.arc(48, 48, 42, 0, Math.PI * 2);
        x.fillStyle = "rgba(255,255,255,.92)";
        x.fill();
        x.font = "52px serif";
        x.textAlign = "center";
        x.textBaseline = "middle";
        x.fillText(char, 48, 52);
        const tex = new THREE.CanvasTexture(c);
        if (THREE.SRGBColorSpace) tex.colorSpace = THREE.SRGBColorSpace;
        emoteSprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false }));
        emoteSprite.position.y = topY + 0.95;
        emoteSprite.scale.set(0.01, 0.01, 1);
        group.add(emoteSprite);
        emoteT = 2.2;
      },
      setPaint(p) {
        paint = Object.assign(defaultPaint(), p);
        mats.head.color.set(paint.head);
        mats.armL.color.set(paint.armL);
        mats.armR.color.set(paint.armR);
        mats.legL.color.set(paint.legL);
        mats.legR.color.set(paint.legR);
        texCtx.fillStyle = paint.torso;
        texCtx.fillRect(0, 0, 128, 128);
        if (paint.doodle) {
          const img = new Image();
          img.onload = () => { texCtx.drawImage(img, 0, 0, 128, 128); torsoTex.needsUpdate = true; };
          img.src = paint.doodle;
        }
        torsoTex.needsUpdate = true;
      },
      getPaint: () => paint,
      setFallen(f) { A.fallTarget = f ? 1 : 0; },
      setLabel(text, color) {
        if (labelSprite) { group.remove(labelSprite); labelSprite = null; }
        if (text) {
          labelSprite = makeLabel(text, color);
          labelSprite.position.y = topY + 0.6;
          group.add(labelSprite);
        }
      },
      setMarker(on, color) {
        marker.visible = !!on;
        if (color) marker.material.color.set(color);
      },
      setFlash(on) { ring.visible = !!on; },
      setCleared(on) {
        clearedSp.visible = !!on;
        for (const k in mats) {
          mats[k].transparent = !!on;
          mats[k].opacity = on ? 0.55 : 1;
        }
      },
      setVisible(v) { group.visible = v; },
      setHat(name) {
        if (name === hatName) return;
        hatName = name;
        if (hatGroup) { group.remove(hatGroup); hatGroup = null; }
        if (!name || name === "none") return;
        hatGroup = buildHat(name);
        hatGroup.position.copy(parts.head.position);
        group.add(hatGroup);
      },
      // startled hop + wobble (hit by a seeker ball, or a ghost's nudge)
      flinch() {
        A.wobble = 0.55;
        A.fvy = 2.4;
      },
      hitMesh: hit,
      dispose() {
        scene.remove(group);
        group.traverse((o) => {
          if (o.geometry) o.geometry.dispose();
        });
        torsoTex.dispose();
      },
    });
    rig.setPose(FIG.defaultPose());
    rig.setPaint(defaultPaint());
    return rig;
  }

  // ---------- procedural hats ----------
  const HATS = ["none", "cap", "party", "tophat", "crown", "halo"];
  function buildHat(name) {
    const g = new THREE.Group();
    const r = RAD.head;
    if (name === "cap") {
      const dome = new THREE.Mesh(
        new THREE.SphereGeometry(r * 1.02, 16, 10, 0, Math.PI * 2, 0, Math.PI / 2.6),
        std({ color: 0xd8535f })
      );
      dome.position.y = r * 0.28;
      g.add(dome);
      const brim = new THREE.Mesh(new THREE.CylinderGeometry(r * 0.75, r * 0.75, 0.02, 14), std({ color: 0xb03a46 }));
      brim.position.set(0, r * 0.42, r * 0.9);
      g.add(brim);
    } else if (name === "party") {
      const cone = new THREE.Mesh(new THREE.ConeGeometry(r * 0.65, r * 1.9, 12), std({ color: 0x3ecbe8 }));
      cone.position.y = r * 1.55;
      cone.rotation.z = 0.12;
      g.add(cone);
      const pom = new THREE.Mesh(new THREE.SphereGeometry(0.05, 8, 6), std({ color: 0xffe066 }));
      pom.position.set(-0.028, r * 2.5, 0);
      g.add(pom);
    } else if (name === "tophat") {
      const tube = new THREE.Mesh(new THREE.CylinderGeometry(r * 0.78, r * 0.82, r * 1.5, 14), std({ color: 0x26222e }));
      tube.position.y = r * 1.3;
      g.add(tube);
      const brim = new THREE.Mesh(new THREE.CylinderGeometry(r * 1.35, r * 1.35, 0.03, 16), std({ color: 0x26222e }));
      brim.position.y = r * 0.6;
      g.add(brim);
      const band = new THREE.Mesh(new THREE.CylinderGeometry(r * 0.84, r * 0.84, 0.09, 14), std({ color: 0xd8535f }));
      band.position.y = r * 0.75;
      g.add(band);
    } else if (name === "crown") {
      const mat = std({ color: 0xf5c93c, metalness: 0.7, roughness: 0.25 });
      const ringM = new THREE.Mesh(new THREE.CylinderGeometry(r * 0.85, r * 0.9, r * 0.5, 12, 1, true), mat);
      ringM.position.y = r * 0.85;
      g.add(ringM);
      for (let i = 0; i < 5; i++) {
        const a = (i / 5) * Math.PI * 2;
        const spike = new THREE.Mesh(new THREE.ConeGeometry(0.045, 0.13, 6), mat);
        spike.position.set(Math.cos(a) * r * 0.85, r * 1.18, Math.sin(a) * r * 0.85);
        g.add(spike);
      }
    } else if (name === "halo") {
      const halo = new THREE.Mesh(
        new THREE.TorusGeometry(r * 0.8, 0.035, 8, 24),
        std({ color: 0xffe066, emissive: 0xbb8a1f, metalness: 0.4 })
      );
      halo.rotation.x = Math.PI / 2;
      halo.position.y = r * 1.9;
      g.add(halo);
    }
    g.traverse((o) => { if (o.isMesh) o.castShadow = true; });
    return g;
  }

  function createPedestal(scene, x, z) {
    const m = new THREE.Mesh(
      new THREE.CylinderGeometry(0.7, 0.85, 0.24, 20),
      std({ color: 0x8d8398 })
    );
    m.position.set(x, 0.12, z);
    m.receiveShadow = true;
    scene.add(m);
    return m;
  }

  return { create, createPedestal, defaultPaint, randomPaint, PALETTE, HATS };
})();
