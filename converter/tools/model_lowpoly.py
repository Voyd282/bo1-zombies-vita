"""model_lowpoly.py - the low-poly step of the Vita conversion (zone_repack.py --drop-lod0 / --low-poly).

Every model of a zone, retail or custom, gets at most the detail a retail Black Ops model of its kind has:
  - a model with a lower LOD (and not first-person) leaves out its LOD 0: the LOD table moves up one, LOD 0's
    surfaces stay in the surfs array, empty (zeroed counts and pointers: the loader reads nothing for them);
  - then, if the LOD it starts at has more triangles than BUDGETS for its kind, those surfaces are simplified.
Kinds: first-person (a weapon or the hands in view; by name), skinned (characters, zombies: a surface with skin
weights) and rigid (props, world weapons). The budgets are retail's own: first-person viewmodels go up to ~17k
triangles, characters 3-13k at LOD 0 and 3-8k at LOD 1, props mostly under 2k (zonestats / model stats of Kino and
Five, 2026-10-20); custom ports (BO2/BO3/IW models) reach 20-60k with a single LOD.

Simplifying only rewrites the index buffer: meshoptimizer (tools/vita/meshopt, MIT; built by its build.bat into
build/tools/meshopt.dll, or next to this file) collapses edges onto existing vertices, with each surface's open
borders locked (no cracks between surfaces). The vertices that are left keep their order, so a skinned surface's
vertices stay sorted by bone count (vertInfo.vertCount[4] and the vertsBlend records follow) and a rigid surface's
per-bone segments (XRigidVertList) stay whole: a segment is simplified on its own. A rigid segment's collision tree
(only decals on models use it: XSurfaceVisitTrianglesInAabb) is built again for the new triangles with the original
tree's quantization; its leaves take triangle pairs, so the segment's triangles are written in leaf order.

A surface is only rewritten when all its data is its own (inline pointers, no tension data) and laid out as
expected; anything else keeps the surface as it is.
"""
import array
import bisect
import hashlib
import ctypes
import math
import os
import struct

BUDGETS = {"first-person": 20000, "skinned": 5000, "rigid": 4000}
# meshoptimizer's error limit, relative to the surface's extents: a first-person model fills the screen (at 0.04
# WDS's AN94 lost its rails' edges and shaded in waves, 2026-10-20)
TARGET_ERRORS = {"first-person": 0.01, "skinned": 0.03, "rigid": 0.04}
NORMAL_WEIGHT = 1.0       # how much the vertex normals count against a collapse (keeps creases and curvature shaded)
MIN_SURFACE_TRIS = 16     # smaller surfaces (or segments) stay as they are
# a big map's models (zone_repack.py --drop-lods): None, "over" or "all" - see _plan. Der Berg at --low-poly-scale 0.25
# (errors x4: a prop's shape may move 16% of its size) had mangled props such as its teleporters, while the debug
# switch bo1_minlod 1 (the model's own next LOD) looked better (user, 2026-11-11)
EXTRA_LOD_DROP = None
# ... or only these models (names, lower case: zone_repack.py --drop-lods-models; the converter picks the biggest
# ones its measured budget needs, tools: zone_repack.py --budget)
EXTRA_LOD_MODELS = frozenset()
# the budgets and errors at the retail scale: weapons keep them whatever --low-poly-scale says (the mystery box and the
# wall buys show a world weapon up close; Der Berg's 72 weapon models were 13 of its 91 MB, user 2026-11-24)
BASE_BUDGETS = dict(BUDGETS)
BASE_ERRORS = dict(TARGET_ERRORS)
# zone_repack.py --low-poly-weapons: weapons scaled too - "gentle": their triangle budgets scaled but their error
# limits the retail ones (the simplifier stops where the shape would change more: each model only as far as it
# allows), True: budgets and errors scaled as every other model's (the last resort)
SCALE_WEAPONS = False  # ("gentle-world": only the world models gently - the box's and wall buys'; first-person kept)
# zone_repack.py --weapon-scale: the "gentle" weapons' budgets scaled by their own factor (the other models keep
# --low-poly-scale's): a mod's weapons simplified a little while its perk machines and the rest stay as they are
# (user 2026-11-24: WDS at 1/8 made both look bad)
WEAPON_BUDGETS = None
# zone_repack.py --character-scale: characters and zombies (is_character) with their own budgets and errors (scaled
# as --low-poly-scale scales every model's), the other models keep theirs: a mod's zombies a little simpler while its
# perk machines stay (user 2026-11-24: "zombie modelle koennen wir noch bisschen runter schrauben")
CHARACTER_BUDGETS = CHARACTER_ERRORS = None
# zone_repack.py --dedup-surfaces: a surface whose data (blend info, vertices, vertex lists and their trees, triangles)
# is byte for byte the same as an earlier surface's in the zone leaves its copy out and points at that one's - as the
# retail zones' upgraded weapons share their base model's meshes. WDS carries 9.6 MB of such copies (its upgraded
# weapons, its zombies' bodies; 2026-11-24): nothing changes on screen
DEDUP = False
# the sights of a first-person weapon kept as they are when it gets simpler (user 2026-11-24: simpler weapons in hand
# are fine, but their sights were blocked - rear sights and apertures merged into solid blocks). A first-person
# weapon's vertices are in model space, the barrel along x, its middle at y = 0, the top up (z): the sights are the
# band along its top middle, |y| <= SIGHT_HALF_WIDTH and z >= (the band's highest point) - SIGHT_DEPTH. Its vertices
# are locked (meshoptimizer's vertex_lock: never moved nor collapsed); the rest of the weapon gets simpler. And it is
# measured: the band's silhouette looking down the barrel (sight_view), before and after - a weapon whose view
# through its sights changes by more than SIGHT_MAX_CHANGE of the window keeps its detail
SIGHT_HALF_WIDTH = 0.9
SIGHT_DEPTH = 2.5
# the bands tried, narrowest first (half width, depth): each weapon gets the first that keeps its view - a wide band
# for all cost WDS 6 MB more than none (52.8 / 46.8 MB), so most weapons get a narrow one (measured 2026-11-24)
SIGHT_BANDS = ((0.4, 0.8), (0.6, 1.5), (0.9, 2.5))
SIGHT_MAX_CHANGE = 0.01
SIGHT_REGION = None  # (half width, lowest z) of the model being planned, or None
# the window the view through the sights is measured in: fixed, whatever band is locked (a narrower lock must not make
# the check more lenient)
SIGHT_VIEW_HALF_WIDTH = 0.9
SIGHT_VIEW_DEPTH = 2.5
# models whose geometry is left out (names, lower case; zone_repack.py --empty-models): models nothing can show any
# more when a mod runs - the converter's PC run lists what the game can still show (bo1_livemodels: precached, the
# registered weapons', the map's static models, effects', destructibles') and these are the others in a zone the mod
# brings its own copy of (WDS on Kino: common_zombie's stock weapons, 13.6 MB). Every surface of every LOD is left
# empty as a left-out LOD 0's is: the model stays, with its name, and draws nothing
EMPTY_MODELS = frozenset()
LEAF_TRIS = 4             # triangles under a collision tree node that has leaves (2 a leaf)

