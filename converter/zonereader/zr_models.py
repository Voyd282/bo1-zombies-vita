"""zr_models.py - the models (and weapon view anims) a level can show, from its zones alone: what the engine's
'bo1_livemodels' run (win_main.cpp Sys_LiveModelsDump: a minute of the map) listed, without running the map.

The engine's run lists the server's precached models, the registered weapons' models, the map's static models and the
models of every loaded effect, destructible and model-pieces asset. Here: the static, effect, destructible and pieces
models as there; for the dynamic part (what the scripts precache) every weapon whose name the scripts, tables or map
entities name, with all its models and view anims - the converter also keeps every model named anywhere ('quoted').
"""
import os
import re
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zr_runtime  # noqa: E402
import zr_load_gen  # noqa: E402,F401

T_XMODELPIECES, T_DESTRUCTIBLE, T_XANIM, T_XMODEL, T_GFXWORLD, T_MAPENTS = 0, 3, 4, 5, 0x11, 0x10
T_WEAPON, T_WEAPON_VARIANT, T_FX, T_RAWFILE, T_STRINGTABLE = 0x18, 0x1A, 0x1C, 0x24, 0x25
WEAPON_MODEL_FIELDS = (8, 784, 788, 792, 796, 1512)  # WeaponDef: handXModel, worldClipModel, rocketModel, mountedModel,
#                                                       additionalMeleeModel, projectileModel (gunXModel +4 / worldModel
#                                                       +780: arrays of 16)


