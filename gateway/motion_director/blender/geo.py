"""bmesh primitive helpers. Each returns the new vertices."""

import bmesh
from mathutils import Matrix, Vector


def _diag(size):
    return Matrix.Diagonal((*size, 1.0))


def ellipsoid(bm, centre, radii, segments=(16, 10), smooth=True):
    r = bmesh.ops.create_uvsphere(bm, u_segments=segments[0], v_segments=segments[1],
                                  radius=1.0, matrix=Matrix.Translation(centre) @ _diag(radii))
    if smooth:
        for f in {f for v in r["verts"] for f in v.link_faces}:
            f.smooth = True
    return r["verts"]


def box(bm, lo, hi):
    centre = [(a + b) / 2.0 for a, b in zip(lo, hi)]
    size = [abs(b - a) for a, b in zip(lo, hi)]
    return bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation(centre) @ _diag(size))["verts"]


def cylinder(bm, a, b, radius_a, radius_b=None, segments=16, smooth=True):
    """Capped, optionally tapered cylinder from point a to point b."""
    a, b = Vector(a), Vector(b)
    axis = b - a
    rot = Vector((0.0, 0.0, 1.0)).rotation_difference(axis.normalized()).to_matrix().to_4x4()
    r = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segments,
                              radius1=radius_a, radius2=radius_a if radius_b is None else radius_b,
                              depth=axis.length, matrix=Matrix.Translation((a + b) / 2.0) @ rot)
    verts = r["verts"]
    if smooth:
        # Smooth sides, flat caps, hard edge between them.
        faces = {f for v in verts for f in v.link_faces}
        for f in faces:
            f.smooth = len(f.verts) == 4
        rim = [e for f in faces for e in f.edges
               if len(e.link_faces) == 2 and e.link_faces[0].smooth != e.link_faces[1].smooth]
        bmesh.ops.split_edges(bm, edges=list(set(rim)))
    return verts
