#!/usr/bin/env python3
"""
jcistartup - the Windows Run key, so JCIAlert comes back after a reboot.

The key name is JCIAlert, deliberately distinct from IDXAlert3. The two apps
autostart independently and neither can orphan the other's entry.
"""

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "JCIAlert"


def supported():
    return os.name == "nt"


def _target():
    """What to run at login: the exe if frozen, else pythonw on the tray."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    here = os.path.dirname(os.path.abspath(__file__))
    tray = os.path.join(here, "jcitray.py")
    exe = sys.executable
    # pythonw keeps the console window closed at login; fall back if absent.
    pyw = exe.replace("python.exe", "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    return f'"{exe}" "{tray}"'


def is_enabled():
    if not supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, NAME)
        return True
    except OSError:
        return False


def enable():
    if not supported():
        return False
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, _target())
    return True


def disable():
    if not supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, NAME)
    except OSError:
        pass
    return True
