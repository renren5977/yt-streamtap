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
    args = parser.parse_args()

    # ロギング設定
    logging.basicConfig(level=getattr(logging, args.log_level))

    # 出力ディレクトリ作成
    os.makedirs(args.output_dir, exist_ok=True)

    try:
        # ストリームデータ収集
        uid = str(uuid.uuid4())
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        id = f"{timestamp}_{uid}"

        if args.record_browser:
            print(f"Recording browser... ")

        # データ収集・処理
        raw = collector.collect_data(args.url, args.record_browser, id)
        proc = processor.Processor(raw)
        built = proc.built
        csv_log = proc.get_timeline_log()

        # CSV保存 (cli.py 側で行う)
        csv_path = os.path.join(args.output_dir, f"timeline_{id}.csv")
        with open(csv_path, "w") as f:
            writer = csv.writer(f)
            writer.writerows(csv_log)
        print(f"  Timeline CSV saved: {csv_path} ({len(csv_log)-1} rows)")

        # 動画/音声ファイル保存
        if built["video"]["type"] == "fmp4":
            video_path = os.path.join(args.output_dir, f"video_{id}.mp4")
        elif built["video"]["type"] == "webm":
            video_path = os.path.join(args.output_dir, f"video_{id}.webm")
        else:
            raise ValueError(f"Unknown video type: {built['video']['type']}")
        audio_path = os.path.join(args.output_dir, f"audio_{id}.webm")
        output_path = os.path.join(args.output_dir, f"output_{id}.mkv")

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
                print("Failed to merge")
                print(proc.stderr)
                return 1
            print(f"Successed: {output_path}")
        else:
            logger.info(f"saved: {video_path}, {audio_path}")

    except Exception as e:
        logger.exception("error")
        return 1

    return 0


if __name__ == "__main__":
    exit(cli())
