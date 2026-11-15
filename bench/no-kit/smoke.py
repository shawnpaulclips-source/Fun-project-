import time, sys, math
import lib, build, bpy
sc = lib.boot()
s = build.build_set()
YAW = math.radians(-38)
s["mario"]["root"].location=(-1.0,0,0); s["mario"]["root"].scale=(0.9,)*3
s["mario"]["root"].rotation_euler=(0,0,YAW)
s["luigi"]["root"].location=(1.1,0,0);  s["luigi"]["root"].scale=(0.9,)*3
s["luigi"]["root"].rotation_euler=(0,0,YAW)
s["koopa"]["root"].location=(3.8,0,0); s["koopa"]["root"].scale=(1.12,)*3;  s["koopa"]["root"].rotation_euler=(0,0,math.pi-YAW)
s["cam"].location=(0.6,-10.2,1.60)
bpy.context.view_layer.update()
v,t = lib.poly_count(); print("VERTS",v,"TRIS",t)
t2=time.time(); lib.set_cycles(24,3,True); lib.render_still("//tmp/smoke_cy.png")
print("CYCLES 1frame %.2fs"%(time.time()-t2))
