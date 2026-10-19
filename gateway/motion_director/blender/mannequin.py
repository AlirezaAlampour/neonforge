"""Rigged primitive mannequin: an armature plus one mesh, rigidly skinned."""

import math

import bmesh
import bpy
from mathutils import Euler, Quaternion, Vector

from . import rig
from . import geo


def build(name, material, collection, look=None, make_material=None):
    """Create the rig and body. Returns the armature object."""
    arm_data = bpy.data.armatures.new(f"{name}_rig")
    arm = bpy.data.objects.new(name, arm_data)
    collection.objects.link(arm)

    view_layer = bpy.context.view_layer
    view_layer.objects.active = arm
    look = look or {}
    xscale = look.get('shoulder_width', .4)/.4
    transform = lambda p: (p[0]*xscale,p[1],p[2])
    bpy.ops.object.mode_set(mode="EDIT")
    for bone, head, tail, parent in rig.BONES:
        eb = arm_data.edit_bones.new(bone)
        eb.head, eb.tail, eb.roll = transform(head), transform(tail), 0.0
        if parent:
            eb.parent = arm_data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")

    group_index = {bone: i for i, (bone, *_rest) in enumerate(rig.BONES)}
    bm = bmesh.new()
    deform = bm.verts.layers.deform.verify()
    colors = ['skin','top','bottom','shoes','hair','bag']
    palette = [make_material('#553721' if k=='bag' else look.get(k, '#8d7561')) for k in colors] if make_material else [material]
    parts=list(rig.PARTS)
    if look:
        parts.append(('ellipsoid','head',(0,.015,1.75),(.102,.115,.075)))
        if look.get('bag'):
            parts.append(('box','chest',(.1,.16,1.15),(.22,.12,.26)))
    for index,(kind, bone, *spec) in enumerate(parts):
        old_faces=set(bm.faces)
        old_vertices=set(bm.verts)
        if kind == "ellipsoid":
            verts = geo.ellipsoid(bm, transform(spec[0]), transform(spec[1]), segments=(20, 12), smooth=True)
        elif kind == "limb":
            _, head, tail, _ = rig.BONE_INDEX[bone]
            verts = geo.cylinder(bm, transform(head), transform(tail), spec[0], spec[1], segments=16, smooth=True)
        elif kind == "box":
            c, s = spec
            verts = geo.box(bm, transform([c[i] - s[i] / 2 for i in range(3)]), transform([c[i] + s[i] / 2 for i in range(3)]))
        else:
            raise ValueError(kind)
        for v in set(bm.verts)-old_vertices:
            v[deform][group_index[bone]] = 1.0
        part='skin'
        if bone in {'spine','chest'}:part='top'
        if bone=='hips' or bone.startswith('thigh'):part='bottom'
        if bone.startswith('foot'):part='shoes'
        if index>=len(rig.PARTS):part='hair' if kind=='ellipsoid' else 'bag'
        for face in set(bm.faces)-old_faces:
            face.material_index=colors.index(part) if make_material else 0

    mesh = bpy.data.meshes.new(f"{name}_body")
    bm.to_mesh(mesh)
    bm.free()
    for mat in palette:mesh.materials.append(mat)
    body = bpy.data.objects.new(f"{name}_body", mesh)
    collection.objects.link(body)
    for bone, *_rest in rig.BONES:
        body.vertex_groups.new(name=bone)
    body.parent = arm
    body.modifiers.new("rig", "ARMATURE").object = arm
    if any(not v.groups for v in mesh.vertices):
        raise ValueError('Every body vertex, including split cap vertices, must belong to the reusable rig')

    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
    return arm


def apply_pose(arm, pose, frame=None):
    """Set an evaluate() pose on the rig; keyframe it if `frame` is given."""
    for pb in arm.pose.bones:
        rest = pb.bone.matrix_local.to_quaternion()
        deg = pose["bones"].get(pb.name, (0.0, 0.0, 0.0))
        delta = Euler([math.radians(a) for a in deg], "XYZ").to_quaternion()
        # Rotation about the rig's rest axes, expressed in the bone's own frame.
        q = rest.inverted() @ delta @ rest
        q.make_compatible(Quaternion(pb.rotation_quaternion))
        pb.rotation_quaternion = q
        if pb.name == "hips":
            pb.location = rest.inverted() @ Vector(pose["hips"])
        if frame is not None:
            pb.keyframe_insert("rotation_quaternion", frame=frame)
            if pb.name == "hips":
                pb.keyframe_insert("location", frame=frame)
