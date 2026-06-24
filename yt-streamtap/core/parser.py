import io
from pprint import pprint
import struct
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

def parse_fmp4(data: bytes) -> dict:
    try:
        offset = 0
        CONTAINER_BOXES = {
            "moov", "trak", "mdia", "minf", "stbl",
            "edts", "dinf",
            "moof", "traf", "mvex", "mfra",
            "udta", "meta",
        }

        def parse(offset: int, end: int) -> None:
            result = {}
            while offset < end:
                size = int.from_bytes(data[offset:offset + 4], "big")
                box_type = data[offset + 4:offset + 8].decode("ascii")
                next_offset = int.from_bytes(data[offset:offset + 4], "big") + offset

                if box_type in CONTAINER_BOXES:
                    tmp_offset = offset + 8
                    tmp_result = parse(tmp_offset, next_offset)
                    result[box_type] = tmp_result
                else:
                    result[box_type] = data[offset + 8: next_offset]
                    # result[box_type] = f"{size} bytes"
                offset = next_offset
                
            return result
        
        return parse(int(offset), len(data))

    except UnicodeDecodeError:
        with open(f"tmp/error_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.bin", "wb") as f:
            f.write(data)
        raise

def get_video_fmp4_init_info(data):
    # logger.debug(data["moov"]["mvhd"][0])
    if data["moov"]["mvhd"][0] == 0:
        time_scale = int.from_bytes(data["moov"]["mvhd"][12:16], "big")
        duration = int.from_bytes(data["moov"]["mvhd"][16:20], "big")
    elif data["moov"]["mvhd"][0] == 1:
        time_scale = int.from_bytes(data["moov"]["mvhd"][20:24], "big")
        duration = int.from_bytes(data["moov"]["mvhd"][24:32], "big")
    else:
        raise RuntimeError("unknown video type")

    return {
        "time_scale": time_scale,
        "duration": duration,
    }

def get_video_mp4_chunk_info(data):
    traf = data["moof"]["traf"]
    tfdt = traf["tfdt"]
    version = tfdt[0]
    # version 1 なら 8バイト、version 0 なら 4バイト読み込む
    ts_start = int.from_bytes(tfdt[4:12] if version == 1 else tfdt[4:8], "big")

    trun = traf["trun"]
    flags = int.from_bytes(trun[1:4], 'big')

    # sample_duration_presentフラグが立っているかチェック
    has_sample_duration = (flags & 0x000100) != 0

    if has_sample_duration:
        raise RuntimeError("sample_duration_present が立っている")

    # サンプル数を取得
    sample_count = int.from_bytes(trun[4:8], "big")

    tfhd = traf["tfhd"]
    tfhd_flags = int.from_bytes(tfhd[1:4], "big")
    # default_sample_duration_present (0x000008) が立っているかチェック
    has_default_sample_duration = (tfhd_flags & 0x000008) != 0
    if not has_default_sample_duration:
        raise RuntimeError("default_sample_duration が存在しません")

    # tfhd のヘッダ (1 byte version + 3 bytes flags + 4 bytes track_ID) の後ろから計算
    offset = 8
    # base_data_offset_present (0x000001)
    if (tfhd_flags & 0x000001) != 0:
        offset += 8
    # sample_description_index_present (0x000002)
    if (tfhd_flags & 0x000002) != 0:
        offset += 4

    default_sample_duration = int.from_bytes(tfhd[offset:offset+4], "big")

    # 4. チャンクの総時間と終了時刻を計算
    chunk_duration = sample_count * default_sample_duration
    ts_end = ts_start + chunk_duration

    return{
        "ts_start": ts_start,
        "ts_end": ts_end,
    }

