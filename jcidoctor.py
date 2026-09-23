#!/usr/bin/env python3
"""
jcidoctor - why JCIAlert did not start, in words.

THE PROBLEM THIS EXISTS FOR
---------------------------
JCIAlert.exe is built `--noconsole`. That is right for a tray app and it has
one brutal consequence: **there is no stderr.** `raise SystemExit("emiten.json
is missing")` prints to a stream that does not exist, the process ends, and the
user sees precisely nothing - no window, no tray icon, no error, no log line.

Reported 2026-09-17: *"when i try on the office computer it doesnt show on the
tray."* That is the whole symptom, because that is all the app was capable of
producing. A tray app that can vanish without saying why cannot be handed to a
colleague.

So every reason it can fail to start is checked BEFORE anything is built, and
each one carries the sentence a person needs. The checks are pure - they take
paths and return findings - so the suite can drive every branch without a
Windows box, a registry, or a missing file.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not try to fix anything. An installer that silently recreates a missing
emiten.json would hide the fact that the exe was copied on its own, which is
the thing worth knowing.
"""

import json
import os

# (level, headline, what to do about it). FATAL stops the app; WARN does not.
FATAL, WARN, OK = "FATAL", "WARN", "ok"


class Finding:
    __slots__ = ("level", "what", "detail")

    def __init__(self, level, what, detail=""):
        self.level, self.what, self.detail = level, what, detail

    def __repr__(self):
        return f"Finding({self.level}, {self.what!r})"


def check_files(here, needed=("emiten.json",), optional=("config.json",)):
    """The single most likely cause: the exe was copied on its own.

    emiten.json is 250 KB of ticker data and is NOT bundled into the exe - on
    purpose, so it can be refreshed without a rebuild. Copy JCIAlert.exe to
    another machine by itself and it dies before it can draw anything.
    """
    out = []
    for name in needed:
        path = os.path.join(here, name)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            out.append(Finding(OK, f"{name} is here"))
        else:
            out.append(Finding(
                FATAL, f"{name} is missing from {here}",
                "JCIAlert.exe cannot run on its own - it needs the files that "
                "ship beside it. Copy the WHOLE folder, or run the installer."))
    for name in optional:
        path = os.path.join(here, name)
        out.append(Finding(OK, f"{name} is here") if os.path.exists(path)
                   else Finding(WARN, f"{name} is missing",
                                "A default one will be written on first run."))
    return out


def check_writable(path, label="the app folder"):
    """Program Files is not writable by a normal user, and config.json lives
    beside the exe. An install there looks fine until the first Save."""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".jcialert-write-test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        # CREATING is the test. A failed delete is NOT a failed write, and
        # treating it as one is a bug this project has already shipped once:
        # IDXAlert 2.x required the probe to unlink cleanly and, on a share
        # that allows create but not delete, exiled the whole app to
        # LocalAppData - the data was fine, it was just somewhere nobody
        # looked. Found again here by running --doctor on a sandbox that
        # refuses unlink. See resolve_data_dir, which says the same thing.
        try:
            os.remove(probe)
        except OSError:
            pass
        return Finding(OK, f"{label} is writable")
    except OSError as exc:
        return Finding(
            FATAL, f"{label} is not writable ({path})",
            f"{type(exc).__name__}: {exc}. Settings could not be saved here. "
            "Install under your own user folder rather than Program Files.")


def check_config(path):
    if not os.path.exists(path):
        return Finding(WARN, "no config.json yet",
                       "A default one will be written on first run.")
    try:
        with open(path, encoding="utf-8") as f:
            json.load(f)
        return Finding(OK, "config.json parses")
    except ValueError as exc:
        return Finding(FATAL, "config.json is not valid JSON", str(exc))
    except OSError as exc:
        return Finding(FATAL, "config.json cannot be read", str(exc))


def check_imports():
    """pystray and pillow are what draw the tray icon. If either is missing or
    broken there is no icon to look for, which is the reported symptom."""
    out = []
    for mod, why in (("pystray", "draws the tray icon"),
                     ("PIL", "renders the icon image"),
                     ("tkinter", "draws the popup and the windows")):
        try:
            __import__(mod)
            out.append(Finding(OK, f"{mod} loads ({why})"))
        except Exception as exc:
            out.append(Finding(FATAL, f"{mod} will not load - it {why}",
                               f"{type(exc).__name__}: {exc}"))
    return out


def check_single_instance(already_running):
    """Two copies share one seen.json, so the second exits on purpose. Silently,
    until now - which looks exactly like a crash."""
    if already_running:
        return Finding(
            FATAL, "JCIAlert is already running",
            "Only one copy may run: two would share one seen.json and "
            "double every alert. Look in the hidden-icons area of the tray "
            "(the ^ chevron) - it is probably there already.")
    return Finding(OK, "no other copy is running")


def run(here, data_dir, already_running=False, want_imports=True):
    """-> [Finding]. Pure apart from the filesystem it is pointed at."""
    out = list(check_files(here))
    out.append(check_writable(here))
    if os.path.abspath(data_dir) != os.path.abspath(here):
        out.append(check_writable(data_dir, "the data folder"))
    out.append(check_config(os.path.join(here, "config.json")))
    if want_imports:
        out.extend(check_imports())
    out.append(check_single_instance(already_running))
    return out


def fatal(findings):
    return [f for f in findings if f.level == FATAL]


def report(findings, here="", version=""):
    """The text a person reads. Problems first - the reason they opened it."""
    lines = []
    if version:
        lines.append(version)
    if here:
        lines.append(f"Folder: {here}")
    lines.append("")
    bad = [f for f in findings if f.level != OK]
    if not bad:
        lines.append("No problems found. JCIAlert should start normally.")
    for f in bad:
        lines.append(f"[{f.level}] {f.what}")
        if f.detail:
            lines.append(f"        {f.detail}")
    ok = [f for f in findings if f.level == OK]
    if ok:
        lines.append("")
        lines.append("Checked and fine:")
        for f in ok:
            lines.append(f"  - {f.what}")
    return "\n".join(lines)


def tell(title, text):
    """Say it where a --noconsole exe can actually be heard.

    ctypes rather than tkinter on purpose: if tkinter is the thing that is
    broken, a tkinter dialog reporting that tkinter is broken is no use. The
    MessageBox is the one output that survives almost anything.
    """
    try:
        import ctypes
        MB_ICONERROR, MB_TOPMOST, MB_SETFOREGROUND = 0x10, 0x40000, 0x10000
        ctypes.windll.user32.MessageBoxW(
            None, text, title, MB_ICONERROR | MB_TOPMOST | MB_SETFOREGROUND)
        return True
    except Exception:
        return False


def save(text, name="startup-error.txt"):
    """A file in a place that is writable even when the app folder is not, so
    there is something to read after the dialog is dismissed."""
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(root, "JCIAlert")
    try:
        os.makedirs(path, exist_ok=True)
        full = os.path.join(path, name)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)
        return full
    except OSError:
        return ""
