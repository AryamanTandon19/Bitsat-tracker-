// Procedural 3D park: museum colonnade, houses, trees, fountain plaza,
// hedge garden, lampposts, benches — mannequin spots scattered across all
// of them. Everything is low-poly primitives: instant load, runs on phones.
window.WORLD3D = (function () {
  const SIZE = { x: 92, z: 62 }; // half extents: ±46, ±31
  const colliders = []; // {x,z,r} circles players can't walk through
  const decoySpots = []; // {x,z,yaw} where mannequins may stand

  function col(x, z, r) { colliders.push({ x, z, r }); }
  function spot(x, z, yaw) { decoySpots.push({ x, z, yaw: yaw || 0 }); }

  const M = (c, extra) => new THREE.MeshLambertMaterial(Object.assign({ color: c }, extra));

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
    const m = new THREE.Mesh(new THREE.CylinderGeometry(rt, rb, h, seg || 10), mat);
    m.position.set(x, y, z);
    m.castShadow = true;
    m.receiveShadow = true;
    scene.add(m);
    return m;
  }

  // ---------- pieces ----------
  function tree(scene, x, z, kind, s) {
    s = s || 1;
    const trunk = M(0x7a5230);
    if (kind === "pine") {
      cyl(scene, 0.14 * s, 0.2 * s, 1.4 * s, x, 0.7 * s, z, trunk, 7);
      const g = M(0x2e6b3e);
      for (let i = 0; i < 3; i++) {
        const r = (1.5 - i * 0.38) * s, y = (1.6 + i * 1.0) * s;
        const cone = new THREE.Mesh(new THREE.ConeGeometry(r, 1.6 * s, 8), g);
        cone.position.set(x, y, z);
        cone.castShadow = true;
        scene.add(cone);
      }
      col(x, z, 0.5 * s);
    } else {
      cyl(scene, 0.16 * s, 0.24 * s, 1.8 * s, x, 0.9 * s, z, trunk, 7);
      const g = M(kind === "autumn" ? 0xc27b3a : 0x4a8a4a);
      const blobs = [[0, 2.6, 0, 1.3], [0.8, 2.2, 0.3, 0.9], [-0.7, 2.3, -0.4, 0.95], [0.1, 3.2, 0.5, 0.8]];
      for (const [bx, by, bz, br] of blobs) {
        const s2 = new THREE.Mesh(new THREE.SphereGeometry(br * s, 8, 6), g);
        s2.position.set(x + bx * s, by * s, z + bz * s);
        s2.castShadow = true;
        scene.add(s2);
      }
      col(x, z, 0.5 * s);
    }
  }

  function house(scene, x, z, ry, bodyC, roofC) {
    const g = new THREE.Group();
    const w = 6, d = 4.6, h = 3;
    const body = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), M(bodyC));
    body.position.y = h / 2;
    body.castShadow = true; body.receiveShadow = true;
    g.add(body);
    const roof = new THREE.Mesh(new THREE.ConeGeometry(Math.hypot(w, d) / 2 + 0.4, 2.2, 4), M(roofC));
    roof.position.y = h + 1.1;
    roof.rotation.y = Math.PI / 4;
    roof.castShadow = true;
    g.add(roof);
    const door = new THREE.Mesh(new THREE.BoxGeometry(1.1, 1.9, 0.12), M(0x5b4030));
    door.position.set(0.8, 0.95, d / 2 + 0.06);
    g.add(door);
    for (const wx of [-1.7, -0.4]) {
      const win = new THREE.Mesh(new THREE.BoxGeometry(1.0, 0.9, 0.1), M(0xbfe3ef, { emissive: 0x223644 }));
      win.position.set(wx, 1.7, d / 2 + 0.05);
      g.add(win);
    }
    const chim = new THREE.Mesh(new THREE.BoxGeometry(0.6, 1.6, 0.6), M(0x8d7364));
    chim.position.set(-w / 2 + 0.9, h + 1.3, -0.8);
    g.add(chim);
    g.position.set(x, 0, z);
    g.rotation.y = ry;
    scene.add(g);
    // fat circle colliders approximating the box
    const c = Math.cos(ry), s = Math.sin(ry);
    for (const off of [-1.8, 0, 1.8]) {
      col(x + off * c, z - off * s, 2.35);
    }
    // mannequin spots by the walls
    const front = { x: x + 2.6 * s + 0 * c, z: z + 2.6 * c };
    spot(x + (w / 2 + 1) * c, z - (w / 2 + 1) * s, ry + Math.PI / 2);
    spot(x - (w / 2 + 1) * c, z + (w / 2 + 1) * s, ry - Math.PI / 2);
    spot(x + 2.2 * s + 2 * c, z + (d / 2 + 1.2) * c - 2 * s, ry);
  }

  function fountain(scene, x, z) {
    cyl(scene, 3.2, 3.5, 0.7, x, 0.35, z, M(0x9aa0b4), 18);
    const water = new THREE.Mesh(new THREE.CylinderGeometry(2.9, 2.9, 0.15, 18), M(0x4fc3dd, { emissive: 0x0d3a46 }));
    water.position.set(x, 0.72, z);
    scene.add(water);
    cyl(scene, 0.4, 0.55, 1.8, x, 1.3, z, M(0x9aa0b4), 10);
    const bowl = new THREE.Mesh(new THREE.CylinderGeometry(1.1, 0.7, 0.5, 12), M(0x9aa0b4));
    bowl.position.set(x, 2.3, z);
    bowl.castShadow = true;
    scene.add(bowl);
    col(x, z, 3.7);
    for (let i = 0; i < 6; i++) {
      const a = (i / 6) * Math.PI * 2;
      spot(x + Math.cos(a) * 5.4, z + Math.sin(a) * 5.4, -a + Math.PI / 2);
    }
  }

  function colonnade(scene, cx, cz) {
    // open museum pavilion: marble floor, columns, flat roof, pedestal rows
    const floor = box(scene, 18, 0.3, 12, cx, 0.15, cz, M(0xcfc8bc));
    const colMat = M(0xe2dbcf);
    const positions = [];
    for (const px of [-8, -2.7, 2.7, 8]) for (const pz of [-5, 5]) positions.push([px, pz]);
    for (const [px, pz] of positions) {
      cyl(scene, 0.35, 0.42, 4.2, cx + px, 2.4, cz + pz, colMat, 10);
      col(cx + px, cz + pz, 0.6);
    }
    // emissive so the underside isn't a black slab against the sky
    box(scene, 19, 0.5, 13, cx, 4.8, cz, M(0xbfb7a8, { emissive: 0x5a5348 }), 0, true);
    const ped = new THREE.Mesh(new THREE.ConeGeometry(0.001, 0.001, 3), M(0x000000)); // noop keeper
    // pedestal decoy spots inside, two rows
    for (const px of [-6.4, -3.2, 0, 3.2, 6.4]) {
      spot(cx + px, cz - 2.6, 0);
      spot(cx + px, cz + 2.6, Math.PI);
    }
  }

  function hedge(scene, x, z, len, ry) {
    box(scene, len, 1.5, 1.0, x, 0.75, z, M(0x3c7a44), ry, true);
    const c = Math.cos(ry || 0), s = Math.sin(ry || 0);
    const n = Math.max(1, Math.round(len / 2));
    for (let i = 0; i < n; i++) {
      const t = -len / 2 + (i + 0.5) * (len / n);
      col(x + t * c, z - t * s, 1.0);
    }
  }

  function lamppost(scene, x, z) {
    cyl(scene, 0.07, 0.1, 3.4, x, 1.7, z, M(0x3a3f4c), 8);
    const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.28, 8, 6), M(0xffe9a3, { emissive: 0x8a6d1f }));
    bulb.position.set(x, 3.5, z);
    scene.add(bulb);
    col(x, z, 0.35);
  }

  function bench(scene, x, z, ry) {
    box(scene, 2.0, 0.12, 0.55, x, 0.55, z, M(0x8a6844), ry, true);
    const c = Math.cos(ry), s = Math.sin(ry);
    box(scene, 2.0, 0.5, 0.1, x - 0.28 * s, 0.9, z - 0.28 * c, M(0x8a6844), ry);
    col(x, z, 0.8);
  }

  function flowerbed(scene, x, z, r, c1) {
    const disc = new THREE.Mesh(new THREE.CylinderGeometry(r, r, 0.12, 14), M(0x6b4d33));
    disc.position.set(x, 0.06, z);
    disc.receiveShadow = true;
    scene.add(disc);
    for (let i = 0; i < 9; i++) {
      const a = Math.random() * Math.PI * 2, rr = Math.random() * (r - 0.4);
      const f = new THREE.Mesh(new THREE.SphereGeometry(0.14, 6, 5), M(c1, { emissive: 0x1c0f18 }));
      f.position.set(x + Math.cos(a) * rr, 0.24, z + Math.sin(a) * rr);
      scene.add(f);
    }
  }

  function path(scene, x1, z1, x2, z2, w) {
    const dx = x2 - x1, dz = z2 - z1;
    const len = Math.hypot(dx, dz);
    const p = box(scene, len, 0.08, w, (x1 + x2) / 2, 0.04, (z1 + z2) / 2, M(0xb9a583));
    p.rotation.y = -Math.atan2(dz, dx);
  }

  // ---------- build ----------
  function build(scene) {
    // ground
    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(SIZE.x + 30, SIZE.z + 30, 1, 1),
      M(0x679a53)
    );
    ground.rotation.x = -Math.PI / 2;
    ground.receiveShadow = true;
    scene.add(ground);

    // paths connecting the zones
    path(scene, -30, -12, 0, 0, 3);
    path(scene, 0, 0, 18, -20, 3);
    path(scene, 0, 0, 34, -8, 2.5);
    path(scene, 0, 0, -14, 20, 3);
    path(scene, 0, 0, 22, 16, 3);

    // zones
    colonnade(scene, -30, -12);
    fountain(scene, 0, 0);
    house(scene, 18, -21, 0.35, 0xd8b46a, 0xa8503e);
    house(scene, 35, -8, -0.9, 0xc7d3e0, 0x50657f);
    house(scene, -14, 21, 2.7, 0xd9a4a4, 0x7c4646);

    // hedge garden (SE)
    hedge(scene, 18, 12, 8, 0);
    hedge(scene, 26, 16, 8, Math.PI / 2);
    hedge(scene, 20, 22, 10, 0);
    hedge(scene, 13, 17, 7, Math.PI / 2);
    spot(16, 15, 0.5); spot(22, 14, -0.4); spot(18, 19.5, 2.5);
    spot(24, 20, 1.2); spot(15, 20.5, -1.8);
    flowerbed(scene, 30, 20, 2.2, 0xe66aa8);
    flowerbed(scene, 10, 24, 1.8, 0xf0c04a);

    // trees scattered around the park
    const trees = [
      [-38, 8, "pine", 1.2], [-33, 16, "oak", 1], [-24, 24, "pine", 1],
      [-40, -24, "oak", 1.1], [-16, -24, "pine", 1.3], [-6, -18, "oak", 0.9],
      [8, -26, "pine", 1.1], [10, -10, "oak", 1], [28, -24, "autumn", 1],
      [42, -20, "pine", 1.2], [40, 4, "oak", 1.1], [36, 24, "pine", 1.4],
      [4, 14, "autumn", 0.9], [-4, 26, "oak", 1.2], [-28, 2, "autumn", 0.9],
      [-42, 26, "pine", 1], [12, 4, "pine", 0.8],
    ];
    for (const [x, z, k, s] of trees) tree(scene, x, z, k, s);
    // decoy spots near some trees / open lawn
    spot(-36, 6, 0.8); spot(-31, 14, -0.6); spot(-8, -16, 0.2);
    spot(9, -12, 2.2); spot(30, -22, -1.4); spot(38, 2, 1.7);
    spot(-2, 24, 0.4); spot(-26, 0, -2.2); spot(5, -24, 1.1);
    spot(44, -12, -0.8); spot(-20, -20, 1.9); spot(26, 4, 2.8);

    // furniture
    lamppost(scene, -15, -6); lamppost(scene, 8, -4); lamppost(scene, 10, 8);
    lamppost(scene, 26, -14); lamppost(scene, -8, 10); lamppost(scene, 32, 12);
    bench(scene, -6, 6, 0.6); bench(scene, 6, 6, -0.6);
    bench(scene, -22, -16, 1.4); bench(scene, 24, -4, 2.4);

    // perimeter fence
    const fenceMat = M(0x776a55);
    for (let x = -46; x <= 46; x += 4) {
      box(scene, 0.18, 1.2, 0.18, x, 0.6, -31, fenceMat);
      box(scene, 0.18, 1.2, 0.18, x, 0.6, 31, fenceMat);
    }
    for (let z = -31; z <= 31; z += 4) {
      box(scene, 0.18, 1.2, 0.18, -46, 0.6, z, fenceMat);
      box(scene, 0.18, 1.2, 0.18, 46, 0.6, z, fenceMat);
    }
    box(scene, 92.4, 0.35, 0.14, 0, 1.05, -31, fenceMat);
    box(scene, 92.4, 0.35, 0.14, 0, 1.05, 31, fenceMat);
    const fz1 = box(scene, 0.14, 0.35, 62.4, -46, 1.05, 0, fenceMat);
    const fz2 = box(scene, 0.14, 0.35, 62.4, 46, 1.05, 0, fenceMat);
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

  return { SIZE, build, clampPos, randomOpenSpot, decoySpots, colliders };
})();
