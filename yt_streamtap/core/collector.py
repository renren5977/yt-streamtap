import subprocess
import os
import socket
import random
from time import sleep, time
import re
import json
from playwright.sync_api import sync_playwright
import logging

logger = logging.getLogger(__name__)

def with_retry(func):
    def wrapper(*args, **kwargs):
        last_exception = None
        for _ in range(3):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                print(f"buffering error: {e}")
                os.system("kill -9 $(lsof -t -i:9222) 2>/dev/null; kill -9 $(lsof -t -i:9223) 2>/dev/null")
                print("Retrying...")
                last_exception = e
        raise last_exception
    return wrapper

# @with_retry
def collect_data(url: str, record_browser: bool=False, dir: str="") -> dict:
    """
    Collect data from video and audio stream.
    """

    # Launch brave (headless: Xvfb 不要で安定動作)
    brave_proc = subprocess.Popen(
        ["/usr/bin/brave-browser", "--remote-debugging-port=9222", "--disable-web-security", "--no-sandbox"],
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
            s.connect(("127.0.0.1", 9222))
            s.close()
            break
        except:
            sleep(0.5)

    # Connect to brave
    with sync_playwright() as pw:

        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        if record_browser:
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

        page.locator(".ytp-settings-button").click()
        page.get_by_text("画質", exact=True).click()

        quality_item = page.get_by_role("menuitemradio").filter(
            has_text=re.compile(r"\d+p")
        ).nth(0)
        quality = re.findall(r"\d+", quality_item.inner_text())[0]

        print(f"quality: {quality}")

        if int(quality) >= 1080:

            page.evaluate("() => {window.__clearBufferRequested__ = true;}")
            quality_item.click()

        page.keyboard.press("Escape")

        # Stop video and seek to start
        page.evaluate("document.querySelector('video')?.click()")
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
        # ascii_art = ['⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏']
        # current = 0

        # Buffering
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

            if buffered < old_buffered:
                brave_proc.kill()
                xvfb_proc.kill()
                raise RuntimeError("Buffering progress lost: browser discarded previously buffered data")

            print(f"loading...  buffered: {int(buffered)} / {int(duration)} sec", end="\r", flush=True)

            page.evaluate(f"""
                () => {{
                    const video = document.querySelector('video');
                    video.currentTime = {(buffered - old_buffered)* random.uniform(0.5, 0.95) + old_buffered};
                }}
            """)

            sleep(random.uniform(0.3, 1.2))

        # Buffering complete
        print(f"Buffering complete: {int(buffered)} sec                                             ")

        # ---- JS の __popSegment__() で segment を1件ずつ取得 ----
        total = page.evaluate("window.__segmentBuffer__?.length || 0")
        items = []
        while True:
            item = page.evaluate("window.__popSegment__()")
            if item is None:
                break
            items.append(item)
            if len(items) % 10 == 0 or len(items) == total:
                print(f"collecting segments...  collected: {len(items)} / {total} segments", end="\r", flush=True)
        print(f"Complete: {len(items)} segments                                             ")

    brave_proc.kill()
    xvfb_proc.kill()
    return items
