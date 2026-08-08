"""Shared, isolated runtime state for the test suite.

`XDG_CONFIG_HOME`/`TMPDIR` are process-global and read at import time, so every
test module must use ONE shared temp root. `clean()` resets it between tests so
no module leaks state (config.json, queue.json) into another.
"""

import os
import shutil
import tempfile

_root = tempfile.mkdtemp(prefix="tune-test-")
_XDG = os.path.join(_root, "xdg")
_TMP = os.path.join(_root, "tmp")
os.environ["XDG_CONFIG_HOME"] = _XDG
os.environ["TMPDIR"] = _TMP
os.makedirs(_XDG, exist_ok=True)
os.makedirs(_TMP, exist_ok=True)


def clean() -> None:
    for d in (_XDG, _TMP):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)
