"""Rigged primitive mannequin: an armature plus one mesh, rigidly skinned."""

import math

import bmesh
import bpy
from mathutils import Euler, Quaternion, Vector

from . import rig
from . import geo


def build(name, material, collection):
    """Create the rig and body. Returns the armature object."""
    arm_data = bpy.data.armatures.new(f"{name}_rig")
    arm = bpy.data.objects.new(name, arm_data)
    collection.objects.link(arm)

    view_layer = bpy.context.view_layer
    view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    for bone, head, tail, parent in rig.BONES:
        eb = arm_data.edit_bones.new(bone)
        eb.head, eb.tail, eb.roll = head, tail, 0.0
        if parent:
            eb.parent = arm_data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")

    group_index = {bone: i for i, (bone, *_rest) in enumerate(rig.BONES)}
    bm = bmesh.new()
    deform = bm.verts.layers.deform.verify()
    for kind, bone, *spec in rig.PARTS:
        if kind == "ellipsoid":
            verts = geo.ellipsoid(bm, spec[0], spec[1], segments=(14, 9), smooth=False)
        elif kind == "limb":
            _, head, tail, _ = rig.BONE_INDEX[bone]
            verts = geo.cylinder(bm, head, tail, spec[0], spec[1], segments=12, smooth=False)
        elif kind == "box":
            c, s = spec
            verts = geo.box(bm, [c[i] - s[i] / 2 for i in range(3)], [c[i] + s[i] / 2 for i in range(3)])
        else:
            raise ValueError(kind)
        for v in verts:
            v[deform][group_index[bone]] = 1.0

    mesh = bpy.data.meshes.new(f"{name}_body")
    bm.to_mesh(mesh)
    bm.free()
    mesh.materials.append(material)
    body = bpy.data.objects.new(f"{name}_body", mesh)
    collection.objects.link(body)
    for bone, *_rest in rig.BONES:
        body.vertex_groups.new(name=bone)
    body.parent = arm
    body.modifiers.new("rig", "ARMATURE").object = arm

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
