"""
NETA FIGHTERS - Character #1: Narendra Sodhi
Blender block-out builder (parametric base mesh for sculpting).

This is a BLOCK-OUT, not final art. It builds a proportional 3D base
that matches the design notes (oval-square head, broad forehead, full
white beard, rimless glasses, cream knee-length kurta, saffron-bordered
stole, white churidar, black sandals, hands folded in front).
You then sculpt / retopo / texture / rig from this base.

HOW TO RUN
----------
GUI:  open Blender -> Scripting tab -> open this file -> Run.
CLI:  blender --background --python build_blockout.py
      (add  -- --export  to also write a .glb into ../exports/)

Units are meters. Character height ~1.75 m. Origin at feet, +Z up,
character faces +Y (camera looks down -Y). Head turns gently to HIS left.
"""

import bpy
import bmesh
import math
import sys
from mathutils import Vector

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
HEIGHT = 1.75            # total height (m)
HEAD_TURN = math.radians(10)   # head gently turned to his left
EXPORT = "--export" in sys.argv

# Colour palette (linear-ish RGBA) from the design
COL = {
    "skin":     (0.86, 0.66, 0.52, 1),   # fair warm
    "skin_pk":  (0.88, 0.60, 0.52, 1),   # pink cheeks
    "beard":    (0.92, 0.92, 0.90, 1),   # white/grey beard + brows + hair
    "hair":     (0.85, 0.85, 0.84, 1),
    "eye":      (0.95, 0.95, 0.93, 1),   # sclera
    "iris":     (0.18, 0.10, 0.05, 1),   # dark brown
    "kurta":    (0.93, 0.90, 0.82, 1),   # off-white / cream
    "churidar": (0.96, 0.96, 0.95, 1),   # white
    "saffron":  (0.95, 0.55, 0.15, 1),   # stole border
    "stole":    (0.97, 0.97, 0.96, 1),   # white stole body
    "sandal":   (0.06, 0.06, 0.06, 1),   # black
    "glass":    (0.30, 0.30, 0.32, 1),   # thin metal
    "mouth":    (0.30, 0.16, 0.14, 1),
}

# ----------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------
def reset_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for blk in (bpy.data.meshes, bpy.data.materials):
        for d in list(blk):
            if d.users == 0:
                blk.remove(d)

def get_collection(name):
    if name in bpy.data.collections:
        return bpy.data.collections[name]
    c = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(c)
    return c

def mat(name, key):
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = COL[key]
    if key in ("glass",):
        bsdf.inputs["Metallic"].default_value = 0.9
        bsdf.inputs["Roughness"].default_value = 0.25
    elif key in ("beard", "hair"):
        bsdf.inputs["Roughness"].default_value = 0.85
    else:
        bsdf.inputs["Roughness"].default_value = 0.6
    return m

def put(obj, coll, key, name=None):
    """Move obj into our collection, assign material, rename."""
    for c in obj.users_collection:
        c.objects.unlink(obj)
    coll.objects.link(obj)
    obj.data.materials.clear()
    obj.data.materials.append(mat("NS_" + key, key))
    if name:
        obj.name = name
    return obj

def add_sphere(loc, scale, key, coll, name, segs=24):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segs, ring_count=segs//2,
                                          location=loc)
    o = bpy.context.active_object
    o.scale = scale
    bpy.ops.object.shade_smooth()
    return put(o, coll, key, name)

def add_cube(loc, scale, key, coll, name):
    bpy.ops.mesh.primitive_cube_add(location=loc)
    o = bpy.context.active_object
    o.scale = scale
    return put(o, coll, key, name)

def add_cyl(loc, radius, depth, key, coll, name, rot=(0, 0, 0), verts=20):
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=radius,
                                         depth=depth, location=loc)
    o = bpy.context.active_object
    o.rotation_euler = rot
    bpy.ops.object.shade_smooth()
    return put(o, coll, key, name)

def add_torus(loc, major, minor, key, coll, name, rot=(0, 0, 0)):
    bpy.ops.mesh.primitive_torus_add(location=loc, major_radius=major,
                                     minor_radius=minor)
    o = bpy.context.active_object
    o.rotation_euler = rot
    bpy.ops.object.shade_smooth()
    return put(o, coll, key, name)

