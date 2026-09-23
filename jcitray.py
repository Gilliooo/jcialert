#!/usr/bin/env python3
"""
JCIAlert - tray app. The thing that actually runs.

    python jcitray.py           run it
    build.bat                   make JCIAlert.exe

Same inversion as IDXAlert 3.x, and for the same reason: **the engine owns the
loop.** This file is only a face. It starts a Watcher on a background thread,
hangs a channel off its dispatcher, and turns menu clicks into requests the
Watcher honours on its own schedule. Nothing here decides when to fetch, and
"Check now" never polls from the click thread - two threads polling at once
double-alert.

THE ICON IS DELIBERATELY NOT IDXAlert'S
---------------------------------------
Both apps sit in the same tray. IDXAlert draws a bar chart; JCIAlert draws
stacked lines - a page of headlines. The STATUS COLOURS are shared on purpose,
so green/amber/blue/red mean the same thing in both:

    green   running, every source answered
    amber   paused
    blue    a source's newest item is older than its threshold - which is what
            a dead feed looks like from the inside (one source served a clean
            200 over content from July 2016)
    red     a source failed outright
"""

import os
import queue
import sys
import threading
import time
import webbrowser

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("Missing dependencies. Run:  pip install pystray pillow")

import jci
import jcidash
import jcidoctor
import jcidashwindow
import jciengine
import jcipopup
import jcistartup
import jciview
import jciwindow

jcipopup.TRAY_MODE = True      # sticky popups must not block the notify thread

state = {"watcher": None, "alerts": 0, "cfg": {},
         "options_open": False, "dash_open": False, "news": "",
         "shown": None, "pump_at": 0.0, "pump_error": "",
         "threads": {}}
outbox = queue.Queue()


# ------------------------------------------------------------------------ icon

