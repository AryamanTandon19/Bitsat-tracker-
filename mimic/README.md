# MIMIC! 🗿 — pose · freeze · survive

A cross-platform (mobile + desktop) multiplayer hide-and-seek party game, playable
right in the browser. Hiders pose as mannequins in a museum; the Seeker hunts them
with a flashlight. Concept #1 from [`GAME_CONCEPTS.md`](../GAME_CONCEPTS.md).

## How to play

| Phase | What happens |
|---|---|
| **Hide & Pose** (40s) | Hiders walk anywhere in the gallery and strike a pose with the drag-the-limbs pose editor. The Seeker's screen is covered — no peeking. |
| **Seek!** (100s) | Everyone renders as an identical mannequin. The Seeker patrols with a flashlight and taps statues to accuse them. Wrong guess: **−10**. Catch: **+30**. |
| **The twist** | Glowing orbs (+10) spawn around the map — hiders must *move* to grab them. Moving inside the flashlight beam makes you flash red. Survive the round: **+25**. |

The seeker role rotates each round; most points after all rounds wins.

- **Desktop:** A/D or ◀ ▶ arrow keys to move, click to accuse.
- **Mobile:** on-screen ◀ ▶ buttons, tap to accuse, touch-drag to pose.

## Game modes

- **🎯 Practice Solo** — you hide from *Inspector Botto*, an AI seeker, alongside AI
  mannequin bots (works fully offline).
- **🏠 Create Room / Join** — private rooms with 4-letter codes for friends (2–8+ players).
- **🌍 Quick Match** — a shared public room to play with strangers.
- Small lobbies are padded with AI hider bots so 2-player games still feel busy.

## Run it

It's a static site — no build step, no game server:

```bash
cd mimic
python3 -m http.server 8000    # or any static file server
# open http://localhost:8000
```

Deploy for free on **GitHub Pages** (Settings → Pages → deploy from branch, folder `/mimic`
— or copy the folder to any static host / Netlify / Vercel / itch.io).

## Architecture

```
index.html        screens: menu / lobby / game (canvas + HUD overlays)
style.css         responsive, touch-first UI
vendor/           supabase-js UMD bundle (vendored, no CDN dependency)
js/config.js      Supabase URL + publishable key, quick-match room code
js/util.js        helpers, seeded RNG (identical background on all clients)
js/figure.js      ragdoll skeleton: 10 joint angles, preset poses, 2-bone IK,
                  auto foot-grounding, canvas drawing, hit-testing
js/poseEditor.js  bottom-sheet editor: drag hands/feet/head/chest handles
js/render.js      museum scene: walls, paintings, pedestals, flashlight cone, orbs
js/input.js       keyboard + hold-buttons + tap routing
js/ai.js          AI seeker (patrol/inspect/accuse) and AI hider bots
js/net.js         Supabase Realtime: presence roster + broadcast messages
js/game.js        phase state machine, host authority, scoring, HUD, main loop
js/main.js        app shell and screen wiring
```

**Multiplayer model:** no server code at all. Each room is one Supabase Realtime
channel — presence provides the roster, broadcast carries gameplay messages. The
earliest-joined player is the **host** and is the only one who advances phases,
places decoys/orbs and resolves accusations; everyone else streams their own
position/pose (~9 Hz while moving) and renders shared state. If the host leaves,
host duty automatically falls to the next-earliest player. No tables, rows or
auth are used, so the publishable key in `config.js` is safe to ship.

## Tuning knobs

All in `js/game.js`: `SETUP_MS`, `HUNT_MS`, `CONE_RANGE`, `ACCUSE_RANGE`,
speeds in `SPEED`, points in `PTS`, and world width `WORLD_W`. Poses live in
`js/figure.js` (`PRESETS`).

## Roadmap to a paid release

The free browser version is the marketing funnel (as with Meccha Chameleon).
Paid upsell candidates: more maps/themes, cosmetic skins & hats, custom round
settings, bigger private lobbies, map editor. Wrap with Capacitor for the app
stores or Electron/Tauri for Steam.
