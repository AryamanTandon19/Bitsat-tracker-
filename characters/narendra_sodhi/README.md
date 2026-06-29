# Narendra Sodhi — Fighter #1

Files:
- `DESIGN.md` — full character design (your description, organized).
- `stats.json` — combat stats + 4 special moves.
- `blender/build_blockout.py` — builds the 3D base mesh in Blender.
- `exports/` — exported `.glb` / `.fbx` go here.
- `reference/` — drop reference images here.

## Run the Blender block-out
**GUI:** open Blender → *Scripting* tab → open `build_blockout.py` → **Run Script**.

**Command line:**
```
blender --background --python blender/build_blockout.py -- --export
```
`--export` also writes `exports/narendra_sodhi_blockout.glb`.

## What you get
A proportional base mesh (NOT final art): oval-square head, broad forehead,
full white beard + mustache, rimless glasses, cream knee-length kurta,
saffron-bordered stole, white churidar, black sandals, hands folded in front,
head gently turned to his left.

## Next steps after running
1. Sculpt face detail (wrinkles, nose, beard strands).
2. Retopo to a clean low-poly mesh.
3. UV unwrap + texture (skin tones, cream, saffron border).
4. Rig + weight paint.
5. Animate idle + the 4 specials.
