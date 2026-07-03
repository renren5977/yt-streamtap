import os
import csv
import subprocess
from datetime import datetime
import base64
import re
import uuid
from time import sleep
import portion as P
import logging
import argparse
from .core import collector, processor
import traceback

logger = logging.getLogger(__name__)


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
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)"
    )
    parser.add_argument(
        "--record-browser",
        action="store_true",
        help="Record browser for debugging"
    )
    parser.add_argument(
        "-r", "--retry-count",
        type=int,
        default=1,
        help="Number of retries (default: 1)"
    )
    args = parser.parse_args()

    # ロギング設定
    logging.basicConfig(level=getattr(logging, args.log_level))


    # ストリームデータ収集
    uid = str(uuid.uuid4())
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    id = f"{timestamp}{uid}"
    dir = f"{args.output_dir}/{id}"
    print(f"Output directory: {dir}")
    os.makedirs(dir, exist_ok=True)
    error_log_path = os.path.join(dir, "error.log")

    if args.record_browser:
        print(f"Recording browser... ")

    retry_count = 0
    while retry_count < args.retry_count:
        try:
            # データ収集・処理
            try:
                raw = collector.collect_data(args.url, args.record_browser, dir)
            except Exception as e:
                with open(error_log_path, "a", encoding="utf-8") as f:
                    f.write("=" * 80 + "\n")
                    f.write(f"{datetime.now().isoformat(timespec='seconds')}\n")
                    traceback.print_exc(file=f)
                    f.write("\n")
                raise RuntimeError(f"Failed to collect data.")
                
            proc = processor.Processor(raw)
            built = proc.built
            csv_log = proc.get_timeline_log()

            # CSV保存 (cli.py 側で行う)
            csv_path = os.path.join(dir, "timeline.csv")
            with open(csv_path, "w") as f:
                writer = csv.writer(f)
                writer.writerows(csv_log)

            # 動画/音声ファイル保存
            if built["video"]["type"] == "fmp4":
                video_path = os.path.join(dir, f"video.mp4")
            elif built["video"]["type"] == "webm":
                video_path = os.path.join(dir, f"video.webm")
            else:
                raise ValueError(f"Unknown video type: {built['video']['type']}")
            audio_path = os.path.join(dir, f"audio.mp4")
            output_path = os.path.join(dir, f"output.mkv")

            with open(video_path, "wb") as f:
                f.write(built["video"]["init"] + b"".join(built["video"]["chunks"]))

            with open(audio_path, "wb") as f:
                f.write(built["audio"]["init"] + b"".join(built["audio"]["chunks"]))

            if not args.no_merge:
                print("merging video and audio...")
                mkvmerge_cmd = [
                    "/usr/bin/mkvmerge",
                    "-o", output_path,
                    video_path,
                    audio_path
                ]

                print("command:", " ".join(mkvmerge_cmd))

                proc = subprocess.run(
                    mkvmerge_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                if proc.returncode != 0:
                    raise RuntimeError(f"Failed to merge video and audio.")
                else:
                    print(f"Successed")
                    break

        except Exception as e:
            print(f"Error: {e}")
            print(f"Retrying in 1 seconds...")
            sleep(1)
            retry_count += 1
    else:
        print(f"Failed after {retry_count} retries. Exiting.")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(cli())
