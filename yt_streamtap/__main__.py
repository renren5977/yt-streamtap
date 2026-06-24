import sys
from .cli import cli  # cli.py 内に argparse の main を定義
sys.exit(cli())