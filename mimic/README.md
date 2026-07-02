# MIMIC! 🗿 — pose · paint · freeze · survive

A cross-platform (mobile + desktop) **3D** multiplayer hide-and-seek party game,
playable right in the browser. Hiders pose as mannequins scattered around a park —
museum pavilion, houses, gardens, fountain plaza — and **paint themselves to blend
in**. At dusk the Seeker hunts them with a flashlight.
Concept #1 from [`GAME_CONCEPTS.md`](../GAME_CONCEPTS.md), evolved.

## How to play

| Phase | What happens |
|---|---|
| **Hide & Paint** (45s) | Hiders roam the 3D park, strike a pose with the drag-the-limbs pose editor, and use the **Paint Studio**: colour every body part and doodle freehand on your torso. Tap any mannequin to **repaint it** — or copy its look onto yourself, chameleon-style. The Seeker's screen is covered. |
| **Seek!** (100s) | Dusk falls. The Seeker patrols with a real flashlight and taps statues to accuse them. Wrong guess: **−10**. Catch: **+30**. |
| **The twist** | Glowing orbs (+10) spawn around the map — hiders must *move* to grab them. Moving inside the flashlight beam flashes a red ring around you. Survive the round: **+25**. |

The seeker role rotates each round; most points after all rounds wins.

- **Desktop:** WASD/arrows to move, Q/E or mouse-drag to orbit the camera, click to accuse/paint.
- **Mobile:** virtual joystick, drag to look around, tap to accuse/paint.

## Game modes

- **🎯 Practice Solo** — hide from *Inspector Botto*, an AI seeker, alongside AI
  mannequin bots (fully offline).
- **🏠 Create Room / Join** — private rooms with 4-letter codes for friends (2–8+ players).
- **🌍 Quick Match** — a shared public room to play with strangers.
- Small lobbies are padded with AI hider bots so 2-player games still feel busy.

## Play it live (GitHub Pages)

A deploy workflow is included (`.github/workflows/pages.yml`). One-time setup:

1. On GitHub: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
2. Re-run the "Deploy Mimic! to GitHub Pages" workflow (Actions tab) or push any commit.
3. The game is live at `https://<your-username>.github.io/<repo-name>/` — share that
   link + a room code and play from any phone or computer.

Or run locally:

```bash
cd mimic
python3 -m http.server 8000    # any static file server works
```

## Architecture

```
index.html        screens: menu / lobby / game (WebGL canvas + HUD overlays)
style.css         responsive, touch-first UI
vendor/           three.js + supabase-js (vendored, zero CDN dependencies)
js/config.js      Supabase URL + publishable key, quick-match room code
js/util.js        helpers, seeded RNG
js/figure.js      2D skeleton math: 10 joint angles, preset poses, 2-bone IK
js/figure3d.js    3D rig built from the same skeleton; per-part paint materials,
                  torso doodle canvas texture, labels, markers, hit capsules
js/world3d.js     procedural park: colonnade, houses, trees, fountain, hedges,
                  lampposts, benches, fence + collision + mannequin spots
js/render3d.js    three.js scene: day/dusk moods, third-person camera,
                  seeker spotlight + visible beam, orbs, tap raycasts
js/poseEditor.js  bottom-sheet pose editor (drag hands/feet/head/chest, IK)
js/paint.js       Paint Studio: body-part palette + freehand doodle canvas
js/input.js       WASD + virtual joystick + orbit-drag + tap routing
js/ai.js          AI seeker (patrol/inspect/accuse) and AI hider bots
js/net.js         Supabase Realtime: presence roster + broadcast messages
js/game.js        phase state machine, host authority, scoring, HUD, main loop
js/main.js        app shell and screen wiring
```

**Multiplayer model:** no server code at all. Each room is one Supabase Realtime
channel — presence provides the roster, broadcast carries gameplay messages
(positions ~9 Hz while moving, paint/pose on change, decoy repaints, host
snapshots every 2.5s). The earliest-joined player is the **host** and is the only
one who advances phases, scatters mannequins and resolves accusations; if the
host leaves, host duty falls to the next-earliest player automatically. No
tables, rows or auth are used, so the publishable key in `config.js` is safe to ship.

## Tuning knobs

`js/game.js`: `SETUP_MS`, `HUNT_MS`, `CONE_RANGE`, `ACCUSE_RANGE`, `PAINT_RANGE`,
speeds in `SPEED`, points in `PTS`. Poses: `js/figure.js` (`PRESETS`). Map layout &
mannequin spots: `js/world3d.js`. Paint palette: `js/figure3d.js` (`PALETTE`).

## Roadmap to a paid release

The free browser version is the marketing funnel (as with Meccha Chameleon).
Paid upsell candidates: more maps/biomes, cosmetic hats & skins, custom round
settings, bigger private lobbies, a map editor. Wrap with Capacitor for the app
stores or Electron/Tauri for Steam.
