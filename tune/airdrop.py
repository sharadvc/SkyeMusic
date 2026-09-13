"""AirDrop URL sharing helper for macOS."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys


def trigger_airdrop(url: str) -> bool:
    """Create a temporary .webloc file and trigger macOS AirDrop share sheet."""
    if sys.platform != "darwin":
        return False
    try:
        path = "/tmp/skye.webloc"
        with open(path, "wb") as f:
            plistlib.dump({"URL": url}, f)

        script = """
        tell application "Finder"
            activate
            set f to (POSIX file "/tmp/skye.webloc") as alias
            reveal f
        end tell
        delay 0.3
        tell application "System Events"
            tell process "Finder"
                click menu item "Share…" of menu 1 of menu bar item "File" of menu bar 1
                delay 0.6
                keystroke "AirDrop"
                delay 0.2
                key code 36
            end tell
        end tell
        """
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
        return True
    except Exception:
        return False