def parse_webm(data: bytes) -> dict:
    """WebM をパース。コンテナ要素は再帰、リーフはサイズだけ記録。"""

    def read_elem_id(offset: int) -> tuple[int, int]:
        """Element ID 用 VINT: マーカービットを保持したまま生バイト列を読む"""
        b = data[offset]
        if   b & 0x80: n = 1
        elif b & 0x40: n = 2
        elif b & 0x20: n = 3
        elif b & 0x10: n = 4
        else: raise ValueError(f"bad element id at offset {offset}")
        val = int.from_bytes(data[offset:offset + n], "big")
        return val, offset + n

    def read_vint(offset: int) -> tuple[int, int]:
        """Data Size 用 VINT: マーカービットをマスクして数値を返す（最大8バイト対応）"""
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
        # 全ビット1 = 不定サイズ (EBML unknown size)
        max_val = (1 << (7 * n)) - 1
        if val == max_val:
            return -1, offset + n  # -1 で不定サイズを表現
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

    def parse(offset: int, end: int) -> dict:
        result = {}
        while offset < end:
            if offset >= len(data):
                break
            elem_id, offset = read_elem_id(offset)
            size, offset = read_vint(offset)

            if size == -1:  # unknown size: ファイル末尾まで
                data_end = len(data)
            else:
                data_end = offset + size

            key = f"{elem_id:X}"  # ゼロパディングなし → "AE", "1A45DFA3" 等

            if key in CONTAINERS:
                result[key] = parse(offset, data_end)
            else:
                # result[key] = f"({size} bytes)"
                result[key] = data[offset:data_end]

            offset = data_end
        return result

    return parse(0, len(data))

def get_video_webm_cluster_info(cluster: dict) -> dict:
    """
    parse_webm() 後の Cluster dict だけを受け取って、
    ts_start / ts_end を返す。

    cluster は data["18538067"]["1F43B675"] などを想定。

    戻り値:
        ts_start:
            Cluster Timestamp(E7) または最小Block timestamp

        ts_end:
            Cluster内の最後のSimpleBlock/Blockの開始timestamp

    注意:
        ts_end は厳密な終了時刻ではなく、最後のBlockの開始時刻。
        initなしでは最後のフレームdurationが分からないため。
    """

    def as_list(v):
        if v is None:
            return []
        return v if isinstance(v, list) else [v]

    def read_block_relative_timestamp(block: bytes) -> int:
        """
        SimpleBlock(A3) / Block(A1) の relative timestamp を読む。

        Block構造:
            TrackNumber VINT | Timestamp int16 | Flags | Frame Data

        見る場所:
            1. block[0] で TrackNumber VINT の長さを判定
            2. TrackNumber の直後2bytesを signed int16 として読む
        """

        if not block:
            raise RuntimeError("空の SimpleBlock/Block です")

        first = block[0]

        # TrackNumber VINT の長さ判定
        if first & 0x80:
            track_number_size = 1
        elif first & 0x40:
            track_number_size = 2
        elif first & 0x20:
            track_number_size = 3
        elif first & 0x10:
            track_number_size = 4
        elif first & 0x08:
            track_number_size = 5
        elif first & 0x04:
            track_number_size = 6
        elif first & 0x02:
            track_number_size = 7
        elif first & 0x01:
            track_number_size = 8
        else:
            raise RuntimeError("不正な TrackNumber VINT です")

        ts_offset = track_number_size

        if len(block) < ts_offset + 2:
            raise RuntimeError("SimpleBlock/Block が短すぎます")

        # TrackNumber の直後2bytesが relative timestamp
        return int.from_bytes(
            block[ts_offset:ts_offset + 2],
            "big",
            signed=True,
        )

    # E7 = Cluster Timestamp
    if "E7" not in cluster:
        raise RuntimeError("Cluster Timestamp(E7) が存在しません")

    cluster_ts = int.from_bytes(cluster["E7"], "big")

    timestamps = [cluster_ts]

    # A3 = SimpleBlock
    for block in as_list(cluster.get("A3")):
        relative_ts = read_block_relative_timestamp(block)
        absolute_ts = cluster_ts + relative_ts
        timestamps.append(absolute_ts)

    # A0 = BlockGroup, A1 = Block
    # SimpleBlockではなくBlockGroup形式の場合も一応見る
    for block_group in as_list(cluster.get("A0")):
        block = block_group.get("A1")
        if block is None:
            continue

        relative_ts = read_block_relative_timestamp(block)
        absolute_ts = cluster_ts + relative_ts
        timestamps.append(absolute_ts)

    return {
        "ts_start": min(timestamps),
        "ts_end": max(timestamps),
        "cluster_ts": cluster_ts,
        "estimated_end": True,
    }