# ----------------------------------------------------------------------
# BUILD: HEAD  (broad forehead, oval-square, full beard, glasses)
# ----------------------------------------------------------------------
def build_head(coll, neck_top_z):
    cx = 0.0
    # head center a bit above neck top
    hz = neck_top_z + 0.105
    head_h = 0.23
    # Cranium: oval, taller (broad forehead) -> scale Z a touch up, slightly squared
    head = add_sphere((cx, 0, hz), (0.092, 0.105, 0.118), "skin",
                      coll, "NS_Head", segs=32)
    # squarish jaw: small box blended low-front
    add_cube((cx, 0.02, hz - 0.085), (0.085, 0.075, 0.045), "skin",
             coll, "NS_Jaw")

    # Forehead emphasis (broad/tall) – subtle bulge
    add_sphere((cx, 0.055, hz + 0.06), (0.082, 0.05, 0.05), "skin",
               coll, "NS_Forehead")

    # Nose: medium-large, straight bridge, rounded wide tip
    add_cube((cx, 0.105, hz - 0.01), (0.018, 0.03, 0.05), "skin",
             coll, "NS_NoseBridge")
    add_sphere((cx, 0.125, hz - 0.045), (0.026, 0.028, 0.024), "skin",
               coll, "NS_NoseTip")

    # Eyes: almond, slightly deep-set, ~one eye-width apart, gaze to HIS left
    eye_y = 0.085
    eye_z = hz + 0.01
    for side, name in ((-1, "R"), (1, "L")):  # his left = +X is his right... keep symmetric
        ex = side * 0.035
        add_sphere((ex, eye_y, eye_z), (0.018, 0.015, 0.015), "eye",
                   coll, f"NS_Eye_{name}", segs=16)
        # iris pushed slightly to his left (+X from his view => -X world here)
        add_sphere((ex - 0.004, eye_y + 0.012, eye_z), (0.008, 0.008, 0.008),
                   "iris", coll, f"NS_Iris_{name}", segs=12)
        # eyebrow: medium-thick white, slight arch, denser inner
        add_cube((ex, eye_y + 0.005, eye_z + 0.028),
                 (0.026, 0.012, 0.006), "beard", coll, f"NS_Brow_{name}")
        # ear: medium, slightly protruding (top~brow, bottom~nose)
        add_sphere((side * 0.092, 0.0, hz - 0.01),
                   (0.012, 0.022, 0.032), "skin", coll, f"NS_Ear_{name}", segs=16)

    # Rimless glasses: two thin lens rings + thin bridge + arms
    for side, name in ((-1, "R"), (1, "L")):
        ex = side * 0.035
        add_torus((ex, eye_y + 0.02, eye_z), 0.022, 0.0018, "glass",
                  coll, f"NS_Lens_{name}", rot=(math.radians(90), 0, 0))
        add_cyl((side * 0.07, 0.04, eye_z + 0.005), 0.0015, 0.075, "glass",
                coll, f"NS_Temple_{name}", rot=(0, math.radians(90), 0))
    add_cyl((0, eye_y + 0.02, eye_z + 0.005), 0.0015, 0.03, "glass",
            coll, "NS_GlassBridge", rot=(0, math.radians(90), 0))

    # Mouth: closed, mostly hidden, slight downward
    add_cube((cx, 0.10, hz - 0.075), (0.022, 0.012, 0.004), "mouth",
             coll, "NS_Mouth")

    # Hair: very short, receded "M", combed back -> thin cap on upper-back skull
    add_sphere((cx, -0.01, hz + 0.085), (0.088, 0.10, 0.05), "hair",
               coll, "NS_Hair")

    # FULL WHITE BEARD: rounded volume wrapping chin/jaw/sideburns/upper neck
    beard = add_sphere((cx, 0.045, hz - 0.085), (0.097, 0.085, 0.085),
                       "beard", coll, "NS_Beard", segs=28)
    # mustache merges into beard, covers upper lip
    add_cube((cx, 0.10, hz - 0.058), (0.03, 0.012, 0.012), "beard",
             coll, "NS_Mustache")
    # sideburns connect beard up to hair
    for side, name in ((-1, "R"), (1, "L")):
        add_cube((side * 0.085, 0.0, hz - 0.02),
                 (0.012, 0.02, 0.05), "beard", coll, f"NS_Sideburn_{name}")
    return head

