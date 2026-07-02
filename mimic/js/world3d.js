// Procedural 3D park: museum colonnade, houses, trees, fountain plaza,
// hedge garden, lampposts, benches — mannequin spots scattered across all
// of them. Low-poly-but-smooth primitives with a few living details
// (clouds, butterflies, animated water) so the world feels alive.
window.WORLD3D = (function () {
  const SIZE = { x: 92, z: 62 }; // half extents: ±46, ±31
  const colliders = []; // {x,z,r} circles players can't walk through
  const decoySpots = []; // {x,z,yaw} where mannequins may stand
  const animated = []; // {fn(time, dt)} living details
  const occluders = []; // big solids the camera shouldn't clip through

  // colour palettes per map theme
  const THEMES = {
    park: {
      ground: ["#679a53", "#7fb267", "#527d41"],
      sand: ["#b9a583", "#cbb896", "#a08b6a"],
      pineA: 0x2e6b3e, pineB: 0x38804a,
      oak: 0x4a8a4a, oakLight: 0x5c9c5c,
      autumn: 0xc27b3a, autumnLight: 0xd6924e,
      hedge: 0x3c7a44, tuftH: 0.29, tuftS: 0.42, tuftL: 0.32,
      stem: 0x3f7a3a, water: 0x3f9fc0,
    },
    snow: {
      ground: ["#e8eef4", "#ffffff", "#c9d6e2"],
      sand: ["#c3cdd8", "#d8e0ea", "#aab6c4"],
      pineA: 0x3d6b52, pineB: 0xdfe9f0, // frosted
      oak: 0x8fa8a0, oakLight: 0xdfe9f0,
      autumn: 0xb08a62, autumnLight: 0xdfe9f0,
      hedge: 0x51806a, tuftH: 0.55, tuftS: 0.12, tuftL: 0.75,
      stem: 0x51806a, water: 0x9fd4e8,
    },
  };
  let T = THEMES.park;
  let themeName = "park";

  function col(x, z, r) { colliders.push({ x, z, r }); }
  function spot(x, z, yaw) { decoySpots.push({ x, z, yaw: yaw || 0 }); }
  function occ(mesh) { occluders.push(mesh); return mesh; }

  const M = (c, extra) => new THREE.MeshStandardMaterial(
    Object.assign({ color: c, roughness: 0.85, metalness: 0.02 }, extra)
  );

  function box(scene, w, h, d, x, y, z, mat, ry, shadow) {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
    m.position.set(x, y, z);
    if (ry) m.rotation.y = ry;
    if (shadow) m.castShadow = true;
    m.receiveShadow = true;
    scene.add(m);
    return m;
  }
  function cyl(scene, rt, rb, h, x, y, z, mat, seg) {
    const m = new THREE.Mesh(new THREE.CylinderGeometry(rt, rb, h, seg || 18), mat);
    m.position.set(x, y, z);
    m.castShadow = true;
    m.receiveShadow = true;
    scene.add(m);
    return m;
  }

  // ---------- procedural textures ----------
  function speckleTexture(base, fleck1, fleck2, n) {
    const c = document.createElement("canvas");
    c.width = c.height = 256;
    const x = c.getContext("2d");
    x.fillStyle = base;
    x.fillRect(0, 0, 256, 256);
    for (let i = 0; i < (n || 900); i++) {
      x.fillStyle = Math.random() < 0.5 ? fleck1 : fleck2;
      const s = 1 + Math.random() * 3;
      x.globalAlpha = 0.25 + Math.random() * 0.4;
      x.fillRect(Math.random() * 256, Math.random() * 256, s, s);
    }
    x.globalAlpha = 1;
    const t = new THREE.CanvasTexture(c);
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    if (THREE.SRGBColorSpace) t.colorSpace = THREE.SRGBColorSpace;
    return t;
  }

  // gentle wind: canopy meshes drift around their base position
  function swayCanopy(meshes, s) {
    const bases = meshes.map((m) => ({ m, x: m.position.x, z: m.position.z, k: 0.5 + m.position.y * 0.02 }));
    const ph = Math.random() * 9;
    animated.push((t) => {
      const w = Math.sin(t * 1.15 + ph) * 0.05 * s + Math.sin(t * 2.3 + ph * 2) * 0.02 * s;
      for (const b of bases) {
        b.m.position.x = b.x + w * b.k;
        b.m.position.z = b.z + w * b.k * 0.4;
      }
    });
  }

  // ---------- pieces ----------
  function tree(scene, x, z, kind, s) {
    s = s || 1;
    const trunk = M(0x7a5230, { roughness: 0.95 });
    const lean = (Math.random() - 0.5) * 0.08;
    if (kind === "pine") {
      const t = cyl(scene, 0.13 * s, 0.2 * s, 1.5 * s, x, 0.75 * s, z, trunk, 12);
      t.rotation.z = lean;
      const g = M(T.pineA);
      const g2 = M(T.pineB);
      const canopy = [];
      for (let i = 0; i < 3; i++) {
        const r = (1.5 - i * 0.38) * s, y = (1.7 + i * 1.0) * s;
        const cone = new THREE.Mesh(new THREE.ConeGeometry(r, 1.7 * s, 14), i % 2 ? g2 : g);
        cone.position.set(x, y, z);
        cone.rotation.y = i * 0.4;
        cone.castShadow = true;
        scene.add(cone);
        canopy.push(cone);
      }
      swayCanopy(canopy, s);
      col(x, z, 0.5 * s);
    } else {
      const t = cyl(scene, 0.15 * s, 0.24 * s, 1.9 * s, x, 0.95 * s, z, trunk, 12);
      t.rotation.z = lean;
      const g = M(kind === "autumn" ? T.autumn : T.oak);
      const gLight = M(kind === "autumn" ? T.autumnLight : T.oakLight);
      const blobs = [[0, 2.7, 0, 1.35], [0.85, 2.3, 0.3, 0.95], [-0.75, 2.4, -0.4, 1.0],
                     [0.1, 3.35, 0.5, 0.85], [-0.3, 3.1, -0.6, 0.7]];
      const canopy = [];
      blobs.forEach(([bx, by, bz, br], i) => {
        const s2 = new THREE.Mesh(new THREE.SphereGeometry(br * s, 14, 11), i % 2 ? gLight : g);
        s2.position.set(x + bx * s, by * s, z + bz * s);
        s2.castShadow = true;
        scene.add(s2);
        canopy.push(s2);
      });
      swayCanopy(canopy, s);
      col(x, z, 0.5 * s);
    }
  }

  function house(scene, x, z, ry, bodyC, roofC) {
    const g = new THREE.Group();
    const w = 6, d = 4.6, h = 3;
    const body = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), M(bodyC));
    body.position.y = h / 2;
    body.castShadow = true; body.receiveShadow = true;
    g.add(occ(body));
    // eave slab + pyramid roof
    const eave = new THREE.Mesh(new THREE.BoxGeometry(w + 0.7, 0.22, d + 0.7), M(roofC, { roughness: 0.7 }));
    eave.position.y = h + 0.11;
    eave.castShadow = true;
    g.add(eave);
    const roof = new THREE.Mesh(new THREE.ConeGeometry(Math.hypot(w, d) / 2 + 0.3, 2.2, 4), M(roofC, { roughness: 0.7 }));
    roof.position.y = h + 1.3;
    roof.rotation.y = Math.PI / 4;
    roof.castShadow = true;
    g.add(roof);
    // door with frame, knob and a step
    const frame = new THREE.Mesh(new THREE.BoxGeometry(1.3, 2.05, 0.1), M(0x3e2c20));
    frame.position.set(0.8, 1.02, d / 2 + 0.05);
    g.add(frame);
    const door = new THREE.Mesh(new THREE.BoxGeometry(1.05, 1.85, 0.12), M(0x5b4030));
    door.position.set(0.8, 0.95, d / 2 + 0.09);
    g.add(door);
    const knob = new THREE.Mesh(new THREE.SphereGeometry(0.05, 10, 8), M(0xd8b46a, { metalness: 0.6, roughness: 0.3 }));
    knob.position.set(1.15, 0.95, d / 2 + 0.17);
    g.add(knob);
    const step = new THREE.Mesh(new THREE.BoxGeometry(1.5, 0.16, 0.7), M(0x9aa0b4));
    step.position.set(0.8, 0.08, d / 2 + 0.4);
    g.add(step);
    // framed glowing windows
    for (const wx of [-1.7, -0.4]) {
      const wf = new THREE.Mesh(new THREE.BoxGeometry(1.16, 1.06, 0.08), M(0xf2ede2));
      wf.position.set(wx, 1.7, d / 2 + 0.04);
      g.add(wf);
      const win = new THREE.Mesh(new THREE.BoxGeometry(1.0, 0.9, 0.1),
        M(0xbfe3ef, { emissive: 0x3a5866, roughness: 0.2 }));
      win.position.set(wx, 1.7, d / 2 + 0.06);
      g.add(win);
      // cross mullions
      const mv = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.9, 0.12), M(0xf2ede2));
      mv.position.set(wx, 1.7, d / 2 + 0.07);
      g.add(mv);
      const mh = new THREE.Mesh(new THREE.BoxGeometry(1.0, 0.05, 0.12), M(0xf2ede2));
      mh.position.set(wx, 1.7, d / 2 + 0.07);
      g.add(mh);
    }
    // side window too
    const sw = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.9, 1.0),
      M(0xbfe3ef, { emissive: 0x3a5866, roughness: 0.2 }));
    sw.position.set(w / 2 + 0.04, 1.7, -0.6);
    g.add(sw);
    const chim = new THREE.Mesh(new THREE.BoxGeometry(0.6, 1.7, 0.6), M(0x8d7364));
    chim.position.set(-w / 2 + 0.9, h + 1.3, -0.8);
    chim.castShadow = true;
    g.add(chim);
    const chimTop = new THREE.Mesh(new THREE.BoxGeometry(0.75, 0.18, 0.75), M(0x6e5648));
    chimTop.position.set(-w / 2 + 0.9, h + 2.2, -0.8);
    g.add(chimTop);
    g.position.set(x, 0, z);
    g.rotation.y = ry;
    scene.add(g);
    const c = Math.cos(ry), s = Math.sin(ry);
    for (const off of [-1.8, 0, 1.8]) col(x + off * c, z - off * s, 2.35);
    spot(x + (w / 2 + 1) * c, z - (w / 2 + 1) * s, ry + Math.PI / 2);
    spot(x - (w / 2 + 1) * c, z + (w / 2 + 1) * s, ry - Math.PI / 2);
    spot(x + 2.2 * s + 2 * c, z + (d / 2 + 1.2) * c - 2 * s, ry);
  }

  function fountain(scene, x, z) {
    const stone = M(0x9aa0b4, { roughness: 0.6 });
    cyl(scene, 3.2, 3.5, 0.7, x, 0.35, z, stone, 32);
    // carved rim
    const rim = new THREE.Mesh(new THREE.TorusGeometry(3.2, 0.14, 10, 40), M(0xb0b6c8, { roughness: 0.5 }));
    rim.rotation.x = Math.PI / 2;
    rim.position.set(x, 0.72, z);
    rim.castShadow = true;
    scene.add(rim);
    const water = new THREE.Mesh(
      new THREE.CylinderGeometry(2.95, 2.95, 0.14, 32),
      M(0x4fc3dd, { emissive: 0x0d3a46, roughness: 0.15, transparent: true, opacity: 0.9 })
    );
    water.position.set(x, 0.7, z);
    scene.add(water);
    cyl(scene, 0.35, 0.5, 1.8, x, 1.3, z, stone, 18);
    const bowl = new THREE.Mesh(new THREE.CylinderGeometry(1.1, 0.55, 0.5, 24), stone);
    bowl.position.set(x, 2.3, z);
    bowl.castShadow = true;
    scene.add(bowl);
    const bowlWater = new THREE.Mesh(
      new THREE.CylinderGeometry(0.95, 0.95, 0.1, 24),
      M(0x6fd7ea, { emissive: 0x14454f, roughness: 0.15 })
    );
    bowlWater.position.set(x, 2.52, z);
    scene.add(bowlWater);
    const jet = new THREE.Mesh(
      new THREE.CylinderGeometry(0.06, 0.11, 0.9, 10),
      M(0xbdeff8, { emissive: 0x3d7482, transparent: true, opacity: 0.8 })
    );
    jet.position.set(x, 3.0, z);
    scene.add(jet);
    animated.push((t) => {
      water.rotation.y = t * 0.25;
      bowlWater.rotation.y = -t * 0.4;
      jet.scale.y = 1 + Math.sin(t * 5) * 0.1;
      jet.position.y = 3.0 + Math.sin(t * 5) * 0.04;
    });
    col(x, z, 3.7);
    for (let i = 0; i < 6; i++) {
      const a = (i / 6) * Math.PI * 2;
      spot(x + Math.cos(a) * 5.4, z + Math.sin(a) * 5.4, -a + Math.PI / 2);
    }
  }

  function colonnade(scene, cx, cz) {
    const marble = M(0xe2dbcf, { roughness: 0.4 });
    const marbleDark = M(0xcfc8bc, { roughness: 0.5 });
    // two-step base
    box(scene, 19.4, 0.18, 13.4, cx, 0.09, cz, marbleDark);
    box(scene, 18, 0.3, 12, cx, 0.3, cz, marble);
    const positions = [];
    for (const px of [-8, -2.7, 2.7, 8]) for (const pz of [-5, 5]) positions.push([px, pz]);
    for (const [px, pz] of positions) {
      // base, shaft, capital
      cyl(scene, 0.5, 0.55, 0.25, cx + px, 0.57, cz + pz, marbleDark, 18);
      const shaft = cyl(scene, 0.32, 0.38, 3.7, cx + px, 2.45, cz + pz, marble, 20);
      cyl(scene, 0.52, 0.42, 0.3, cx + px, 4.42, cz + pz, marbleDark, 18);
      col(cx + px, cz + pz, 0.6);
    }
    // roof: trim + slab (emissive so the underside isn't black)
    box(scene, 19.6, 0.25, 13.6, cx, 4.72, cz, M(0xd8d0c2, { emissive: 0x5a5348 }), 0, true);
    occ(box(scene, 19, 0.45, 13, cx, 5.05, cz, M(0xbfb7a8, { emissive: 0x4a453c }), 0, true));
    for (const px of [-6.4, -3.2, 0, 3.2, 6.4]) {
      spot(cx + px, cz - 2.6, 0);
      spot(cx + px, cz + 2.6, Math.PI);
    }
  }

  // rounded hedge: a horizontal capsule
  function hedge(scene, x, z, len, ry) {
    const m = new THREE.Mesh(
      new THREE.CapsuleGeometry(0.75, Math.max(0.5, len - 1.5), 6, 14),
      M(T.hedge, { roughness: 0.95 })
    );
    m.rotation.z = Math.PI / 2;
    m.rotation.y = ry || 0;
    m.position.set(x, 0.72, z);
    m.castShadow = true;
    m.receiveShadow = true;
    scene.add(occ(m));
    const c = Math.cos(ry || 0), s = Math.sin(ry || 0);
    const n = Math.max(1, Math.round(len / 2));
    for (let i = 0; i < n; i++) {
      const t = -len / 2 + (i + 0.5) * (len / n);
      col(x + t * c, z - t * s, 1.0);
    }
  }

  function lamppost(scene, x, z) {
    const iron = M(0x3a3f4c, { metalness: 0.5, roughness: 0.45 });
    cyl(scene, 0.32, 0.4, 0.22, x, 0.11, z, iron, 14);
    cyl(scene, 0.06, 0.09, 3.3, x, 1.75, z, iron, 12);
    cyl(scene, 0.14, 0.05, 0.16, x, 3.42, z, iron, 12);
    // glass lantern + warm bulb + glow sprite
    const glass = new THREE.Mesh(
      new THREE.CylinderGeometry(0.2, 0.26, 0.42, 8),
      M(0xfff3c4, { transparent: true, opacity: 0.35, roughness: 0.1 })
    );
    glass.position.set(x, 3.68, z);
    scene.add(glass);
    const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.11, 12, 10),
      M(0xffe9a3, { emissive: 0xcfa73a }));
    bulb.position.set(x, 3.66, z);
    scene.add(bulb);
    const cap = new THREE.Mesh(new THREE.ConeGeometry(0.3, 0.22, 8), iron);
    cap.position.set(x, 3.98, z);
    cap.castShadow = true;
    scene.add(cap);
    col(x, z, 0.35);
  }

  function bench(scene, x, z, ry) {
    const wood = M(0x8a6844, { roughness: 0.8 });
    const iron = M(0x2f333d, { metalness: 0.4, roughness: 0.5 });
    const g = new THREE.Group();
    // slatted seat
    for (const off of [-0.19, 0, 0.19]) {
      const slat = new THREE.Mesh(new THREE.BoxGeometry(2.0, 0.07, 0.16), wood);
      slat.position.set(0, 0.55, off);
      slat.castShadow = true;
      g.add(slat);
    }
    for (const off of [-0.12, 0.1]) {
      const back = new THREE.Mesh(new THREE.BoxGeometry(2.0, 0.14, 0.06), wood);
      back.position.set(0, 0.86 + off, -0.31);
      back.rotation.x = -0.22;
      g.add(back);
    }
    for (const lx of [-0.85, 0.85]) {
      const leg = new THREE.Mesh(new THREE.BoxGeometry(0.09, 0.55, 0.5), iron);
      leg.position.set(lx, 0.28, 0);
      g.add(leg);
    }
    g.position.set(x, 0, z);
    g.rotation.y = ry;
    scene.add(g);
    col(x, z, 0.8);
  }

  function flowerbed(scene, x, z, r, c1) {
    const disc = new THREE.Mesh(
      new THREE.CylinderGeometry(r, r + 0.15, 0.14, 24),
      M(0x6b4d33, { roughness: 1 })
    );
    disc.position.set(x, 0.07, z);
    disc.receiveShadow = true;
    scene.add(disc);
    const rimStones = new THREE.Mesh(new THREE.TorusGeometry(r + 0.08, 0.07, 8, 26), M(0x9aa0b4));
    rimStones.rotation.x = Math.PI / 2;
    rimStones.position.set(x, 0.12, z);
    scene.add(rimStones);
    const petal = M(c1, { emissive: 0x1c0f18, roughness: 0.6 });
    const petal2 = M(0xffffff, { roughness: 0.6 });
    const stemM = M(T.stem);
    for (let i = 0; i < 12; i++) {
      const a = Math.random() * Math.PI * 2, rr = Math.random() * (r - 0.35);
      const fx = x + Math.cos(a) * rr, fz = z + Math.sin(a) * rr;
      const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.015, 0.02, 0.24, 6), stemM);
      stem.position.set(fx, 0.26, fz);
      scene.add(stem);
      const f = new THREE.Mesh(new THREE.SphereGeometry(0.09, 10, 8), Math.random() < 0.3 ? petal2 : petal);
      f.position.set(fx, 0.42, fz);
      scene.add(f);
    }
  }

  function path(scene, x1, z1, x2, z2, w, sandTex) {
    const dx = x2 - x1, dz = z2 - z1;
    const len = Math.hypot(dx, dz);
    const tex = sandTex.clone();
    tex.needsUpdate = true;
    tex.repeat.set(len / 4, w / 4);
    const p = new THREE.Mesh(
      new THREE.PlaneGeometry(len, w),
      M(0xb9a583, { map: tex, roughness: 1 })
    );
    p.rotation.x = -Math.PI / 2;
    p.rotation.z = -Math.atan2(dz, dx);
    p.position.set((x1 + x2) / 2, 0.03, (z1 + z2) / 2);
    p.receiveShadow = true;
    scene.add(p);
  }

  function clouds(scene) {
    const cm = M(0xffffff, { roughness: 1, emissive: 0x555560, transparent: true, opacity: 0.92 });
    for (let i = 0; i < 6; i++) {
      const g = new THREE.Group();
      const n = 3 + (Math.random() * 3 | 0);
      for (let k = 0; k < n; k++) {
        const s = 1.6 + Math.random() * 2.4;
        const b = new THREE.Mesh(new THREE.SphereGeometry(s, 12, 9), cm);
        b.position.set(k * 2.2 - n, Math.random() * 0.8, Math.random() * 2 - 1);
        b.scale.y = 0.6;
        g.add(b);
      }
      g.position.set(MU.rand(-50, 50), MU.rand(22, 32), MU.rand(-38, 38));
      scene.add(g);
      const speed = MU.rand(0.15, 0.45);
      animated.push((t, dt) => {
        g.position.x += speed * dt;
        if (g.position.x > 60) g.position.x = -60;
      });
    }
  }

  function butterflies(scene) {
    for (let i = 0; i < 7; i++) {
      const colr = MU.choice([0xffb43a, 0xf068c0, 0x3ecbe8, 0xa3e048, 0xff5d6c]);
      const wm = M(colr, { side: THREE.DoubleSide, emissive: 0x221122 });
      const g = new THREE.Group();
      const wingGeo = new THREE.CircleGeometry(0.16, 10);
      const w1 = new THREE.Mesh(wingGeo, wm);
      const w2 = new THREE.Mesh(wingGeo, wm);
      w1.position.x = -0.09; w2.position.x = 0.09;
      g.add(w1); g.add(w2);
      const cx = MU.rand(-35, 35), cz = MU.rand(-24, 24);
      const r1 = MU.rand(2, 6), r2 = MU.rand(2, 6), ph = Math.random() * 9;
      scene.add(g);
      animated.push((t) => {
        const flap = Math.sin(t * 14 + ph) * 0.9;
        w1.rotation.y = flap; w2.rotation.y = -flap;
        g.position.set(
          cx + Math.sin(t * 0.5 + ph) * r1,
          1.2 + Math.sin(t * 1.3 + ph) * 0.5,
          cz + Math.cos(t * 0.38 + ph) * r2
        );
        g.rotation.y = Math.atan2(Math.cos(t * 0.5 + ph) * r1 * 0.5, -Math.sin(t * 0.38 + ph) * r2 * 0.38);
      });
    }
  }

  function grassTufts(scene) {
    const geo = new THREE.ConeGeometry(0.05, 0.28, 5);
    const mat = M(0xffffff, { roughness: 1 });
    const inst = new THREE.InstancedMesh(geo, mat, 420);
    const m4 = new THREE.Matrix4();
    const col3 = new THREE.Color();
    let placed = 0;
    for (let i = 0; i < 420 && placed < 420; i++) {
      const x = MU.rand(-44, 44), z = MU.rand(-29, 29);
      if (colliders.some((c) => (x - c.x) ** 2 + (z - c.z) ** 2 < (c.r + 0.4) ** 2)) continue;
      m4.makeRotationY(Math.random() * 3);
      m4.setPosition(x, 0.13, z);
      inst.setMatrixAt(placed, m4);
      col3.setHSL(T.tuftH + Math.random() * 0.05, T.tuftS, T.tuftL + Math.random() * 0.1);
      inst.setColorAt(placed, col3);
      placed++;
    }
    inst.count = placed;
    inst.receiveShadow = true;
    scene.add(inst);
  }

  // ---------- playground: swings, slide, seesaw ----------
  function playground(scene, x, z) {
    const wood = M(0xc98d4e, { roughness: 0.8 });
    const metal = M(0xd8535f, { metalness: 0.35, roughness: 0.4 });
    const metal2 = M(0x3ecbe8, { metalness: 0.35, roughness: 0.4 });

    // --- swing frame + two animated swings ---
    const sx = x - 3, sz = z;
    for (const off of [-1.6, 1.6]) {
      const legL = cyl(scene, 0.07, 0.09, 2.6, sx + off, 1.3, sz - 0.7, metal, 10);
      legL.rotation.x = 0.25;
      const legR = cyl(scene, 0.07, 0.09, 2.6, sx + off, 1.3, sz + 0.7, metal, 10);
      legR.rotation.x = -0.25;
    }
    const bar = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 3.4, 10), metal);
    bar.rotation.z = Math.PI / 2;
    bar.position.set(sx, 2.5, sz);
    bar.castShadow = true;
    scene.add(bar);
    for (const off of [-0.8, 0.8]) {
      const sw = new THREE.Group();
      const ropeM = M(0x9a8a6a);
      for (const rx of [-0.25, 0.25]) {
        const rope = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 1.7, 6), ropeM);
        rope.position.set(rx, -0.85, 0);
        sw.add(rope);
      }
      const seat = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.06, 0.3), wood);
      seat.position.y = -1.72;
      seat.castShadow = true;
      sw.add(seat);
      sw.position.set(sx + off, 2.5, sz);
      scene.add(sw);
      const ph = Math.random() * 6;
      animated.push((t) => { sw.rotation.x = Math.sin(t * 1.6 + ph) * 0.38; });
    }
    col(sx, sz - 0.7, 0.4); col(sx, sz + 0.7, 0.4);

    // --- slide ---
    const lx = x + 2.5, lz = z - 1;
    const ladder = new THREE.Mesh(new THREE.BoxGeometry(0.6, 1.7, 0.12), wood);
    ladder.position.set(lx - 0.9, 0.85, lz);
    ladder.rotation.z = 0.18;
    scene.add(ladder);
    const platform = new THREE.Mesh(new THREE.BoxGeometry(0.7, 0.1, 0.7), wood);
    platform.position.set(lx - 0.3, 1.65, lz);
    platform.castShadow = true;
    scene.add(platform);
    const chute = new THREE.Mesh(new THREE.BoxGeometry(2.2, 0.09, 0.65), metal2);
    chute.position.set(lx + 0.85, 0.95, lz);
    chute.rotation.z = -0.62;
    chute.castShadow = true;
    scene.add(chute);
    for (const cz2 of [-0.34, 0.34]) {
      const rail = new THREE.Mesh(new THREE.BoxGeometry(2.2, 0.16, 0.05), metal2);
      rail.position.set(lx + 0.85, 1.03, lz + cz2);
      rail.rotation.z = -0.62;
      scene.add(rail);
    }
    col(lx, lz, 0.9);

    // --- seesaw ---
    const ssx = x, ssz = z + 3.2;
    const base = new THREE.Mesh(new THREE.ConeGeometry(0.35, 0.6, 12), metal);
    base.position.set(ssx, 0.3, ssz);
    base.castShadow = true;
    scene.add(base);
    const plank = new THREE.Mesh(new THREE.BoxGeometry(3.4, 0.09, 0.42), wood);
    plank.position.set(ssx, 0.62, ssz);
    plank.castShadow = true;
    scene.add(plank);
    for (const hx of [-1.5, 1.5]) {
      const grip = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.3, 8), metal);
      grip.rotation.x = Math.PI / 2;
      grip.position.set(hx, 0.12, 0);
      plank.add(grip);
    }
    animated.push((t) => { plank.rotation.z = Math.sin(t * 0.9) * 0.16; });
    col(ssx, ssz, 0.5);

    spot(x - 5.5, z + 1.5, 1.2); spot(x + 4.5, z + 2.5, -1.9); spot(x - 1, z - 2.5, 0.2);
  }

  // ---------- pond with ducks, reeds and lily pads ----------
  function pond(scene, x, z, r) {
    const water = new THREE.Mesh(
      new THREE.CylinderGeometry(r, r, 0.1, 36),
      M(T.water, { roughness: 0.12, transparent: true, opacity: 0.92, emissive: 0x0a2e3a })
    );
    water.position.set(x, 0.06, z);
    scene.add(water);
    const rim = new THREE.Mesh(new THREE.TorusGeometry(r, 0.22, 10, 40), M(0x6b5a40, { roughness: 1 }));
    rim.rotation.x = Math.PI / 2;
    rim.position.set(x, 0.1, z);
    scene.add(rim);
    animated.push((t) => { water.rotation.y = t * 0.12; });
    // reeds
    const reedM = M(T.stem);
    for (let i = 0; i < 10; i++) {
      const a = Math.random() * Math.PI * 2;
      const rr = r + 0.35 + Math.random() * 0.4;
      const h = 0.7 + Math.random() * 0.6;
      const reed = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.035, h, 6), reedM);
      reed.position.set(x + Math.cos(a) * rr, h / 2, z + Math.sin(a) * rr);
      reed.rotation.z = (Math.random() - 0.5) * 0.2;
      scene.add(reed);
      const tip = new THREE.Mesh(new THREE.CapsuleGeometry(0.05, 0.18, 4, 8), M(0x8a5a3c));
      tip.position.set(reed.position.x, h + 0.1, reed.position.z);
      scene.add(tip);
    }
    // lily pads
    for (let i = 0; i < 4; i++) {
      const a = Math.random() * Math.PI * 2, rr = Math.random() * (r - 1);
      const pad = new THREE.Mesh(new THREE.CircleGeometry(0.3 + Math.random() * 0.2, 12), M(0x4a9a4a));
      pad.rotation.x = -Math.PI / 2;
      pad.position.set(x + Math.cos(a) * rr, 0.13, z + Math.sin(a) * rr);
      scene.add(pad);
    }
    // ducks paddling in circles
    for (let i = 0; i < 3; i++) {
      const g = new THREE.Group();
      const bodyC = i === 2 ? 0xf5e6c4 : 0xf0c04a;
      const body = new THREE.Mesh(new THREE.SphereGeometry(0.24, 14, 10), M(bodyC, { roughness: 0.7 }));
      body.scale.set(1.25, 0.85, 1);
      g.add(body);
      const head = new THREE.Mesh(new THREE.SphereGeometry(0.13, 12, 9), M(i === 1 ? 0x2e6b3e : bodyC));
      head.position.set(0.22, 0.24, 0);
      g.add(head);
      const beak = new THREE.Mesh(new THREE.ConeGeometry(0.05, 0.14, 8), M(0xff8b4a));
      beak.rotation.z = -Math.PI / 2;
      beak.position.set(0.38, 0.22, 0);
      g.add(beak);
      scene.add(g);
      const orbitR = 0.8 + i * 0.75, speed = 0.4 + i * 0.15, ph = i * 2.2;
      animated.push((t) => {
        const a = t * speed + ph;
        g.position.set(x + Math.cos(a) * orbitR, 0.16 + Math.sin(t * 3 + ph) * 0.02, z + Math.sin(a) * orbitR);
        g.rotation.y = -a;
      });
    }
    col(x, z, r + 0.35);
    spot(x + r + 1.6, z - 1, -0.8); spot(x - r - 1.4, z + 1.5, 1.9);
  }

  // ---------- windmill with rotating blades ----------
  function windmill(scene, x, z) {
    const towerM = M(0xd9cdb8, { roughness: 0.8 });
    const tower = new THREE.Mesh(new THREE.CylinderGeometry(1.0, 1.6, 5.5, 14), towerM);
    tower.position.set(x, 2.75, z);
    tower.castShadow = true;
    tower.receiveShadow = true;
    scene.add(occ(tower));
    const cap = new THREE.Mesh(new THREE.ConeGeometry(1.25, 1.3, 14), M(0xa8503e, { roughness: 0.7 }));
    cap.position.set(x, 6.1, z);
    cap.castShadow = true;
    scene.add(cap);
    const door = new THREE.Mesh(new THREE.BoxGeometry(0.7, 1.3, 0.1), M(0x5b4030));
    door.position.set(x, 0.65, z + 1.52);
    scene.add(door);
    const win = new THREE.Mesh(new THREE.CircleGeometry(0.28, 12), M(0xbfe3ef, { emissive: 0x3a5866 }));
    win.position.set(x, 3.6, z + 1.23);
    scene.add(win);
    // hub + 4 blades on the front face
    const hub = new THREE.Group();
    const hubBall = new THREE.Mesh(new THREE.SphereGeometry(0.22, 12, 10), M(0x5b4030));
    hub.add(hubBall);
    const bladeM = M(0xf2ede2, { side: THREE.DoubleSide, roughness: 0.85 });
    const armM = M(0x8a6844);
    for (let i = 0; i < 4; i++) {
      const arm = new THREE.Mesh(new THREE.BoxGeometry(0.1, 2.6, 0.1), armM);
      arm.position.y = 1.3;
      const blade = new THREE.Mesh(new THREE.PlaneGeometry(0.75, 2.1), bladeM);
      blade.position.set(0.42, 1.5, 0);
      const wing = new THREE.Group();
      wing.add(arm); wing.add(blade);
      wing.rotation.z = (i * Math.PI) / 2;
      hub.add(wing);
    }
    hub.position.set(x, 5.4, z + 1.35);
    scene.add(hub);
    animated.push((t) => { hub.rotation.z = t * 0.55; });
    col(x, z, 1.9);
    spot(x - 2.6, z + 1.2, 1.4); spot(x + 2.4, z - 1.6, -2.2);
  }

  // ---------- balloon stand ----------
  function balloons(scene, x, z) {
    const cart = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.7, 0.6), M(0xd8535f, { roughness: 0.6 }));
    cart.position.set(x, 0.45, z);
    cart.castShadow = true;
    scene.add(cart);
    for (const wz of [-0.32, 0.32]) {
      const wheel = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.16, 0.06, 12), M(0x3a3f4c));
      wheel.rotation.x = Math.PI / 2;
      wheel.position.set(x - 0.25, 0.16, z + wz);
      scene.add(wheel);
    }
    const stringM = M(0xdddddd);
    const cols = [0xff5d6c, 0xffb43a, 0x4cd97b, 0x3ecbe8, 0xf068c0];
    cols.forEach((c, i) => {
      const a = (i / cols.length) * Math.PI * 2;
      const bx = Math.cos(a) * 0.28, bz = Math.sin(a) * 0.28;
      const str = new THREE.Mesh(new THREE.CylinderGeometry(0.008, 0.008, 1.5, 4), stringM);
      str.position.set(x + bx, 1.55, z + bz);
      scene.add(str);
      const b = new THREE.Mesh(new THREE.SphereGeometry(0.22, 12, 10), M(c, { roughness: 0.3 }));
      b.scale.y = 1.15;
      scene.add(b);
      const ph = i * 1.4;
      animated.push((t) => {
        b.position.set(
          x + bx + Math.sin(t * 1.1 + ph) * 0.06,
          2.42 + Math.sin(t * 1.7 + ph) * 0.08,
          z + bz + Math.cos(t * 0.9 + ph) * 0.06
        );
      });
    });
    col(x, z, 0.55);
  }

  // ---------- birds circling overhead ----------
  function birds(scene) {
    for (let i = 0; i < 4; i++) {
      const g = new THREE.Group();
      const wm = M(0x3a3f4c, { side: THREE.DoubleSide });
      const wingGeo = new THREE.PlaneGeometry(0.5, 0.2);
      const w1 = new THREE.Mesh(wingGeo, wm);
      const w2 = new THREE.Mesh(wingGeo, wm);
      w1.position.x = -0.25; w2.position.x = 0.25;
      g.add(w1); g.add(w2);
      scene.add(g);
      const cx = MU.rand(-25, 25), cz = MU.rand(-18, 18);
      const r = MU.rand(10, 22), h = MU.rand(9, 15), sp = MU.rand(0.12, 0.22), ph = Math.random() * 9;
      animated.push((t) => {
        const flap = Math.sin(t * 9 + ph) * 0.7;
        w1.rotation.y = flap; w2.rotation.y = -flap;
        const a = t * sp + ph;
        g.position.set(cx + Math.cos(a) * r, h + Math.sin(t * 0.7 + ph), cz + Math.sin(a) * r);
        g.rotation.y = -a;
      });
    }
  }

  // ---------- small props ----------
  function mushrooms(scene, x, z) {
    for (let i = 0; i < 3; i++) {
      const mx = x + MU.rand(-0.7, 0.7), mz = z + MU.rand(-0.7, 0.7);
      const h = 0.14 + Math.random() * 0.12;
      const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.07, h, 8), M(0xf2ede2));
      stem.position.set(mx, h / 2, mz);
      scene.add(stem);
      const cap = new THREE.Mesh(new THREE.SphereGeometry(0.12, 12, 8, 0, Math.PI * 2, 0, Math.PI / 2), M(0xd8535f));
      cap.position.set(mx, h, mz);
      cap.castShadow = true;
      scene.add(cap);
    }
  }
  function stump(scene, x, z) {
    const s = cyl(scene, 0.4, 0.5, 0.5, x, 0.25, z, M(0x7a5230, { roughness: 1 }), 14);
    const top = new THREE.Mesh(new THREE.CircleGeometry(0.4, 14), M(0xc9a878));
    top.rotation.x = -Math.PI / 2;
    top.position.set(x, 0.51, z);
    scene.add(top);
    col(x, z, 0.55);
  }
  function pumpkin(scene, x, z) {
    const p = new THREE.Mesh(new THREE.SphereGeometry(0.3, 14, 10), M(0xe8762c, { roughness: 0.6 }));
    p.scale.y = 0.8;
    p.position.set(x, 0.24, z);
    p.castShadow = true;
    scene.add(p);
    const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.06, 0.16, 8), M(0x3f7a3a));
    stem.position.set(x, 0.52, z);
    scene.add(stem);
  }

  function rocks(scene) {
    const mat = M(0x8d93a3, { roughness: 0.9 });
    const mat2 = M(0x767c8c, { roughness: 0.9 });
    const spotsR = [[-8, -22], [15, 8], [-36, 20], [42, 16], [-22, 10], [6, 22], [38, -26], [-44, -8]];
    for (const [x, z] of spotsR) {
      const s = MU.rand(0.35, 0.9);
      const r = new THREE.Mesh(new THREE.DodecahedronGeometry(s, 0), Math.random() < 0.5 ? mat : mat2);
      r.position.set(x, s * 0.55, z);
      r.rotation.set(Math.random() * 3, Math.random() * 3, Math.random() * 3);
      r.castShadow = true;
      r.receiveShadow = true;
      scene.add(r);
      col(x, z, s + 0.15);
    }
  }

  // ---------- build ----------
  function build(scene, theme) {
    themeName = THEMES[theme] ? theme : "park";
    T = THEMES[themeName];
    colliders.length = 0;
    decoySpots.length = 0;
    animated.length = 0;
    occluders.length = 0;
    const grassTex = speckleTexture(T.ground[0], T.ground[1], T.ground[2], 1400);
    grassTex.repeat.set(26, 18);
    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(SIZE.x + 30, SIZE.z + 30, 1, 1),
      M(0xffffff, { map: grassTex, roughness: 1 })
    );
    ground.rotation.x = -Math.PI / 2;
    ground.receiveShadow = true;
    scene.add(ground);

    const sandTex = speckleTexture(T.sand[0], T.sand[1], T.sand[2], 800);
    path(scene, -30, -12, 0, 0, 3, sandTex);
    path(scene, 0, 0, 18, -20, 3, sandTex);
    path(scene, 0, 0, 34, -8, 2.5, sandTex);
    path(scene, 0, 0, -14, 20, 3, sandTex);
    path(scene, 0, 0, 22, 16, 3, sandTex);

    colonnade(scene, -30, -12);
    fountain(scene, 0, 0);
    house(scene, 18, -21, 0.35, 0xd8b46a, 0xa8503e);
    house(scene, 35, -8, -0.9, 0xc7d3e0, 0x50657f);
    house(scene, -14, 21, 2.7, 0xd9a4a4, 0x7c4646);

    hedge(scene, 18, 12, 8, 0);
    hedge(scene, 26, 16, 8, Math.PI / 2);
    hedge(scene, 20, 22, 10, 0);
    hedge(scene, 13, 17, 7, Math.PI / 2);
    spot(16, 15, 0.5); spot(22, 14, -0.4); spot(18, 19.5, 2.5);
    spot(24, 20, 1.2); spot(15, 20.5, -1.8);
    flowerbed(scene, 30, 20, 2.2, 0xe66aa8);
    flowerbed(scene, 10, 24, 1.8, 0xf0c04a);
    flowerbed(scene, -6, -6, 1.6, 0xe66a6a);

    const trees = [
      [-38, 8, "pine", 1.2], [-33, 16, "oak", 1], [-24, 24, "pine", 1],
      [-40, -24, "oak", 1.1], [-16, -24, "pine", 1.3], [-6, -18, "oak", 0.9],
      [8, -26, "pine", 1.1], [10, -10, "oak", 1], [28, -24, "autumn", 1],
      [42, -20, "pine", 1.2], [40, 4, "oak", 1.1], [36, 24, "pine", 1.4],
      [4, 14, "autumn", 0.9], [-4, 26, "oak", 1.2], [-28, 2, "autumn", 0.9],
      [-42, 26, "pine", 1], [12, 4, "pine", 0.8],
    ];
    for (const [x, z, k, s] of trees) tree(scene, x, z, k, s);
    spot(-36, 6, 0.8); spot(-31, 14, -0.6); spot(-8, -16, 0.2);
    spot(9, -12, 2.2); spot(30, -22, -1.4); spot(38, 2, 1.7);
    spot(-2, 24, 0.4); spot(-26, 0, -2.2); spot(5, -24, 1.1);
    spot(44, -12, -0.8); spot(-20, -20, 1.9); spot(26, 4, 2.8);

    lamppost(scene, -15, -6); lamppost(scene, 8, -4); lamppost(scene, 10, 8);
    lamppost(scene, 26, -14); lamppost(scene, -8, 10); lamppost(scene, 32, 12);
    bench(scene, -6, 6, 0.6); bench(scene, 6, 6, -0.6);
    bench(scene, -22, -16, 1.4); bench(scene, 24, -4, 2.4);

    // perimeter fence
    const fenceMat = M(0x776a55, { roughness: 0.9 });
    for (let x = -46; x <= 46; x += 4) {
      box(scene, 0.18, 1.2, 0.18, x, 0.6, -31, fenceMat);
      box(scene, 0.18, 1.2, 0.18, x, 0.6, 31, fenceMat);
    }
    for (let z = -31; z <= 31; z += 4) {
      box(scene, 0.18, 1.2, 0.18, -46, 0.6, z, fenceMat);
      box(scene, 0.18, 1.2, 0.18, 46, 0.6, z, fenceMat);
    }
    box(scene, 92.4, 0.3, 0.12, 0, 1.05, -31, fenceMat);
    box(scene, 92.4, 0.3, 0.12, 0, 1.05, 31, fenceMat);
    box(scene, 0.12, 0.3, 62.4, -46, 1.05, 0, fenceMat);
    box(scene, 0.12, 0.3, 62.4, 46, 1.05, 0, fenceMat);
    box(scene, 92.4, 0.14, 0.1, 0, 0.55, -31, fenceMat);
    box(scene, 92.4, 0.14, 0.1, 0, 0.55, 31, fenceMat);
    box(scene, 0.1, 0.14, 62.4, -46, 0.55, 0, fenceMat);
    box(scene, 0.1, 0.14, 62.4, 46, 0.55, 0, fenceMat);

    // new attractions
    playground(scene, -33, 22);
    pond(scene, -27, -23, 4.5);
    windmill(scene, 42, 26);
    balloons(scene, -3, -9);
    birds(scene);
    mushrooms(scene, -15, -21); mushrooms(scene, 11, -8); mushrooms(scene, 37, 20);
    mushrooms(scene, -25, 3); mushrooms(scene, 8, 26);
    stump(scene, -9, 14); stump(scene, 24, -27);
    pumpkin(scene, 5, 15.6); pumpkin(scene, 29, -22.4); pumpkin(scene, -26.6, 3.6);

    rocks(scene);
    grassTufts(scene);
    clouds(scene);
    butterflies(scene);
  }

  function tick(t, dt) {
    for (const fn of animated) fn(t, dt);
  }

  // keep a position inside the map and outside solid props
  function clampPos(p) {
    p.x = MU.clamp(p.x, -44.5, 44.5);
    p.z = MU.clamp(p.z, -29.5, 29.5);
    for (const c of colliders) {
      const dx = p.x - c.x, dz = p.z - c.z;
      const d2 = dx * dx + dz * dz;
      const min = c.r + 0.35;
      if (d2 < min * min && d2 > 1e-6) {
        const d = Math.sqrt(d2);
        p.x = c.x + (dx / d) * min;
        p.z = c.z + (dz / d) * min;
      }
    }
    return p;
  }

  function randomOpenSpot(rng) {
    rng = rng || Math.random;
    for (let i = 0; i < 40; i++) {
      const p = { x: (rng() * 2 - 1) * 42, z: (rng() * 2 - 1) * 27 };
      if (colliders.every((c) => (p.x - c.x) ** 2 + (p.z - c.z) ** 2 > (c.r + 1) ** 2)) return p;
    }
    return { x: 0, z: -8 };
  }

  return { SIZE, build, tick, clampPos, randomOpenSpot, decoySpots, colliders, occluders,
           THEME_NAMES: Object.keys(THEMES), theme: () => themeName };
})();
