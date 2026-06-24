# yt-streamtap
yt-streamtap is a command-line tool for downloading videos automatically.

## Installation
yt-streamtap supports the following installation methods:
- Ubuntu / Debian
- Docker

### Ubuntu / Debian
#### Requirements
yt-streamtap requires the following external tools:
- Brave Browser
- mkvmerge
- Xvfb

#### Install system dependencies
```bash
sudo apt update
sudo apt install -y mkvtoolnix xvfb playwright
```

#### Install brave browser
```bash
curl -fsS https://dl.brave.com/install.sh | sh
```
source: [brave official](https://brave.com/download/brave-browser/)

#### Install yt-streamtap
```bash
git clone https://github.com/renren5977/yt-streamtap.git
cd yt-streamtap
pip install -e.
```

## run
```bash
yts [URL]
```
[!IMPORTANT]
URLs may contain special characters such as `&`.
It is recommended to wrap the URL in double quotes.

### Options
```Text
usage: yts [-h] [-o OUTPUT_DIR] [--no-merge] [--log-level {DEBUG,INFO,WARNING,ERROR}] [--record-browser] url

yt-streamtap is a command-line tool for downloading videos and other media streams from websites automatically.

positional arguments:
  url                   URL of the video to be downloaded

options:
  -h, --help            show this help message and exit
  -o OUTPUT_DIR, --output-dir OUTPUT_DIR
                        Output directory (default: output)
  --no-merge            Don't merge video and audio into a single file
  --log-level {DEBUG,INFO,WARNING,ERROR}
                        Log level (default: INFO)
  --record-browser      Record browser for debugging
  ```