# ----------------------------------------------------------------------
# BUILD: BODY (medium build, kurta + stole + churidar + sandals, folded hands)
# ----------------------------------------------------------------------
def build_body(coll):
    # vertical landmarks (m)
    ankle_z   = 0.06
    knee_z    = 0.48
    hip_z     = 0.95
    waist_z   = 1.06
    chest_z   = 1.25
    shldr_z   = 1.42
    neck_z    = 1.50
    neck_top  = 1.55

    # Legs: white churidar, slim ankles
    for side, name in ((-1, "R"), (1, "L")):
        lx = side * 0.085
        add_cyl((lx, 0, (knee_z + hip_z) / 2), 0.075, hip_z - knee_z,
                "churidar", coll, f"NS_Thigh_{name}")
        add_cyl((lx, 0.01, (ankle_z + knee_z) / 2 + 0.03), 0.045,
                knee_z - ankle_z, "churidar", coll, f"NS_Calf_{name}")
        # black open-toe sandal, flat sole, two straps
        add_cube((lx, 0.06, ankle_z - 0.02), (0.05, 0.11, 0.02),
                 "sandal", coll, f"NS_Sole_{name}")
        add_cube((lx, 0.02, ankle_z + 0.02), (0.05, 0.02, 0.015),
                 "sandal", coll, f"NS_Strap1_{name}")
        add_cube((lx, 0.08, ankle_z + 0.015), (0.05, 0.02, 0.012),
                 "sandal", coll, f"NS_Strap2_{name}")

    # Hips / pelvis (under kurta)
    add_cube((0, 0, hip_z), (0.13, 0.085, 0.06), "kurta", coll, "NS_Hips")

    # KURTA: knee-length straight tunic -> tapered box from hips to knees
    add_cube((0, 0, (knee_z + waist_z) / 2 + 0.04),
             (0.16, 0.10, (waist_z - knee_z) / 2 + 0.06),
             "kurta", coll, "NS_KurtaSkirt")
    # Torso of kurta
    add_cube((0, 0, (waist_z + chest_z) / 2),
             (0.155, 0.095, (chest_z - waist_z) / 2 + 0.04),
             "kurta", coll, "NS_KurtaTorso")
    # Chest / shoulders (moderately broad ~2 head widths)
    add_cube((0, 0, chest_z + 0.02), (0.185, 0.10, 0.10),
             "kurta", coll, "NS_Chest")
    # Mandarin collar
    add_cyl((0, 0, neck_z - 0.01), 0.05, 0.05, "kurta", coll, "NS_Collar")

    # Neck (medium, slightly short due to beard)
    add_cyl((0, 0, (neck_z + neck_top) / 2), 0.045, neck_top - neck_z + 0.02,
            "skin", coll, "NS_Neck")

    # Shoulders + upper arms (relaxed, close to torso)
    for side, name in ((-1, "R"), (1, "L")):
        sx = side * 0.185
        add_sphere((sx, 0, shldr_z), (0.055, 0.06, 0.06), "kurta",
                   coll, f"NS_Shoulder_{name}")
        # upper arm angled slightly inward
        add_cyl((sx, 0.02, chest_z - 0.04), 0.045, 0.22, "kurta",
                coll, f"NS_UpperArm_{name}",
                rot=(math.radians(8), 0, side * math.radians(8)))
        # forearm angles inward toward center (clasped pose)
        fx = side * 0.10
        add_cyl((fx, 0.13, waist_z - 0.02), 0.04, 0.20, "kurta",
                coll, f"NS_Forearm_{name}",
                rot=(math.radians(70), 0, side * math.radians(35)))

    # HANDS folded together in front of lower torso
    add_sphere((0, 0.18, waist_z - 0.04), (0.05, 0.05, 0.035), "skin",
               coll, "NS_HandsFolded")

    # SIGNATURE STOLE: white scarf with saffron borders, draped around neck
    # back of neck piece
    add_cube((0, -0.03, neck_z - 0.02), (0.07, 0.02, 0.04),
             "stole", coll, "NS_StoleNeck")
    for side, name in ((-1, "R"), (1, "L")):
        sx = side * 0.07
        # long front drape down the chest
        add_cube((sx, 0.10, (chest_z + waist_z) / 2),
                 (0.035, 0.015, (chest_z - waist_z) / 2 + 0.12),
                 "stole", coll, f"NS_StoleDrape_{name}")
        # saffron border strips on each drape (front + side)
        add_cube((sx + side * 0.03, 0.105, (chest_z + waist_z) / 2),
                 (0.006, 0.016, (chest_z - waist_z) / 2 + 0.12),
                 "saffron", coll, f"NS_StoleBorderA_{name}")
        add_cube((sx - side * 0.03, 0.105, (chest_z + waist_z) / 2),
                 (0.006, 0.016, (chest_z - waist_z) / 2 + 0.12),
                 "saffron", coll, f"NS_StoleBorderB_{name}")

    return neck_top

# ----------------------------------------------------------------------
# POSE / FINISH
# ----------------------------------------------------------------------
def main():
    reset_scene()
    coll = get_collection("Narendra_Sodhi_Blockout")

    neck_top = build_body(coll)
    head = build_head(coll, neck_top)

    # gentle head turn to his left – rotate all head-named objects around neck
    pivot = Vector((0, 0, neck_top + 0.05))
    for o in coll.objects:
        if o.name.startswith(("NS_Head", "NS_Jaw", "NS_Forehead", "NS_Nose",
                              "NS_Eye", "NS_Iris", "NS_Brow", "NS_Ear",
                              "NS_Lens", "NS_Temple", "NS_GlassBridge",
                              "NS_Mouth", "NS_Hair", "NS_Beard", "NS_Mustache",
                              "NS_Sideburn", "NS_Neck")):
            o.rotation_euler[2] += HEAD_TURN

    # Reference empty marking eye-line height for the rigger
    bpy.ops.object.empty_add(location=(0, 0.2, neck_top + 0.16))
    bpy.context.active_object.name = "NS_Aim_Reference"

    print("[NETA FIGHTERS] Narendra Sodhi block-out built: "
          f"{len(coll.objects)} parts.")

    if EXPORT:
        import os
        out = os.path.join(os.path.dirname(bpy.data.filepath or __file__),
                           "..", "exports", "narendra_sodhi_blockout.glb")
        out = os.path.abspath(out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.export_scene.gltf(filepath=out, use_selection=True,
                                  export_format='GLB')
        print(f"[NETA FIGHTERS] Exported -> {out}")


if __name__ == "__main__":
    main()
