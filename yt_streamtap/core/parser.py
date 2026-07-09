import subprocess
import os
import re
import json
from .console import *

def get_init_info(data, track ,dir, debug=False):
    path = os.path.join(dir, "tmp.bin")
    with open(path, "wb") as f:
        f.write(data)
    
    if track == "audio":
        cmd = [
            "ffprobe",
            "-v", "error",
            #"-select_streams", "a:0",
            "-show_streams",
            "-show_format",
            "-show_entries", "stream=time_base,",
            "-of", "json",
            path,
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
        time_base = info["streams"][0]["time_base"]
        duration = info["format"]["duration"]
        duration_ts = float(duration) / eval(time_base)

    elif track == "video":
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            #"-show_streams",
            "-show_entries", "stream=duration_ts,time_base",
            "-of", "json",
            path,
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
        time_base = info["streams"][0]["time_base"]
        duration_ts = info["streams"][0]["duration_ts"]

    else:
        raise ValueError(f"Unknown track type: {track}")
    
    os.remove(path)
    result = {
        "time_base": time_base,
        "duration_ts": duration_ts,
    }

    return result
    
def get_chunk_info(data, track, dir, debug=False):
    path = os.path.join(dir, "tmp.bin")
    with open(path, "wb") as f:
        f.write(data)
    
    if track == "audio":
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "a:0",
            #"-show_packets",
            "-show_format",
            "-show_frames",
            #"-show_entries", "packet=pts",
            "-of", "json",
            path,
        ]
    elif track == "video":
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_format",
            "-show_frames",
            #"-show_packets",
            #"-show_entries", "packet=pts, duration_ts",
            "-of", "json",
            path,
        ]

    else:
        raise ValueError(f"Unknown track type: {track}")
    
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    frames = json.loads(result.stdout)["frames"]
    duration = frames[-1].get("duration", 0)

    ts_start = frames[0]["pkt_dts"]
    ts_end = frames[-1]["pkt_dts"] + duration
    
    result = {
        "ts_start": ts_start,
        "ts_end": ts_end,
    }

    os.remove(path)
    return result
