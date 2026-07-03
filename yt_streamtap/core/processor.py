import base64
import hashlib
import logging
from . import parser, collector
import csv

logger = logging.getLogger(__name__)


class Processor:
    """キャプチャデータを処理し、items/built/timeline_log を生成する。"""

    def __init__(self, batch: list):
        """batch: キャプチャデータのリスト（hook.jsから取得）"""
        self.batch = batch
        self.items = []

        self.built = {
            "video": 
                {"init": None, "chunks": [], "type": None},
            "audio": 
                {"init": None, "chunks": [], "type": "webm"},
        }

        self._process()

    def _process(self):
        seen_init = {"audio": False, "video": False}
        items = []

        # --- batch の中身を分類 ---
        for v in self.batch:
            frag = base64.b64decode(v["data"])
            video_time = v.get("videoTime", -1)
            seq = v.get("seq", 0)
            track_raw = v["track"]

            if track_raw.startswith("audio"):
                if frag[24:28] == b"webm":
                    if not seen_init["audio"]:
                        seen_init["audio"] = True
                        cls = {"track": "audio", "data_type": "init", "container_type": "webm", "is_valid": True}
                    else:
                        cls = {"track": "audio", "data_type": "init", "container_type": "webm", "is_valid": False}
                elif frag[:4] == b"\x1f\x43\xb6\x75":
                    cls = {"track": "audio", "data_type": "cluster", "container_type": "webm", "is_valid": True}
                else:
                    cls = {"track": "audio", "data_type": "piece", "container_type": "webm", "is_valid": True}
            elif track_raw.startswith("video"):
                if frag[24:28] == b"webm":
                    if not seen_init["video"]:
                        seen_init["video"] = True
                        cls = {"track": "video", "data_type": "init", "container_type": "webm", "is_valid": True}
                    else:
                        print(f"Init fragment detected.")
                        cls = {"track": "video", "data_type": "init", "container_type": "webm", "is_valid": False}
                elif frag[:4] == b"\x1f\x43\xb6\x75":
                    cls = {"track": "video", "data_type": "cluster", "container_type": "webm", "is_valid": True}
                elif frag[4:8] == b"ftyp":
                    if not seen_init["video"]:
                        seen_init["video"] = True
                        cls = {"track": "video", "data_type": "init", "container_type": "fmp4", "is_valid": True}
                    else:
                        cls = {"track": "video", "data_type": "init", "container_type": "fmp4", "is_valid": False}
                elif frag[4:8] == b"moof":
                    cls = {"track": "video", "data_type": "segment", "container_type": "fmp4", "is_valid": True}
                else:
                    cls = {"track": "video", "data_type": "piece", "container_type": "unknown", "is_valid": True}
            else:
                cls = {"track": track_raw, "data_type": "unknown", "container_type": "unknown", "is_valid": False}

            h = hashlib.sha256(frag).hexdigest()[:16]

            item = {
                "seq": seq,
                "video_time": video_time,
                "track_raw": track_raw,
                "track": cls["track"],
                "data_type": cls["data_type"],
                "container_type": cls["container_type"],
                "is_valid": cls["is_valid"],
                "size": len(frag),
                "frag": frag,
                "hash": h,
                "ts_start": -1,
                "ts_end": -1,
                "duration": -1,
                "timescale": -1,
            }
            items.append(item)

        # --- item の timescale,duration, ts_start, ts_end を計算 ---
        video_items = [i for i in items if i["track"] == "video"]
        audio_items = [i for i in items if i["track"] == "audio"]
        
        i = 0
        while i < len(video_items):
            if video_items[i]["data_type"] == "init":
                result = parser.get_init_info(video_items[i]["frag"])
                video_items[i]["duration"] = result["duration"]
                video_items[i]["timescale"] = result["timescale"]
                i += 1
            elif video_items[i]["data_type"] in ["cluster", "segment"]:
                chunk_start = i
                i += 1
                while i < len(video_items) and video_items[i]["data_type"] == "piece":
                    i += 1
                chunk_end = i
                chunk_frag = bytes()
                for v in video_items[chunk_start:chunk_end]:
                    chunk_frag += v["frag"]
                result = parser.get_chunk_info(chunk_frag)
                for video_item in video_items[chunk_start:chunk_end]:
                        video_item["ts_start"] = result["ts_start"]
                        video_item["ts_end"] = result["ts_end"]
                for k in range(chunk_start):
                    if video_items[k]["data_type"] in ["cluster", "segment", "piece"] and video_items[k]["ts_start"] == result["ts_start"]:
                        video_items[k]["is_valid"] = False
            else:
                i += 1

        i = 0
        while i < len(audio_items):
            if audio_items[i]["data_type"] == "init":
                result = parser.get_init_info(audio_items[i]["frag"])
                audio_items[i]["duration"] = result["duration"]
                audio_items[i]["timescale"] = result["timescale"]
                i += 1
            elif audio_items[i]["data_type"] in ["cluster", "segment"]:
                chunk_start = i
                i += 1
                while i < len(audio_items) and audio_items[i]["data_type"] == "piece":
                    i += 1
                chunk_end = i
                chunk_frag = bytes()
                for a in audio_items[chunk_start:chunk_end]:
                    chunk_frag += a["frag"]
                result = parser.get_chunk_info(chunk_frag)
                for audio_item in audio_items[chunk_start:chunk_end]:
                        audio_item["ts_start"] = result["ts_start"]
                        audio_item["ts_end"] = result["ts_end"]
                for k in range(chunk_start):
                    if audio_items[k]["data_type"] in ["cluster", "segment", "piece"] and audio_items[k]["ts_start"] == result["ts_start"]:
                        audio_items[k]["is_valid"] = False
            else:
                i += 1

        items = list()
        items.extend(video_items)
        items.extend(audio_items)
        self.items = items

        for item in items:
            if item["data_type"] == "segment" and item["is_valid"]:
                self.built["video"]["type"] = item["container_type"]
                break
        else:
            raise RuntimeError("No segment found in the batch")


        # --- built を作成 ---
        video_tmp_chunk = bytes()
        audio_tmp_chunk = bytes()

        for item in items:
            if item["is_valid"]:
                if item["track"] == "video":
                    if item["data_type"] == "init":
                        self.built["video"]["init"] = item["frag"]
                    elif item["data_type"] in ["cluster", "segment"]:
                        if video_tmp_chunk:
                            self.built["video"]["chunks"].append(video_tmp_chunk)
                            video_tmp_chunk = bytes()
                        video_tmp_chunk += item["frag"]
                    elif item["data_type"] == "piece":
                        video_tmp_chunk += item["frag"]
                elif item["track"] == "audio":
                    if item["data_type"] == "init":
                        self.built["audio"]["init"] = item["frag"]
                    elif item["data_type"] in ["cluster", "segment"]:
                        if audio_tmp_chunk:
                            self.built["audio"]["chunks"].append(audio_tmp_chunk)
                            audio_tmp_chunk = bytes()
                        audio_tmp_chunk += item["frag"]
                    elif item["data_type"] == "piece":
                        audio_tmp_chunk += item["frag"]

        if video_tmp_chunk:
            self.built["video"]["chunks"].append(video_tmp_chunk)
        if audio_tmp_chunk:
            self.built["audio"]["chunks"].append(audio_tmp_chunk)

    def get_items(self) -> list:
        """個別のappendBufferデータ（タイムスタンプ/種別/valid情報付き）を返す。"""
        return self.items

    def get_timeline_log(self) -> list:
        """CSV出力用の簡易ログを返す。"""
        log = [[
            "seq", 
            "video_time", 
            "track_raw", 
            "track", 
            "data_type", 
            "container_type", 
            "is_valid", 
            "size", 
            "frag",
            "hash", 
            "ts_start", 
            "ts_end", 
            "duration", 
            "timescale"
        ]]

        for item in self.items:
            log.append([
                item["seq"],
                item["video_time"],
                item["track_raw"],
                item["track"],
                item["data_type"],
                item["container_type"],
                item["is_valid"],
                item["size"],
                "******",
                item["hash"],
                item["ts_start"],
                item["ts_end"],
                item["duration"],
                item["timescale"],
            ])

        return log

if __name__ == "__main__":
    id = "test"
    batch = collector.collect_data("https://www.youtube.com/watch?v=fY93kZK5t4I&list=RDfY93kZK5t4I&start_radio=1", id=id)
    proc = Processor(batch)
    proc.get_timeline_log()

    with open(f"{id}.csv", "w") as f:
        writer = csv.writer(f)
        writer.writerows(proc.get_timeline_log())