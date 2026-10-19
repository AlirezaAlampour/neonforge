"""Trusted Blender compiler for a validated scene.json; CPU only.

Scene data is embedded in the exported scene.py launcher. Never exec LLM code.
The same script produces .blend, rendered frames and evaluated transform data.
"""
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blender import mannequin, clips, sets


def sample(path, t, fallback):
    if not path:
        return Vector(fallback)
    if t >= path[-1]['time']:
        return Vector(list(path[-1]['position'].values()))
    i = next(i for i in range(len(path)-1) if path[i]['time'] <= t <= path[i+1]['time'])
    a,b=path[i],path[i+1]
    p,q=Vector(list(a['position'].values())),Vector(list(b['position'].values()))
    span=b['time']-a['time'];u=(t-a['time'])/span
    before=path[max(0,i-1)];after=path[min(len(path)-1,i+2)]
    m0=(q-Vector(list(before['position'].values())))/(b['time']-before['time'])
    m1=(Vector(list(after['position'].values()))-p)/(after['time']-a['time'])
    return (2*u**3-3*u*u+1)*p+(u**3-2*u*u+u)*span*m0+(-2*u**3+3*u*u)*q+(u**3-u*u)*span*m1


KEY_DIRECTION = Vector((-.4,-.3,1)).normalized()


def material(color, emission=0, metallic=0, roughness=.65):
    # Analytic directional shading keeps motion references sharp and stable on
    # CPU without spending preview time on photorealistic path tracing.
    rgb=[int(color[i:i+2],16)/255 for i in (1,3,5)]
    mat=bpy.data.materials.new(color);mat.diffuse_color=(*rgb,1);mat.use_nodes=True
    nodes=mat.node_tree.nodes;nodes.clear()
    geometry=nodes.new('ShaderNodeNewGeometry');dot=nodes.new('ShaderNodeVectorMath');dot.operation='DOT_PRODUCT'
    dot.inputs[1].default_value=KEY_DIRECTION;mat.node_tree.links.new(geometry.outputs['Normal'],dot.inputs[0])
    shade=nodes.new('ShaderNodeMapRange');shade.inputs['From Min'].default_value=-1;shade.inputs['From Max'].default_value=1
    shade.inputs['To Min'].default_value=.28+emission;shade.inputs['To Max'].default_value=1+emission
    mat.node_tree.links.new(dot.outputs['Value'],shade.inputs['Value'])
    surface=nodes.new('ShaderNodeEmission');surface.inputs['Color'].default_value=(*rgb,1)
    mat.node_tree.links.new(shade.outputs['Result'],surface.inputs['Strength'])
    output=nodes.new('ShaderNodeOutputMaterial');mat.node_tree.links.new(surface.outputs[0],output.inputs['Surface'])
    return mat


def object_for(e, assets):
    if e['geometry']=='mannequin':
        look=e.get('character') or {'skin':'#b98a6d','hair':'#3f2c25','top':e['color'],'bottom':e['color'],'shoes':'#927047'}
        return mannequin.build(e['id'], material(e['color']), bpy.context.scene.collection, look, material)
    if e['geometry'] in {'train','building','sign','canopy'}:
        return sets.build(e,material)
    if e['geometry']=='asset':
        before=set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=assets[e['asset_id']])
        added=set(bpy.data.objects)-before
        root=bpy.data.objects.new(e['id'],None);bpy.context.scene.collection.objects.link(root)
        # Imported animations stay disabled: scene paths own every motion.
        for obj in added:
            obj.animation_data_clear()
            if obj.parent not in added:
                obj.parent=root
        points=[obj.matrix_world @ Vector(c) for obj in added if obj.type=='MESH' for c in obj.bound_box]
        if not points:
            raise ValueError('Asset contains no geometry')
        lo=Vector([min(p[i] for p in points) for i in range(3)])
        hi=Vector([max(p[i] for p in points) for i in range(3)])
        center=(lo+hi)/2
        for obj in added:
            if obj.parent==root:
                obj.location-=center
        root.scale=[e['size'][k]/max(.001,hi[i]-lo[i]) for i,k in enumerate('xyz')]
        return root
    {'box':bpy.ops.mesh.primitive_cube_add,'sphere':bpy.ops.mesh.primitive_uv_sphere_add,'cylinder':bpy.ops.mesh.primitive_cylinder_add}[e['geometry']]()
    obj=bpy.context.object;obj.name=e['id'];obj.dimensions=list(e['size'].values())
    obj.data.materials.append(material(e['color']))
    return obj