class Collector:
    def __init__(self):
        self.weapons = {}     # name: (models, anims)
        self.static = set()   # the maps' static models
        self.fx = set()
        self.destructible = set()
        self.pieces = set()
        self.models = set()   # every model asset
        self.anims = set()    # every anim asset
        self.texts = []       # raw files, string tables, map entities: where the scripts name things
        self.text_names = []  # (their raw file names, '' for the map entities)

    @staticmethod
    def cstr(z, addr):
        if not addr or addr in (0xFFFFFFFF, 0xFFFFFFFE):
            return None
        try:
            m, o = z.region(addr)
        except zr_runtime.ZoneError:
            return None
        end = m.find(b'\0', o)
        return m[o:end].decode('latin-1') if end >= 0 else None

    def model(self, z, ptr):
        """the name of the XModel at ptr (',name': a reference to another zone's)"""
        if not ptr or ptr >= 0xFFFFFFFE:
            return None
        try:
            n = self.cstr(z, z.rd(ptr, 'I'))
        except zr_runtime.ZoneError:
            return None
        return n.lstrip(',').lower() if n else None

    def add(self, dst, z, ptr):
        n = self.model(z, ptr)
        if n:
            dst.add(n)

    def asset(self, r, type_, var):
        z = r.z
        h = z.rd(var + 4, 'I')
        if not h or h >= 0xFFFFFFFE:
            return
        try:
            self._asset(z, type_, h)
        except zr_runtime.ZoneError:
            pass

    def _asset(self, z, type_, h):
        rd = z.rd
        if type_ == T_XMODEL:
            n = self.cstr(z, rd(h, 'I'))
            if n:
                self.models.add(n.lstrip(',').lower())
        elif type_ == T_XANIM:
            n = self.cstr(z, rd(h, 'I'))
            if n:
                self.anims.add(n.lstrip(',').lower())
        elif type_ in (T_WEAPON, T_WEAPON_VARIANT):
            name = self.cstr(z, rd(h, 'I'))
            if not name:
                return
            models, anims, refs = set(), set(), set()
            d = rd(h + 8, 'I')
            # the weapons it brings along: its alternate weapon (variant +20), parent, mounted, spawned grenade and
            # dual wield (left hand) weapons (WeaponDef +60, +376, +380, +384, +1420, +1424)
            for addr, off in ((h, 20),) + (((d, 60), (d, 376), (d, 380), (d, 384), (d, 1420), (d, 1424))
                                           if d and d < 0xFFFFFFFE else ()):
                ref = self.cstr(z, rd(addr + off, 'I'))
                if ref:
                    refs.add(ref.lower())
            if d and d < 0xFFFFFFFE:
                for arr in (rd(d + 4, 'I'), rd(d + 780, 'I')):
                    if arr and arr < 0xFFFFFFFE:
                        for k in range(16):
                            self.add(models, z, rd(arr + 4 * k, 'I'))
                for off in WEAPON_MODEL_FIELDS:
                    self.add(models, z, rd(d + off, 'I'))
            xa = rd(h + 16, 'I')
            if xa and xa < 0xFFFFFFFE:
                for k in range(66):
                    a = self.cstr(z, rd(xa + 4 * k, 'I'))
                    if a:
                        anims.add(a.lower())
            self.weapons[name.lstrip(',').lower()] = (models, anims, refs)
        elif type_ == T_GFXWORLD:
            count, insts = rd(h + 836, 'I'), rd(h + 836 + 96, 'I')
            for i in range(count if insts and insts < 0xFFFFFFFE else 0):
                self.add(self.static, z, rd(insts + 76 * i + 56, 'I'))
        elif type_ == T_FX:
            count = rd(h + 16, 'i') + rd(h + 20, 'i') + rd(h + 24, 'i')
            elems = rd(h + 28, 'I')
            for i in range(count if elems and elems < 0xFFFFFFFE else 0):
                e = elems + 292 * i
                if rd(e + 184, 'B') != 7 or not rd(e + 185, 'B'):
                    continue
                vc, vis = rd(e + 185, 'B'), rd(e + 196, 'I')
                if vc == 1:
                    self.add(self.fx, z, vis)
                elif vis and vis < 0xFFFFFFFE:
                    for k in range(vc):
                        self.add(self.fx, z, rd(vis + 4 * k, 'I'))
        elif type_ == T_DESTRUCTIBLE:
            self.add(self.destructible, z, rd(h + 4, 'I'))
            self.add(self.destructible, z, rd(h + 8, 'I'))
            n, pieces = rd(h + 12, 'i'), rd(h + 16, 'I')
            for i in range(n if pieces and pieces < 0xFFFFFFFE else 0):
                for k in range(5):
                    for m in range(3):
                        self.add(self.destructible, z, rd(pieces + 312 * i + 48 * k + 32 + 4 * m, 'I'))
        elif type_ == T_XMODELPIECES:
            n, pieces = rd(h + 4, 'i'), rd(h + 8, 'I')
            for i in range(n if pieces and pieces < 0xFFFFFFFE else 0):
                self.add(self.pieces, z, rd(pieces + 16 * i, 'I'))
        elif type_ == T_MAPENTS:
            s, n = rd(h + 4, 'I'), rd(h + 8, 'i')
            if s and n > 0:
                m, o = z.region(s)
                self.texts.append(bytes(m[o:o + n]))
                self.text_names.append('')
        elif type_ == T_RAWFILE:
            name = (self.cstr(z, rd(h, 'I')) or '').lower()
            n, b = rd(h + 4, 'i'), rd(h + 8, 'I')
            if b and 0 < n < 64 << 20:
                m, o = z.region(b)
                data = bytes(m[o:o + n])
                if name.endswith(('.gsc', '.csc')) and len(data) >= 8:
                    # (scripts: their size, the compressed size, zlib - clientscript/cscr_parser.cpp Scr_ReadFile)
                    try:
                        data = zlib.decompress(data[8:8 + struct.unpack_from('<I', data, 4)[0]])
                    except zlib.error:
                        pass
                self.texts.append(data)
                self.text_names.append(name.replace(chr(92), '/'))


def collect(zones):
    """zones: [(name, .ff path)] -> Collector"""
    c = Collector()
    for name, path in zones:
        zone = zr_runtime.Zone(open(path, 'rb').read(), name)
        zr_runtime.Loader(zone).load_zone(asset_hook=c.asset)
    return c


WORD = re.compile(rb'[A-Za-z0-9_]+')


COMMENT = re.compile(rb'//[^\n]*|/\*.*?\*/', re.S)


def strip_comments(texts):
    """the scripts without their comments (a weapon named only in a commented-out line is not registered)"""
    return [COMMENT.sub(b' ', t) for t in texts]


