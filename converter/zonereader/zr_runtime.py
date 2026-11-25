"""zr_runtime.py - the engine's zone loading machinery in Python (database/db_stream.cpp, db_stream_load.cpp,
db_file_load.cpp, db_zonemap.cpp), for the generated loaders (zr_load_gen.py, made by gen_reader.py from the engine's
Load_ functions). Reads a zone (.ff) the way the engine does and writes the same zone map (.zmap) the engine's
bo1_zonemap recorder writes - so the converter needs no engine run to know a zone's layout.

Memory: the zone's 7 blocks are bytearrays; an address is (block + 1) << 28 | offset. Region 8 (address 8 << 28) holds
what the engine keeps outside the blocks (the asset list header). Values are Python ints, pointers unsigned 32-bit.
"""
import bisect
import struct
import zlib

FMT = {'b': '<b', 'B': '<B', 'h': '<h', 'H': '<H', 'i': '<i', 'I': '<I', 'q': '<q', 'Q': '<Q', 'f': '<f', 'd': '<d'}
SIZE = {'b': 1, 'B': 1, 'h': 2, 'H': 2, 'i': 4, 'I': 4, 'q': 8, 'Q': 8, 'f': 4, 'd': 8}
STRUCTS = {k: struct.Struct(v) for k, v in FMT.items()}
OUTSIDE = 7  # the zone map's block number for memory outside the blocks

# zone map events (db_zonemap.h)
ZM_READ, ZM_ALIGN, ZM_INC, ZM_RESET, ZM_OFFSET, ZM_MARK_BEGIN, ZM_MARK_END, ZM_ASSET = 1, 2, 3, 4, 5, 6, 7, 8


class ZoneError(Exception):
    pass