def make_icon(color):
    """Stacked lines, not IDXAlert's bars - the two must be tellable apart at
    16 pixels, because they run side by side."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, 62, 62], radius=14, fill=color)
    white = (255, 255, 255, 235)
    for i, (x0, x1) in enumerate(((14, 50), (14, 44), (14, 50), (14, 38))):
        y = 16 + i * 10
        d.rounded_rectangle([x0, y, x1, y + 5], radius=2, fill=white)
    return img


# THE STATUS COLOURS ARE A LANGUAGE, NOT A THEME. Green/amber/blue/red mean
# the same thing in both apps and are not JCIAlert's to restyle - green has to
# stay green, or a healthy tray reads as a warning. What distinguishes the two
# apps is the SHAPE (stacked lines here, bars in IDXAlert) and the orange
# accent inside the windows, which is decoration and can be whatever it likes.
ICONS = {
    "ok":     make_icon((34, 139, 84, 255)),
    "paused": make_icon((190, 140, 20, 255)),
    "stale":  make_icon((40, 105, 190, 255)),
    "error":  make_icon((176, 42, 42, 255)),
}
_KEY = {"green": "ok", "amber": "paused", "blue": "stale", "red": "error"}


def tooltip(w):
    if w is None:
        return jci.APP + " - starting"
    if w.paused:
        return jci.APP + " - paused"
    bad = [n for n, s in w.state.items() if not s.ok]
    if bad:
        first = w.state[bad[0]]
        return f"{jci.APP} - {bad[0]} failing: {first.last_error[:50]}"
    stale = [n for n, s in w.state.items() if s.stale]
    if stale:
        return f"{jci.APP} - {', '.join(stale)} looks stale"
    when = w.last_poll_at.astimezone().strftime("%H:%M:%S") if w.last_poll_at \
        else "pending"
    return f"{jci.APP} - {state['alerts']} alerts today, last check {when}"


def refresh_icon(icon):
    """Only assign when something CHANGED.

    The pump called this once a second whether or not anything had moved -
    around 86,000 Shell_NotifyIcon calls a day, every one of them a chance for
    the one thread that draws notifications to die on an exception from a tray
    handle Windows has taken away. Fewer calls is not the fix (see pump), but
    there is no reason to make the same call 86,000 times to set the same two
    values."""
    w = state["watcher"]
    key = _KEY.get(w.status() if w else "green", "ok")
    title = tooltip(w)
    if (key, title) == state.get("shown"):
        return False
    state["shown"] = (key, title)
    icon.icon = ICONS[key]
    icon.title = title
    return True


# --------------------------------------------------------------------- plumbing

def alert_channel(payload):
    """Hung off Watcher.dispatcher.channels. Runs on the poll thread, so it
    must only queue - drawing a window from here would stall the next tick."""
    state["alerts"] += 1
    outbox.put(payload)


def show(icon, payloads):
    """Draw the popup. Never fail silently: a notification nobody sees is the
    same as no notification, which is why the style is a window we draw
    ourselves rather than a toast Windows can suppress."""
    try:
        # INSIDE the guard. This was above it, so a single malformed payload
        # raised straight through show() and out of the pump loop, which had
        # no guard of its own - one bad row and the app never notified again.
        rows = jciview.rows_for(payloads)
    except Exception as exc:
        jci.log(f"could not build popup rows: {type(exc).__name__}: {exc}")
        return False
    if not rows:
        return False
    opts = {"max_visible": state["cfg"].get("max_visible", 3),
            "duration_seconds": state["cfg"].get("duration_seconds", 0),
            "corner": state["cfg"].get("corner", "bottom right")}
    try:
        jcipopup.show(rows, opts)
        return True
    except Exception as exc:
        jci.log(f"popup failed ({type(exc).__name__}: {exc}) - trying balloon")
    try:
        icon.notify(jciview.summary(rows) + "\n" + rows[0]["title"][:120],
                    jci.APP)
        return True
    except Exception as exc:
        jci.log(f"ALL notification methods failed: {exc}")
        return False


def pump(icon):
    """Drain queued alerts on their own thread, batching whatever arrived in
    the same tick so one poll produces one popup rather than five.

    NOTHING IN HERE MAY KILL THIS LOOP. It is the only thread that draws a
    notification, and it used to run its body bare: one exception out of
    refresh_icon or show - a tray handle Windows reclaimed, one malformed
    payload - and the thread ended silently. Alerts then queued up for ever
    and the app looked alive while never notifying again. Reported
    2026-09-15 as "notification not showing up after some time running".

    So every cycle is guarded, the failure is logged once rather than per
    tick, and `pump_at` is stamped so the watchdog can tell a wedged pump
    from a quiet one.
    """
    while not getattr(icon, "visible", False):    # notify() needs a live icon
        time.sleep(0.3)
    while True:
        try:
            _pump_once(icon)
            if state.get("pump_error"):
                jci.log("notifications are working again")
                state["pump_error"] = ""
            state["pump_at"] = time.time()
        except Exception as exc:
            # Stamp BEFORE the backoff. The watchdog reads this to tell a
            # wedged pump from a quiet one, and a pump that is failing but
            # still going round is not wedged - it must keep saying so.
            state["pump_at"] = time.time()
            what = f"{type(exc).__name__}: {exc}"
            if what != state.get("pump_error"):
                jci.log(f"notification pump failed, still running: {what}")
                state["pump_error"] = what
            time.sleep(1)          # a failure that repeats must not spin


def _pump_once(icon):
    try:
        batch = [outbox.get(timeout=1)]
    except queue.Empty:
        if state["watcher"] is not None:
            refresh_icon(icon)
        return
    while True:
        try:
            batch.append(outbox.get_nowait())
        except queue.Empty:
            break
    show(icon, batch)
    refresh_icon(icon)


WORKERS = {}          # name -> the callable each supervised thread runs


def spawn(name, icon):
    t = threading.Thread(target=WORKERS[name], args=(icon,), daemon=True,
                         name=name)
    state["threads"][name] = t
    t.start()
    return t


def watchdog(icon, interval=30, forever=True):
    """Restart a worker thread that has died, and say so.

    Belt and braces over the guard inside pump(): a guard cannot catch what
    kills the interpreter's own thread - a MemoryError, a C-level fault in the
    tray backend, or a bug in the guard itself. The cost of being wrong here is
    total and silent (no notifications, ever again, while the app looks fine),
    so it is worth thirty seconds of a sleeping thread to make it recoverable.

    A pump that is ALIVE but has not completed a cycle in five minutes is also
    reported: it cannot be waiting on the queue, because that wait times out
    after a second.
    """
    while True:
        time.sleep(interval)
        w = state["watcher"]
        if w is not None and getattr(w, "_stop", False):
            return                 # quitting; do not resurrect anything
        for name in WORKERS:
            t = state["threads"].get(name)
            if t is None or not t.is_alive():
                jci.log(f"the {name} thread had died - restarting it")
                spawn(name, icon)
        last = state.get("pump_at") or 0.0
        if last and time.time() - last > 300:
            stuck = f"{int((time.time() - last) / 60)} min"
            if state.get("pump_stuck") != stuck:
                state["pump_stuck"] = stuck
                jci.log(f"the notification pump has not completed a cycle in "
                        f"{stuck} - {outbox.qsize()} alert(s) waiting")
        else:
            state["pump_stuck"] = ""
        if not forever:
            return


def engine(icon):
    w = state["watcher"]
    try:
        w.run()
    except Exception as exc:
        jci.log(f"engine stopped: {type(exc).__name__}: {exc}")
        w.last_error = str(exc)[:200]
        refresh_icon(icon)


# ------------------------------------------------------------------ menu items

def do_check(icon, _item):
    w = state["watcher"]
    if w:
        w.request_check()          # sets a flag; the loop decides when


def do_pause(icon, _item):
    w = state["watcher"]
    if w:
        w.set_paused(not w.paused)
    refresh_icon(icon)


def do_status(icon, _item):
    """A WINDOW, not a balloon.

    This used to call icon.notify() with fifteen lines of source health.
    Windows 10/11 caps a tray balloon at a couple of short lines and drops it
    entirely under Focus Assist or a per-app notification setting - so on
    Gill's machine the menu item did nothing at all, every time. A menu item
    that silently does nothing is worse than no menu item. It now opens the
    dashboard on its Status tab, where the text has room and stays put.
    """
    open_dashboard(icon, tab="Status")


def do_doctor(icon, _item):
    """The same checks the app runs at startup, on demand. Useful when it DID
    start but something is off, and useful to hand to a colleague as "click
    this and send me what it says"."""
    def run():
        try:
            data_dir = jci.resolve_data_dir(state["cfg"])
            findings = jcidoctor.run(jci.HERE, data_dir, already_running=False)
            text = jcidoctor.report(findings, jci.HERE, jci.APP)
            where = jcidoctor.save(text, "diagnostics.txt")
            if where:
                text += f"\n\nSaved to:\n{where}"
            jcidoctor.tell(f"{jci.APP} diagnostics", text)
        except Exception as exc:
            jci.log(f"diagnostics failed: {type(exc).__name__}: {exc}")
    threading.Thread(target=run, daemon=True).start()


