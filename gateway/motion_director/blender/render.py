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
from bpy_extras.object_utils import world_to_camera_view

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blender import mannequin, clips, sets, authored
from blender.camera import FollowRig
from blender.paths import sample as path_position


def sample(path, t, fallback):
    return Vector(path_position(path,t,fallback))


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


def object_for(e, assets, data=None, folder=None):
    if e['geometry']=='mannequin':
        if e.get('rig')=='quaternius-v1':
            return authored.build(e,folder,data['animation_library'],24,data['duration'],material)
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
    objects={e['id']:object_for(e,data.get('assets',{}),data,folder) for e in spec['entities']}
    camera_data=bpy.data.cameras.new('DirectorCamera');camera_data.lens=spec['camera']['lens'];camera_data.clip_end=400
    camera_data.sensor_fit='VERTICAL';camera_data.sensor_height=24
    camera=bpy.data.objects.new('DirectorCamera',camera_data);scene.collection.objects.link(camera);scene.camera=camera
    dolly=bpy.data.objects.new('CameraFollowRig',None);scene.collection.objects.link(dolly);camera.parent=dolly
    focus=bpy.data.objects.new('CameraAim',None);scene.collection.objects.link(focus)
    track=camera.constraints.new('TRACK_TO');track.target=focus;track.track_axis='TRACK_NEGATIVE_Z';track.up_axis='UP_Y'
    solver=FollowRig(spec['camera']);headings={};velocities={}
    travel={k:0 for k in objects};last={};measurements=[]
    for f in range(1,frames+1):
        t=(f-1)/fps
        for e in spec['entities']:
            obj=objects[e['id']];pos=sample(e['path'],t,list(e['position'].values()))
            ta,tb=max(0,t-.12),min(duration,t+.12)
            delta=sample(e['path'],tb,pos)-sample(e['path'],ta,pos)
            velocities[e['id']]=delta/max(.001,tb-ta)
            travel[e['id']]+= (pos-last[e['id']]).length if e['id'] in last else 0;last[e['id']]=pos.copy()
            yaw=math.atan2(delta.x,-delta.y) if delta.length>.00001 else headings.get(e['id'],math.radians(e['yaw']))
            if e.get('facing'):
                other=next(o for o in spec['entities'] if o['id']==e['facing'])
                facing=sample(other['path'],t,list(other['position'].values()))-pos
                if facing.length>.001:yaw=math.atan2(facing.x,-facing.y)
            headings[e['id']]=yaw
            obj.location=pos;obj.rotation_euler=(0,0,yaw)
            if e['geometry']=='mannequin':
                if e.get('rig')!='quaternius-v1':
                    pose,phase=clips.evaluate(e['clip'],travel[e['id']]);mannequin.apply_pose(obj,pose,frame=f)
                obj.scale=(e['size']['x']*(e.get('character') or {}).get('height_m',1.8)/1.8,)*3
            obj.keyframe_insert('location',frame=f);obj.keyframe_insert('rotation_euler',frame=f)
        c=spec['camera'];target=objects[c['target']]
        framed=[target,*(objects[i] for i in c.get('include',[]) if i!=c['target'])]
        center=sum((o.location for o in framed),Vector())/len(framed)
        explicit=sample(c['path'],t,(0,-4,2)) if c['path'] else None
        angle=math.radians(c['orbit_start']+(c['orbit_end']-c['orbit_start'])*t/duration) if c['mode']=='orbit' else None
        velocity=sum((velocities[o.name] for o in framed),Vector())/len(framed)
        position,aim=solver.step(center,velocity,target.rotation_euler.z,1/fps,explicit,angle)
        dolly.location=position;focus.location=aim
        dolly.keyframe_insert('location',frame=f);focus.keyframe_insert('location',frame=f)
        scene.frame_set(f);bpy.context.view_layer.update()
        native=[e for e in spec['entities'] if e.get('rig')=='quaternius-v1']
        for e in native:authored.plant(objects[e['id']],t,f)
        bpy.context.view_layer.update()
        for e in native:
            obj=objects[e['id']];authored.ground(obj,e['position']['z']);obj.keyframe_insert('location',frame=f)
        bpy.context.view_layer.update()
        evaluated=camera.evaluated_get(bpy.context.evaluated_depsgraph_get()).matrix_world
        bounds=[]
        for e in native:
            for name,point in authored.joints(objects[e['id']]).items():
                v=Vector(point)
                if name=='Head':v.z+=.23*objects[e['id']].scale.z
                if name.startswith('ball_'):v.z-=.015*objects[e['id']].scale.z
                screen=world_to_camera_view(scene,camera,v);bounds.append((screen.x,screen.y,screen.z))
        measurements.append({'frame':f,'time':t,'camera':list(evaluated.translation),'aim':list(aim),
          'framing':{'left':min(p[0] for p in bounds),'right':max(p[0] for p in bounds),'bottom':min(p[1] for p in bounds),'top':max(p[1] for p in bounds),'depth':min(p[2] for p in bounds)} if bounds else None,
          'forward':list(evaluated.to_quaternion() @ Vector((0,0,-1))),
          'entities':{e['id']:{'position':list(objects[e['id']].matrix_world.translation),'yaw':objects[e['id']].rotation_euler.z,
             'distance':travel[e['id']], 'phase':travel[e['id']]/clips.STRIDE.get(e['clip'],1),
             'shin':list(objects[e['id']].pose.bones['shin.L'].rotation_quaternion) if e['geometry']=='mannequin' and e.get('rig')!='quaternius-v1' else None,
             'joints':authored.joints(objects[e['id']]) if e.get('rig')=='quaternius-v1' else None,
             'actions':[a['clip'] for a in e.get('actions',[]) if a['start']<=t<a['end']]} for e in spec['entities']}})
    for obj in (dolly,focus):
        if obj.animation_data and obj.animation_data.action:
            for layer in obj.animation_data.action.layers:
                for strip in layer.strips:
                    for bag in strip.channelbags:
                        for curve in bag.fcurves:
                            for key in curve.keyframe_points:key.interpolation='LINEAR'
    scene.frame_set(1);bpy.ops.wm.save_as_mainfile(filepath=str(folder/'scene.blend'))
    (folder/'transforms.json').write_text(json.dumps({'blender':bpy.app.version_string,'device':'CPU','fps':fps,'frames':measurements}))
    if spec.get('interactions'):
        from blender.validation import validate
        report=validate(spec,measurements)
        (folder/'timeline-validation.json').write_text(json.dumps(report,indent=2))
        if not report['valid']:raise ValueError('Choreography preview failed: '+ '; '.join(report['errors']))
    if data.get('render_images',True):
        (folder/'frames').mkdir(exist_ok=True);scene.render.filepath=str(folder/'frames'/'frame-')
        bpy.ops.render.render(animation=True)


if __name__=='__main__':
    args=sys.argv[sys.argv.index('--')+1:];main(json.loads(Path(args[0]).read_text()),args[1])
