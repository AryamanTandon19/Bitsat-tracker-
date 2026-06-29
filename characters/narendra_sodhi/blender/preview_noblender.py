"""
Narendra Sodhi - improved block-out preview (v2, no Blender).
Now with tapered torso, sloped shoulders, real upper-arm + forearm
cylinders folding to the clasped hands, tapered kurta skirt and legs.
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
norm = np.linalg.norm

def _shade(c, f):
    c = np.array(matplotlib.colors.to_rgb(c)); return tuple(np.clip(c*f,0,1))

def sphere(cx,cy,cz,sx,sy,sz,color,n=22):
    u=np.linspace(0,2*np.pi,n); v=np.linspace(0,np.pi,n)
    x=cx+sx*np.outer(np.cos(u),np.sin(v))
    y=cy+sy*np.outer(np.sin(u),np.sin(v))
    z=cz+sz*np.outer(np.ones_like(u),np.cos(v))
    ax.plot_surface(x,y,z,color=color,linewidth=0,shade=True,rcount=n,ccount=n)

def box(cx,cy,cz,sx,sy,sz,color):
    frustum(cx,cy,cz,(sx,sy),(sx,sy),sz,color)

def frustum(cx,cy,cz,bot,top,zh,color):
    bx,by=bot; tx,ty=top
    b=cz-zh; t=cz+zh
    p=np.array([
        [-bx,-by,b],[bx,-by,b],[bx,by,b],[-bx,by,b],
        [-tx,-ty,t],[tx,-ty,t],[tx,ty,t],[-tx,ty,t]],float)+[cx,cy,0]
    faces=[(0,1,2,3),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]
    sh=[0.75,0.95,1.0,0.8,0.7,0.85]
    pc=Poly3DCollection([[p[i] for i in f] for f in faces],
        facecolors=[_shade(color,s) for s in sh],
        edgecolors=(0,0,0,0.06),linewidths=0.2)
    ax.add_collection3d(pc)

def cyl_between(p1,p2,r,color,n=16):
    p1=np.array(p1,float); p2=np.array(p2,float); v=p2-p1; L=norm(v)
    if L<1e-6: return
    nh=v/L
    a=np.array([1.,0,0]) if abs(nh[0])<0.9 else np.array([0,1.,0])
    b1=np.cross(nh,a); b1/=norm(b1); b2=np.cross(nh,b1)
    t=np.linspace(0,2*np.pi,n); zz=np.linspace(0,L,2)
    T,Z=np.meshgrid(t,zz)
    X=p1[0]+Z*nh[0]+r*np.cos(T)*b1[0]+r*np.sin(T)*b2[0]
    Y=p1[1]+Z*nh[1]+r*np.cos(T)*b1[1]+r*np.sin(T)*b2[1]
    Zc=p1[2]+Z*nh[2]+r*np.cos(T)*b1[2]+r*np.sin(T)*b2[2]
    ax.plot_surface(X,Y,Zc,color=color,linewidth=0,shade=True)

def build():
    HT=math.radians(8)
    ankle=0.06; knee=0.50; hip=0.92; waist=1.05; chest=1.27
    shldr=1.40; neck=1.48; neck_top=1.54
    # legs: tapered thigh + calf, narrow ankle (churidar)
    for s in (-1,1):
        lx=s*0.075
        cyl_between((lx,0.01,hip),(lx,0.0,knee),0.072,COL["churidar"])
        cyl_between((lx,0.01,knee),(lx,0.02,ankle+0.02),0.040,COL["churidar"])
        box(lx,0.055,ankle-0.015,0.05,0.10,0.018,COL["sandal"])
        box(lx,0.02,ankle+0.02,0.05,0.018,0.012,COL["sandal"])
        box(lx,0.075,ankle+0.018,0.05,0.018,0.010,COL["sandal"])
    # pelvis + tapered kurta (skirt flares to knee, torso narrows to waist)
    frustum(0,0,(knee+hip)/2,(0.10,0.075),(0.135,0.085),(hip-knee)/2,COL["kurta"])
    frustum(0,0,(hip+waist)/2,(0.135,0.085),(0.135,0.085),(waist-hip)/2,COL["kurta"])
    frustum(0,0,(waist+chest)/2,(0.135,0.085),(0.165,0.092),(chest-waist)/2,COL["kurta"])
    # chest -> sloped shoulders up to mandarin collar
    frustum(0,0,(chest+neck)/2,(0.165,0.092),(0.06,0.06),(neck-chest)/2,COL["kurta"])
    cyl_between((0,0,neck-0.02),(0,0,neck+0.05),0.05,COL["kurta"])     # collar
    cyl_between((0,0,neck+0.02),(0,0,neck_top+0.02),0.043,COL["skin"]) # neck
    # arms: shoulder ball, upper arm down-inward, forearm folding to hands
    for s in (-1,1):
        sh=(s*0.165,0.0,shldr)
        elbow=(s*0.135,0.06,1.07)
        hands=(0,0.17,waist-0.05)
        sphere(*sh,0.052,0.058,0.058,COL["kurta"])
        cyl_between(sh,elbow,0.045,COL["kurta"])
        cyl_between(elbow,(s*0.07,0.15,waist-0.04),0.040,COL["kurta"])
    sphere(0,0.18,waist-0.05,0.052,0.052,0.038,COL["skin"])   # folded hands
    # stole: white drapes + saffron borders down chest
    box(0,-0.035,neck-0.02,0.07,0.02,0.04,COL["stole"])
    for s in (-1,1):
        sx=s*0.065
        box(sx,0.095,(chest+waist)/2,0.032,0.014,(chest-waist)/2+0.13,COL["stole"])
        box(sx+s*0.028,0.10,(chest+waist)/2,0.007,0.015,(chest-waist)/2+0.13,COL["saffron"])
    # ---- head (turned gently to his left)
    hz=neck_top+0.12
    sphere(0,0,hz,0.090,0.103,0.115,COL["skin"])
    sphere(0,0.05,hz+0.055,0.080,0.05,0.05,COL["skin"])     # forehead
    frustum(0,0.015,hz-0.08,(0.082,0.07),(0.088,0.075),0.045,COL["skin"]) # jaw
    sphere(0,-0.012,hz+0.082,0.086,0.098,0.05,COL["hair"])  # hair
    sphere(0,0.045,hz-0.082,0.095,0.083,0.083,COL["beard"]) # beard
    box(0,0.098,hz-0.055,0.03,0.012,0.012,COL["beard"])    # mustache
    for s in (-1,1):
        box(s*0.083,0.0,hz-0.018,0.012,0.02,0.05,COL["beard"]) # sideburn
    box(0,0.10,hz-0.008,0.017,0.028,0.048,COL["skin"])   # nose bridge
    sphere(0,0.122,hz-0.042,0.025,0.027,0.023,COL["skin"])  # nose tip
    box(0,0.097,hz-0.072,0.022,0.012,0.004,COL["mouth"]) # mouth
    eye_y=0.082; eye_z=hz+0.012
    for s in (-1,1):
        ex=s*0.034
        sphere(ex,eye_y,eye_z,0.018,0.014,0.014,COL["eye"])
        sphere(ex-0.004,eye_y+0.011,eye_z,0.008,0.008,0.008,COL["iris"])
        box(ex,eye_y+0.004,eye_z+0.027,0.025,0.011,0.006,COL["beard"])
        sphere(s*0.090,0,hz-0.012,0.012,0.021,0.031,COL["skin"])
        cyl_between((ex-0.022,eye_y+0.02,eye_z),(ex+0.022,eye_y+0.02,eye_z),0.018,COL["glass"]) if False else None
        # rimless lens hint (thin disk) + temple
        sphere(ex,eye_y+0.022,eye_z,0.022,0.004,0.020,COL["glass"])
        cyl_between((ex,eye_y+0.02,eye_z+0.004),(s*0.078,0.02,eye_z+0.006),0.0016,COL["glass"])

def render(view,name,elev,azim):
    global ax
    fig=plt.figure(figsize=(6,9),dpi=130)
    ax=fig.add_subplot(111,projection="3d")
    build()
    ax.set_box_aspect((1,1,2.4))
    ax.set_xlim(-0.4,0.4); ax.set_ylim(-0.4,0.4); ax.set_zlim(0,1.95)
    ax.set_axis_off(); ax.view_init(elev=elev,azim=azim)
    fig.patch.set_facecolor("#d8dee6"); ax.set_facecolor("#d8dee6")
    ax.set_title("Narendra Sodhi - block-out v2 ("+view+")",fontsize=11)
    out=os.path.join(OUT,name); fig.savefig(out,bbox_inches="tight",facecolor="#d8dee6")
    plt.close(fig); print("saved",out)

OUT=os.path.dirname(__file__)
render("front","ns2_front.png",6,-90)
render("3-4 view","ns2_threequarter.png",8,-58)
render("side","ns2_side.png",6,0)