ZM_READ, ZM_MARK_BEGIN, ZM_MARK_END = 1, 6, 7
MARK_XMODEL, MARK_XSURFACE = 5, 6
XMODEL_SIZE = 252
INLINE = 0xFFFFFFFF


def is_character(name):
    """a character's or zombie's model (bodies, heads, gib pieces: c_*, char_*, *zombie* / *zom_*, by its name) - not
    a weapon, nor a perk machine or a power-up (zombie_vending_*, zombie_perk_*, zombie_<powerup>)"""
    n = name.lower()
    if is_weapon(n) or "vending" in n or "perk" in n or "bottle" in n:
        return False
    parts = n.replace("-", "_").split("_")
    return n.startswith(("c_", "char_")) or "zom" in parts or "body" in n or "head" in parts or "gib" in parts or         n.startswith("zombie_") and any(p in parts for p in ("body", "head", "arm", "leg", "torso"))


def is_weapon(name):
    """a weapon's model, first-person or world (t5_weapon_*, weapon_*, t6_wpn_*, *_attach_*), by its name"""
    n = name.lower()
    return is_first_person(n) or "weapon" in n or "wpn" in n.replace("-", "_").split("_") or "_attach_" in n \
        or n.startswith("attach_")


def is_first_person(name):
    """a first-person model, by its name - as the engine's XModel_IsFirstPerson (src/xanim/xmodel_utils.cpp):
    viewmodel_*, *_viewhands, *_view / *_view_* (BO2/BO3 ports) and *_vm / *_vm_* (IW ports, the WDS mod)"""
    n = name.lower()
    parts = n.replace("-", "_").split("_")
    return "viewmodel" in n or "viewhands" in n or "view" in parts[1:] or "vm" in parts[1:]


# ---- meshoptimizer -----------------------------------------------------------------------------------------------

_meshopt = None


def meshopt():
    global _meshopt
    if _meshopt is None:
        here = os.path.dirname(os.path.abspath(__file__))
        tried = [os.path.join(here, "meshopt.dll"), os.path.join(here, "..", "..", "build", "tools", "meshopt.dll")]
        path = next((p for p in tried if os.path.isfile(p)), None)
        if path is None:
            raise RuntimeError("meshopt.dll not found (build it: tools\\vita\\meshopt\\build.bat); looked in "
                               + ", ".join(os.path.normpath(p) for p in tried))
        dll = ctypes.CDLL(os.path.abspath(path))
        f = dll.meshopt_simplifyWithAttributes
        f.restype = ctypes.c_size_t
        f.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
                      ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
                      ctypes.c_void_p, ctypes.c_size_t, ctypes.c_float, ctypes.c_uint, ctypes.c_void_p]
        _meshopt = dll
    return _meshopt