def live_models(c, named, replaced=frozenset(), file_words=frozenset()):
    """the live set: c's static / fx / destructible / pieces models, and the models and view anims of every weapon whose
    name is in 'named' (the words the scripts, tables, weapon files and map entities contain, lower case; None: every
    weapon) and of the weapons those bring along (alternate, dual wield, ...) -> (models, anims, dead anims: the weapon
    view anims only weapons nothing names use)"""
    models = set(c.static) | c.fx | c.destructible | c.pieces
    live = [w for w in c.weapons if named is None or w in named or w.replace('_upgraded', '') in named]
    seen = set(live)
    while live:
        w = live.pop()
        for ref in c.weapons[w][2]:
            if ref in c.weapons and ref not in seen:
                seen.add(ref)
                live.append(ref)
    anims, weapon_anims = set(), set()
    for w, (wm, wa, _) in c.weapons.items():
        weapon_anims |= wa
        if w in seen and w in replaced:
            # (the mod's weapon file of that name is used instead: of the zone weapon's models only those the file
            # names - also as the end of the name, WDS's claymore 'claymore_no_legs' is the game's
            # 'weapon_claymore_no_legs')
            for m in wm:
                parts = m.split('_')
                if any('_'.join(parts[i:]) in file_words for i in range(len(parts))):
                    models.add(m)
        if w in seen and w not in replaced:
            models |= wm
            anims |= wa
    return models, anims, weapon_anims - anims


def words(blobs):
    out = set()
    for b in blobs:
        out |= {m.decode('latin-1').lower() for m in WORD.findall(b)}
    return out


def iwd_texts(iwds):
    """the mod's archives: (script / table texts, the words of its weapon files) - a weapon file (weapons/sp/<name>,
    'WEAPONFILE' and backslash-separated keys and values) replaces the zone's weapon of its name; its models and anims are what it names"""
    import zipfile
    texts, weapon_words, weapon_files = [], set(), set()
    for iwd in iwds:
        with zipfile.ZipFile(iwd) as z:
            for n in z.namelist():
                low = n.lower()
                if low.endswith(('.gsc', '.csc', '.csv', '.txt', '.str')):
                    texts.append(z.read(n))
                elif low.startswith(('weapons/sp/', 'weapons/mp/', 'weapons/zm/')) and not low.endswith('/'):
                    data = z.read(n)
                    texts.append(data)
                    weapon_words |= {w.strip().lower() for w in data.decode('latin-1').split(chr(92)) if w.strip()}
                    weapon_words.add(os.path.basename(low))
                    weapon_files.add(os.path.basename(low))
    return texts, weapon_words, weapon_files


PRECACHE = re.compile(rb'precachemodel\s*\(\s*"([^"]+)"', re.I)
ASSIGNED = re.compile(rb'=\s*"([A-Za-z0-9_]+)"')


FUNC_DEF = re.compile(rb'^[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*\(([^()]*)\)[ \t\r\n]*\{', re.M)
PRECACHE_ARG = re.compile(rb'(?:precachemodel|setmodel|attach|spawn_model|spawn)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*[,)]',
                          re.I)


def _calls(text, name):
    """the argument lists (top-level split) of every call of 'name' in text"""
    out = []
    for m in re.finditer(rb'(?<![A-Za-z0-9_])' + re.escape(name) + rb'\s*\(', text, re.I):
        i, depth, cur, args = m.end(), 1, bytearray(), []
        quote = False
        while i < len(text) and depth:
            ch = text[i:i + 1]
            if ch == b'"':
                quote = not quote
            if not quote:
                if ch in b'([':
                    depth += 1
                elif ch in b')]':
                    depth -= 1
                    if not depth:
                        break
                elif ch == b',' and depth == 1:
                    args.append(bytes(cur).strip())
                    cur = bytearray()
                    i += 1
                    continue
            cur += ch
            i += 1
        args.append(bytes(cur).strip())
        out.append(args)
    return out


ASSIGN_VAR = re.compile(rb'([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*"([A-Za-z0-9_]+)"')


