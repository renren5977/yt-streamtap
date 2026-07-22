import subprocess
import os
import socket
import random
from time import sleep, time
import re
import json
from playwright.sync_api import sync_playwright
import sys
from .console import *
from sqlite_utils import Database
import uuid as ud
from swapcollection import SwapDict, SwapList


def collect_data(url: str, port: int=9222, dir: str="", debug: bool=False) -> dict:
    """
    Collect data from video and audio stream.
    """

    # ポート使用中チェック
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"Port {port} is already in use")

    display = f":{port}"

    # ディスプレイ使用中チェック
    display_num = display.lstrip(":")
    if os.path.exists(f"/tmp/.X11-unix/X{display_num}"):
        raise RuntimeError(f"Display {display} is already in use")

    # Xvfb 起動
    print(f"Launching Xvfb (display={display})...", end="", file=sys.stderr)
    xvfb_proc = subprocess.Popen(
        ["Xvfb", display, "-screen", "0", "1920x1080x24", "-nolisten", "tcp", "-ac"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    sleep(1)

    if xvfb_proc.poll() is not None:
        raise RuntimeError(f"Failed to start Xvfb (display={display})")

    print(f"{CR}{CLEAR_LINE}{GREEN}Completed: Xvfb ready (display={display}){RESET}", file=sys.stderr)

    os.environ["DISPLAY"] = display

    # # Brave 起動
    print(f"Launching Brave (port={port})... waiting", end="", file=sys.stderr)
    brave_proc = subprocess.Popen(
        [
            "/usr/bin/brave-browser",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={dir}/brave",
            "--no-sandbox"
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=os.environ
    )

    # Brave のデバッグポートが利用可能になるまで待つ（秒数カウント付き）
    timeout = 5
    start = time()
    connected = False
    while time() - start < timeout:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
            s.close()
            connected = True
            break
        except (ConnectionRefusedError, OSError):
            sleep(0.5)
    else:
        raise RuntimeError(f"Failed to connect to Brave (port={port})")

    print(f"{CR}{CLEAR_LINE}{GREEN}Completed: Brave ready (port={port}){RESET}", file=sys.stderr)

    # Connect to brave
    with sync_playwright() as pw:

        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        if debug:
            context = browser.new_context(
                locale="ja-JP",
                viewport={"width": 1920, "height": 1080},
                record_video_dir=dir
            )
        else:
            context = browser.new_context(
                locale="ja-JP",
                viewport={"width": 1920, "height": 1080}
            )

        context.set_default_timeout(10000)
        page = context.new_page()

        # JSinjection
        hook_path = os.path.join(os.getcwd(), "yt_streamtap/hook.js")
        with open(hook_path, "r") as f:
            page.add_init_script(f.read())
        page.goto(url, wait_until="domcontentloaded")

        # If quality > 720p, switch to best quality (Auto caps at 720p)
        page.locator("video").hover()

        duration = page.evaluate("""
            () => {
                const video = document.querySelector('video');
                return video.duration;
            }
        """)

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

        def get_youtube_video_title(page) -> str:
            title_locator = page.locator(
                "h1.ytd-watch-metadata yt-formatted-string"
            )
            title_locator.wait_for(state="visible", timeout=10000)
            title = title_locator.inner_text().strip()
            if not title:
                raise RuntimeError("Failed to get YouTube video title.")
            return title

        title = get_youtube_video_title(page)
        print("========================================================", file=sys.stderr)
        print(f"Title: {title}", file=sys.stderr)
        print(f"Quality: {quality}", file=sys.stderr)
        print("========================================================", file=sys.stderr)

        # Stop video and seek to start
        page.evaluate("document.querySelector('video')?.click()")
        page.evaluate(f"""
            () => {{
                const video = document.querySelector('video');
                video.currentTime = 0;
            }}
        """)

        buffered = 0
        start_time = time()

        batches = SwapList()

        while buffered < duration or buffered == 0: # なぜか、0秒でロード完了してしまうことがあるので、0秒より大きくなるまで待つ。
            old_buffered = buffered

            # 状態を取得
            state = page.evaluate("""
                () => {
                    const video = document.querySelector("video");
                    const ranges = [];

                    if (!video) {
                        return {
                            found: false,
                            ranges: []
                        };
                    }

                    for (let i = 0; i < video.buffered.length; i++) {
                        ranges.push([
                            video.buffered.start(i),
                            video.buffered.end(i)
                        ]);
                    }

                    return {
                        found: true,
                        ranges: ranges,
                        currentTime: video.currentTime,
                        duration: video.duration,
                        paused: video.paused,
                        ended: video.ended,
                        readyState: video.readyState,
                        networkState: video.networkState,
                        currentSrc: video.currentSrc,
                        mediaError: video.error ? video.error.code : null,
                        videoCount: document.querySelectorAll("video").length
                    };
                }
            """)
            # 最後のバッファ範囲の終端を取得（シーク後も正しく捕捉するため）
            buffered = page.evaluate("""
                () => {
                    const video = document.querySelector('video');

                    if (!video || !video.buffered || video.buffered.length === 0) {
                        return 0;
                    }

                    const last = video.buffered.length - 1;
                    return video.buffered.end(last);
                }
            """)

            # readyState==4 かつ currentTime がバッファより遅れているなら、バッファ終端付近にシーク
            if state["readyState"] == 4 and state["currentTime"] < buffered:
                target = min(buffered + 3, duration)
                if target < duration - 2:
                    page.evaluate(f"""
                        () => {{
                            const video = document.querySelector('video');
                            if (video) video.currentTime = {target};
                        }}
                    """)

            if buffered < old_buffered:
                class PlayerError(Exception):
                    def __init__(self, message):
                        self.message = message
                print(f"{CR}{CLEAR_LINE}{RED}✗ Failed: loading error{RESET}", file=sys.stderr)
                raise PlayerError("Buffering error")

            # 毎回セグメントを収集（バッファリング中も取りこぼし防止）
            while True:
                batch = page.evaluate("window.__popSegment__()")
                if batch is None:
                    break
                batches.append(batch)

            elapsed = int(time() - start_time)
            print(
                f"{CR}{CLEAR_LINE}Loading... {int(buffered)}/{int(duration)} sec"
                f"｜collected {len(batches)} items"
                f"｜time {elapsed} sec",
                end="",
                flush=True,
                file=sys.stderr
            )

            sleep(random.uniform(0.3, 0.7))

        # ループ終了後、JS側に残ったセグメントを最終回収
        while True:
            batch = page.evaluate("window.__popSegment__()")
            if batch is None:
                break
            batches.append(batch)

        print(f"{CR}{CLEAR_LINE}{GREEN}Completed: loaded {int(buffered)} sec｜collected {len(batches)} items{RESET}", file=sys.stderr)

    return batches
