import subprocess
import os
import socket
import random
import select
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
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    sleep(1)
    if xvfb_proc.poll() is not None:
        out, err = xvfb_proc.communicate(timeout=3)
        msg = f"Failed to start Xvfb (display={display})"
        if err:
            msg += f"\n  stderr: {err.decode('utf-8', errors='replace')[:500]}"
        if out:
            msg += f"\n  stdout: {out.decode('utf-8', errors='replace')[:500]}"
        raise RuntimeError(msg)

    print(f"{CR}{CLEAR_LINE}{GREEN}✓ Completed: Xvfb {display} ready{RESET}", file=sys.stderr)

    os.environ["DISPLAY"] = display

    # Brave 起動
    print(f"Launching Brave (port={port})... waiting", end="", file=sys.stderr)
    brave_proc = subprocess.Popen(
        [
            "/usr/bin/brave-browser",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={dir}/brave",
            "--no-sandbox",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=os.environ
    )

    # Brave のデバッグポートが利用可能になるまで待つ（秒数カウント付き）
    timeout = 30
    start = time()
    connected = False
    while time() - start < timeout:
        # パイプバッファ掃除（データがあるときだけ非ブロッキングで読む）
        if brave_proc.stdout and select.select([brave_proc.stdout], [], [], 0)[0]:
            try:
                brave_proc.stdout.read(65536)
            except:
                pass
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
            s.close()
            connected = True
            break
        except (ConnectionRefusedError, OSError):
            elapsed = int(time() - start)
            print(
                f"{CR}{CLEAR_LINE}Waiting for Brave CDP port {port}... {elapsed}s",
                end="", flush=True, file=sys.stderr
            )
            sleep(0.5)

    if not connected:
        brave_proc.kill()
        xvfb_proc.kill()
        out = b""
        try:
            if brave_proc.stdout:
                out = brave_proc.stdout.read(10000)
        except:
            pass
        msg = f"Brave CDP port {port} not ready after {timeout}s"
        if out:
            msg += f"\n  output: {out.decode('utf-8', errors='replace')[:1000]}"
        raise RuntimeError(msg)

    print(f"{CR}{CLEAR_LINE}{GREEN}✓ Brave CDP port {port} ready{RESET}", file=sys.stderr)

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
        print(f"Title: {title}", file=sys.stderr)
        print(f"Quality: {quality}", file=sys.stderr)

        # Stop video and seek to start
        page.evaluate("document.querySelector('video')?.click()")
        page.evaluate(f"""
            () => {{
                const video = document.querySelector('video');
                video.currentTime = 0;
            }}
        """)

        buffered = 0
        saved = 0

        batches = SwapList()

        while buffered < duration or buffered == 0: # なぜか、0秒でロード完了してしまうことがあるので、0秒より大きくなるまで待つ。
            old_buffered = buffered

            # 現在のバッファ取得済み時間を取得
            buffered = page.evaluate("""
                () => {
                    const video = document.querySelector('video');

                    if (!video || !video.buffered || video.buffered.length === 0) {
                        return 0;
                    }

                    return video.buffered.end(0);
                }
            """)

            # 現在のビデオの再生位置を取得
            seek = page.evaluate("""
                () => {
                    const video = document.querySelector('video');
                    return video.currentTime;
                }
            """)

            if buffered < old_buffered:
                class PlayerError(Exception):
                    def __init__(self, message):
                        self.message = message
                brave_proc.kill()
                xvfb_proc.kill()
                print(f"{CR}{CLEAR_LINE}{RED}✗ Failed: loading error{RESET}", file=sys.stderr)
                raise PlayerError("Buffering error")

            if buffered - seek > random.uniform(10, 20):
                # random seek
                page.evaluate(f"""
                    () => {{
                        const video = document.querySelector('video');
                        video.currentTime = {seek + (buffered - seek) * random.uniform(0.7, 0.9)};
                    }}
                """)

            if buffered - saved >= 10:
                while True:
                    batch = page.evaluate("window.__popSegment__()")
                    if batch is None:
                        break
                    batches.append(batch)

            print(
                f"{CR}{CLEAR_LINE}Loading buffer and collecting data...  loaded: {int(buffered)} / {int(duration)} sec｜collected: {len(batches)} items",
                end="",
                flush=True,
                file=sys.stderr
            )

            sleep(random.uniform(1.2, 2))

        print(f"{CR}{CLEAR_LINE}{GREEN}✓ Completed: loaded: {int(buffered)} sec｜collected: {len(batches)} items{RESET}", file=sys.stderr)

    brave_proc.kill()
    xvfb_proc.kill()
    return batches
