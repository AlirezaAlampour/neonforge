"""Native skeletal actions, Blender NLA blending and evaluated contact evidence."""
import json
import math
from pathlib import Path

import bpy
from mathutils import Vector

RIGS = {}


def curves(action):
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                yield from bag.fcurves


def import_pack(path):
    before, before_actions = set(bpy.data.objects), set(bpy.data.actions)
    bpy.ops.import_scene.gltf(filepath=str(path))
    objects = set(bpy.data.objects)-before
    rig = next(o for o in objects if o.type=='ARMATURE')
    actions = {a.name.split('.')[0]: a for a in set(bpy.data.actions)-before_actions}
    for a in actions.values(): a.use_fake_user = True
    for track in list(rig.animation_data.nla_tracks):rig.animation_data.nla_tracks.remove(track)
    rig.animation_data.action = None
    return rig, objects, actions


def build(entity, folder, library, fps, duration, material):
    # Import at the scene fps so source seconds remain independent of output fps.
    rig, objects, actions = import_pack(Path(folder)/'animation_assets/UAL1_Standard.glb')
    keep = {rig, *(o for o in objects if o.type=='MESH' and o.find_armature()==rig)}
    for obj in objects-keep:bpy.data.objects.remove(obj,do_unlink=True)
    if any(library[c['clip']][0]=='UAL2_Standard' for c in entity['actions']):
        other, imported, extra = import_pack(Path(folder)/'animation_assets/UAL2_Standard.glb')
        # Native skeleton compatibility, not an arbitrary-name retarget guess.
        for bone in rig.data.bones:
            source = other.data.bones.get(bone.name)
            if source is None or (source.head_local-bone.head_local).length>.005 or (source.tail_local-bone.tail_local).length>.005:
                raise ValueError('Animation pack bind skeleton is incompatible')
        actions.update(extra)
        for obj in imported:bpy.data.objects.remove(obj,do_unlink=True)
    rig.name = entity['id'];rig.rotation_mode='XYZ'
    rig.data=rig.data.copy();rig.data.name=entity['id']+' independent skeleton'
    look = entity.get('character') or {}
    palette = {k:material(look.get(k, v)) for k,v in {'skin':'#bd8b68','top':entity['color'],'bottom':'#363348','shoes':'#383038'}.items()}
    for mesh in keep-{rig}:
        mesh.name = entity['id']+' body';mesh.data=mesh.data.copy();mesh.data.materials.clear()
        for mat in palette.values():mesh.data.materials.append(mat)
        group_names = {g.index:g.name for g in mesh.vertex_groups}
        for poly in mesh.data.polygons:
            weights = {}
            for vi in poly.vertices:
                for group in mesh.data.vertices[vi].groups:
                    name=group_names.get(group.group,'')
                    weights[name]=weights.get(name,0)+group.weight
            bone=max(weights,key=weights.get) if weights else 'spine'
            region = 'shoes' if any(x in bone for x in ('foot','ball')) else 'bottom' if any(x in bone for x in ('pelvis','thigh','calf')) else 'skin' if any(x in bone for x in ('Head','neck','hand','arm','thumb','index','middle','ring','pinky')) else 'top'
            poly.material_index=list(palette).index(region)
    # Hold the source boxing stance throughout gaps; all overlays crossfade to it.
    guard = actions['Punch_Jab'].copy();guard.name=entity['id']+' authored guard'
    for curve in curves(guard):
        value=curve.evaluate(0);curve.keyframe_points.clear()
        for frame in (0,1):curve.keyframe_points.insert(frame,value)
    track=rig.animation_data.nla_tracks.new();track.name='Authored boxing stance'
    strip=track.strips.new('Guard',1,guard);strip.action_slot=guard.slots[0]
    strip.action_frame_start=0;strip.action_frame_end=1;strip.scale=duration*fps;strip.extrapolation='NOTHING'
    for index,cue in enumerate(entity['actions']):
        source, action_name, start, end, category = library[cue['clip']]
        if cue['clip']=='guard':continue
        action=actions[action_name]
        track=rig.animation_data.nla_tracks.new();track.name=f'{index+1}: {cue["clip"]}'
        strip=track.strips.new(cue['clip'],round(cue['start']*fps)+1,action)
        strip.action_slot=action.slots[0]
        strip.action_frame_start=start*fps
        strip.action_frame_end=end*fps if end is not None else action.frame_range[1]
        strip.scale=(cue['end']-cue['start'])*fps/max(.001,strip.action_frame_end-strip.action_frame_start)
        strip.extrapolation='NOTHING';strip.blend_type='REPLACE';strip.use_auto_blend=False
        strip.blend_in=cue['blend']*fps;strip.blend_out=cue['blend']*fps
    rig['animation_source']='Quaternius CC0 native skeletal clips'
    # During a deep crouch crossfade, interpolating joint rotations alone can
    # swing a planted foot into the air. Sample the original actions separately
    # and solve ankles toward interpolated authored contact positions.
    raw=rig.copy();raw.data=rig.data.copy();raw.name=entity['id']+' source sampling'
    bpy.context.scene.collection.objects.link(raw)
    for track in raw.animation_data.nla_tracks:
        for strip in track.strips:strip.blend_in=strip.blend_out=0
    stance=raw.copy();stance.data=raw.data.copy();stance.name=entity['id']+' guard sampling'
    bpy.context.scene.collection.objects.link(stance)
    for track in list(stance.animation_data.nla_tracks)[1:]:stance.animation_data.nla_tracks.remove(track)
    targets={}
    for side in ('l','r'):
        target=bpy.data.objects.new(entity['id']+' ankle '+side,None);bpy.context.scene.collection.objects.link(target)
        target.rotation_mode='QUATERNION'
        ik=rig.pose.bones['calf_'+side].constraints.new('IK');ik.target=target;ik.chain_count=2;ik.use_stretch=False
        rotate=rig.pose.bones['foot_'+side].constraints.new('COPY_ROTATION');rotate.target=target
        rotate.owner_space='WORLD';rotate.target_space='WORLD'
        targets[side]=target
    RIGS[rig.name]=(raw,stance,targets,entity)
    return rig


