import base64
import logging
from . import parser

logger = logging.getLogger(__name__)

def process_data(batch : list) -> dict:
    """
    Process the data.
    """
  
    FORMAT = {
        "video": {"init": None, "chunks": [], "type": "null"},
        "audio": {"init": None, "chunks": [], "type": "webm"},
    }

    data = FORMAT.copy()

    building_video = b""
    building_audio = b""

    init_flags = {
        "video": True,
        "audio": True,
    }

    # Format the data
    for v in batch:
        frag = base64.b64decode(v["data"]) #データをbase64からバイナリに変換

        if v["track"].startswith("audio"):
            if frag[24:28] == b"webm":
                if init_flags["audio"]:
                    logger.debug("audio/webm")
                    data["audio"]["init"] = frag
                    init_flags["audio"] = False
                else:
                    break
            elif frag[:4] == b"\x1f\x43\xb6\x75":
                logger.debug("audio/cluster")
                if building_audio:
                    data["audio"]["chunks"].append(building_audio)
                building_audio = frag
            else:
                if building_audio[:4] == b"\x1f\x43\xb6\x75":
                    logger.debug("audio/chunk")
                    building_audio += frag
                else:
                    raise RuntimeError("building_audioに異常値")

        elif v["track"].startswith("video"):
            if frag[24:28] == b"webm":
                if init_flags["video"]:
                    logger.debug("video/webm")
                    data["video"]["init"] = frag
                    data["video"]["type"] = "webm"
                    init_flags["video"] = False
                else:
                    break
                logger.debug("video/webm")

            elif frag[4:8] == b"ftyp":
                if init_flags["video"]:
                    logger.debug("video/fmp4")
                    data["video"]["init"] = frag
                    data["video"]["type"] = "fmp4"
                    init_flags["video"] = False
                else:
                    break
            elif frag[:4] == b"\x1f\x43\xb6\x75":
                logger.debug("video/cluster")
                data["video"]["type"] = "webm"
                if building_video:
                    data["video"]["chunks"].append(building_video)
                building_video = frag
            elif frag[4:8] == b"moof":
                # with open(f"tmp/{uuid.uuid4()}.mp4", "wb") as f:
                #     f.write(frag)
                logger.debug("video/segment")
                data["video"]["type"] = "fmp4"
                if building_video:
                    data["video"]["chunks"].append(building_video)
                building_video = frag
            else:
                if building_video[:4] == b"\x1f\x43\xb6\x75" or building_video[4:8] == b"moof":
                    logger.debug(f"video/chank")
                    building_video += frag
                else:
                    raise RuntimeError("building_videoに異常値")

    if building_audio:
        data["audio"]["chunks"].append(building_audio)

    if building_video:
        data["video"]["chunks"].append(building_video)

    # Sort chunks by ts_start (video)
    chunk_infos = []
    chunks = data["video"]["chunks"]

    for j, chunk in enumerate(chunks):
        r = parser.get_video_chunk_info(chunk, type=data["video"]["type"])

        ts_start = r["ts_start"]
        ts_end = r["ts_end"]

        chunk_infos.append({
            "chunk": chunk,
            "ts_start": ts_start,
            "ts_end": ts_end,
            "original_index": j,
        })

    chunk_infos.sort(key=lambda x: x["ts_start"])
    data["video"]["chunks"] = [x["chunk"] for x in chunk_infos]

    # Sort chunks by ts_start (audio)
    chunk_infos = []
    chunks = data["audio"]["chunks"]

    for j, chunk in enumerate(chunks):
        r = parser.get_audio_chunk_info(chunk)

        ts_start = r["ts_start"]
        ts_end = r["ts_end"]

        chunk_infos.append({
            "chunk": chunk,
            "ts_start": ts_start,
            "ts_end": ts_end,
            "original_index": j,
        })

    chunk_infos.sort(key=lambda x: x["ts_start"])
    data["audio"]["chunks"] = [x["chunk"] for x in chunk_infos]

    return data