def main(data, folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    global KEY_DIRECTION
    spec=data['scene'];duration=data['duration'];fps=24;frames=duration*fps
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene=bpy.context.scene;scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=2
    scene.cycles.use_denoising=False;scene.cycles.max_bounces=0;scene.render.threads_mode='FIXED';scene.render.threads=2
    scene.render.resolution_x=data['width'];scene.render.resolution_y=data['height'];scene.render.resolution_percentage=100
    scene.render.fps=fps;scene.frame_start=1;scene.frame_end=frames
    scene.render.image_settings.file_format='PNG';scene.render.image_settings.compression=15
    scene.view_settings.view_transform='Standard';scene.world=bpy.data.worlds.new('Sky');scene.world.use_nodes=True
    scene.world.node_tree.nodes['Background'].inputs[0].default_value=(*[int(spec['sky_color'][i:i+2],16)/255 for i in (1,3,5)],1)
    scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value=.65
    lighting=spec.get('lighting',{});key=lighting.get('key_position',{'x':-4,'y':-2,'z':7});KEY_DIRECTION=Vector(list(key.values())).normalized()
    # Broad fixed lights along the scene, never attached to the character.
    for y in (-2,10,22,34):
        lamp=bpy.data.lights.new('Reference key','AREA');lamp.energy=lighting.get('strength',800);lamp.shape='DISK';lamp.size=9
        col=lighting.get('key_color','#ffe9cc');lamp.color=[int(col[i:i+2],16)/255 for i in (1,3,5)]
        obj=bpy.data.objects.new('Reference key',lamp);scene.collection.objects.link(obj);obj.location=(key['x'],y,key['z'])
        obj.rotation_euler=(Vector((0,y,0))-obj.location).to_track_quat('-Z','Y').to_euler()
    # All environment geometry is fixed unless the specification gives a path.
    bpy.ops.mesh.primitive_plane_add(size=320,location=(0,0,-.03));ground=bpy.context.object;ground.name='Ground';ground.data.materials.append(material(spec['ground_color'],metallic=.18,roughness=.38))
    objects={e['id']:object_for(e,data.get('assets',{})) for e in spec['entities']}
    camera_data=bpy.data.cameras.new('DirectorCamera');camera_data.lens=spec['camera']['lens'];camera_data.clip_end=400
    camera_data.sensor_fit='VERTICAL';camera_data.sensor_height=24
    camera=bpy.data.objects.new('DirectorCamera',camera_data);scene.collection.objects.link(camera);scene.camera=camera
    camera.rotation_mode='QUATERNION';previous_q=None;travel={k:0 for k in objects};last={};measurements=[]
    for f in range(1,frames+1):
        t=(f-1)/fps
        for e in spec['entities']:
            obj=objects[e['id']];pos=sample(e['path'],t,list(e['position'].values()))
            delta=sample(e['path'],min(duration,t+.12),pos)-sample(e['path'],max(0,t-.12),pos)
            travel[e['id']]+= (pos-last[e['id']]).length if e['id'] in last else 0;last[e['id']]=pos.copy()
            yaw=math.atan2(delta.x,-delta.y) if delta.length>.00001 else math.radians(e['yaw'])
            obj.location=pos;obj.rotation_euler=(0,0,yaw)
            if e['geometry']=='mannequin':
                pose,phase=clips.evaluate(e['clip'],travel[e['id']]);mannequin.apply_pose(obj,pose,frame=f)
                obj.scale=(e['size']['x']*(e.get('character') or {}).get('height_m',1.8)/1.8,)*3
            obj.keyframe_insert('location',frame=f);obj.keyframe_insert('rotation_euler',frame=f)
        c=spec['camera'];target=objects[c['target']];aim=target.location+Vector((0,0,c['aim_height']))
        if c['mode']=='follow':
            # Local mannequin faces -Y. Camera remains behind its travel heading.
            yaw=target.rotation_euler.z
            behind=Vector((-math.sin(yaw),math.cos(yaw),0))
            camera.location=target.location+behind*c['distance']+Vector((0,0,c['height']))
        elif c['mode']=='orbit':
            angle=math.radians(c['orbit_start']+(c['orbit_end']-c['orbit_start'])*t/duration)
            camera.location=target.location+Vector((math.sin(angle)*c['distance'],math.cos(angle)*c['distance'],c['height']))
        else:
            camera.location=sample(c['path'],t,(0,-4,2))
        q=(aim-camera.location).to_track_quat('-Z','Y')
        if previous_q:q.make_compatible(previous_q)
        camera.rotation_quaternion=q;previous_q=q.copy();camera.keyframe_insert('location',frame=f);camera.keyframe_insert('rotation_quaternion',frame=f)
        scene.frame_set(f);bpy.context.view_layer.update()
        measurements.append({'frame':f,'time':t,'camera':list(camera.matrix_world.translation),'aim':list(aim),
          'forward':list(camera.matrix_world.to_quaternion() @ Vector((0,0,-1))),
          'entities':{e['id']:{'position':list(objects[e['id']].matrix_world.translation),'yaw':objects[e['id']].rotation_euler.z,
             'distance':travel[e['id']], 'phase':travel[e['id']]/clips.STRIDE.get(e['clip'],1),
             'shin':list(objects[e['id']].pose.bones['shin.L'].rotation_quaternion) if e['geometry']=='mannequin' else None} for e in spec['entities']}})
    scene.frame_set(1);bpy.ops.wm.save_as_mainfile(filepath=str(folder/'scene.blend'))
    (folder/'transforms.json').write_text(json.dumps({'blender':bpy.app.version_string,'device':'CPU','fps':fps,'frames':measurements}))
    (folder/'frames').mkdir(exist_ok=True);scene.render.filepath=str(folder/'frames'/'frame-')
    bpy.ops.render.render(animation=True)


if __name__=='__main__':
    args=sys.argv[sys.argv.index('--')+1:];main(json.loads(Path(args[0]).read_text()),args[1])