class Zone:
    def __init__(self, data, name):
        """data: the .ff file's bytes"""
        if data[:8] not in (b'IWffu100', b'IWff0100'):
            raise ZoneError('%s: not a zone file' % name)
        if data[:8] == b'IWff0100':
            raise ZoneError('%s: signed zone (not supported)' % name)
        version = struct.unpack_from('<I', data, 8)[0]
        self.version = version
        if version & ~0x30000 != 473:
            raise ZoneError('%s: zone version %d (expected 473)' % (name, version))
        self.name = name
        self.stream = zlib.decompress(data[12:])
        self.pos = 0  # the stream's read position
        self.mem = [None] * 9
        self.mem[8] = bytearray(4096)  # (the asset list header; from 4096 on: the asset pool's copies, see pool_copy)
        self.events = []
        self.reads = [[] for _ in range(7)]  # per block: sorted [offset, stream, size] (db_zonemap.cpp NoteRead)
        self.read_keys = [[] for _ in range(7)]
        self.unmapped = 0
        self.delayed = []
        self.stack = []
        self.string_ids = {}

    # ---- memory
    def region(self, addr):
        r = addr >> 28
        if r < 1 or r > 8 or self.mem[r] is None:
            raise ZoneError('bad address 0x%08x' % addr)
        return self.mem[r], addr & 0x0FFFFFFF

    def rd(self, addr, code):
        m, off = self.region(addr)
        return STRUCTS[code].unpack_from(m, off)[0]

    def wr(self, addr, code, value):
        m, off = self.region(addr)
        if code == 'f' or code == 'd':
            STRUCTS[code].pack_into(m, off, float(value))
        else:
            bits = SIZE[code] * 8
            value &= (1 << bits) - 1
            if code in 'bhiq' and value >> (bits - 1):
                value -= 1 << bits
            STRUCTS[code].pack_into(m, off, value)

    def pool_copy(self, src, size):
        """a copy of an asset's structure outside the blocks (DB_AddXAsset links images by copying them into the asset
        pool: the zone's copy may be in the temp block, which later assets reuse); its address"""
        m8 = self.mem[8]
        off = (len(m8) + 15) & ~15
        m8.extend(bytes(off + size - len(m8)))
        addr = (8 << 28) | off
        self.copy(addr, src, size)
        return addr

    def copy(self, dst, src, size):
        md, od = self.region(dst)
        ms, os_ = self.region(src)
        md[od:od + size] = ms[os_:os_ + size]

    def block_of(self, addr):
        """(zone map block, offset): block 7 for memory outside the blocks"""
        r = addr >> 28
        if 1 <= r <= 7 and self.mem[r] is not None and (addr & 0x0FFFFFFF) < len(self.mem[r]):
            return r - 1, addr & 0x0FFFFFFF
        return OUTSIDE, 0

    # ---- zone map
    def emit(self, type_, block, extra, a, b, c):
        self.events.append((type_, block, extra, a & 0xFFFFFFFF, b & 0xFFFFFFFF, c & 0xFFFFFFFF))

    def note_read(self, block, offset, size, stream):
        """block bytes [offset, offset + size) came from the stream at 'stream' (a newer read replaces older ones)"""
        keys, reads = self.read_keys[block], self.reads[block]  # reads[i] = [offset, stream, size], keys[i] = offset
        end = offset + size
        i = bisect.bisect_left(keys, offset)
        if i > 0:
            p = reads[i - 1]
            pend = p[0] + p[2]
            if pend > offset:
                if pend > end:  # the old read covers the new one: keep its tail
                    keys.insert(i, end)
                    reads.insert(i, [end, p[1] + (end - p[0]), pend - end])
                p[2] = offset - p[0]
        i = bisect.bisect_left(keys, offset)
        j = i
        tail = None
        while j < len(keys) and keys[j] < end:
            r = reads[j]
            rend = r[0] + r[2]
            j += 1
            if rend > end:
                tail = [end, r[1] + (end - r[0]), rend - end]
                break
        del keys[i:j]
        del reads[i:j]
        if tail:
            keys.insert(i, end)
            reads.insert(i, tail)
        keys.insert(i, offset)
        reads.insert(i, [offset, stream, size])

    def stream_offset_of(self, addr):
        """(stream offset of the byte at addr or 0xFFFFFFFF, its encoded block offset or 0)"""
        block, off = self.block_of(addr)
        if block == OUTSIDE:
            return 0xFFFFFFFF, 0
        encoded = ((block << 29) | off) + 1
        keys = self.read_keys[block]
        i = bisect.bisect_right(keys, off)
        if i == 0:
            return 0xFFFFFFFF, encoded
        r = self.reads[block][i - 1]
        if off >= r[0] + r[2]:
            return 0xFFFFFFFF, encoded
        return r[1] + (off - r[0]), encoded

    def write_zmap(self, path):
        head = b'BO1ZMAP1' + self.name.encode('latin-1')[:63]
        head += b'\0' * (72 - len(head))
        rec = struct.Struct('<BBHIII')
        with open(path, 'wb') as f:
            f.write(head)
            f.write(b''.join(rec.pack(*e) for e in self.events))

    # ---- the stream (db_file_load.cpp)
    def load_data(self, addr, size):
        """DB_LoadXFileData: size stream bytes to addr"""
        if size <= 0:
            raise ZoneError('read of %d bytes' % size)
        if self.pos + size > len(self.stream):
            raise ZoneError('read past the end of the stream (%d + %d > %d)' % (self.pos, size, len(self.stream)))
        block, off = self.block_of(addr) if self.pos else (OUTSIDE, 0)
        self.emit(ZM_READ, block, 0, off, self.pos, size)
        if block < OUTSIDE:
            self.note_read(block, off, size, self.pos)
        m, o = self.region(addr)
        m[o:o + size] = self.stream[self.pos:self.pos + size]
        self.pos += size

    def load_string(self, addr):
        """DB_LoadXFileDataNullTerminated: the bytes up to and with the 0 to addr; their count"""
        end = self.stream.index(b'\0', self.pos) + 1
        size = end - self.pos
        block, off = self.block_of(addr)
        self.emit(ZM_READ, block, 0, off, self.pos, size)
        if block < OUTSIDE:
            self.note_read(block, off, size, self.pos)
        m, o = self.region(addr)
        m[o:o + size] = self.stream[self.pos:end]
        self.pos = end
        return size


