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

  const MOODS = {
    day:  { sky: 0x9fd4e8, fog: 0xaed6e6, sun: 1.1, hemi: 0.75, flash: 0 },
    dusk: { sky: 0x232742, fog: 0x232742, sun: 0.22, hemi: 0.3, flash: 1 },
  };
  let mood = "day", moodT = 1;

  function init(c) {
    canvas = c;
    renderer = new THREE.WebGLRenderer({ canvas: c, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFShadowMap;

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
    flash = new THREE.SpotLight(0xffe9b0, 0, 16, 0.42, 0.45, 1.1);
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
    return { scene, camera };
  }

  function setMood(name) { mood = name; moodT = 0; }

  function lerpMood(dt) {
    if (moodT >= 1) return;
    moodT = Math.min(1, moodT + dt * 1.2);
    const m = MOODS[mood];
    sun.intensity += (m.sun - sun.intensity) * 0.08;
    hemi.intensity += (m.hemi - hemi.intensity) * 0.08;
    scene.background.lerp(new THREE.Color(m.sky), 0.08);
    scene.fog.color.lerp(new THREE.Color(m.fog), 0.08);
  }

  // pos {x,z}, yaw — orient the flashlight from the seeker's hand
  function setFlashlight(on, pos, yaw) {
    flash.visible = coneMesh.visible = !!on && mood === "dusk";
    if (!on || !pos) return;
    const fx = Math.sin(yaw), fz = Math.cos(yaw);
    flash.position.set(pos.x, 1.6, pos.z);
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
          new THREE.SphereGeometry(0.22, 10, 8),
          new THREE.MeshBasicMaterial({ color: 0x9dffe2 })
        );
        const halo = new THREE.Mesh(
          new THREE.SphereGeometry(0.5, 10, 8),
          new THREE.MeshBasicMaterial({
            color: 0x66ffd0, transparent: true, opacity: 0.25,
            blending: THREE.AdditiveBlending, depthWrite: false,
          })
        );
        g.add(core); g.add(halo);
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
    updateCamera, render, pickFigure,
    scene: () => scene,
    camera: () => camera,
  };
})();
