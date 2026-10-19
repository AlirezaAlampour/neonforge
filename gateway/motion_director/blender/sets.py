"""Reusable low-detail environment geometry; no invented motion or external assets."""
import math
import bpy


def box(root, name, location, size, mat):
    bpy.ops.mesh.primitive_cube_add(size=1)
    obj=bpy.context.object;obj.name=name;obj.parent=root;obj.location=location;obj.dimensions=size
    obj.data.materials.append(mat)
    return obj


def build(e, material):
    root=bpy.data.objects.new(e['id'],None);bpy.context.scene.collection.objects.link(root)
    sx,sy,sz=(e['size'][k] for k in 'xyz');base=material(e['color']);accent=material(e['accent_color'],emission=.25)
    dark=material('#1a2938',metallic=.5,roughness=.25)
    if e['geometry']=='train':
        box(root,'Train body',(0,0,0),(sx,sy,sz),material(e['color'],metallic=.45,roughness=.38))
        count=min(20,max(2,round(sy/2.4)))
        for side in (-1,1):
            box(root,'Reference color stripe',(side*sx*.505,0,-sz*.21),(.035,sy,sz*.045),accent)
            for i in range(count):
                y=(i+.5)*sy/count-sy/2
                is_door=i%4==1
                box(root,'Door' if is_door else 'Window',(side*sx*.51,y,-sz*.02 if is_door else sz*.15),(.045,sy/count*.7,sz*(.8 if is_door else .32)),dark)
                if is_door:
                    box(root,'Door seam',(side*sx*.52,y,-sz*.02),(.05,.025,sz*.8),base)
        box(root,'Roof',(0,0,sz*.5),(sx*1.02,sy,.12),dark)
    elif e['geometry']=='building':
        box(root,'Building mass',(0,0,0),(sx,sy,sz),base)
        for z in range(min(14,max(2,round(sz/1.5)))):
            for y in range(min(7,max(1,round(sy/2)))):
                for side in (-1,1):
                    box(root,'Facade light',(side*sx*.505,(y+.5)*sy/max(1,round(sy/2))-sy/2,(z+.5)*sz/max(2,round(sz/1.5))-sz/2),(.025,.5,.3),accent)
    elif e['geometry']=='sign':
        box(root,'Sign panel',(0,0,0),(sx,sy,sz),base)
        box(root,'Sign upper edge',(0,-sy*.55,sz*.46),(sx,.025,sz*.07),accent)
        data=bpy.data.curves.new('Reference sign','FONT');data.body=e['label'][:40];data.align_x='CENTER';data.size=min(sz*.5,sx/max(2,len(data.body)*.65))
        text=bpy.data.objects.new('Sign label',data);bpy.context.scene.collection.objects.link(text);text.parent=root
        text.location=(0,-sy*.6,-sz*.2);text.rotation_euler=(math.pi/2,0,0);data.materials.append(accent)
    else: # canopy: broad plane and structural beams, inferred from the reference
        box(root,'Canopy roof',(0,0,0),(sx,sy,max(.12,sz)),base)
        count=min(12,max(2,round(sy/6)))
        for i in range(count):
            y=(i+.5)*sy/count-sy/2
            box(root,'Roof beam',(0,y,-.15),(sx,.15,.3),dark)
            box(root,'Ceiling light',(0,y,-.32),(sx*.65,.08,.03),accent)
    if e['geometry'] in {'train','building'}:
        for child in root.children:child.location.z+=sz/2
    return root
