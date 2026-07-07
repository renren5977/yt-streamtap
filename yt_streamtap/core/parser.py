import subprocess
import os
import re
import json

def get_init_info(data, track ,dir, debug=False):
    path = os.path.join(dir, "tmp.bin")
    with open(path, "wb") as f:
        f.write(data)
    
    if track == "audio":
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=duration_ts,time_base,duration",
            "-of", "json",
            path,
        ]
    elif track == "video":
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=duration_ts,time_base",
            "-of", "json",
            path,
        ]
    else:
        raise ValueError(f"Unknown track type: {track}")

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    info = json.loads(result.stdout)["streams"][0]
    print(info)
    time_base = info["time_base"]
    duration_ts = info.get("duration_ts")
    if duration_ts is None:
        duration_ts = float(info.get("duration", 0)) * eval(time_base)

    os.remove(path)

    return {
        "duration": duration_ts,
        "timescale": time_base,
    }

def get_chunk_info(data, track, dir, debug=False):
    path = os.path.join(dir, "tmp.bin")
    with open(path, "wb") as f:
        f.write(data)
    
    if track == "audio":
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "a:0",
            "-show_packets",
            "-show_entries", "packet=pts",
            "-of", "json",
            path,
        ]
    elif track == "video":
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_packets",
            "-show_entries", "packet=pts",
            "-of", "json",
            path,
        ]
    else:
        raise ValueError(f"Unknown track type: {track}")
    
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    packets = json.loads(result.stdout)["packets"]

    os.remove(path)

    return {
        "ts_start": packets[0]["pts"],
        "ts_end": packets[-1]["pts"],
    }