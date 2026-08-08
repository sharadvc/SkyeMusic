"""Entry point for `python3 -m tune`."""

import sys

from .cli import run

if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]) or 0)