def normals_of(verts, count):
    """the vertices' normals as floats (GfxPackedVertex +24, a PackedUnitVec: Vec3UnpackUnitVec)"""
    out = array.array("f")
    for v in range(count):
        x, y, z, w = verts[32 * v + 24:32 * v + 28]
        scale = (w + 192.0) / 32385.0
        out.extend(((x - 127.0) * scale, (y - 127.0) * scale, (z - 127.0) * scale))
    return out


def sight_lock(verts, count):
    """the vertices in SIGHT_REGION (1 = locked) as a ctypes array, or None"""
    if SIGHT_REGION is None or os.environ.get("BO1_NO_SIGHT_LOCK"):  # (the variable: to measure without the lock)
        return None
    width, low = SIGHT_REGION
    lock = (ctypes.c_ubyte * count)()
    for i in range(count):
        x, y, z = struct.unpack_from("<3f", verts, 32 * i)
        if abs(y) <= width and z >= low:
            lock[i] = 1
    return lock


def sight_view(verts, idx, region, top, cells=64):
    """the set of cells the triangles in the sight band cover, looking down the barrel (y, z), over
    |y| <= width, low <= z <= top + 0.5"""
    width, low = region
    high = top + 0.5
    sy, sz = cells / (2.0 * width), cells / max(high - low, 1e-3)
    covered = set()
    for t in range(0, len(idx), 3):
        pts = [struct.unpack_from("<3f", verts, 32 * idx[t + k]) for k in range(3)]
        ys = [(p[1] + width) * sy for p in pts]
        zs = [(p[2] - low) * sz for p in pts]
        if max(ys) < 0 or min(ys) >= cells or max(zs) < 0 or min(zs) >= cells:
            continue
        # rasterize: cell centres inside the projected triangle
        (y0, y1, y2), (z0, z1, z2) = ys, zs
        area = (y1 - y0) * (z2 - z0) - (y2 - y0) * (z1 - z0)
        if abs(area) < 1e-9:
            continue
        for cy in range(max(0, int(min(ys))), min(cells, int(max(ys)) + 1)):
            for cz in range(max(0, int(min(zs))), min(cells, int(max(zs)) + 1)):
                py, pz = cy + 0.5, cz + 0.5
                w0 = ((y1 - py) * (z2 - pz) - (y2 - py) * (z1 - pz)) / area
                w1 = ((y2 - py) * (z0 - pz) - (y0 - py) * (z2 - pz)) / area
                if w0 >= 0 and w1 >= 0 and w0 + w1 <= 1:
                    covered.add((cy, cz))
    return covered


def simplify(indices, verts, normals, vertex_count, target_tris, error, lock_border=True):
    """indices (array 'I', triangles) over verts (bytes, 32 a vertex, position first; normals: their unpacked
    normals) -> fewer indices. With lock_border=False a mesh whose edges are all open (some exports split every face)
    still gets simpler, though a border shared with another surface may then show a crack."""
    src = (ctypes.c_uint * len(indices)).from_buffer(indices)
    dst = (ctypes.c_uint * len(indices))()
    vbuf = ctypes.create_string_buffer(bytes(verts), len(verts))
    nbuf = (ctypes.c_float * len(normals)).from_buffer(normals)
    weights = (ctypes.c_float * 3)(NORMAL_WEIGHT, NORMAL_WEIGHT, NORMAL_WEIGHT)
    err = ctypes.c_float()
    lock = sight_lock(verts, vertex_count)  # (a first-person weapon's sights: SIGHT_REGION)
    n = meshopt().meshopt_simplifyWithAttributes(dst, src, len(indices), vbuf, vertex_count, 32, nbuf, 12, weights, 3,
                                                 lock, max(3, target_tris * 3), error, 1 if lock_border else 0,
                                                 ctypes.byref(err))  # 1: meshopt_SimplifyLockBorder
    return array.array("I", dst[:n])


# ---- a surface's data ---------------------------------------------------------------------------------------------

class Surface:
    """an XSurface (68 bytes at stream offset a) and the stream ranges its data was read from, in load order"""

    def __init__(self, stream, a, reads):
        self.a = a
        self.reads = reads
        self.vert_lists, self.flags, self.verts, self.tris = struct.unpack_from("<xBHHH", stream, a)
        self.tri_ptr = struct.unpack_from("<I", stream, a + 12)[0]
        self.blend_counts = struct.unpack_from("<4h", stream, a + 16)
        self.blend_ptr, self.tension_ptr, self.verts_ptr = struct.unpack_from("<III", stream, a + 24)
        self.vert_list_ptr = struct.unpack_from("<I", stream, a + 40)[0]


