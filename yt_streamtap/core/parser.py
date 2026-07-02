import struct
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

def get_chunk_info(data: bytes) -> dict:
    """
    chunk（segment または cluster）を解析し、タイムスタンプ範囲を返す。

    入力: fmp4 segment または webm cluster の生バイト
    出力: {"ts_start": int, "ts_end": int}
    """
    fmt = _detect_chunk_type(data)
    if fmt == "fmp4":
        parsed = _parse_fmp4(data)
        return _get_fmp4_segment_info(parsed)
    elif fmt == "webm":
        parsed = _parse_webm(data)
        cluster = parsed.get("1F43B675", parsed)
        return _get_webm_cluster_timestamps(cluster)
    else:
        raise RuntimeError(f"unknown chunk type: {fmt}")


def get_init_info(data: bytes) -> dict:
    """
    init セグメントを解析し、timescale と duration を返す。

    入力: fmp4 init または webm init の生バイト
    出力: {"timescale": int, "duration": int|float}
          - fmp4: timescale=mvhd.timescale, duration=mvhd.duration
          - webm: timescale=timecode_scale(default=1000000), duration=EBML float
    """
    fmt = _detect_init_type(data)
    if fmt == "fmp4":
        parsed = _parse_fmp4(data)
        return _get_fmp4_init_info(parsed)
    elif fmt == "webm":
        parsed = _parse_webm(data)
        return _get_webm_init_info(parsed)
    else:
        raise ValueError(f"unsupported init format: {fmt}")


# ─── internal ────────────────────────────────────────────────────────

def _detect_init_type(data: bytes) -> str:
    """init データからフォーマットを検知する。"""
    if len(data) < 28:
        raise ValueError(f"init data too short ({len(data)} bytes)")
    if data[4:8] == b"ftyp":
        return "fmp4"
    if data[24:28] == b"webm":
        return "webm"
    raise ValueError(f"unknown init format: ftyp={data[4:8]!r} doctype={data[24:28]!r}")


def _detect_chunk_type(data: bytes) -> str:
    """chunk データからフォーマットを検知する。"""
    if data[4:8] == b"moof" or data[4:8] == b"ftyp":
        return "fmp4"
    if data[:4] == b"\x1f\x43\xb6\x75":
        return "webm"
    with open(f"error_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.bin", "wb") as f:
        f.write(data)
    raise RuntimeError(f"unknown chunk type: first16={data[:16].hex()}")


# ── fmp4 ─────────────────────────────────────────────────────────────

def _parse_fmp4(data: bytes) -> dict:
    """fmp4 をボックス構造の dict に解析する。"""
    try:
        CONTAINER_BOXES = {
            "moov", "trak", "mdia", "minf", "stbl",
            "edts", "dinf", "moof", "traf", "mvex", "mfra",
            "udta", "meta",
        }

        def _parse(offset: int, end: int) -> dict:
            result = {}
            while offset < end:
                size = int.from_bytes(data[offset:offset + 4], "big")
                box_type = data[offset + 4:offset + 8].decode("ascii")
                next_offset = size + offset

                if box_type in CONTAINER_BOXES:
                    result[box_type] = _parse(offset + 8, next_offset)
                else:
                    result[box_type] = data[offset + 8: next_offset]
                offset = next_offset
            return result

        return _parse(0, len(data))

    except UnicodeDecodeError:
        with open(f"tmp/error_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.bin", "wb") as f:
            f.write(data)
        raise


def _get_fmp4_segment_info(parsed: dict) -> dict:
    """moof/traf から ts_start / ts_end を算出する。"""
    traf = parsed["moof"]["traf"]
    tfdt = traf["tfdt"]
    version = tfdt[0]
    ts_start = int.from_bytes(tfdt[4:12] if version == 1 else tfdt[4:8], "big")

    trun = traf["trun"]
    flags = int.from_bytes(trun[1:4], "big")
    if flags & 0x000100:
        raise RuntimeError("sample_duration_present が立っている")

    sample_count = int.from_bytes(trun[4:8], "big")
    tfhd = traf["tfhd"]
    tfhd_flags = int.from_bytes(tfhd[1:4], "big")

    if not (tfhd_flags & 0x000008):
        raise RuntimeError("default_sample_duration が存在しません")

    offset = 8
    if tfhd_flags & 0x000001:
        offset += 8
    if tfhd_flags & 0x000002:
        offset += 4

    default_sample_duration = int.from_bytes(tfhd[offset:offset + 4], "big")
    chunk_duration = sample_count * default_sample_duration
    return {"ts_start": ts_start, "ts_end": ts_start + chunk_duration}


def _get_fmp4_init_info(parsed: dict) -> dict:
    """moov > mvhd から timescale / duration を返す。"""
    mvhd = parsed["moov"]["mvhd"]
    version = mvhd[0]
    if version == 0:
        timescale = int.from_bytes(mvhd[12:16], "big")
        duration = int.from_bytes(mvhd[16:20], "big")
    elif version == 1:
        timescale = int.from_bytes(mvhd[20:24], "big")
        duration = int.from_bytes(mvhd[24:32], "big")
    else:
        raise RuntimeError(f"unsupported mvhd version: {version}")
    return {"timescale": timescale, "duration": duration}