def plant(rig,t,frame):
    raw,stance,targets,entity=RIGS[rig.name]
    cue=next((a for a in entity['actions'] if a['start']<=t<a['end'] and a['clip']!='guard'),None)
    weight=min(1,(t-cue['start'])/cue['blend'],(cue['end']-t)/cue['blend']) if cue else 0
    for side,target in targets.items():
        a=stance.pose.bones['foot_'+side].matrix
        b=raw.pose.bones['foot_'+side].matrix
        target.location=rig.matrix_world @ a.translation.lerp(b.translation,weight)
        target.rotation_quaternion=rig.matrix_world.to_quaternion() @ a.to_quaternion().slerp(b.to_quaternion(),weight)
        target.keyframe_insert('location',frame=frame);target.keyframe_insert('rotation_quaternion',frame=frame)


def joints(rig):
    names=('hand_l','hand_r','foot_l','foot_r','ball_l','ball_r','Head','spine_03')
    return {n:list(rig.matrix_world @ rig.pose.bones[n].head) for n in names}


def ground(rig, base_z):
    points=joints(rig)
    # Authored foot/toe joint offsets above their sole, scaled to this character.
    scale=rig.scale.z
    low=min(points['foot_l'][2]-.103*scale,points['foot_r'][2]-.103*scale,
            points['ball_l'][2]-.015*scale,points['ball_r'][2]-.015*scale)
    # Preserve authored jumps/rolls; only remove tiny import/scale floor error.
    correction=max(-.04,min(.08,base_z-low))
    rig.location.z+=correction
    return correction
