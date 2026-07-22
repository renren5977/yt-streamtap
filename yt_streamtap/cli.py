import os
import csv
import subprocess
from datetime import datetime
import base64
import re
import uuid
from time import sleep
import portion as P
import argparse
from .core import collector, processor
from .core.console import *
import traceback
import sys

def save_timeline_csv(timeline: list, output_path: str):
    """
    timeline をCSVに保存する。
    カラム: wall_time_s, video_time_s, seq, track, data_type, is_valid, size_bytes, hash, ts_start, ts_end, duration, timescale
    """
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "wall_time_s", "video_time_s", "seq", "track", "data_type",
            "is_valid", "size_bytes", "hash", "ts_start", "ts_end",
            "duration", "timescale"
        ])
        for row in timeline:
            writer.writerow([
                f"{row['wall_time']:.4f}" if row['wall_time'] else "",
                f"{row['video_time']:.4f}" if row['video_time'] >= 0 else "",
                row['seq'],
                row['track'],
                row['data_type'],
                str(row['is_valid']),
                row['size'],
                row['hash'],
                row['ts_start'],
                row['ts_end'],
                row['duration'],
                row['timescale'],
            ])


def _cleanup(port: int):
    """Brave/Xvfb を強制終了し、X のロックファイルを削除する。"""
    os.system(f"kill -9 $(lsof -t -i:{port}) 2>/dev/null")
    os.system("pkill -9 Xvfb 2>/dev/null")
    os.system(f"rm -f /tmp/.X{port}-lock /tmp/.X11-unix/X{port} 2>/dev/null")


def cli():
    parser = argparse.ArgumentParser(
        description="yt-streamtap is a command-line tool for downloading videos and other media streams from websites automatically."
    )
    parser.add_argument(
        "url",
        help="URL of the video to be downloaded"
    )
    parser.add_argument(
        "-o", "--output-dir",
        default="output",
        help="Output directory (default: output)"
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Don't merge video and audio into a single file"
    )
    parser.add_argument(
        "-d", "--debug",
        action="store_true",
        help="Enable debug mode"
    )
    # parser.add_argument(
    #     "-rb", "--record-browser",
    #     action="store_true",
    #     help="Record browser for debugging"
    # )
    parser.add_argument(
        "-r", "--retry-count",
        type=int,
        default=0,
        help="Number of retries (default: 0)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9222,
        help="Port for launching Brave browser (default: 9222)"
    )
    args = parser.parse_args()

    # ストリームデータ収集
    uid = str(uuid.uuid4())
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    id = f"{timestamp}{uid}"
    dir = f"{args.output_dir}/{id}"
    print(f"Output directory: {dir}", file=sys.stderr)
    os.makedirs(dir, exist_ok=True)
    error_log_path = os.path.join(dir, "error.log")

    if args.debug:
        print(f"{GRAY}Debug: Recording browser...{RESET}", file=sys.stderr)

    retry_count = 0
    while True:
        try:
            # データ収集・処理
            batches = collector.collect_data(args.url, args.port, dir, args.debug)
            proc = processor.Processor(batches, dir, args.debug)
            artifacts = proc.artifacts
            csv_log = proc.get_timeline_log()

            # CSV保存 (cli.py 側で行う)
            csv_path = os.path.join(dir, "timeline.csv")
            with open(csv_path, "w") as f:
                writer = csv.writer(f)
                writer.writerows(csv_log)

            # 動画/音声ファイル保存
            if artifacts["video"]["type"] == "fmp4":
                video_path = os.path.join(dir, f"video.mp4")
            elif artifacts["video"]["type"] == "webm":
                video_path = os.path.join(dir, f"video.webm")
            else:
                raise ValueError(f"Unknown video type: {artifacts['video']['type']}")
            audio_path = os.path.join(dir, f"audio.mp4")
            output_path = os.path.join(dir, f"output.mkv")

            with open(video_path, "wb") as f:
                f.write(artifacts["video"]["init"])

            for i in artifacts["video"]["chunks"]:
                with open(video_path, "ab") as f:
                    f.write(i)

            with open(audio_path, "wb") as f:
                f.write(artifacts["audio"]["init"])

            for i in artifacts["audio"]["chunks"]:
                with open(audio_path, "ab") as f:
                    f.write(i)

            if not args.no_merge:
                mkvmerge_cmd = [
                    "/usr/bin/mkvmerge",
                    "-o", output_path,
                    video_path,
                    audio_path
                ]

                proc = subprocess.run(
                    mkvmerge_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                if proc.returncode != 0:
                    raise RuntimeError(f"Failed to merge video and audio.")
                else:
                    print(f"{GREEN}Successed: file saved {output_path}{RESET}", file=sys.stderr)
                    break

        except Exception as e:
            with open(error_log_path, "a", encoding="utf-8") as f:
                f.write("=" * 80 + "\n")
                f.write(f"{datetime.now().isoformat(timespec='seconds')}\n")
                traceback.print_exc(file=f)
                f.write("\n")
            print(f"{RED}Critical: {type(e).__name__}｜{e}{RESET}", file=sys.stderr)
            if retry_count == args.retry_count:
                print(f"Failed after {retry_count} retries. Exiting.", file=sys.stderr)
                print(f"Error log: {error_log_path}", file=sys.stderr)
                return 1
            print(f"Retrying... {retry_count + 1}/{args.retry_count}", file=sys.stderr)
            retry_count += 1
        finally:
            _cleanup(args.port)
    
    return 0

if __name__ == "__main__":
    exit(cli())
