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

# def with_retry(func):
#     def wrapper(*args, **kwargs):
#         last_exception = None
#         for _ in range(3):
#             try:
#                 return func(*args, **kwargs)
#             except Exception as e:
#                 print(f"buffering error: {e}")
#                 os.system("kill -9 $(lsof -t -i:9222) 2>/dev/null; kill -9 $(lsof -t -i:9223) 2>/dev/null")
#                 print("Retrying...")
#                 last_exception = e
#         raise last_exception
#     return wrapper

# @with_retry
def collect_data(url: str, port: int=9222, dir: str="", debug: bool=False) -> dict:
    """
    Collect data from video and audio stream.
    """

    brave_proc = subprocess.Popen(
        ["/usr/bin/brave-browser", f"--remote-debugging-port={port}", "--disable-web-security", "--no-sandbox"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    xvfb_proc = subprocess.Popen(
        ["Xvfb", ":99", "-screen", "0", "1920x1080x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    os.environ["DISPLAY"] = ":99"

    # Wait for Brave's debugging port to be ready
    for _ in range(30):
        try:
            s = socket.socket()
            s.settimeout(1)
            s.connect(("127.0.0.1", port))
            s.close()
            break
        except:
            sleep(0.5)

    # Connect to brave
    with sync_playwright() as pw:

        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        if debug:
            context = browser.new_context(
                locale="ja-JP",
                viewport={"width": 1920, "height": 1080},
                record_video_dir=dir
            )
            os.makedirs(f"{os.getcwd()}/record", exist_ok=True)
        else:
            context = browser.new_context(
                locale="ja-JP",
                viewport={"width": 1920, "height": 1080}
            )

        context.set_default_timeout(5000)
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
        items = []
        # ascii_art = ['⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏']
        # current = 0

        # Buffering
        while buffered < duration or buffered == 0: # なぜか、0秒でロード完了してしまうことがあるので、0秒より大きくなるまで待つ。
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

            if buffered - seek > random.uniform(4, 10):
                # random seek
                page.evaluate(f"""
                    () => {{
                        const video = document.querySelector('video');
                        video.currentTime = {seek + (buffered - seek) * random.uniform(0.6, 0.9)};
                    }}
                """)

            if buffered - saved >= 10:
                while True:
                    item = page.evaluate("window.__popSegment__()")
                    if item is None:
                        break
                    items.append(item)
                
            print(
                f"{CR}{CLEAR_LINE}Loading buffer and collecting data...  loaded: {int(buffered)} / {int(duration)} sec｜collected: {len(items)} items", 
                end="", 
                flush=True,
                file=sys.stderr
            )   

            sleep(random.uniform(0.9, 1.5))

        print(f"{CR}{CLEAR_LINE}{GREEN}✓ Completed: loaded: {int(buffered)} sec｜collected: {len(items)} items{RESET}", file=sys.stderr)

    brave_proc.kill()
    xvfb_proc.kill()
    return items
