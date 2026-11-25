"""zr_record.py - one pass over a level's zones for the converter, with no engine run: what the engine's zone reader run
(bo1_zonereader: zone maps, imagestats / matstats, sound lists) and its live models run (bo1_livemodels) gave.

Each zone is read once (zr_runtime + the generated loaders) for everything: its zone map (written only for the zones
asked for - the mod's own), its image and material lines, its sound assets and its model facts (zr_models). A game
zone's results do not depend on the mod: they are kept in a cache folder and read back next time (a level's base zones
are the same for every mod and map).
"""
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zr_extract  # noqa: E402
import zr_models  # noqa: E402
import zr_runtime  # noqa: E402

CACHE_VERSION = 2

# the zones a zombie level loads before its own, in the engine's order (code and patch zones at startup, the mod's
# after them, then the common zone): common.cpp Com_LoadLevelFastFiles / DB_LoadXAssets, as the engine's log shows them
BASE_ORDER = ['code_pre_gfx_mp', 'en_code_pre_gfx_mp', 'en_code_post_gfx_mp', 'code_post_gfx_mp', 'en_code_post_gfx',
              'code_post_gfx', 'en_patch_mp', 'patch_mp', 'en_patch', 'patch', 'mod', 'mod1', 'mod2', 'mod3', 'mod4',
              'mod5', 'mod6', 'mod7', 'common_zombie_patch', 'en_common_zombie', 'common_zombie']


def level_zone_names(level, extra=()):
    """the zone names a level loads, in order (a name whose file is missing is left out by find)"""
    return BASE_ORDER + ['%s_patch' % level, 'en_%s' % level, level, '%s_patch_override' % level] + list(extra)


def find_zones(names, mod_dirs, game):
    """[(name, path)]: a mod folder's zone of a name first (it replaces the game's), then the game's"""
    out = []
    for n in names:
        for d in list(mod_dirs) + [os.path.join(game, 'zone', 'Common'), os.path.join(game, 'zone', 'English')]:
            p = os.path.join(d, n + '.ff')
            if os.path.isfile(p):
                out.append((n, p))
                break
    return out


class ZoneResult:
    """what one zone gave"""

    def __init__(self):
        self.lines = []
        self.sounds = zr_extract.Sounds()
        self.models = zr_models.Collector()


def read_zone(name, path, zmap_path=None):
    r = ZoneResult()
    zone = zr_runtime.Zone(open(path, 'rb').read(), name)
    rec = zr_extract.Recorder(zone, r.lines)

    def hook(rr, t, v):
        if t in (9, 10, 0x1B):
            r.sounds.asset(rr, t, v)
        r.models.asset(rr, t, v)
    rec.load_zone(asset_hook=hook)
    if zmap_path:
        os.makedirs(os.path.dirname(zmap_path), exist_ok=True)
        zone.write_zmap(zmap_path)
    return r


def cached_zone(name, path, cache_dir):
    st = os.stat(path)
    key = os.path.join(cache_dir, '%s_%d_%d.pkl' % (name, st.st_size, int(st.st_mtime)))
    if os.path.isfile(key):
        try:
            with open(key, 'rb') as f:
                version, r = pickle.load(f)
            if version == CACHE_VERSION:
                return r
        except Exception:
            pass
    r = read_zone(name, path)
    os.makedirs(cache_dir, exist_ok=True)
    tmp = key + '.tmp'
    with open(tmp, 'wb') as f:
        pickle.dump((CACHE_VERSION, r), f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, key)
    return r


def record_level(zones, own, out_dir, cache_dir, memo=None):
    """zones: [(name, path)] in load order; own: the zone names whose zone maps are written (out_dir/zonemap/) and that
    are never cached (the mod's own). Writes out_dir/snddump_streams.txt and snddump_usage.txt; returns (the image and
    material lines, the merged zr_models.Collector)"""
    lines = []
    sounds = zr_extract.Sounds()
    models = zr_models.Collector()
    for name, path in zones:
        if name in own:  # (memo: the mod's own zones read once for all its maps)
            if memo is not None and path in memo:
                r = memo[path]
            else:
                r = read_zone(name, path, os.path.join(out_dir, 'zonemap', name + '.zmap'))
                if memo is not None:
                    memo[path] = r
        else:
            r = cached_zone(name, path, cache_dir)
        lines += r.lines
        # (the asset pool: a later zone's asset of a name replaces the earlier one)
        sounds.banks.update(r.sounds.banks)
        sounds.patches.update(r.sounds.patches)
        if r.sounds.pans is not None:
            sounds.pans = r.sounds.pans
        for k in ('static', 'fx', 'destructible', 'pieces', 'models', 'anims'):
            getattr(models, k).update(getattr(r.models, k))
        models.weapons.update(r.models.weapons)
        models.texts += r.models.texts
        models.text_names += r.models.text_names
    sounds.dump(out_dir)
    return lines, models
