// 3D poseable figure. Reuses FIG.joints() (the 2D skeleton) mapped into the
// figure's local Y-up/Z-forward plane, with arms/legs offset sideways for
// depth. Each body part has its own paintable material; the torso carries a
// canvas texture so players can doodle on themselves.
window.FIG3D = (function () {
  const S = 0.0125; // px -> meters (150px figure ≈ 1.87m)
  const RAD = { torso: 0.125, limb: 0.062, head: 0.175 };
  const ARM_X = 0.27, LEG_X = 0.15;
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
    // mannequins are usually mostly one colour with a couple of accents
    const base = pick();
    const p = defaultPaint(base);
    if (rng() < 0.7) p.torso = pick();
    if (rng() < 0.4) { p.armL = p.armR = pick(); }
    if (rng() < 0.35) { p.legL = p.legR = pick(); }
    if (rng() < 0.25) p.head = pick();
    return p;
  }

  function seg(mat, r, rTop) {
    const g = new THREE.CylinderGeometry(rTop || r, r * 0.85, 1, 8);
    const m = new THREE.Mesh(g, mat);
    m.castShadow = true;
    return m;
  }
  function ball(mat, r) {
    const m = new THREE.Mesh(new THREE.SphereGeometry(r, 10, 8), mat);
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

    const mats = {
      head: new THREE.MeshLambertMaterial({ color: "#e9e4d6" }),
      torso: new THREE.MeshLambertMaterial({ map: torsoTex }),
      armL: new THREE.MeshLambertMaterial({ color: "#e9e4d6" }),
      armR: new THREE.MeshLambertMaterial({ color: "#e9e4d6" }),
      legL: new THREE.MeshLambertMaterial({ color: "#e9e4d6" }),
      legR: new THREE.MeshLambertMaterial({ color: "#e9e4d6" }),
    };

    const parts = {
      // wider at the shoulders, narrow at the waist
      torso: seg(mats.torso, RAD.torso, RAD.torso * 1.5),
      head: ball(mats.head, RAD.head),
      armL1: seg(mats.armL, RAD.limb), armL2: seg(mats.armL, RAD.limb),
      armR1: seg(mats.armR, RAD.limb), armR2: seg(mats.armR, RAD.limb),
      legL1: seg(mats.legL, RAD.limb * 1.15), legL2: seg(mats.legL, RAD.limb * 1.15),
      legR1: seg(mats.legR, RAD.limb * 1.15), legR2: seg(mats.legR, RAD.limb * 1.15),
      hip: ball(mats.torso, RAD.torso * 0.95),
    };
    for (const k in parts) group.add(parts[k]);

    // invisible hit capsule for tap raycasting
    const hit = new THREE.Mesh(
      new THREE.CylinderGeometry(0.55, 0.55, 2.1, 6),
      new THREE.MeshBasicMaterial({ visible: false })
    );
    hit.position.y = 1.05;
    group.add(hit);

    // "this is you" marker + spotted flash ring
    const marker = new THREE.Mesh(
      new THREE.ConeGeometry(0.18, 0.34, 4),
      new THREE.MeshBasicMaterial({ color: 0xffffff })
    );
    marker.rotation.x = Math.PI;
    marker.visible = false;
    group.add(marker);

    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(0.62, 0.05, 8, 24),
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
    clearedSp.position.y = 2.3;
    clearedSp.visible = false;
    group.add(clearedSp);

    let labelSprite = null;
    let curPose = null, topY = 2.0;
    let paint = defaultPaint();

    function place(mesh, a, b) {
      // stretch a unit cylinder between two local-space points
      const dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z;
      const len = Math.max(0.01, Math.hypot(dx, dy, dz));
      mesh.position.set((a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2);
      mesh.scale.set(1, len, 1);
      mesh.quaternion.setFromUnitVectors(UP, new THREE.Vector3(dx / len, dy / len, dz / len));
    }

    const rig = {
      group, mats,
      setPose(pose) {
        curPose = pose;
        const J = FIG.joints(pose);
        // 2D (x right, y down) -> local 3D (z forward, y up)
        const j = {};
        for (const k in J) j[k] = { y: -J[k].y * S * 1.28, z: J[k].x * S };
        const P = (k, x) => new THREE.Vector3(x, j[k].y, j[k].z);
        place(parts.torso, P("hip", 0), P("chest", 0));
        parts.torso.scale.x = parts.torso.scale.z = 1;
        parts.head.position.set(0, j.headC.y + 0.04, j.headC.z);
        parts.hip.position.set(0, j.hip.y, j.hip.z);
        place(parts.armL1, P("chest", -ARM_X), P("elbL", -ARM_X));
        place(parts.armL2, P("elbL", -ARM_X), P("handL", -ARM_X));
        place(parts.armR1, P("chest", ARM_X), P("elbR", ARM_X));
        place(parts.armR2, P("elbR", ARM_X), P("handR", ARM_X));
        place(parts.legL1, P("hip", -LEG_X), P("kneeL", -LEG_X));
        place(parts.legL2, P("kneeL", -LEG_X), P("footL", -LEG_X));
        place(parts.legR1, P("hip", LEG_X), P("kneeR", LEG_X));
        place(parts.legR2, P("kneeR", LEG_X), P("footR", LEG_X));
        topY = Math.max(j.headC.y + 0.28, 1.4);
        marker.position.set(0, topY + 0.25, 0);
        if (labelSprite) labelSprite.position.y = topY + 0.75;
      },
      setPos(x, z, yaw) {
        group.position.x = x;
        group.position.z = z;
        group.rotation.y = yaw || 0;
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
      setFallen(f) {
        group.rotation.x = f ? -Math.PI / 2 + 0.12 : 0;
        group.position.y = f ? 0.5 : 0;
      },
      setLabel(text, color) {
        if (labelSprite) { group.remove(labelSprite); labelSprite = null; }
        if (text) {
          labelSprite = makeLabel(text, color);
          labelSprite.position.y = topY + 0.75;
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
      hitMesh: hit,
      dispose() {
        scene.remove(group);
        group.traverse((o) => {
          if (o.geometry) o.geometry.dispose();
        });
        torsoTex.dispose();
      },
    };
    rig.setPose(FIG.defaultPose());
    rig.setPaint(defaultPaint());
    return rig;
  }

  function createPedestal(scene, x, z) {
    const m = new THREE.Mesh(
      new THREE.CylinderGeometry(0.7, 0.85, 0.24, 10),
      new THREE.MeshLambertMaterial({ color: 0x8d8398 })
    );
    m.position.set(x, 0.12, z);
    m.receiveShadow = true;
    scene.add(m);
    return m;
  }

  return { create, createPedestal, defaultPaint, randomPaint, PALETTE };
})();