def precached(texts, direct=rb'precachemodel|setmodel', argfuncs=None, assigned=True):
    """the models the scripts precache or set by name: precacheModel( "name" ) / setModel( "name" ); the names they
    assign to a variable (level.x = "name"; precacheModel( level.x ) - what such a variable holds is not followed: kept
    whatever it is); and the names passed to a function whose parameter it precaches - over any number of such
    functions (_zombiemode_powerups.gsc add_zombie_powerup( "carpenter", "zombie_carpenter", ... ) precaches its
    model_name). direct / argfuncs: other calls (weapons: precacheItem); assigned False: a variable's name counts only
    when that variable is precached (level.start_weapon = "m1911_zm"; PrecacheItem( level.start_weapon ))"""
    argfuncs = argfuncs or PRECACHE_ARG
    out = set()
    direct_re = re.compile(rb'(?:' + direct + rb')\s*\(\s*"([^"]+)"', re.I)
    var_re = re.compile(rb'(?:' + direct + rb')\s*\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*[,)]', re.I)
    values, used = {}, set()
    for t in texts:
        out |= {m.decode('latin-1').lower() for m in direct_re.findall(t)}
        if assigned:
            out |= {m.decode('latin-1').lower() for m in ASSIGNED.findall(t)}
        else:  # (a dotted variable - level.x, self.x - is the same in every script; a local one only in its file)
            local, local_used = {}, set()
            for v, n in ASSIGN_VAR.findall(t):
                v = v.lower()
                (values if b'.' in v else local).setdefault(v, set()).add(n.decode('latin-1').lower())
            for v in var_re.findall(t):
                v = v.lower()
                if b'.' in v:
                    used.add(v)
                else:
                    out |= local.get(v, set())
    for v in used:
        out |= values.get(v, set())
    # functions: {name: parameter positions that end in a precache} - to a fixed point (a function passing its
    # parameter on to such a function precaches it too)
    defs = []
    for t in texts:
        for m in FUNC_DEF.finditer(t):
            params = [p.strip().lower() for p in m.group(2).split(b',') if p.strip()]
            depth, i = 1, m.end()
            while i < len(t) and depth:
                c = t[i]
                depth += (c == 0x7B) - (c == 0x7D)
                i += 1
            defs.append((m.group(1).lower(), params, t[m.end():i]))
    caching = {}
    for _ in range(4):
        changed = False
        for name, params, body in defs:
            pos = set(caching.get(name, ()))
            for m in argfuncs.finditer(body):
                v = m.group(1).lower()
                if v in params:
                    pos.add(params.index(v))
            for callee, cpos in list(caching.items()):
                for args in _calls(body, callee):
                    for k in cpos:
                        if k < len(args) and args[k].lower() in params:
                            pos.add(params.index(args[k].lower()))
            if pos != set(caching.get(name, ())):
                caching[name] = pos
                changed = True
        if not changed:
            break
    for t in texts:
        for name, pos in caching.items():
            if not pos:
                continue
            for args in _calls(t, name):
                for k in pos:
                    if k < len(args):
                        m = re.match(rb'^"([^"]+)"$', args[k])
                        if m:
                            out.add(m.group(1).decode('latin-1').lower())
    return out


GATED = ('character/', 'xmodelalias/', 'aitype/', 'clientscripts/character/')


def read_mod_files(iwds):
    """{lower path: bytes} of the mod archives' scripts, tables and weapon files"""
    import zipfile
    files = {}
    for iwd in iwds:
        with zipfile.ZipFile(iwd) as z:
            for n in z.namelist():
                low = n.lower().replace(chr(92), '/')
                if low.endswith('/'):
                    continue
                if low.endswith(('.gsc', '.csc', '.csv', '.txt', '.str')) or low.startswith(
                        ('weapons/sp/', 'weapons/mp/', 'weapons/zm/')):
                    files[low] = z.read(n)
    return files


def _for_level(path, level, levels):
    """False for another level's own script (maps/<other level>.gsc, maps/<other level>_amb.gsc, ...)"""
    base = os.path.basename(path)
    for lv in levels:
        if lv != level and (base == lv or base.startswith(lv + '_') or base.startswith(lv + '.')) \
                and not (level.startswith(lv + '_') or level == lv):
            return False
    return True


