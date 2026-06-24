import subprocess
import os
from datetime import datetime
from playwright.sync_api import sync_playwright
import base64
import re
import uuid
from time import sleep
import portion as P
import logging
import argparse
from .core import collector, processor

logger = logging.getLogger(__name__)

def cli():
    parser = argparse.ArgumentParser(
        description="YouTube 動画・音声ストリームを取得し、結合する"
    )
    parser.add_argument(
        "url",
        help="取得する YouTube 動画の URL"
    )
    parser.add_argument(
        "-o", "--output-dir",
        default="output",
        help="出力先ディレクトリ (デフォルト: output)"
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="音声と動画の結合を行わない (動画と音声を個別に保存)"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="ログレベル (デフォルト: INFO)"
    )
    parser.add_argument(
        "--record-browser",
        action="store_true",
        help="デバッグ用にブラウザを録画する"
    )
    args = parser.parse_args()

    # ロギング設定
    logging.basicConfig(level=getattr(logging, args.log_level))

    # 出力ディレクトリ作成
    os.makedirs(args.output_dir, exist_ok=True)

    # Xvfb 起動（仮想ディスプレイ）
    xvfb_proc = subprocess.Popen(
        ["/usr/bin/Xvfb", ":99", "-screen", "0", "1920x1080x24", "-ac", "+extension", "RANDR"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    os.environ["DISPLAY"] = ":99"

    try:
  # ストリームデータ収集

        # ファイル名用の UUID
        uid = str(uuid.uuid4())
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

        id = f"{timestamp}_{uid}"

        if args.record_browser:
            print(f"Recording browser... ")

        result = collector.collect_data(args.url, args.record_browser, id)
        result = processor.process_data(result)

        if result["video"]["type"] == "fmp4":
            video_path = os.path.join(args.output_dir, f"video_{id}.mp4")
        elif result["video"]["type"] == "webm":
            video_path = os.path.join(args.output_dir, f"video_{id}.webm")

        audio_path = os.path.join(args.output_dir, f"audio_{id}.webm")
        output_path = os.path.join(args.output_dir, f"output_{id}.mkv")

        with open(video_path, "wb") as f:
            f.write(result["video"]["init"] + b"".join(result["video"]["chunks"]))

        with open(audio_path, "wb") as f:
            f.write(result["audio"]["init"] + b"".join(result["audio"]["chunks"]))

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
    finally:
        # Xvfb 終了
        xvfb_proc.kill()

    return 0

if __name__ == "__main__":
    exit(cli())
