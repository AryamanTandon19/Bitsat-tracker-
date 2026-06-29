# 🤖 Make Sodhi Life-Like with an AI 3D Generator

You picked: build the final model with an **AI 3D tool**, then rig it.
This guide gives the **exact steps + ready prompts**.

> ⚠️ Keep it FICTIONAL. Do NOT upload a real politician's photo or ask the AI
> for a real person by name. Use our fake "Narendra Sodhi" description /
> our own concept image. This keeps us safe (likeness + platform rules).

---

## Which tool to use (pick one)
| Tool | Type | Good for |
|------|------|----------|
| **Meshy.ai** | text→3D & image→3D | fast, game-ready, auto-rig + animate |
| **Tripo3D** | text→3D & image→3D | clean topology, free credits |
| **Rodin / Hyper3D** | image→3D | high detail faces |
| **Luma Genie** | text→3D | quick concepts |

**Best path for a good face = IMAGE → 3D** (likeness follows the picture).

---

## PATH A — Image → 3D (recommended)
1. Make ONE clean concept image of Sodhi (front, neutral light, plain background).
   - Use any image AI with the **prompt below**, OR draw/commission it.
   - Our block-out renders in `reference/` show the pose + proportions to match.
2. Upload that image to Meshy/Tripo → "Image to 3D".
3. Settings: **T-pose / A-pose** if asked (better for rigging), high poly, PBR textures ON.
4. Download **`.fbx`** (with textures) or **`.glb`**.
5. Auto-rig: upload `.fbx` to **Mixamo** (free) → get skeleton + animations.
6. Import to engine (Unreal/Unity) → retarget to your fight animations.

## PATH B — Text → 3D (faster, less exact)
1. Paste the **text prompt below** into Meshy/Tripo "Text to 3D".
2. Generate, pick best, refine, download `.fbx`/`.glb`.
3. Same rig + import steps as Path A.

---

## ✅ READY TEXT PROMPT (copy-paste)
> Stylized realistic 3D game character, full body, A-pose, mature South Asian
> male statesman, around 175 cm, medium build, upright calm posture.
> Oval slightly-square face, broad tall forehead, receded hairline with very
> short combed-back white-grey hair. Full rounded white beard with grey
> shading covering jaw and upper neck, thick white mustache. Medium-large
> straight nose with rounded tip. Calm almond dark-brown eyes, hooded lids,
> medium white-grey eyebrows. Rimless thin metal eyeglasses. Wearing an
> off-white cream knee-length full-sleeve mandarin-collar kurta, white
> churidar trousers, black two-strap open-toe sandals, and a white stole
> with saffron-orange decorative borders draped around the neck. Clean game
> topology, PBR textures, neutral studio lighting, plain background.
> Fictional character, not a real person.

### Negative prompt (if tool supports)
> extra limbs, fused fingers, melted face, blurry, text, watermark, logo,
> real politician, real celebrity, cartoon toy.

---

## After you get the model
- [ ] Check scale = ~1.75 m tall in engine.
- [ ] Auto-rig (Mixamo) → spine, arms, legs, fingers.
- [ ] Test idle + walk so hands and legs move in sync.
- [ ] Add our 4 specials (Mann Ki Jab, Stole Flick, Namaste Clap, Unity Wave).
- [ ] Drop `.fbx`/`.glb` into this folder's `exports/`.

## Files to feed the AI
- `reference/ns2_front.png`, `ns2_threequarter.png`, `ns2_side.png` — pose + proportion guide.
- `DESIGN.md` — full detail list.
