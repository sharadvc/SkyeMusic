"""Global media-key control on macOS (play/pause, next, prev).

This is an *optional* enhancement: it needs `pyobjc-framework-Quartz`
(pip install pyobjc-framework-Quartz) plus Accessibility permission for the
terminal that runs the daemon. Without either it quietly disables itself, so
the rest of tune is unaffected. No dependencies are imported at module load
unless the feature is actually started.
"""

from __future__ import annotations

import threading

# NX_KEYTYPE_* values used by the aux-control button events.
NX_PLAY = 10
NX_NEXT = 8
NX_PREV = 9

_NSEVENT_TYPE_SYSTEM_DEFINED = 14


def start(on_key) -> bool:
    """Install a listen-only event tap; `on_key(code)` receives NX keycodes.
    Returns False if the feature can't run (no Quartz / no permission)."""
    try:
        import Quartz  # pyobjc, optional
    except ImportError:
        return False

    def callback(proxy, etype, event, refcon):
        if etype != _NSEVENT_TYPE_SYSTEM_DEFINED:
            return event
        # aux-control buttons (media keys) have subtype 8; the NX key code and
        # key-down bit live in field 9 (kCGKeyboardEventKeycode).
        if (Quartz.CGEventGetIntegerValueField(event, 104) & 0xFFFF0000) >> 16 != 8:
            return event
        data = Quartz.CGEventGetIntegerValueField(event, 9)
        code = (data >> 16) & 0xFF
        keydown = (data >> 8) & 0x1
        if keydown and code in (NX_PLAY, NX_NEXT, NX_PREV):
            on_key(code)
        return event

    try:
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGHIDEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            1 << _NSEVENT_TYPE_SYSTEM_DEFINED,  # mask: system-defined events
            callback,
            None,
        )
        if tap is None:
            return False  # usually a missing Accessibility permission
        src = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), src,
                                  Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
    except Exception:
        return False

    threading.Thread(target=Quartz.CFRunLoopRun, daemon=True).start()
    return True
