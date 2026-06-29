"""
Quick 3D PREVIEW of Narendra Sodhi block-out (no Blender needed).
Mirrors the proportions in blender/build_blockout.py and renders PNGs
from 3 angles so we can SEE the model. This is a rough block-out preview,
not the final sculpted/textured art.
"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import math, os

COL = {
    "skin": "#dba884", "beard": "#ededea", "hair": "#d9d9d6",
    "eye": "#f2f2ee", "iris": "#2e1a0d", "kurta": "#ede6d1",
    "churidar": "#f5f5f2", "saffron": "#f28c26", "stole": "#f7f7f4",
    "sandal": "#101010", "glass": "#4d4d52", "mouth": "#5a2a24",
}

ax = None
def _shade(color, f):
    c = np.array(matplotlib.colors.to_rgb(color))
    return tuple(np.clip(c * f, 0, 1))

def sphere(cx, cy, cz, sx, sy, sz, color, n=22):
    u = np.linspace(0, 2*np.pi, n); v = np.linspace(0, np.pi, n)
    x = cx + sx*np.outer(np.cos(u), np.sin(v))
    y = cy + sy*np.outer(np.sin(u), np.sin(v))
    z = cz + sz*np.outer(np.ones_like(u), np.cos(v))
    ax.plot_surface(x, y, z, color=color, linewidth=0, antialiased=True,
                    shade=True, rcount=n, ccount=n)

def cyl(cx, cy, cz, r, h, color, axis="z", n=18):
    t = np.linspace(0, 2*np.pi, n); zz = np.linspace(-h/2, h/2, 2)
    T, Z = np.meshgrid(t, zz)
    if axis == "z":
        X = cx + r*np.cos(T); Y = cy + r*np.sin(T); ZZ = cz + Z
    elif axis == "x":
        X = cx + Z; Y = cy + r*np.cos(T); ZZ = cz + r*np.sin(T)
    else:
        X = cx + r*np.cos(T); Y = cy + Z; ZZ = cz + r*np.sin(T)
    ax.plot_surface(X, Y, ZZ, color=color, linewidth=0, shade=True)

def box(cx, cy, cz, sx, sy, sz, color):
    # 8 corners
    p = np.array([[ix, iy, iz] for ix in (-sx, sx)
                  for iy in (-sy, sy) for iz in (-sz, sz)], float)
    p += [cx, cy, cz]
    faces = [(0,1,3,2),(4,5,7,6),(0,1,5,4),(2,3,7,6),(0,2,6,4),(1,3,7,5)]
    polys = [[p[i] for i in f] for f in faces]
    shades = [0.78, 0.78, 0.95, 0.7, 0.85, 1.0]
    fc = [_shade(color, s) for s in shades]
    pc = Poly3DCollection(polys, facecolors=fc, edgecolors=(0,0,0,0.08),
                          linewidths=0.2)
    ax.add_collection3d(pc)

def build():
    HT = math.radians(10)
    # ---- body landmarks
    ankle=0.06; knee=0.48; hip=0.95; waist=1.06; chest=1.25
    shldr=1.42; neck=1.50; neck_top=1.55
    # legs
    for s in (-1, 1):
        lx = s*0.085
        cyl(lx, 0, (knee+hip)/2, 0.075, hip-knee, COL["churidar"], "z")
        cyl(lx, 0.01, (ankle+knee)/2+0.03, 0.045, knee-ankle, COL["churidar"], "z")
        box(lx, 0.06, ankle-0.02, 0.05, 0.11, 0.02, COL["sandal"])
        box(lx, 0.02, ankle+0.02, 0.05, 0.02, 0.015, COL["sandal"])
        box(lx, 0.08, ankle+0.015, 0.05, 0.02, 0.012, COL["sandal"])
    # hips + kurta
    box(0,0,hip, 0.13,0.085,0.06, COL["kurta"])
    box(0,0,(knee+waist)/2+0.04, 0.16,0.10,(waist-knee)/2+0.06, COL["kurta"])
    box(0,0,(waist+chest)/2, 0.155,0.095,(chest-waist)/2+0.04, COL["kurta"])
    box(0,0,chest+0.02, 0.185,0.10,0.10, COL["kurta"])
    cyl(0,0,neck-0.01,0.05,0.05,COL["kurta"],"z")
    cyl(0,0,(neck+neck_top)/2,0.045,neck_top-neck+0.02,COL["skin"],"z")
    # shoulders + arms
    for s in (-1,1):
        sx=s*0.185
        sphere(sx,0,shldr,0.055,0.06,0.06,COL["kurta"])
        cyl(sx,0.02,chest-0.04,0.045,0.22,COL["kurta"],"z")
        fx=s*0.10
        # forearm tilted forward toward center (folded hands)
        cyl(fx,0.11,waist-0.02,0.04,0.18,COL["kurta"],"y")
    sphere(0,0.18,waist-0.04,0.05,0.05,0.035,COL["skin"])  # folded hands
    # stole
    box(0,-0.03,neck-0.02,0.07,0.02,0.04,COL["stole"])
    for s in (-1,1):
        sx=s*0.07
        box(sx,0.10,(chest+waist)/2,0.035,0.015,(chest-waist)/2+0.12,COL["stole"])
        box(sx+s*0.03,0.105,(chest+waist)/2,0.008,0.016,(chest-waist)/2+0.12,COL["saffron"])
    # ---- head (turned gently to his left ~ +Z rotation)
    hz = neck_top+0.105
    def rot(x,y):
        return (x*math.cos(HT)-y*math.sin(HT), x*math.sin(HT)+y*math.cos(HT))
    # cranium / forehead / jaw
    sphere(0,0,hz,0.092,0.105,0.118,COL["skin"])
    sphere(0,0.055,hz+0.06,0.082,0.05,0.05,COL["skin"])      # forehead
    box(0,0.02,hz-0.085,0.085,0.075,0.045,COL["skin"])       # jaw
    sphere(0,-0.01,hz+0.085,0.088,0.10,0.05,COL["hair"])     # hair
    # beard
    sphere(0,0.045,hz-0.085,0.097,0.085,0.085,COL["beard"])
    box(0,0.10,hz-0.058,0.03,0.012,0.012,COL["beard"])       # mustache
    for s in (-1,1):
        box(s*0.085,0.0,hz-0.02,0.012,0.02,0.05,COL["beard"])# sideburns
    # nose
    box(0,0.105,hz-0.01,0.018,0.03,0.05,COL["skin"])
    sphere(0,0.125,hz-0.045,0.026,0.028,0.024,COL["skin"])
    box(0,0.10,hz-0.075,0.022,0.012,0.004,COL["mouth"])
    # eyes / brows / ears / glasses
    eye_y=0.085; eye_z=hz+0.01
    for s in (-1,1):
        ex=s*0.035
        sphere(ex,eye_y,eye_z,0.018,0.015,0.015,COL["eye"])
        sphere(ex-0.004,eye_y+0.012,eye_z,0.008,0.008,0.008,COL["iris"])
        box(ex,eye_y+0.005,eye_z+0.028,0.026,0.012,0.006,COL["beard"])  # brow
        sphere(s*0.092,0,hz-0.01,0.012,0.022,0.032,COL["skin"])         # ear
        # rimless lens (thin ring approx by flat disc edge)
        cyl(ex,eye_y+0.02,eye_z,0.022,0.004,COL["glass"],"y")
        cyl(s*0.07,0.04,eye_z+0.005,0.0015,0.075,COL["glass"],"x")      # temple

def render(view, name, elev, azim):
    global ax
    fig = plt.figure(figsize=(6,9), dpi=130)
    ax = fig.add_subplot(111, projection="3d")
    build()
    ax.set_box_aspect((1,1,2.4))
    ax.set_xlim(-0.4,0.4); ax.set_ylim(-0.4,0.4); ax.set_zlim(0,1.9)
    ax.set_axis_off()
    ax.view_init(elev=elev, azim=azim)
    fig.patch.set_facecolor("#d8dee6")
    ax.set_facecolor("#d8dee6")
    ax.set_title("Narendra Sodhi - block-out ("+view+")", fontsize=11)
    out = os.path.join(OUT, name)
    fig.savefig(out, bbox_inches="tight", facecolor="#d8dee6")
    plt.close(fig)
    print("saved", out)

OUT = os.path.join(os.path.dirname(__file__))
render("front", "ns_front.png", elev=6, azim=-90)
render("3-4 view", "ns_threequarter.png", elev=8, azim=-60)
render("side", "ns_side.png", elev=6, azim=0)
