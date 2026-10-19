"""Mannequin skeleton and body parts, in the rig's rest pose.

Units are metres. The rig stands on the floor at the origin, Z up, facing -Y
(Blender's front view), so its left side is +X. About 1.8 m tall.
"""

# (bone, head, tail, parent)
BONES = [
    ("hips", (0.0, 0.0, 0.96), (0.0, 0.0, 1.06), None),
    ("spine", (0.0, 0.0, 1.06), (0.0, 0.0, 1.26), "hips"),
    ("chest", (0.0, 0.0, 1.26), (0.0, 0.0, 1.46), "spine"),
    ("neck", (0.0, 0.0, 1.46), (0.0, 0.0, 1.57), "chest"),
    ("head", (0.0, 0.0, 1.57), (0.0, 0.0, 1.80), "neck"),
]
for _side, _x in (("L", 1.0), ("R", -1.0)):
    BONES += [
        (f"upper_arm.{_side}", (0.20 * _x, 0.0, 1.42), (0.20 * _x, 0.0, 1.13), "chest"),
        (f"forearm.{_side}", (0.20 * _x, 0.0, 1.13), (0.20 * _x, 0.0, 0.87), f"upper_arm.{_side}"),
        (f"hand.{_side}", (0.20 * _x, 0.0, 0.87), (0.20 * _x, 0.0, 0.78), f"forearm.{_side}"),
        (f"thigh.{_side}", (0.10 * _x, 0.0, 0.96), (0.10 * _x, 0.0, 0.52), "hips"),
        (f"shin.{_side}", (0.10 * _x, 0.0, 0.52), (0.10 * _x, 0.0, 0.08), f"thigh.{_side}"),
        (f"foot.{_side}", (0.10 * _x, 0.0, 0.08), (0.10 * _x, -0.15, 0.02), f"shin.{_side}"),
    ]

BONE_INDEX = {b[0]: b for b in BONES}

HIP_HEIGHT = 0.96          # hip joint above the floor when standing
SEATED_HIP_ABOVE_SEAT = 0.07  # hip joint above the seat surface when sitting

# Body parts. Each is rigidly weighted to one bone.
#   ("ellipsoid", bone, centre, (rx, ry, rz))
#   ("limb", bone, head_radius, tail_radius)   -- tapered cylinder along the bone
#   ("box", bone, centre, (sx, sy, sz))
PARTS = [
    ("ellipsoid", "hips", (0.0, 0.0, 0.99), (0.165, 0.11, 0.10)),
    ("ellipsoid", "spine", (0.0, 0.0, 1.15), (0.14, 0.10, 0.11)),
    ("ellipsoid", "chest", (0.0, 0.0, 1.35), (0.19, 0.12, 0.155)),
    ("limb", "neck", 0.045, 0.05),
    ("ellipsoid", "head", (0.0, 0.0, 1.685), (0.095, 0.11, 0.12)),
    # Nose, so head direction reads in a flat-colour silhouette.
    ("ellipsoid", "head", (0.0, -0.105, 1.67), (0.025, 0.03, 0.03)),
]
for _side, _x in (("L", 1.0), ("R", -1.0)):
    PARTS += [
        ("ellipsoid", f"upper_arm.{_side}", (0.20 * _x, 0.0, 1.42), (0.055, 0.055, 0.055)),
        ("limb", f"upper_arm.{_side}", 0.048, 0.040),
        ("ellipsoid", f"forearm.{_side}", (0.20 * _x, 0.0, 1.13), (0.042, 0.042, 0.042)),
        ("limb", f"forearm.{_side}", 0.038, 0.031),
        ("ellipsoid", f"hand.{_side}", (0.20 * _x, 0.0, 0.825), (0.028, 0.045, 0.06)),
        ("ellipsoid", f"thigh.{_side}", (0.10 * _x, 0.0, 0.96), (0.075, 0.075, 0.075)),
        ("limb", f"thigh.{_side}", 0.070, 0.052),
        ("ellipsoid", f"shin.{_side}", (0.10 * _x, 0.0, 0.52), (0.055, 0.055, 0.055)),
        ("limb", f"shin.{_side}", 0.050, 0.038),
        ("box", f"foot.{_side}", (0.10 * _x, -0.06, 0.035), (0.09, 0.24, 0.07)),
    ]
