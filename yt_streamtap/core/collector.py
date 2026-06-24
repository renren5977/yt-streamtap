import subprocess
import os
from time import sleep
import re
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
                print(f"buffering error, retrying...")
                last_exception = e
        raise last_exception
    return wrapper

@with_retry
def collect_data(url: str, record_browser: bool=False, id: str="") -> dict:
    """
    Collect data from video and audio stream.
    """

    # Launch brave
    brave_proc = subprocess.Popen(
        ["/usr/bin/brave-browser", "--remote-debugging-port=9222"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # Connect to brave
    with sync_playwright() as pw:

        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        if record_browser:
            context = browser.new_context(
                locale="ja-JP",
                viewport={"width": 1920, "height": 1080},
                record_video_dir=f"record/{id}"
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
        ascii_art = ['⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏']
        current = 0

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
                raise RuntimeError("Buffering error")

            print(f"  {ascii_art[current % len(ascii_art)]} loading...  buffered: {int(buffered)} / {int(duration)} sec", end="\r")
            current += 1

            page.evaluate(f"""
                () => {{
                    const video = document.querySelector('video');
                    video.currentTime = {int((buffered - old_buffered) / 4 * 3 + old_buffered)};
                }}
            """)

            sleep(1)

        # Get buffered data
        tmp_batch = page.evaluate("""
            () => {
                const buf = window.__segmentBuffer__ || [];
                window.__segmentBuffer__ = [];
                return buf;
            }
        """)

        print("Complete                                             ")
        
    brave_proc.kill()
    return tmp_batch