class Loader:
    """the engine's stream globals and functions (g_streamPos etc.); the generated Load_ functions are its methods"""

    def __init__(self, zone):
        self.z = zone
        self.g = {}  # the generated code's globals (varXModel, ...)
        # db_file_load.cpp: a zone re-packed for the Vita (world vertices in the physical block / in 36 bytes)
        self.g['g_dbWorldVertsPhysical'] = 1 if zone.version & 0x10000 else 0
        self.g['g_dbWorldVertsCompact'] = 1 if zone.version & 0x20000 else 0
        self.g['g_bo1WorldVertsCompact'] = self.g['g_dbWorldVertsCompact']
        self.g['g_bo1WorldVbStride'] = 44

    # ---- db_stream.cpp
    def init_streams(self, sizes):
        z = self.z
        for b in range(7):
            z.mem[b + 1] = bytearray(sizes[b])
        self.posIndex = 0
        self.pos = 1 << 28
        self.posArray = [0] + [(b + 1) << 28 for b in range(1, 7)]
        self.stackPos = []
        self.delay = []

    def DB_PushStreamPos(self, index):
        self.stackPos.append([self.posIndex, 0])
        self.DB_SetStreamIndex(index)
        self.stackPos[-1][1] = self.pos

    def DB_SetStreamIndex(self, index):
        if index != self.posIndex:
            self.posArray[self.posIndex] = self.pos
            self.posIndex = index
            self.pos = self.posArray[index]
            self.posArray[index] = 0

    def DB_PopStreamPos(self):
        index, pos = self.stackPos.pop()
        if not self.posIndex:
            self.zpos(ZM_RESET, 0, self.pos, pos, 0)
            self.pos = pos
        self.DB_SetStreamIndex(index)

    def DB_GetStreamPos(self):
        return self.pos

    def DB_AllocStreamPos(self, alignment):
        old = self.pos
        self.pos = (~alignment) & (self.pos + alignment) & 0xFFFFFFFF
        self.zpos(ZM_ALIGN, self.posIndex, old, self.pos, alignment + 1)
        return self.pos

    def DB_IncStreamPos(self, size):
        self.zpos(ZM_INC, self.posIndex, self.pos, self.pos + size, 0)
        self.pos += size

    def DB_InsertPointer(self):
        self.DB_PushStreamPos(4)
        p = self.DB_AllocStreamPos(3)
        self.DB_IncStreamPos(4)
        self.DB_PopStreamPos()
        return p

    def zpos(self, type_, block, old, new, extra):
        base = (block + 1) << 28
        if type_ == ZM_INC:
            self.z.emit(ZM_INC, block, 0, old - base, new - old, 0)
        else:
            self.z.emit(type_, block, extra, old - base, new - base, 0)

    # ---- db_stream_load.cpp
    def Load_Stream(self, atStreamStart, ptr, size):
        if atStreamStart and size:
            if 0 <= self.posIndex - 1 < 3:
                if self.posIndex == 1:
                    m, o = self.z.region(ptr)
                    m[o:o + size] = bytes(size)
                else:
                    self.delay.append((ptr, size))
            else:
                self.z.load_data(ptr, size)
            self.DB_IncStreamPos(size)

    def Load_DelayStream(self):
        for ptr, size in self.delay:
            self.z.load_data(ptr, size)

    def DB_ConvertOffsetToAlias(self, field):
        z = self.z
        offset = z.rd(field, 'I')
        self.zoffset(field, offset, 2)
        if not offset or offset >= 0xFFFFFFFE:
            raise ZoneError('bad alias offset 0x%08x' % offset)
        target = (((offset - 1) >> 29) + 1 << 28) | ((offset - 1) & 0x1FFFFFFF)
        z.wr(field, 'I', z.rd(target, 'I'))

    def DB_ConvertOffsetToPointer(self, field):
        z = self.z
        offset = z.rd(field, 'I')
        self.zoffset(field, offset, 1)
        z.wr(field, 'I', ((((offset - 1) & 0xFFFFFFFF) >> 29) + 1 << 28) | ((offset - 1) & 0x1FFFFFFF))

    def zoffset(self, field, encoded, kind):
        stream, field_encoded = self.z.stream_offset_of(field)
        if stream == 0xFFFFFFFF:
            self.z.unmapped += 1
        self.z.emit(ZM_OFFSET, 0, kind, stream, encoded, field_encoded)

    def Load_XStringCustom(self, strAddr):
        size = self.z.load_string(self.z.rd(strAddr, 'I'))
        self.DB_IncStreamPos(size)

    def Load_TempStringCustom(self, strAddr):
        self.Load_XStringCustom(strAddr)
        if self.z.rd(strAddr, 'I'):
            self.z.wr(strAddr, 'I', 0x7000)  # (SL_GetString's id: not used by the loading)

    def Load_ScriptStringCustom(self, var):
        pass  # (the zone's string table index becomes a script string: not used by the loading)

    # ---- zone map markers
    def DB_ZoneMap_MarkBegin(self, mark, structPtr):
        stream, encoded = self.z.stream_offset_of(structPtr)
        self.z.emit(ZM_MARK_BEGIN, 0, mark, stream, self.z.pos, encoded)

    def DB_ZoneMap_MarkEnd(self, mark):
        self.z.emit(ZM_MARK_END, 0, mark, 0, self.z.pos, 0)

    # ---- the engine's side effects while loading (asset registration, GPU resources): none here
    def Assert_MyHandler(self, *args):
        return 1

    def __debugbreak(self):
        raise ZoneError('assert')

    def DB_ModLinkDeferredAlias(self, data):
        return data  # (the engine may link a mod's asset there later: the pointer stays as loaded)

    def R_BO1LoadWorldVertexBuffer(self, *args):
        pass

    def link_asset(self, slot, size):
        """DB_AddXAsset: the asset linked into the pool - an asset in the temp block is copied there (later assets reuse
        the temp block), the slot points at the copy from then on"""
        asset = self.z.rd(slot, 'I')
        if asset and (asset >> 28 == 1 or size == 52):
            self.z.wr(slot, 'I', self.z.pool_copy(asset, size))

    def Load_GfxImageAsset(self, imagePtr):
        self.link_asset(imagePtr, 52)  # (images: always a copy - db_registry.cpp Load_GfxImageAsset)

    # Load_<X>Asset: the structure sizes (the PC's layouts) of the assets DB_AddXAsset links
    ASSET_SIZES = {'Load_XModelPiecesAsset': 12, 'Load_PhysPresetAsset': 84, 'Load_PhysConstraintsAsset': 2696,
                   'Load_DestructibleDefAsset': 24, 'Load_XAnimPartsAsset': 104, 'Load_XModelAsset': 252,
                   'Load_MaterialAsset': 192, 'Load_MaterialTechniqueSetAsset': 528, 'Load_SndBankAsset': 40,
                   'Load_SndPatchAsset': 20, 'Load_ClipMapAsset': 332, 'Load_ComWorldAsset': 64,
                   'Load_GameWorldSpAsset': 44, 'Load_GameWorldMpAsset': 44, 'Load_MapEntsAsset': 12,
                   'Load_GfxWorldAsset': 1084, 'Load_LightDefAsset': 16, 'Load_FontAsset': 24, 'Load_MenuListAsset': 12,
                   'Load_MenuAsset': 400, 'Load_LocalizeEntryAsset': 8, 'Load_WeaponVariantDefAsset': 228,
                   'Load_SndDriverGlobalsAsset': 52, 'Load_FxEffectDefAsset': 60, 'Load_FxImpactTableAsset': 8,
                   'Load_RawFileAsset': 12, 'Load_StringTableAsset': 20, 'Load_PackIndexAsset': 28,
                   'Load_XGlobalsAsset': 40, 'Load_DDLAsset': 8, 'Load_GlassesAsset': 56, 'Load_EmblemSetAsset': 44}

    def __getattr__(self, name):
        if name in Loader.ASSET_SIZES:
            size = Loader.ASSET_SIZES[name]
            return lambda slot, *rest: self.link_asset(slot, size)
        # Load_<X>Asset, Load_Texture, Load_VertexBuffer, Load_Create*Shader, Load_BuildVertexDecl, Load_PicmipWater:
        # registration and resources only, nothing read from the stream
        if name.startswith('Load_') and (name.endswith('Asset') or name in (
                'Load_Texture', 'Load_VertexBuffer', 'Load_CreateMaterialVertexShader',
                'Load_CreateMaterialPixelShader', 'Load_BuildVertexDecl', 'Load_PicmipWater',
                'Load_FxEffectDefFromName')):
            return lambda *args: None
        raise AttributeError(name)

    # ---- db_file_load.cpp DB_LoadXFileInternal
    def load_zone(self, asset_hook=None):
        z = self.z
        header = 1 << 31  # (region 8: outside the blocks)
        z.load_data(header, 36)
        size, external = z.rd(header, 'I'), z.rd(header + 4, 'I')
        sizes = [z.rd(header + 8 + 4 * b, 'I') for b in range(7)]
        self.init_streams(sizes)
        # Load_XAssetListCustom: the asset list (16 bytes, outside the blocks) and its script strings
        lst = (8 << 28) + 64
        self.g['varXAssetList'] = lst
        z.load_data(lst, 16)
        self.DB_PushStreamPos(4)
        self.g['varScriptStringList'] = lst
        self.Load_ScriptStringList(0)
        self.DB_PopStreamPos()
        self.DB_PushStreamPos(4)
        if z.rd(lst + 12, 'I'):  # assets
            assets = self.AllocLoad_FxElemVisStateSample()
            z.wr(lst + 12, 'I', assets)
            self.g['varXAsset'] = assets
            count = z.rd(lst + 8, 'i')
            # Load_XAssetArrayCustom
            self.Load_Stream(1, assets, 8 * count)
            for i in range(count):
                var = assets + 8 * i
                self.g['varXAsset'] = var
                type_ = z.rd(var, 'i')
                z.emit(ZM_ASSET, 0, type_, i, z.pos, 0)
                self.Load_XAsset(0)
                if asset_hook:
                    asset_hook(self, type_, var)
        self.DB_PopStreamPos()
        self.Load_DelayStream()
        return sizes
