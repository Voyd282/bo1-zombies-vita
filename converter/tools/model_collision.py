"""model_collision.py - the collision step of the Vita conversion (zone_repack.py --simplify-collision).

A model can carry its own collision surfaces (XModel.collSurfs, XModelCollSurf_s: 44 bytes each), which bullet traces
against an entity's model test (xmodel.cpp XModelTraceLine...): an array of XModelCollTri_s per surface, 48 bytes a
triangle - a plane (normal, distance) and two vectors svec / tvec giving the hit point's barycentric s and t
(s = dot(p, svec) - svec.w, t = dot(p, tvec) - tvec.w; inside when s >= 0, t >= 0, s + t <= 1). Retail models carry
small ones; ported ones (WDS's Black Ops III perk machines, 2026-11-24) carry their whole render mesh - 48 bytes a
triangle, more than the visible triangle costs.

A collision surface with more than BUDGET triangles is simplified: its triangles' corners are found again (each corner
is where the plane meets s, t = (0,0), (1,0), (0,1)), welded by position, simplified with meshoptimizer (positions only,
its error limit relative to the surface's extents: a bullet's hit moves at most that far) and the 48-byte records built
again for the new triangles. Its bounds stay (the simplified surface's corners are a subset of the original's).
Only the triangle array is replaced (the same read, a smaller size) and the surface's numCollTris patched.
"""
import array
import math
import struct

import model_lowpoly

BUDGET = 400           # collision triangles a surface keeps as they are
RATIO = 0.15           # the target of a larger one: this share of its triangles (at least BUDGET)
ERROR = 0.03           # meshoptimizer's error limit, relative to the surface's extents
XMODEL_COLLSURFS, XMODEL_NUMCOLLSURFS = 172, 176
COLLSURF_SIZE, COLLTRI_SIZE = 44, 48
INLINE = 0xFFFFFFFF


def _solve(rows, rhs):
    """the 3x3 system rows . p = rhs (Cramer), or None when singular"""
    (a, b, c), (d, e, f), (g, h, i) = rows
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        return None
    x, y, z = rhs
    return ((x * (e * i - f * h) - b * (y * i - f * z) + c * (y * h - e * z)) / det,
            (a * (y * i - f * z) - x * (d * i - f * g) + c * (d * z - y * g)) / det,
            (a * (e * z - y * h) - b * (d * z - y * g) + x * (d * h - e * g)) / det)


def corners(tri):
    """a record's three corners (s, t = (0,0), (1,0), (0,1)), or None"""
    p = struct.unpack("<12f", tri)
    n, sv, tv = p[0:3], p[4:7], p[8:11]
    out = []
    for s, t in ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)):
        c = _solve((n, sv, tv), (p[3], p[7] + s, p[11] + t))
        if c is None:
            return None
        out.append(c)
    return out


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def record(v0, v1, v2):
    """the 48-byte record of the triangle v0 v1 v2 (the order corners() returns them in), or None when degenerate"""
    e1, e2 = _sub(v1, v0), _sub(v2, v0)
    n = _cross(e2, e1)  # (the game's planes face this way: their records rebuilt from their corners match exactly)
    length = math.sqrt(_dot(n, n))
    if length < 1e-9:
        return None
    n = (n[0] / length, n[1] / length, n[2] / length)
    sd = _cross(e2, n)
    sv = tuple(x / _dot(e1, sd) for x in sd)
    td = _cross(n, e1)
    tv = tuple(x / _dot(e2, td) for x in td)
    return struct.pack("<12f", n[0], n[1], n[2], _dot(n, v0), sv[0], sv[1], sv[2], _dot(sv, v0),
                       tv[0], tv[1], tv[2], _dot(tv, v0))


def simplify_tris(data, count):
    """the surface's records (count of them) simplified: (new bytes, new count), or None to keep it"""
    tris, positions, index = [], [], {}
    for k in range(count):
        c = corners(data[COLLTRI_SIZE * k:COLLTRI_SIZE * (k + 1)])
        if c is None:
            return None
        ids = []
        for v in c:
            key = (round(v[0], 3), round(v[1], 3), round(v[2], 3))
            if key not in index:
                index[key] = len(positions)
                positions.append(v)
            ids.append(index[key])
        tris.append(ids)
    target = max(BUDGET, int(count * RATIO))
    idx = array.array("I", (i for t in tris for i in t))
    # meshoptimizer's input: 32-byte vertices (the position first) and no normals to weigh
    verts = b"".join(struct.pack("<3f20x", *p) for p in positions)
    normals = array.array("f", bytes(12 * len(positions)))
    out = model_lowpoly.simplify(idx, verts, normals, len(positions), target, ERROR, lock_border=False)
    if not out or len(out) >= len(idx) * 0.9:
        return None
    records = []
    for i in range(0, len(out), 3):
        r = record(positions[out[i]], positions[out[i + 1]], positions[out[i + 2]])
        if r is not None:
            records.append(r)
    if not records:
        return None
    return b"".join(records), len(records)


class Plan:
    def __init__(self):
        self.replace = {}     # stream offset of a collision triangle array's read -> (model, new bytes)
        self.patches = {}     # stream offset -> bytes (numCollTris)
        self.surfaces = 0
        self.tris = [0, 0]    # before, after


def plan(stream, events, keep=frozenset()):
    """the zone's model collision surfaces to simplify; keep: models (struct offsets) left as they are"""
    p = Plan()
    ZM_READ, ZM_MARK_BEGIN, ZM_ASSET = 1, 6, 8
    # each model's reads until the next asset: its collision arrays come after its struct
    model, reads = None, []
    groups = []
    for kind, block, extra, a, b, c in events:
        if kind == ZM_ASSET:
            if model is not None:
                groups.append((model, reads))
            model, reads = None, []
        elif kind == ZM_MARK_BEGIN and extra == model_lowpoly.MARK_XMODEL and model is None and a != INLINE:
            model, reads = a, []
        elif kind == ZM_READ and model is not None and c:
            reads.append((b, c))
    if model is not None:
        groups.append((model, reads))
    for a, reads in groups:
        if a in keep or struct.unpack_from("<I", stream, a + XMODEL_COLLSURFS)[0] != INLINE:
            continue
        n = struct.unpack_from("<i", stream, a + XMODEL_NUMCOLLSURFS)[0]
        if n <= 0:
            continue
        # the collSurfs array: a read of 44 * n bytes, then each inline triangle array in order
        for i, (off, size) in enumerate(reads):
            if size != COLLSURF_SIZE * n:
                continue
            arrays, ok, j = [], True, i + 1
            for k in range(n):
                cs = off + COLLSURF_SIZE * k
                ptr, count = struct.unpack_from("<Ii", stream, cs)
                if ptr != INLINE or count <= 0:
                    continue
                if j >= len(reads) or reads[j][1] != COLLTRI_SIZE * count:
                    ok = False
                    break
                arrays.append((cs, reads[j][0], count))
                j += 1
            if not ok:
                continue
            for cs, at, count in arrays:
                if count <= BUDGET:
                    continue
                result = simplify_tris(stream[at:at + COLLTRI_SIZE * count], count)
                if result is None:
                    continue
                data, new_count = result
                p.replace[at] = (a, data)
                p.patches[cs + 4] = struct.pack("<i", new_count)
                p.surfaces += 1
                p.tris[0] += count
                p.tris[1] += new_count
            break
    return p
