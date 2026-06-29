# 🛠️ Tech Plan — How we build NETA FIGHTERS

## Engine choice
- **Unreal Engine 5** — best graphics + animation for a Tekken-style 3D fighter.
- Alt: Unity (URP/HDRP) if team prefers C#.
- **Pick:** Unreal 5 (recommended for AAA look).

## Character art pipeline (Blender)
1. **Concept** — drawing + design notes (in each character folder).
2. **Sculpt / Model** — build mesh in Blender.
3. **UV + Texture** — paint skin, clothes, props.
4. **Rig** — add skeleton (bones) so it can move.
5. **Animate** — idle, walk, punch, kick, 4 specials, hit, win, lose.
6. **Export** — `.fbx`/`.glb` into the engine.

> Note: Blender makes the model + animation files. A real human artist polish is needed for true AAA quality. We design every detail here so the modeling is exact.

## Online play (anytime)
- **Netcode:** rollback netcode (best for fighting games, low lag feel).
- **Match flow:** lobby → matchmaking → 1v1 room → result.
- **Backend:** account, matchmaking, rankings.
  - Option A: dedicated game server.
  - Option B: Supabase/Firebase for accounts + leaderboard, P2P rollback for the fight.
- **Anti-cheat:** server checks match result.

## Repo rules
- Big binary art (`.blend`, `.fbx`, textures) → **Git LFS**.
- One folder per character. Keep it clean.

## Build order
1. Combat rules (this doc set) ✅
2. Character #1 full design + Blender model
3. One stage (boxing ring — simplest)
4. Two characters fighting in engine (gray-box)
5. Add online
6. Add more characters one by one
