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

  const MOODS = {
    day:  { sky: 0x9fd4e8, fog: 0xaed6e6, sun: 1.25, hemi: 0.85, sunC: 0xfff2d8 },
    dusk: { sky: 0x1e2340, fog: 0x1e2340, sun: 0.28, hemi: 0.32, sunC: 0x9bb0ff },
  };
  let mood = "day", moodT = 1;

  function init(c) {
    canvas = c;
    renderer = new THREE.WebGLRenderer({ canvas: c, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.12;
    if (THREE.SRGBColorSpace) renderer.outputColorSpace = THREE.SRGBColorSpace;

    scene = new THREE.Scene();
    scene.background = new THREE.Color(MOODS.day.sky);
    scene.fog = new THREE.Fog(MOODS.day.fog, 55, 110);

    camera = new THREE.PerspectiveCamera(60, 1, 0.1, 200);
    camera.position.copy(camPos);

    hemi = new THREE.HemisphereLight(0xdfeeff, 0x6a7a5a, MOODS.day.hemi);
    scene.add(hemi);
    sun = new THREE.DirectionalLight(0xfff2d8, MOODS.day.sun);
    sun.position.set(28, 40, 18);
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

    WORLD3D.build(scene);
    initParticles();
    return { scene, camera };
  }

  function setMood(name) { mood = name; moodT = 0; }

  function lerpMood(dt) {
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
    tickFov(dt);
    shakeAmt = Math.max(0, shakeAmt - dt * 2.2);
    for (const id in orbMeshes) {
      orbMeshes[id].position.y = 0.9 + Math.sin(time * 3 + orbMeshes[id].position.x) * 0.15;
      orbMeshes[id].rotation.y += dt * 2;
    }
    renderer.render(scene, camera);
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
    updateCamera, render, pickFigure, burst, shake, setMoving,
    scene: () => scene,
    camera: () => camera,
  };
})();