# ── webm ─────────────────────────────────────────────────────────────

def _parse_webm(data: bytes) -> dict:
    """webm を EBML 要素の dict に解析する。"""

    def _read_elem_id(offset: int) -> tuple[int, int]:
        b = data[offset]
        if   b & 0x80: n = 1
        elif b & 0x40: n = 2
        elif b & 0x20: n = 3
        elif b & 0x10: n = 4
        else: raise ValueError(f"bad element id at offset {offset}")
        return int.from_bytes(data[offset:offset + n], "big"), offset + n

    def _read_vint(offset: int) -> tuple[int, int]:
        b = data[offset]
        if   b & 0x80: n, mask = 1, 0x7F
        elif b & 0x40: n, mask = 2, 0x3F
        elif b & 0x20: n, mask = 3, 0x1F
        elif b & 0x10: n, mask = 4, 0x0F
        elif b & 0x08: n, mask = 5, 0x07
        elif b & 0x04: n, mask = 6, 0x03
        elif b & 0x02: n, mask = 7, 0x01
        elif b & 0x01: n, mask = 8, 0x00
        else: raise ValueError(f"bad vint at offset {offset}")
        val = b & mask
        for i in range(1, n):
            val = (val << 8) | data[offset + i]
        max_val = (1 << (7 * n)) - 1
        if val == max_val:
            return -1, offset + n
        return val, offset + n

    CONTAINERS = {
        "1A45DFA3", "18538067", "114D9B74", "1549A966",
        "1654AE6B", "1F43B675", "1C53BB6B", "1941A469",
        "1043A770", "1254C367", "4DBB", "AE",
        "E0", "E1", "6D80", "55B0", "55D0", "7670",
        "A0", "75A1", "BB", "B7",
        "61A7", "45B9", "B6", "80", "8F",
        "7373", "63C0", "67C8",
    }

    def _parse(offset: int, end: int) -> dict:
        result = {}
        while offset < end:
            if offset >= len(data):
                break
            elem_id, offset = _read_elem_id(offset)
            size, offset = _read_vint(offset)
            data_end = len(data) if size == -1 else offset + size
            key = f"{elem_id:X}"
            if key in CONTAINERS:
                result[key] = _parse(offset, data_end)
            else:
                result[key] = data[offset:data_end]
            offset = data_end
        return result

    return _parse(0, len(data))


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _read_block_relative_timestamp(block: bytes) -> int:
    """SimpleBlock/Block の relative timestamp を読む。"""
    if not block:
        raise RuntimeError("空の SimpleBlock/Block です")

    first = block[0]
    if   first & 0x80: tn_size = 1
    elif first & 0x40: tn_size = 2
    elif first & 0x20: tn_size = 3
    elif first & 0x10: tn_size = 4
    elif first & 0x08: tn_size = 5
    elif first & 0x04: tn_size = 6
    elif first & 0x02: tn_size = 7
    elif first & 0x01: tn_size = 8
    else: raise RuntimeError("不正な TrackNumber VINT です")

    if len(block) < tn_size + 2:
        raise RuntimeError("SimpleBlock/Block が短すぎます")

    return int.from_bytes(block[tn_size:tn_size + 2], "big", signed=True)


def _get_webm_cluster_timestamps(cluster: dict) -> dict:
    """Cluster から ts_start / ts_end を返す。"""
    if "E7" not in cluster:
        raise RuntimeError("Cluster Timestamp(E7) が存在しません")

    cluster_ts = int.from_bytes(cluster["E7"], "big")
    timestamps = [cluster_ts]

    for block in _as_list(cluster.get("A3")):
        timestamps.append(cluster_ts + _read_block_relative_timestamp(block))

    for bg in _as_list(cluster.get("A0")):
        block = bg.get("A1")
        if block is not None:
            timestamps.append(cluster_ts + _read_block_relative_timestamp(block))

    return {"ts_start": min(timestamps), "ts_end": max(timestamps)}


def _get_webm_init_info(parsed: dict) -> dict:
    """Segment > Info から timecode_scale / duration を返す。"""
    info = parsed.get("18538067", {}).get("1549A966", {})

    if "2AD7B1" in info:
        timecode_scale = int.from_bytes(info["2AD7B1"], "big")
    else:
        timecode_scale = 1000000

    if "4489" not in info:
        raise RuntimeError("Duration(4489) not found in Segment Info")
    raw = info["4489"]
    if len(raw) == 4:
        duration = struct.unpack(">f", raw)[0]
    elif len(raw) == 8:
        duration = struct.unpack(">d", raw)[0]
    else:
        raise RuntimeError(f"unsupported Duration size: {len(raw)} bytes")

    return {"timescale": timecode_scale, "duration": duration}