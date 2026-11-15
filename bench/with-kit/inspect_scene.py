"""Workbench inspection pass. 0.6s a frame, so look constantly."""
import os, sys, math
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib as L
from lib import bpy, Euler, R, RN, INS, M

WORK = os.path.join(HERE, "work")
OUT = os.path.join(WORK, "inspect")
os.makedirs(OUT, exist_ok=True)

bpy.ops.wm.open_mainfile(filepath=os.path.join(WORK, "scene.blend"))
sc = bpy.context.scene
cam = sc.camera
RN.workbench(shading="STUDIO", color_type="MATERIAL", outline=True, cavity=False)

views = [
    ("wide",    (-5.6, -13.1, 1.85), (854, 480), 50.0),
    ("heroes",  (-6.8, -3.3, 0.55),  (400, 400), 60.0),
    ("mario",   (-6.0, -1.7, 0.48),  (400, 400), 55.0),
    ("luigi",   (-7.6, -1.8, 0.52),  (400, 400), 55.0),
    ("koopa",   (11.5, -1.6, 0.42),  (400, 400), 55.0),
    ("qblock",  (0.0, -3.2, 2.5),    (400, 400), 60.0),
    ("pipe",    (8.2, -4.0, 1.1),    (400, 400), 55.0),
]
paths = []
for name, loc, res, lens in views:
    cam.data.lens = lens
    cam.location = loc
    cam.rotation_euler = Euler((R(90), 0, 0), 'XYZ')
    p, dt = RN.shot(os.path.join(OUT, name), resolution=res)
    paths.append(p)

RN.contact_sheet(paths[1:], os.path.join(OUT, "sheet.png"), cols=3, tile=300)
print(INS.report(verbose=False))
print("triangles", L.tri_count())