def read_data(stream, surf):
    """the surface's arrays by name, or None when its data is not laid out as expected (or not all its own)"""
    if any(p not in (0, INLINE) for p in (surf.tri_ptr, surf.blend_ptr, surf.tension_ptr, surf.verts_ptr,
                                          surf.vert_list_ptr)):
        return None
    if surf.tension_ptr or surf.verts_ptr != INLINE or surf.tri_ptr != INLINE or not surf.verts or not surf.tris:
        return None
    reads = list(surf.reads)
    data = {}

    def take(size):
        if not reads or reads[0][1] != size:
            return None
        off, n = reads.pop(0)
        return stream[off:off + n]

    c = surf.blend_counts
    fields = data["fields"] = {}  # pointer field offset in the struct -> stream offset of the data it loads
    if surf.blend_ptr == INLINE:
        fields[24] = reads[0][0] if reads else None
        data["blend"] = take(2 * (c[0] + 3 * c[1] + 5 * c[2] + 7 * c[3]))
        if data["blend"] is None:
            return None
    fields[32] = reads[0][0] if reads else None
    data["verts"] = take(32 * surf.verts)
    if data["verts"] is None:
        return None
    floats = struct.unpack("<%df" % (8 * surf.verts), data["verts"])
    if not all(math.isfinite(floats[8 * v + axis]) for v in range(surf.verts) for axis in range(3)):
        return None  # (a custom model had NaN positions)
    if surf.vert_list_ptr == INLINE:
        fields[40] = reads[0][0] if reads else None
        data["vert_list"] = take(12 * surf.vert_lists)
        if data["vert_list"] is None:
            return None
        data["trees"] = []
        for k in range(surf.vert_lists):
            tree_ptr = struct.unpack_from("<I", data["vert_list"], 12 * k + 8)[0]
            if tree_ptr == 0:
                data["trees"].append(None)
                continue
            if tree_ptr != INLINE:
                return None
            head = take(40)
            if head is None:
                return None
            node_count, nodes_ptr, leaf_count, leafs_ptr = struct.unpack_from("<IIII", head, 24)
            if nodes_ptr != INLINE or leafs_ptr != INLINE or not node_count or not leaf_count:
                return None
            if not all(math.isfinite(x) for x in struct.unpack_from("<6f", head, 0)):
                return None  # (a WDS model's tree had a NaN scale)
            if take(16 * node_count) is None or take(2 * leaf_count) is None:
                return None
            data["trees"].append(head)
    fields[12] = reads[0][0] if reads else None
    data["tris"] = take(6 * surf.tris)
    if data["tris"] is None or reads:
        return None
    return data