def do_dashboard(icon, _item):
    open_dashboard(icon, tab="News")


def open_dashboard(icon, tab="News"):
    """Same one-window-at-a-time rule as Options, and for the same reason:
    two Tk roots on two threads is unsupported, and the second window is
    always the one holding stale rows."""
    if state.get("dash_open"):
        try:
            icon.notify("The news window is already open.", jci.APP)
        except Exception:
            pass
        return
    state["dash_open"] = True

    def run():
        try:
            w = state["watcher"]
            jcidashwindow.open_window(
                lambda: jcidash.read(news_path()),
                (tray_stats if w else None),
                webbrowser.open,
                tab,
                tray_actions(icon),
                opened_path())
        except Exception as exc:
            jci.log(f"news window failed: {type(exc).__name__}: {exc}")
            try:
                icon.notify(f"Could not open the news window: {exc}", jci.APP)
            except Exception:
                pass
        finally:
            state["dash_open"] = False
    threading.Thread(target=run, daemon=True).start()


def news_path():
    return state["news"] or os.path.join(
        jci.resolve_data_dir(state["cfg"]), "news.csv")


def opened_path():
    return os.path.join(jci.resolve_data_dir(state["cfg"]), "opened.json")


def tray_stats():
    """The engine's stats plus the two facts only the tray knows: how many
    alerts are waiting to be drawn, and how long since the thread that draws
    them last completed a cycle. A queue that grows while the engine reports
    every source healthy is the exact shape of the 2026-09-15 bug."""
    w = state["watcher"]
    out = dict(w.stats()) if w else {}
    out["queued"] = outbox.qsize()
    last = state.get("pump_at") or 0.0
    out["pump_age"] = int(time.time() - last) if last else None
    return out


def tray_actions(icon):
    """The tray menu, as callables the dashboard can hang on buttons.

    The window never imports the tray or reaches into its state; it is handed
    functions. That keeps the dashboard testable with none of them, and keeps
    exactly one implementation of "check now" in the app rather than two that
    drift.
    """
    w = state["watcher"]
    return {
        "Check now": lambda: do_check(icon, None),
        "Verify sources": lambda: do_verify(icon, None),
        "Send test notification": lambda: do_test(icon, None),
        ("Resume" if (w and w.paused) else "Pause"):
            lambda: do_pause(icon, None),
        "Options...": lambda: do_options(icon, None),
        "Edit config.json by hand": lambda: do_open("config.json")(icon, None),
        "Open folder": lambda: do_open(None)(icon, None),
        "Quit JCIAlert": lambda: do_quit(icon, None),
    }


