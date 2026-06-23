import subprocess
import os
from datetime import datetime
from playwright.sync_api import sync_playwright
import base64
import re
import uuid
from time import sleep
import struct
import portion as P
import logging
import argparse

logger = logging.getLogger(__name__)

def collect_stream_data(url: str) -> dict:

    brave_proc = subprocess.Popen(
        ["/usr/bin/brave-browser", "--remote-debugging-port=9222"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    with sync_playwright() as pw:

        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.new_context(
            locale="ja-JP",
            viewport={"width": 1920, "height": 1080}
        )

        context.set_default_timeout(5000)
        page = context.new_page()

        # JS注入
        hook_path = os.path.join(os.getcwd(), "yt-streamtap/hook.js")
        with open(hook_path, "r") as f:
            page.add_init_script(f.read())
        page.goto(url, wait_until="domcontentloaded")

        # 1080p以上をサポートする場合、画質選択
        page.locator("video").hover()

        page.locator(".ytp-settings-button").click()
        page.get_by_text("画質", exact=True).click()

        quality_item = page.get_by_role("menuitemradio").filter(
            has_text=re.compile(r"\d+p")
        ).nth(0)

        quality = re.findall(r"\d+", quality_item.inner_text())[0]

        if int(quality) >= 1080:

            page.evaluate("() => {window.__clearBufferRequested__ = true;}")
            quality_item.click()

        page.keyboard.press("Escape")

        page.locator("video").click()
        page.evaluate(f"""
            () => {{
                const video = document.querySelector('video');
                video.currentTime = 0;
            }}
        """)

        duration = page.evaluate("""
            () => {
                const video = document.querySelector('video');
                return video.duration;
            }
        """)
        
        buffered = 0
        ascii_art = ["⚪︎", "⚫︎", "⚬︎", "⚭︎", "⚮︎", "⚯︎", "⚰︎"]
        current = 0

        while buffered < duration:
            old_buffered = buffered
            buffered = page.evaluate("""
                () => {
                    const video = document.querySelector('video');

                    if (!video || !video.buffered || video.buffered.length === 0) {
                        return 0;
                    }

                    return video.buffered.end(0);
                }
            """)

            print(f"{ascii_art[current % len(ascii_art)]} buffered: {int(buffered)} / {int(duration)} sec", end="\r")
            current += 1

            page.evaluate(f"""
                () => {{
                    const video = document.querySelector('video');
                    video.currentTime = {int((buffered - old_buffered) / 4 * 3 + old_buffered)};
                }}
            """)

            sleep(1)

        # initを取得するために、segmentBufferの内容を取得
        tmp_batch = page.evaluate("""
            () => {
                const buf = window.__segmentBuffer__ || [];
                window.__segmentBuffer__ = [];
                return buf;
            }
        """)

        result = parse_batch(tmp_batch)
        
    brave_proc.kill()
    return result
        
def parse_batch(batch):
  
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

    return data

def main():
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
        "--no-ffmpeg",
        action="store_true",
        help="FFmpeg による結合を行わない (動画と音声を個別に保存)"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="ログレベル (デフォルト: INFO)"
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
        result = collect_stream_data(args.url)

        # ファイル名用の UUID
        uid = str(uuid.uuid4())

        if result["video"]["type"] == "fmp4":
            video_path = os.path.join(args.output_dir, f"video_{uid}.mp4")
        elif result["video"]["type"] == "webm":
            video_path = os.path.join(args.output_dir, f"video_{uid}.webm")

        audio_path = os.path.join(args.output_dir, f"audio_{uid}.webm")
        output_path = os.path.join(args.output_dir, f"output_{uid}.webm")

        with open(video_path, "wb") as f:
            f.write(result["video"]["init"] + b"".join(result["video"]["chunks"]))

        with open(audio_path, "wb") as f:
            f.write(result["audio"]["init"] + b"".join(result["audio"]["chunks"]))

        if not args.no_ffmpeg:
            # FFmpeg で結合
            logging.info("FFmpeg で動画と音声を結合中...")
            ffmpeg_cmd = [
                "/usr/bin/ffmpeg",
                "-i", video_path,
                "-i", audio_path,
                "-c", "copy",
                output_path
            ]
            proc = subprocess.run(
                ffmpeg_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            if proc.returncode != 0:
                logging.error("FFmpeg 結合に失敗しました")
                return 1
            logging.info(f"結合完了: {output_path}")
        else:
            logging.info(f"動画: {video_path}, 音声: {audio_path} を個別に保存しました")

    except Exception as e:
        logging.exception("処理中にエラーが発生") 
        return 1
    finally:
        # Xvfb 終了
        xvfb_proc.kill()

    return 0

if __name__ == "__main__":
    exit(main())