def build_tree(head, tris, positions, first_tri):
    """a collision tree over tris (vertex index triples; positions: vertex index -> xyz) whose triangles are written
    from surface triangle first_tri on -> (nodes, leafs, order): nodes [(mins, maxs, child begin, child count)] with
    the root first, leafs (triangle number, + 0x8000 for a pair), order = the tris' indices in writing order"""
    trans = struct.unpack_from("<3f", head, 0)
    scale = struct.unpack_from("<3f", head, 12)

    def q(v, axis):
        return (positions[v][axis] + trans[axis]) * scale[axis]

    boxes = []
    for t in tris:
        mn = [min(q(v, ax) for v in t) for ax in range(3)]
        mx = [max(q(v, ax) for v in t) for ax in range(3)]
        boxes.append((mn, mx))
    nodes, leafs, order = [None], [], []

    def box_of(ids):
        if not ids:
            return [0, 0, 0], [0, 0, 0]
        mn = [min(boxes[i][0][ax] for i in ids) for ax in range(3)]
        mx = [max(boxes[i][1][ax] for i in ids) for ax in range(3)]
        return ([max(0, min(65535, int(v // 1))) for v in mn], [max(0, min(65535, -int(-v // 1))) for v in mx])

    def fill(index, ids, first_tri):
        mn, mx = box_of(ids)
        if len(ids) <= LEAF_TRIS:
            begin = len(leafs)
            for k in range(0, len(ids), 2):
                pair = ids[k:k + 2]
                tri = first_tri + len(order)
                order.extend(pair)
                leafs.append(tri + 0x8000 if len(pair) == 2 else tri)
            nodes[index] = (mn, mx, begin, 0x8000 + len(leafs) - begin)
            return
        axis = max(range(3), key=lambda ax: mx[ax] - mn[ax])
        ids = sorted(ids, key=lambda i: boxes[i][0][axis] + boxes[i][1][axis])
        half = len(ids) // 2
        begin = len(nodes)
        nodes.extend([None, None])
        nodes[index] = (mn, mx, begin, 2)
        fill(begin, ids[:half], first_tri)
        fill(begin + 1, ids[half:], first_tri)

    fill(0, list(range(len(tris))), first_tri)
    return nodes, leafs, order


def tree_bytes(head, nodes, leafs):
    out_nodes = b"".join(struct.pack("<6HHH", *mn, *mx, begin, count) for mn, mx, begin, count in nodes)
    out_leafs = struct.pack("<%dH" % len(leafs), *leafs)
    new_head = bytearray(head)
    struct.pack_into("<I", new_head, 24, len(nodes))
    struct.pack_into("<I", new_head, 32, len(leafs))
    return bytes(new_head), out_nodes, out_leafs


def positions_of(verts, count):
    return [struct.unpack_from("<3f", verts, 32 * i) for i in range(count)]


def simplify_skinned(surf, data, ratio, error):
    """-> (struct patches, [blend, verts, tris] bytes) or None"""
    idx = array.array("I", struct.unpack("<%dH" % (3 * surf.tris), data["tris"]))
    new_idx = simplify(idx, data["verts"], normals_of(data["verts"], surf.verts), surf.verts,
                       max(1, int(surf.tris * ratio)), error)
    if not new_idx or len(new_idx) >= len(idx) * 0.95:
        return None
    kept = sorted(set(new_idx))
    remap = {old: new for new, old in enumerate(kept)}
    # the skin weights: a record per vertex, 1/3/5/7 shorts for 1..4 bones, the vertices sorted by bone count
    c = surf.blend_counts
    records, pos = [], 0
    for group, size in enumerate((1, 3, 5, 7)):
        for _ in range(c[group]):
            records.append((group, data["blend"][pos:pos + 2 * size]))
            pos += 2 * size
    if len(records) != surf.verts:
        return None
    counts = [0, 0, 0, 0]
    for v in kept:
        counts[records[v][0]] += 1
    blend = b"".join(records[v][1] for v in kept)
    verts = b"".join(data["verts"][32 * v:32 * v + 32] for v in kept)
    tris = struct.pack("<%dH" % len(new_idx), *(remap[v] for v in new_idx))
    patches = {surf.a + 4: struct.pack("<HH", len(kept), len(new_idx) // 3), surf.a + 16: struct.pack("<4h", *counts)}
    return patches, [blend, verts, tris]


def simplify_rigid(surf, data, ratio, error):
    """-> (struct patches, [verts, vert_list, (tree, nodes, leafs) per tree, tris] bytes) or None"""
    all_idx = struct.unpack("<%dH" % (3 * surf.tris), data["tris"])
    segments, vstart, tstart = [], 0, 0
    for k in range(surf.vert_lists):
        bone, vc, toff, tc, tree_ptr = struct.unpack_from("<HHHHI", data["vert_list"], 12 * k)
        if toff != tstart:
            return None
        tri = all_idx[3 * toff:3 * (toff + tc)]
        if any(not vstart <= v < vstart + vc for v in tri):
            return None
        segments.append([bone, vstart, vc, tc, tri, data["trees"][k]])
        vstart += vc
        tstart += tc
    if vstart != surf.verts or tstart > surf.tris or not segments:
        return None
    if tstart < surf.tris:
        # triangles after the last segment's (custom exports: Rust's Venom-X has 5862 with 5861 in its one segment):
        # the surface draws them, so they join the last segment when its vertices are theirs
        last = segments[-1]
        extra = all_idx[3 * tstart:]
        if any(not last[1] <= v < last[1] + last[2] for v in extra):
            return None
        last[3] += surf.tris - tstart
        last[4] = last[4] + extra
    positions = positions_of(data["verts"], surf.verts)
    normals = normals_of(data["verts"], surf.verts)
    new_verts, new_list, new_trees, new_tris = [], [], [], []
    changed = False
    for bone, vs, vc, tc, tri, head in segments:
        idx = array.array("I", tri)
        if tc >= MIN_SURFACE_TRIS:
            target = max(1, int(tc * ratio))
            out = simplify(idx, data["verts"], normals, surf.verts, target, error)
            if len(out) > 3 * target * 1.5:  # borders everywhere: a rigid piece may move its borders
                out = simplify(idx, data["verts"], normals, surf.verts, target, error, lock_border=False)
            if out and len(out) < len(idx) * 0.95:
                idx = out
                changed = True
        kept = sorted(set(idx))
        base = len(new_verts)
        remap = {old: base + new for new, old in enumerate(kept)}
        new_verts.extend(kept)
        tris = [tuple(remap[v] for v in idx[i:i + 3]) for i in range(0, len(idx), 3)]
        first_tri = len(new_tris)
        if head is not None:
            new_pos = {remap[v]: positions[v] for v in kept}
            nodes, leafs, order = build_tree(head, tris, new_pos, first_tri)
            tris = [tris[i] for i in order]
            new_trees.append(tree_bytes(head, nodes, leafs))
        new_list.append(struct.pack("<HHHHI", bone, len(kept), first_tri, len(tris), INLINE if head else 0))
        new_tris.extend(tris)
    if not changed or len(new_tris) >= 0x8000:
        return None
    verts = b"".join(data["verts"][32 * v:32 * v + 32] for v in new_verts)
    out = [verts, b"".join(new_list)]
    for t in new_trees:
        out.extend(t)
    out.append(struct.pack("<%dH" % (3 * len(new_tris)), *(v for t in new_tris for v in t)))
    return {surf.a + 4: struct.pack("<HH", len(new_verts), len(new_tris))}, out


def simplify_surface(stream, surf, ratio, error):
    """-> (struct patches, [bytes for each read], {pointer field: stream offset of its original data}) or None"""
    data = read_data(stream, surf)
    if data is None or surf.tris < MIN_SURFACE_TRIS:
        return None
    if surf.vert_lists:
        if surf.blend_ptr or any(surf.blend_counts):
            return None
        result = simplify_rigid(surf, data, ratio, error)
    elif surf.blend_ptr == INLINE:
        result = simplify_skinned(surf, data, ratio, error)
    else:
        return None
    return None if result is None else (result[0], result[1], data["fields"])


# ---- the zone's models -----------------------------------------------------------------------------------------

class Model:
    def __init__(self, a, name):
        self.a = a
        self.name = name
        self.surfaces = []  # (struct stream offset, [(stream offset, size) of each read of its data])


def collect(stream, events):
    """the zone's models in load order, with their surfaces and the stream ranges of each surface's data; and
    resolve: encoded block offset -> stream offset of the data loaded there (blocks 1..6), or None"""
    reads = [([], []) for _ in range(7)]

    def resolve(encoded):
        blk, off = (encoded - 1) >> 29, (encoded - 1) & 0x1FFFFFFF
        if not 1 <= blk <= 6:
            return None
        starts, rd = reads[blk]
        i = bisect.bisect_right(starts, off) - 1
        if i < 0 or off >= starts[i] + rd[i][1]:
            return None
        return rd[i][0] + (off - starts[i])

    models, model, surface_reads = [], None, None
    for kind, block, extra, a, b, c in events:
        if kind == ZM_READ:
            if 1 <= block <= 6 and c:
                reads[block][0].append(a)
                reads[block][1].append((b, c))
            if surface_reads is not None and c:
                surface_reads.append((b, c))
        elif kind == ZM_MARK_BEGIN and extra == MARK_XMODEL and a != INLINE:
            field = struct.unpack_from("<I", stream, a)[0]
            name_at = a + XMODEL_SIZE if field == INLINE else resolve(field)
            name = stream[name_at:stream.index(b"\0", name_at)].decode("latin-1") if name_at is not None else None
            model = Model(a, name)
            models.append(model)
        elif kind == ZM_MARK_BEGIN and extra == MARK_XSURFACE:
            surface_reads = [] if a != INLINE and model is not None else None
            if surface_reads is not None:
                model.surfaces.append((a, surface_reads))
        elif kind == ZM_MARK_END and extra == MARK_XSURFACE:
            surface_reads = None
    return models, resolve


def lod_table(stream, model):
    """[(surface count, first surface)] of the model's LODs; [] for a model without surfaces; None if not understood"""
    num_lods = struct.unpack_from("<h", stream, model.a + 216)[0]
    if num_lods == 0 and stream[model.a + 6] == 0:
        return []
    if not 1 <= num_lods <= 4:
        return None
    lods, first = [], 0
    for i in range(num_lods):
        count, index = struct.unpack_from("<HH", stream, model.a + 40 + 32 * i + 4)
        if index != first or stream[model.a + 40 + 32 * i + 28] != i:
            return None
        lods.append((count, index))
        first += count
    if first != stream[model.a + 6] or first != len(model.surfaces):
        return None
    return lods


POINTER_FIELDS = (12, 24, 28, 32, 40)  # triIndices, vertsBlend, tensionData, verts0, vertList


class Plan:
    def __init__(self):
        self.struct_patches = {}  # model struct offset -> {stream offset: bytes}
        self.empty = {}           # surface offset -> model offset: a left-out LOD 0 surface
        self.dedup = {}           # surface offset -> (model offset, {pointer field: stream offset of the data of the
                                  # identical earlier surface it points at instead}) (DEDUP)
        self.dedup_bytes = 0
        self.sight_checks = []    # each simplified first-person weapon's view change through its sights (0..1)
        self.replace = {}         # surface offset -> (model offset, {stream offset: bytes}, [bytes for each read],
                                  #                    {pointer field: stream offset of the original data})
        self.alias_patches = {}   # stream offset -> bytes: the counts of surfaces that use a replaced surface's data
        self.alias_fields = set() # stream offsets of their pointer fields (may point at the replaced data's start)
        self.aliases = 0
        self.dropped_models = 0
        self.extra_dropped = 0       # models that left out a second LOD (EXTRA_LOD_DROP)
        self.emptied_models = 0      # models whose geometry was left out (EMPTY_MODELS)
        self.simplified = [0, 0, 0]  # models, triangles before, after
        self.skipped = {}

    def skip(self, why):
        self.skipped[why] = self.skipped.get(why, 0) + 1


def plan(stream, events, drop_lod0=True, simplify_models=True, keep=frozenset(), cache=None):
    """what the low-poly step does to the zone's models; keep: models (struct offsets) left as they are.
    A surface whose pointers lead to the data of a simplified surface (an upgraded weapon's or a machine's on/off
    model shares its base model's meshes) gets the same counts; one that leads there in any other way keeps the
    simplified surface's model as it is (planned again without it)."""
    cache = {} if cache is None else cache
    models, resolve = collect(stream, events)
    keep = set(keep)
    while True:
        p = _plan(stream, models, drop_lod0, simplify_models, keep, cache)
        if DEDUP:
            _dedup(stream, models, p, keep)
        conflicts = _aliases(stream, models, resolve, p)
        if not conflicts:
            return p
        keep |= conflicts


def _plan(stream, models, drop_lod0, simplify_models, keep, cache):
    p = Plan()
    for model in models:
        if model.a in keep:
            p.skip("shared data")
            continue
        if model.name is None:
            p.skip("name not found")
            continue
        lods = lod_table(stream, model)
        if lods is None:
            p.skip("LOD table not understood")
            continue
        if not lods:
            p.skip("no surfaces")
            continue
        if model.name.lower() in EMPTY_MODELS:
            for s, _ in model.surfaces:
                p.empty[s] = model.a
            p.emptied_models += 1
            continue
        first_person = is_first_person(model.name)
        weapon = is_weapon(model.name)
        if CHARACTER_BUDGETS and not weapon and is_character(model.name):
            budgets, errors = CHARACTER_BUDGETS, CHARACTER_ERRORS
        elif weapon and not SCALE_WEAPONS:
            budgets, errors = BASE_BUDGETS, BASE_ERRORS
        elif weapon and (SCALE_WEAPONS == "gentle" or SCALE_WEAPONS == "gentle-world" and not first_person):
            budgets, errors = WEAPON_BUDGETS or BUDGETS, BASE_ERRORS
        elif weapon and SCALE_WEAPONS == "gentle-world":  # (a first-person weapon: kept)
            budgets, errors = BASE_BUDGETS, BASE_ERRORS
        else:
            budgets, errors = BUDGETS, TARGET_ERRORS
        top = 0
        if drop_lod0 and len(lods) > 1 and not first_person:
            top = 1
            # EXTRA_LOD_DROP (zone_repack.py --drop-lods 2, big maps): one more of the model's own LODs left out when it
            # has one - "all" models, or only those whose LOD would be "over" its budget and simplified otherwise
            if len(lods) > 2 and not weapon and model.name.lower() in EXTRA_LOD_MODELS:
                top = 2
            elif EXTRA_LOD_DROP and len(lods) > 2 and not weapon:
                count, first = lods[1]
                tris = sum(Surface(stream, s, r).tris for s, r in model.surfaces[first:first + count])
                skinned = any(Surface(stream, s, r).blend_ptr for s, r in model.surfaces[first:first + count])
                if EXTRA_LOD_DROP == "all" or tris > budgets["skinned" if skinned else "rigid"]:
                    top = 2
            table = bytearray()
            for i in range(top, len(lods)):
                entry = bytearray(stream[model.a + 40 + 32 * i:model.a + 72 + 32 * i])
                entry[28] = i - top
                table += entry
            table += bytes(128 - len(table))
            p.struct_patches[model.a] = {model.a + 40: bytes(table),
                                         model.a + 216: struct.pack("<h", len(lods) - top)}
            for level in range(top):
                count, first = lods[level]
                for s, _ in model.surfaces[first:first + count]:
                    p.empty[s] = model.a
            p.dropped_models += 1
            p.extra_dropped += top - 1
        if not simplify_models:
            continue
        count, first = lods[top]
        surfaces = [Surface(stream, s, r) for s, r in model.surfaces[first:first + count]]
        skinned = any(s.blend_ptr for s in surfaces)
        kind = "first-person" if first_person else ("skinned" if skinned else "rigid")
        tris = sum(s.tris for s in surfaces)
        if tris <= budgets[kind]:
            continue
        ratio = budgets[kind] / float(tris)
        after = 0
        global SIGHT_REGION
        # a first-person weapon: the narrowest locked band (SIGHT_BANDS) that keeps the view through its sights
        bands = SIGHT_BANDS if first_person and weapon and not os.environ.get("BO1_NO_SIGHTS") else (None,)
        planned = None
        for band in bands:
            SIGHT_REGION = sight_region(stream, surfaces, band) if band else None
            planned = []
            for s in surfaces:
                key = (s.a, ratio, errors[kind], SIGHT_REGION)
                if key not in cache:
                    cache[key] = simplify_surface(stream, s, ratio, errors[kind])
                planned.append((s, cache[key]))
            if SIGHT_REGION is None or sight_kept(stream, surfaces, planned, p):
                break
            planned = None
        if planned is None:
            SIGHT_REGION = None
            p.skip("sights would change")
            continue
        SIGHT_REGION = None
        for s, result in planned:
            if result is None:
                after += s.tris
                continue
            patches, data, fields = result
            p.replace[s.a] = (model.a, patches, data, fields)
            after += struct.unpack_from("<H", patches[s.a + 4], 2)[0]
        if after < tris:
            p.simplified[0] += 1
            p.simplified[1] += tris
            p.simplified[2] += after
    return p


def sight_region(stream, surfaces, band=None):
    """(half width, lowest z) of a first-person weapon's locked sight band (band: (half width, depth), else
    SIGHT_HALF_WIDTH, SIGHT_DEPTH), its top the highest point of the view window (SIGHT_VIEW_HALF_WIDTH), or None"""
    width, depth = band or (SIGHT_HALF_WIDTH, SIGHT_DEPTH)
    top = None
    for surf in surfaces:
        data = read_data(stream, surf)
        if data is None:
            continue
        for i in range(surf.verts):
            x, y, z = struct.unpack_from("<3f", data["verts"], 32 * i)
            if abs(y) <= SIGHT_VIEW_HALF_WIDTH and (top is None or z > top):
                top = z
    return None if top is None else (width, top - depth)


def sight_top(stream, surfaces):
    """the highest point of a first-person weapon in the sight view window (|y| <= SIGHT_VIEW_HALF_WIDTH)"""
    return sight_region(stream, surfaces, (SIGHT_VIEW_HALF_WIDTH, 0.0))[1]


def sight_kept(stream, surfaces, planned, p):
    """whether the planned simplification keeps the view through the sights (sight_view before / after)"""
    top = sight_top(stream, surfaces)
    window = (SIGHT_VIEW_HALF_WIDTH, top - SIGHT_VIEW_DEPTH)
    before, after = set(), set()
    for surf, result in planned:
        data = read_data(stream, surf)
        if data is None:
            continue
        idx = struct.unpack("<%dH" % (3 * surf.tris), data["tris"])
        view = sight_view(data["verts"], idx, window, top)
        before |= view
        if result is None:
            after |= view
            continue
        patches, new, fields = result
        # the new data: blend? verts ... tris last; the verts read is the one of 32 * new vertex count bytes
        count, tris = struct.unpack_from("<HH", patches[surf.a + 4], 0)
        verts = next((d for d in new if len(d) == 32 * count), None)
        tri_bytes = new[-1]
        if verts is None or len(tri_bytes) != 6 * tris:
            after |= view
            continue
        after |= sight_view(verts, struct.unpack("<%dH" % (3 * tris), tri_bytes), window, top)
    window = 64 * 64
    change = len(before ^ after) / float(window)
    p.sight_checks.append(change)
    return change <= SIGHT_MAX_CHANGE


def _dedup(stream, models, p, keep):
    """fills p.dedup: each surface whose data is the same as an earlier one's (see DEDUP)"""
    first = {}  # (struct header, data hash) -> surface offset
    for model in models:
        for s, reads in model.surfaces:
            if s in p.empty or not reads:
                continue
            surf = Surface(stream, s, reads)
            if read_data(stream, surf) is None:
                continue
            h = hashlib.sha1()
            for off, size in reads:
                h.update(struct.pack("<I", size))
                h.update(stream[off:off + size])
            key = (stream[s + 1:s + 12], stream[s + 16:s + 24],
                   tuple(struct.unpack_from("<I", stream, s + f)[0] for f in POINTER_FIELDS), h.digest())
            o = first.get(key)
            if o is None:
                first[key] = s
                continue
            if model.a in keep:
                continue
            p.replace.pop(s, None)
            if o in p.replace:  # (the earlier one simplified: this one takes its counts as an alias does)
                for at, data in p.replace[o][1].items():
                    p.alias_patches[s + (at - o)] = data
                fields = p.replace[o][3]
            else:
                fields = read_data(stream, Surface(stream, o, _reads_of(models, o)))["fields"]
            p.dedup[s] = (model.a, dict(fields))
            p.dedup_bytes += sum(size for _, size in reads)


def _reads_of(models, s):
    for model in models:
        for t, reads in model.surfaces:
            if t == s:
                return reads
    return []


def _aliases(stream, models, resolve, p):
    """fills p's alias patches; -> models whose simplified surfaces something else points at in a way it cannot
    follow (they have to stay as they are)"""
    sources = {}  # stream offset of a simplified surface's data -> (surface offset, pointer field)
    for s, (model_a, patches, data, fields) in p.replace.items():
        for field, at in fields.items():
            if at is not None:
                sources[at] = (s, field)
    owner = {s: entry[0] for s, entry in p.replace.items()}
    conflicts = set()
    for model in models:
        for t, reads in model.surfaces:
            if t in p.replace or t in p.empty:
                continue
            ptrs = {f: struct.unpack_from("<I", stream, t + f)[0] for f in POINTER_FIELDS}
            hits = {}
            for f, v in ptrs.items():
                if v in (0, INLINE):
                    continue
                at = resolve(v)
                if at in sources:
                    hits[f] = sources[at]
            if not hits:
                continue
            src = {s for s, _ in hits.values()}
            s = next(iter(src))
            same = (len(src) == 1 and all(field == f for f, (_, field) in hits.items())
                    and set(hits) == set(f for f, v in ptrs.items() if v)
                    and all(v not in (0, INLINE) for f, v in ptrs.items() if v and f != 28) and not ptrs[28]
                    and stream[t + 1:t + 8] == stream[s + 1:s + 8] and stream[t + 16:t + 24] == stream[s + 16:s + 24]
                    and set(f for f, v in ptrs.items() if v) == set(p.replace[s][3]))
            if not same:
                conflicts.update(owner[x] for x in src)
                continue
            for at, data in p.replace[s][1].items():
                p.alias_patches[t + (at - s)] = data
            p.alias_fields.update(t + f for f in hits)
            p.aliases += 1
    return conflicts
