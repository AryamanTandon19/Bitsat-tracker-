// Three.js scene management: lighting moods per phase, third-person camera,
// the seeker's flashlight (a real spotlight at dusk), glowing orbs, raycasts.
window.RENDER3D = (function () {
  let renderer, scene, camera;
  let sun, hemi, flash, flashTarget, coneMesh;
  let orbMeshes = {};
  const raycaster = new THREE.Raycaster();
  let canvas;
  let camPos = new THREE.Vector3(0, 8, 14);
  let time = 0;
  let shakeAmt = 0;
  let worldGroup = null;
  let skyDome = null, skyCtx = null, skyTex = null, sunSprite = null, stars = null;

  // painted gradient sky: golden day, sunset-band dusk
  const SKY_STOPS = {
    park: {
      day:  [["0", "#3fa9dd"], ["0.55", "#8fd6f0"], ["0.78", "#ffe6b0"], ["1", "#ffd28a"]],
      dusk: [["0", "#0d1030"], ["0.5", "#33265e"], ["0.78", "#b34a6e"], ["1", "#ff8a5c"]],
    },
    snow: {
      day:  [["0", "#7fb8e0"], ["0.55", "#c8e4f2"], ["0.8", "#ffeed6"], ["1", "#ffdcae"]],
      dusk: [["0", "#0e1434"], ["0.5", "#2c3163"], ["0.78", "#7e5d9e"], ["1", "#e8907c"]],
    },
  };
  let skyTheme = "park";

  function paintSky(moodName) {
    const stops = (SKY_STOPS[skyTheme] || SKY_STOPS.park)[moodName] || SKY_STOPS.park.day;
    const g = skyCtx.createLinearGradient(0, 0, 0, 256);
    for (const [p, c] of stops) g.addColorStop(parseFloat(p), c);
    skyCtx.fillStyle = g;
    skyCtx.fillRect(0, 0, 16, 256);
    skyTex.needsUpdate = true;
  }

  function initSky() {
    const c = document.createElement("canvas");
    c.width = 16; c.height = 256;
    skyCtx = c.getContext("2d");
    skyTex = new THREE.CanvasTexture(c);
    if (THREE.SRGBColorSpace) skyTex.colorSpace = THREE.SRGBColorSpace;
    skyDome = new THREE.Mesh(
      new THREE.SphereGeometry(165, 24, 16),
      new THREE.MeshBasicMaterial({ map: skyTex, side: THREE.BackSide, fog: false, depthWrite: false })
    );
    skyDome.renderOrder = -10;
    scene.add(skyDome);
    paintSky("day");

    // soft sun / moon glow
    const sc = document.createElement("canvas");
    sc.width = sc.height = 128;
    const sx = sc.getContext("2d");
    const sg = sx.createRadialGradient(64, 64, 4, 64, 64, 62);
    sg.addColorStop(0, "rgba(255,250,230,1)");
    sg.addColorStop(0.25, "rgba(255,236,170,.9)");
    sg.addColorStop(1, "rgba(255,220,140,0)");
    sx.fillStyle = sg;
    sx.fillRect(0, 0, 128, 128);
    sunSprite = new THREE.Sprite(new THREE.SpriteMaterial({
      map: new THREE.CanvasTexture(sc), transparent: true,
      blending: THREE.AdditiveBlending, depthWrite: false, fog: false,
    }));
    sunSprite.scale.set(34, 34, 1);
    sunSprite.position.set(85, 55, 30);
    scene.add(sunSprite);

    // stars for dusk
    const starGeo = new THREE.BufferGeometry();
    const pos = [];
    for (let i = 0; i < 260; i++) {
      const a = Math.random() * Math.PI * 2;
      const el = Math.random() * Math.PI * 0.42 + 0.12;
      const r = 150;
      pos.push(Math.cos(a) * Math.cos(el) * r, Math.sin(el) * r, Math.sin(a) * Math.cos(el) * r);
    }
    starGeo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    stars = new THREE.Points(starGeo, new THREE.PointsMaterial({
      color: 0xdfe8ff, size: 1.6, sizeAttenuation: false, transparent: true, opacity: 0, fog: false,
    }));
    scene.add(stars);
  }

  // ---- particle pool (sprites with velocity + gravity) ----
  const POOL = 140;
  const particles = [];
  function makeDotTexture() {
    const c = document.createElement("canvas");
    c.width = c.height = 32;
    const x = c.getContext("2d");
    const g = x.createRadialGradient(16, 16, 1, 16, 16, 15);
    g.addColorStop(0, "rgba(255,255,255,1)");
    g.addColorStop(0.55, "rgba(255,255,255,.65)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    x.fillStyle = g;
    x.fillRect(0, 0, 32, 32);
    return new THREE.CanvasTexture(c);
  }
  function initParticles() {
    const dot = makeDotTexture();
    for (let i = 0; i < POOL; i++) {
      const sp = new THREE.Sprite(new THREE.SpriteMaterial({
        map: dot, color: 0xffffff, transparent: true, opacity: 0,
        blending: THREE.AdditiveBlending, depthWrite: false,
      }));
      sp.visible = false;
      scene.add(sp);
      particles.push({ sp, vel: new THREE.Vector3(), life: 0, max: 1, size: 0.1, grav: 6 });
    }
  }
  let poolIdx = 0;
  function burst(x, y, z, color, count, opts) {
    opts = opts || {};
    const spread = opts.spread || 2.2;
    const up = opts.up != null ? opts.up : 2.4;
    for (let i = 0; i < (count || 16); i++) {
      const p = particles[poolIdx];
      poolIdx = (poolIdx + 1) % POOL;
      p.sp.material.color.set(color);
      p.sp.position.set(x, y, z);
      p.vel.set(
        (Math.random() - 0.5) * spread,
        Math.random() * up + 0.6,
        (Math.random() - 0.5) * spread
      );
      p.max = p.life = 0.5 + Math.random() * 0.5;
      p.size = (opts.size || 0.12) * (0.6 + Math.random() * 0.8);
      p.grav = opts.grav != null ? opts.grav : 6;
      p.sp.visible = true;
    }
  }
  function tickParticles(dt) {
    for (const p of particles) {
      if (p.life <= 0) { if (p.sp.visible) p.sp.visible = false; continue; }
      p.life -= dt;
      p.vel.y -= p.grav * dt;
      p.sp.position.addScaledVector(p.vel, dt);
      const k = Math.max(0, p.life / p.max);
      p.sp.material.opacity = k;
      const s = p.size * (0.5 + k);
      p.sp.scale.set(s, s, 1);
    }
  }
  function shake(amt) { shakeAmt = Math.min(1, shakeAmt + amt); }

  // slight FOV widening while running = sense of speed
  let fovTarget = 60;
  function setMoving(m) { fovTarget = m ? 66 : 60; }
  function tickFov(dt) {
    if (Math.abs(camera.fov - fovTarget) > 0.05) {
      camera.fov += (fovTarget - camera.fov) * Math.min(1, dt * 5);
      camera.updateProjectionMatrix();
    }
  }

  const MOODS_BY_THEME = {
    park: {
      day:  { sky: 0x9fd4e8, fog: 0xffd9a3, sun: 1.45, hemi: 0.9, sunC: 0xffe3b0 },
      dusk: { sky: 0x1e2340, fog: 0x5c3a5e, sun: 0.3, hemi: 0.34, sunC: 0x9bb0ff },
    },
    snow: {
      day:  { sky: 0xd4e6f2, fog: 0xffe9cd, sun: 1.25, hemi: 1.0, sunC: 0xfff0d8 },
      dusk: { sky: 0x232a45, fog: 0x6b4a72, sun: 0.32, hemi: 0.4, sunC: 0xaabfff },
    },
  };
  let MOODS = MOODS_BY_THEME.park;
  let mood = "day", moodT = 1;

  function init(c) {
    canvas = c;
    renderer = new THREE.WebGLRenderer({ canvas: c, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.22;
    if (THREE.SRGBColorSpace) renderer.outputColorSpace = THREE.SRGBColorSpace;

    scene = new THREE.Scene();
    scene.background = new THREE.Color(MOODS.day.sky);
    scene.fog = new THREE.Fog(MOODS.day.fog, 55, 110);

    camera = new THREE.PerspectiveCamera(60, 1, 0.1, 200);
    camera.position.copy(camPos);

    hemi = new THREE.HemisphereLight(0xdfeeff, 0x6a7a5a, MOODS.day.hemi);
    scene.add(hemi);
    sun = new THREE.DirectionalLight(0xfff2d8, MOODS.day.sun);
    sun.position.set(45, 26, 20); // low sun = long dramatic shadows
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    const sc = sun.shadow.camera;
    sc.left = -55; sc.right = 55; sc.top = 40; sc.bottom = -40; sc.far = 120;
    scene.add(sun);

    // seeker flashlight
    flash = new THREE.SpotLight(0xffe9b0, 1.55, 16, 0.42, 0.45, 1.1);
    flash.visible = false;
    flashTarget = new THREE.Object3D();
    scene.add(flashTarget);
    flash.target = flashTarget;
    scene.add(flash);
    // faint visible beam
    coneMesh = new THREE.Mesh(
      new THREE.ConeGeometry(4.4, 11, 20, 1, true),
      new THREE.MeshBasicMaterial({
        color: 0xffe9b0, transparent: true, opacity: 0.12,
        blending: THREE.AdditiveBlending, side: THREE.DoubleSide, depthWrite: false,
      })
    );
    coneMesh.visible = false;
    scene.add(coneMesh);

    worldGroup = new THREE.Group();
    WORLD3D.build(worldGroup, "park");
    scene.add(worldGroup);
    initParticles();
    initSky();
    return { scene, camera };
  }

  // rebuild the world for a different map theme
  function setTheme(name) {
    if (name === WORLD3D.theme() && worldGroup) return;
    if (worldGroup) {
      scene.remove(worldGroup);
      worldGroup.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) {
          (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => {
            if (m.map) m.map.dispose();
            m.dispose();
          });
        }
      });
    }
    MOODS = MOODS_BY_THEME[name] || MOODS_BY_THEME.park;
    skyTheme = name;
    if (skyCtx) paintSky(mood);
    worldGroup = new THREE.Group();
    WORLD3D.build(worldGroup, name);
    scene.add(worldGroup);
    setSnow(name === "snow");
    moodT = 0; // re-lerp lighting to the themed mood
  }

  // gentle snowfall for the snow theme
  let snowSprites = null, snowOn = false;
  function setSnow(on) {
    snowOn = on;
    if (on && !snowSprites) {
      snowSprites = [];
      const mat = new THREE.SpriteMaterial({ color: 0xffffff, transparent: true, opacity: 0.85, depthWrite: false });
      for (let i = 0; i < 110; i++) {
        const sp = new THREE.Sprite(mat);
        sp.scale.set(0.07, 0.07, 1);
        sp.position.set(MU.rand(-46, 46), MU.rand(0.5, 16), MU.rand(-31, 31));
        sp.userData.v = MU.rand(0.7, 1.6);
        sp.userData.ph = Math.random() * 9;
        scene.add(sp);
        snowSprites.push(sp);
      }
    }
    if (snowSprites) for (const sp of snowSprites) sp.visible = on;
  }
  function tickSnow(dt) {
    if (!snowOn || !snowSprites) return;
    for (const sp of snowSprites) {
      sp.position.y -= sp.userData.v * dt;
      sp.position.x += Math.sin(time * 1.3 + sp.userData.ph) * dt * 0.4;
      if (sp.position.y < 0.1) {
        sp.position.y = MU.rand(12, 16);
        sp.position.x = camPos.x + MU.rand(-22, 22);
        sp.position.z = camPos.z + MU.rand(-22, 22);
      }
    }
  }

  function setMood(name) {
    mood = name;
    moodT = 0;
    if (skyCtx) paintSky(name);
    if (sunSprite) {
      const dusk = name === "dusk";
      sunSprite.position.set(dusk ? -70 : 85, dusk ? 40 : 55, dusk ? -40 : 30);
      sunSprite.scale.setScalar(dusk ? 18 : 34);
      sunSprite.material.color.set(dusk ? 0xcfd8ff : 0xffffff);
    }
  }

  function lerpMood(dt) {
    if (stars) {
      const want = mood === "dusk" ? 0.9 : 0;
      stars.material.opacity += (want - stars.material.opacity) * Math.min(1, dt * 2);
    }
    if (moodT >= 1) return;
    moodT = Math.min(1, moodT + dt * 1.2);
    const m = MOODS[mood];
    sun.intensity += (m.sun - sun.intensity) * 0.08;
    hemi.intensity += (m.hemi - hemi.intensity) * 0.08;
    sun.color.lerp(new THREE.Color(m.sunC), 0.08);
    scene.background.lerp(new THREE.Color(m.sky), 0.08);
    scene.fog.color.lerp(new THREE.Color(m.fog), 0.08);
  }

  // pos {x,z}, yaw — orient the flashlight from the seeker's hand,
  // with a gentle handheld sway and a subtle flicker
  function setFlashlight(on, pos, yaw) {
    flash.visible = coneMesh.visible = !!on && mood === "dusk";
    if (!on || !pos) return;
    const sway = Math.sin(time * 2.1) * 0.035 + Math.sin(time * 5.3) * 0.012;
    const y2 = yaw + sway;
    const fx = Math.sin(y2), fz = Math.cos(y2);
    flash.intensity = 1.55 + Math.sin(time * 23) * 0.08 + Math.random() * 0.05;
    flash.position.set(pos.x, 1.6 + Math.sin(time * 3.3) * 0.04, pos.z);
    flashTarget.position.set(pos.x + fx * 10, 0.6, pos.z + fz * 10);
    coneMesh.position.set(pos.x + fx * 5.5, 1.15, pos.z + fz * 5.5);
    coneMesh.quaternion.setFromUnitVectors(
      new THREE.Vector3(0, 1, 0),
      new THREE.Vector3(-fx, 0.16, -fz).normalize()
    );
  }

  function syncOrbs(orbs) {
    const seen = {};
    for (const o of orbs) {
      seen[o.id] = true;
      if (o.taken) {
        if (orbMeshes[o.id]) { scene.remove(orbMeshes[o.id]); delete orbMeshes[o.id]; }
        continue;
      }
      if (!orbMeshes[o.id]) {
        const g = new THREE.Group();
        const core = new THREE.Mesh(
          new THREE.IcosahedronGeometry(0.2, 1),
          new THREE.MeshBasicMaterial({ color: 0x9dffe2 })
        );
        const halo = new THREE.Mesh(
          new THREE.SphereGeometry(0.5, 16, 12),
          new THREE.MeshBasicMaterial({
            color: 0x66ffd0, transparent: true, opacity: 0.25,
            blending: THREE.AdditiveBlending, depthWrite: false,
          })
        );
        const orbit = new THREE.Mesh(
          new THREE.TorusGeometry(0.36, 0.025, 8, 28),
          new THREE.MeshBasicMaterial({
            color: 0xc2ffe9, transparent: true, opacity: 0.7,
            blending: THREE.AdditiveBlending, depthWrite: false,
          })
        );
        orbit.rotation.x = Math.PI / 2.6;
        g.add(core); g.add(halo); g.add(orbit);
        g.position.set(o.x, 0.9, o.z);
        scene.add(g);
        orbMeshes[o.id] = g;
      }
    }
    for (const id in orbMeshes) {
      if (!seen[id]) { scene.remove(orbMeshes[id]); delete orbMeshes[id]; }
    }
  }

  function clearOrbs() { syncOrbs([]); }

  // third-person follow (or free) camera
  function updateCamera(dt, target, camYaw, opts) {
    const dist = (opts && opts.dist) || 7.5;
    const height = (opts && opts.height) || 3.4;
    const want = new THREE.Vector3(
      target.x - Math.sin(camYaw) * dist,
      height,
      target.z - Math.cos(camYaw) * dist
    );
    // pull the camera in when a wall/hedge/tree trunk blocks the view
    const lookFrom = new THREE.Vector3(target.x, 1.3, target.z);
    const toCam = want.clone().sub(lookFrom);
    const wantDist = toCam.length();
    raycaster.set(lookFrom, toCam.normalize());
    raycaster.far = wantDist;
    const hits = raycaster.intersectObjects(WORLD3D.occluders, false);
    if (hits.length && hits[0].distance < wantDist) {
      want.copy(lookFrom).addScaledVector(toCam, Math.max(1.6, hits[0].distance * 0.9));
    }
    raycaster.far = Infinity;
    const k = Math.min(1, dt * 6);
    camPos.lerp(want, k);
    camera.position.copy(camPos);
    if (shakeAmt > 0.002) {
      camera.position.x += (Math.random() - 0.5) * shakeAmt * 0.5;
      camera.position.y += (Math.random() - 0.5) * shakeAmt * 0.35;
      camera.position.z += (Math.random() - 0.5) * shakeAmt * 0.5;
    }
    camera.lookAt(target.x, 1.3, target.z);
  }

  function resize() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== w * renderer.getPixelRatio() || canvas.height !== h * renderer.getPixelRatio()) {
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    }
  }

  function render(dt) {
    time += dt;
    resize();
    lerpMood(dt);
    WORLD3D.tick(time, dt);
    tickParticles(dt);
    tickSnow(dt);
    tickFov(dt);
    shakeAmt = Math.max(0, shakeAmt - dt * 2.2);
    for (const id in orbMeshes) {
      orbMeshes[id].position.y = 0.9 + Math.sin(time * 3 + orbMeshes[id].position.x) * 0.15;
      orbMeshes[id].rotation.y += dt * 2;
    }
    if (skyDome) {
      skyDome.position.copy(camera.position);
      if (stars) stars.position.set(camera.position.x, 0, camera.position.z);
    }
    renderer.render(scene, camera);
  }

  // project a screen tap onto the ground plane (y = 0)
  const groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
  function pickGround(sx, sy) {
    const r = canvas.getBoundingClientRect();
    raycaster.setFromCamera(
      { x: (sx / r.width) * 2 - 1, y: -(sy / r.height) * 2 + 1 },
      camera
    );
    const pt = new THREE.Vector3();
    return raycaster.ray.intersectPlane(groundPlane, pt) ? { x: pt.x, z: pt.z } : null;
  }

  // returns userData.figId of the closest figure hit under screen point
  function pickFigure(sx, sy, hitMeshes) {
    const r = canvas.getBoundingClientRect();
    raycaster.setFromCamera(
      { x: ((sx) / r.width) * 2 - 1, y: -((sy) / r.height) * 2 + 1 },
      camera
    );
    const hits = raycaster.intersectObjects(hitMeshes, false);
    return hits.length ? hits[0].object.userData.figId : null;
  }

  return {
    init, setMood, setFlashlight, syncOrbs, clearOrbs,
    updateCamera, render, pickFigure, pickGround, burst, shake, setMoving, setTheme,
    scene: () => scene,
    camera: () => camera,
  };
})();