def do_verify(icon, _item):
    """The guard that catches a source serving a valid 200 over dead content."""
    def run():
        w = state["watcher"]
        if not w:
            return
        bad = [n for n, s in w.state.items() if s.stale or not s.ok]
        icon.notify("all sources fresh" if not bad
                    else "needs attention: " + ", ".join(bad), jci.APP)
    threading.Thread(target=run, daemon=True).start()


def do_test(icon, _item):
    jci.log("test notification requested from the tray menu")

    class _It:
        source, title, url = "katadata", "Uji coba notifikasi JCIAlert", ""
        published = None
    fake = jciengine.Alert(_It(), "TEST", "test", set(), 0, "", [])
    show(icon, [fake])


def do_options(icon, _item):
    """Its own thread: Tk owns whatever thread calls mainloop(), and the tray
    must keep answering clicks while the window is open."""
    # ONE WINDOW AT A TIME. Two Options windows means two Tk roots on two
    # threads, each holding its own snapshot of the config - and whichever one
    # you press Apply on last wins. That is how a watchlist typed into window
    # B came back empty: window A, still open with the older snapshot, wrote
    # its stale copy over it. Tk is not thread-safe either way.
    if state.get("options_open"):
        try:
            icon.notify("The Options window is already open.", jci.APP)
        except Exception:
            pass
        return
    state["options_open"] = True

    def run():
        import json

        def save(new_cfg):
            with open(jci.CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(new_cfg, f, ensure_ascii=False, indent=1)
            state["cfg"] = new_cfg
            w = state["watcher"]
            if w:
                # RE-COMPILE. The comment that used to sit here said "config
                # is re-read each tick", and it was wrong: the rules and the
                # watchlist are compiled once, at build time. Updating the
                # dict changed the poll interval and nothing else, so an
                # emptied watchlist still refused GGRM for "not on the
                # watchlist" until the next restart.
                try:
                    w.reconfigure(new_cfg, jci.build_filters(new_cfg))
                except Exception as exc:
                    jci.log(f"saved, but could not apply the new rules "
                            f"live - restart to pick them up: "
                            f"{type(exc).__name__}: {exc}")
            # RULES LIVE UNDER "filters", NOT AT THE TOP LEVEL. This line
            # read new_cfg["rules"], a key that does not exist, so it printed
            # "0 rule(s)" on every save since it was added - including over a
            # config that had a working rule in it the whole time. A
            # diagnostic that always says zero is worse than no diagnostic:
            # it sends whoever reads it after a config that is fine.
            wl = new_cfg.get("watchlist") or []
            rules = (new_cfg.get("filters") or {}).get("rules") or []
            on = sum(1 for r in rules if isinstance(r, dict)
                     and r.get("enabled", True))
            jci.log(f"options saved - watchlist {len(wl)} ticker(s), "
                    f"{on} of {len(rules)} rule(s) enabled")
        try:
            # Re-read from disk. state["cfg"] was loaded at startup and the
            # file may have been edited by hand since (the tray offers a menu
            # item that does exactly that); opening with the stale copy and
            # saving would silently undo those edits.
            try:
                disk, _ = jci.load_config()
                state["cfg"] = disk
            except Exception as exc:
                jci.log(f"could not re-read config, using the loaded copy: "
                        f"{type(exc).__name__}: {exc}")
            w = state["watcher"]
            jciwindow.open_window(state["cfg"], save,
                                  w.sources if w else [])
        except Exception as exc:
            jci.log(f"options window failed: {type(exc).__name__}: {exc}")
            try:
                icon.notify(f"Could not open Options: {exc}", jci.APP)
            except Exception:
                pass
        finally:
            state["options_open"] = False
    threading.Thread(target=run, daemon=True).start()


def do_open(path):
    def handler(_icon, _item):
        target = jci.resolve_data_dir(state["cfg"]) if path is None \
            else os.path.join(jci.HERE, path)
        try:
            os.startfile(target)
        except Exception as exc:
            jci.log(f"could not open {target}: {exc}")
    return handler


def do_quit(icon, _item):
    w = state["watcher"]
    if w:
        w.request_stop()
    jcipopup.close_live()
    icon.visible = False
    icon.stop()
    os._exit(0)


def build_menu():
    # THE DASHBOARD IS FIRST, and it is the double-click action. It is the
    # only item that shows you anything; "Check now" was the default because
    # the tray came before the window existed, and a double-click that
    # silently polls looks broken to anyone who has not read the log.
    return pystray.Menu(
        pystray.MenuItem("News dashboard...", do_dashboard, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Check now", do_check),
        pystray.MenuItem("Status", do_status),
        pystray.MenuItem("Verify sources", do_verify),
        pystray.MenuItem("Diagnostics...", do_doctor),
        pystray.MenuItem("Send test notification", do_test),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Options...", do_options),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            lambda i: "Resume" if (state["watcher"] and state["watcher"].paused)
            else "Pause", do_pause),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Edit config.json by hand", do_open("config.json")),
        pystray.MenuItem("Open folder", do_open(None)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", do_quit),
    )


# -------------------------------------------------------------------- singleton

def already_running():
    """Two watchers sharing one seen.json means duplicate alerts and double the
    request rate. The name is distinct from IDXAlert3's on purpose - the two
    apps are meant to run side by side."""
    if os.name != "nt":
        return False
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\JCIAlertTray")
    return ctypes.windll.kernel32.GetLastError() == 183       # ALREADY_EXISTS


def main():
    """Wrapped, because a --noconsole exe has no stderr.

    Everything below used to be able to end the process with nothing on
    screen: `emiten.json is missing` raised SystemExit into a stream that does
    not exist. On Gill's own PC that reads as "it works"; on the office
    machine it read as "it doesn't show on the tray", which is all the app was
    capable of saying. Now every exit that is not a clean one says why, in a
    dialog and in a file.
    """
    try:
        return _main()
    except SystemExit as exc:
        code = exc.code
        if code in (0, None):
            return 0
        _say_and_die(str(code) if isinstance(code, str)
                     else f"JCIAlert exited with code {code}.")
        return 1
    except BaseException:                        # including KeyboardInterrupt
        import traceback
        _say_and_die(traceback.format_exc())
        return 1


def _say_and_die(detail):
    text = (f"{jci.APP} could not start.\n\n{detail}\n\n"
            f"Folder: {jci.HERE}")
    try:
        jci.log("could not start: " + detail.replace("\n", " ")[:300])
    except Exception:
        pass
    where = jcidoctor.save(text)
    if where:
        text += f"\n\nThis was also written to:\n{where}"
    jcidoctor.tell(f"{jci.APP} could not start", text)


def _main():
    if already_running():
        jci.log(f"another copy of {jci.APP} is already running - exiting")
        jcidoctor.tell(
            jci.APP,
            "JCIAlert is already running.\n\nOnly one copy may run - two "
            "would share one seen.json and double every alert.\n\nIf you "
            "cannot see it, look under the ^ chevron in the tray: Windows "
            "hides new icons there by default.")
        return 0

    cfg, _created = jci.load_config()
    state["cfg"] = cfg
    try:
        if cfg.get("start_with_windows") and jcistartup.supported() \
                and not jcistartup.is_enabled():
            jcistartup.enable()
    except Exception as exc:
        jci.log(f"could not set the startup entry: {exc}")

    data_dir = jci.resolve_data_dir(cfg)
    jci.set_log_dir(os.path.join(data_dir, "logs"))

    # PREFLIGHT, before anything is built. Each of these can end the process,
    # and each one has a sentence a person can act on - which beats finding
    # out by having no tray icon.
    findings = jcidoctor.run(jci.HERE, data_dir, already_running=False)
    bad = jcidoctor.fatal(findings)
    if bad:
        raise SystemExit(jcidoctor.report(findings, jci.HERE, jci.APP))
    _e, _t, _f, sources, _s, pipeline, fetch = jci.build(
        cfg, os.path.join(data_dir, "seen.json"))
    disp = jciengine.Dispatcher(log=jci.log)
    disp.channels.append(alert_channel)
    state["watcher"] = jciengine.Watcher(sources, pipeline, fetch, disp,
                                         cfg, jci.log)
    # EVERY headline, not just the ones that alerted, WITH its status - so
    # "what did it show me" and "what did it see" come out of one file. A
    # popup is gone the moment it is dismissed and tuning needs the history;
    # a second alerts-only CSV recorded a strict subset of this one and was
    # deleted on 2026-09-23. See jcidash.
    state["news"] = os.path.join(data_dir, "news.csv")
    state["watcher"].recorders.append(jcidash.recorder(state["news"]))

    icon = pystray.Icon("JCIAlert", ICONS["ok"], jci.APP, build_menu())
    WORKERS.update({"notification pump": pump, "engine": engine})
    for name in WORKERS:
        spawn(name, icon)
    threading.Thread(target=watchdog, args=(icon,), daemon=True,
                     name="watchdog").start()
    jci.log(f"{jci.APP} started - {len([s for s in sources if s.enabled])} "
            f"sources, every {cfg.get('poll_seconds', 60)}s")
    icon.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