def live_set(c, files, level, levels):
    """the models and anims a level of the mod can show, from the level's zones (c: collect()) and the mod's archive
    files (read_mod_files) - no engine run. levels: every level's name (another level's own scripts are left out).
    -> (live models, live anims, dead anims: the weapon view anims only weapons nothing names use)

    A script counts when it can run on this level: the level's and the shared ones; a character / model alias / AI type
    script only when a counted text names it (the map's spawners name their AI types, the AI types their characters).
    A weapon is live when a counted text names it (or its name without _upgraded), with what it brings along. Models:
    the static / fx / destructible / pieces ones, the live weapons', and those the counted scripts precache, set or
    pass to a function that precaches them; for a weapon the mod's weapon file replaces, the models that file names."""
    weapon_files = {}
    texts, gated = [], {}
    for p, data in files.items():
        if p.startswith(('weapons/sp/', 'weapons/mp/', 'weapons/zm/')):
            weapon_files[os.path.basename(p)] = {w.strip().lower() for w in data.decode('latin-1').split(chr(92))
                                                 if w.strip()}
        elif not _for_level(p, level, levels):
            continue
        elif p.startswith(GATED):
            gated[p] = data
        else:
            texts.append(data)
    # the zones' raw files: a later zone's of a name replaces an earlier one's, the mod archive's file of that name both
    own = {}
    for n, d in zip(c.text_names, c.texts):
        own[n or len(own)] = d
    zone_texts = [d for n, d in own.items() if not (isinstance(n, str) and n in files)]
    texts = strip_comments(zone_texts + texts)
    named = words(texts)
    named |= {w[6:] for w in named if w.startswith('actor_')}
    gated = {p: strip_comments([d])[0] for p, d in gated.items()}
    while True:
        # (or it replaces a raw file the level's zones have: the zones hold the AI types and characters the map uses)
        add = [p for p in gated if os.path.splitext(os.path.basename(p))[0] in named or p in own]
        if not add:
            break
        for p in add:
            d = gated.pop(p)
            texts.append(d)
            named |= words([d])
    # live weapons, with what they bring along
    names = set(c.weapons) | set(weapon_files)
    items = precached_items(texts)
    ents = {w[7:] for w in named if w.startswith('weapon_')}  # (a map's weapon entity: classname weapon_<name>)
    todo = [w for w in names if w in items or w in ents]
    seen = set(todo)
    why = {w: 'item' if w in items else 'entity' for w in todo}
    while todo:
        w = todo.pop()
        refs = set(c.weapons[w][2]) if w in c.weapons and w not in weapon_files else set()
        refs |= weapon_files.get(w, set()) & names
        for r in refs:
            if r not in seen:
                seen.add(r)
                why[r] = 'from ' + w
                todo.append(r)
    live_set.why = why
    models = set(c.static) | c.fx | c.destructible | c.pieces
    models |= {'fx', 'defaultactor', 'defaultweapon'} & c.models  # (the engine's own fallbacks)
    anims, weapon_anims = set(), set()
    file_words = set()
    for w in seen & set(weapon_files):
        file_words |= weapon_files[w]
    for w, (wm, wa, _) in c.weapons.items():
        weapon_anims |= wa
        if w not in seen:
            continue
        if w in weapon_files:
            for m in wm:  # (also as the end of the name: WDS's 'claymore_no_legs' is 'weapon_claymore_no_legs')
                parts = m.split('_')
                if any('_'.join(parts[i:]) in weapon_files[w] for i in range(len(parts))):
                    models.add(m)
        else:
            models |= wm
            anims |= wa
    quoted = precached(texts) | file_words
    anims |= file_words & weapon_anims
    return models | (quoted & c.models), anims, weapon_anims - anims


PRECACHE_ITEM_ARG = re.compile(rb'(?:precacheitem)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*[,)]', re.I)


def precached_items(texts):
    """the weapons the scripts precache: PrecacheItem by name (GiveWeapon registers only while the level starts: g_weapon.cpp G_GetWeaponIndexForName), through a variable, or through functions
    that pass their parameter on to one (include_weapon, add_zombie_weapon, ...)"""
    return precached(texts, rb'precacheitem', PRECACHE_ITEM_ARG, assigned=False)
