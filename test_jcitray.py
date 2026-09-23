#!/usr/bin/env python3
"""
test_jcitray.py - the tray, driven through a FAKE pystray.

The lesson this pattern exists for: a headless test that dies at
`import pystray` reports success. A skipped test looks identical to a passing
one, and that is how a broken tray ships. So the toolkit is stubbed into
sys.modules and the REAL code path runs.

MONKEYPATCH RULE (learned on Windows/3.13): when substituting for a CLASS,
substitute a class - never a lambda. Everything faked below is a real class.

What matters here, and what each check is guarding:
  · the tray must NEVER poll. It sets flags; the engine owns the loop.
  · icon colour must follow Watcher.status() with IDXAlert's precedence.
  · alerts must be QUEUED on the poll thread, never drawn on it.
  · the icon must be visually distinct from IDXAlert's - they share a tray.

Run:  python test_jcitray.py
"""

import json
import os
import sys
import tempfile
import threading
import time
import types

failures = []


def check(name, cond, detail=None):
    if cond:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}" + (f"   -> {detail!r}" if detail is not None else ""))


# ----------------------------------------------------------------- fake toolkit

class FakeImage:
    def __init__(self, mode, size, color=None):
        self.mode, self.size, self.color = mode, size, color
        self.ops = []

    @staticmethod
    def new(mode, size, color=None):
        return FakeImage(mode, size, color)


class FakeDraw:
    def __init__(self, img):
        self.img = img

    def rounded_rectangle(self, box, radius=0, fill=None):
        self.img.ops.append(("rounded_rectangle", tuple(box), fill))

    def rectangle(self, box, fill=None):
        self.img.ops.append(("rectangle", tuple(box), fill))

    def ellipse(self, box, fill=None):
        self.img.ops.append(("ellipse", tuple(box), fill))


class FakeMenuItem:
    def __init__(self, text, action=None, default=False):
        self.text, self.action, self.default = text, action, default

    def label(self, icon=None):
        return self.text(icon) if callable(self.text) else self.text


class FakeMenu:
    SEPARATOR = "----"

    def __init__(self, *items):
        self.items = list(items)


class FakeIcon:
    def __init__(self, name, image=None, title=None, menu=None):
        self.name, self.icon, self.title, self.menu = name, image, title, menu
        self.visible = True
        self.notifications = []
        self.stopped = False

    def notify(self, message, title=None):
        self.notifications.append((title, message))

    def run(self):
        self.ran = True

    def stop(self):
        self.stopped = True


pystray = types.ModuleType("pystray")
pystray.Icon, pystray.Menu, pystray.MenuItem = FakeIcon, FakeMenu, FakeMenuItem
PIL = types.ModuleType("PIL")
ImageMod = types.ModuleType("PIL.Image")
ImageMod.new = FakeImage.new
DrawMod = types.ModuleType("PIL.ImageDraw")
DrawMod.Draw = FakeDraw
PIL.Image, PIL.ImageDraw = ImageMod, DrawMod
sys.modules.update({"pystray": pystray, "PIL": PIL,
                    "PIL.Image": ImageMod, "PIL.ImageDraw": DrawMod})

import jcitray as TR                                    # noqa: E402
import jciengine as E                                   # noqa: E402
import jcisource as S                                   # noqa: E402


class FakeWatcher:
    def __init__(self, status="green"):
        self._status = status
        self.paused = False
        self.checks = 0
        self.stops = 0
        self.last_poll_at = None
        self.state = {}

    def status(self):
        return "amber" if self.paused else self._status

    def request_check(self):
        self.checks += 1

    def request_stop(self):
        self.stops += 1

    def set_paused(self, v):
        self.paused = bool(v)

    def stats(self):
        return {"our_lag": 61.0, "sources": {
            "katadata": {"ok": True, "items": 25, "stale": False, "error": ""},
            "iqplus-stock": {"ok": False, "items": 0, "stale": False,
                             "error": "TimeoutError: connect timed out"}}}


def main():
    print("\n== the icon is tellable apart from IDXAlert's ==")
    img = TR.make_icon((1, 2, 3, 255))
    shapes = [o[0] for o in img.ops]
    check("it is drawn, not a blank square", len(img.ops) >= 4, shapes)
    check("the glyph is stacked lines, not IDXAlert's bar chart",
          shapes.count("rounded_rectangle") >= 5 and "rectangle" not in shapes,
          shapes)
    check("all four status colours exist",
          set(TR.ICONS) == {"ok", "paused", "stale", "error"}, sorted(TR.ICONS))
    check("they are four DIFFERENT images",
          len({id(v) for v in TR.ICONS.values()}) == 4)

    print("\n== icon follows Watcher.status(), with IDXAlert's precedence ==")
    icon = FakeIcon("t")
    for status, want in (("green", "ok"), ("red", "error"),
                         ("blue", "stale"), ("amber", "paused")):
        TR.state["watcher"] = FakeWatcher(status)
        TR.refresh_icon(icon)
        check(f"{status} -> {want} icon", icon.icon is TR.ICONS[want], status)
    w = FakeWatcher("red")
    w.paused = True
    TR.state["watcher"] = w
    TR.refresh_icon(icon)
    check("paused outranks error, as in IDXAlert",
          icon.icon is TR.ICONS["paused"])

    print("\n== the tray never polls ==")
    w = FakeWatcher()
    TR.state["watcher"] = w
    TR.do_check(icon, None)
    check("'Check now' sets a request, it does not fetch",
          w.checks == 1, w.checks)
    TR.do_pause(icon, None)
    check("Pause toggles on", w.paused is True)
    TR.do_pause(icon, None)
    check("and back off", w.paused is False)
    check("the icon updates with it", icon.icon is TR.ICONS["ok"])
    src = [n for n in dir(TR) if n.startswith("do_")]
    body = open(TR.__file__, encoding="utf-8").read()
    check("no menu handler calls poll_once", "poll_once" not in body)
    check("Options opens on its own thread - Tk owns whoever calls mainloop",
          "def do_options" in body and "threading.Thread" in
          body[body.index("def do_options"):body.index("def do_open")])
    check("nor constructs a Watcher outside main()",
          body.count("jciengine.Watcher(") == 1, body.count("jciengine.Watcher("))

    print("\n== alerts are queued, never drawn on the poll thread ==")
    while not TR.outbox.empty():
        TR.outbox.get()
    TR.state["alerts"] = 0
    payload = object()
    TR.alert_channel(payload)
    check("the channel only enqueues", TR.outbox.qsize() == 1)
    check("and counts it", TR.state["alerts"] == 1)
    check("the queued object is the payload itself",
          TR.outbox.get() is payload)

    print("\n== Status opens a window, because the balloon never showed ==")
    # Windows 10/11 truncates a tray balloon to a line or two and drops it
    # outright under Focus Assist. Fifteen lines of source health never had a
    # chance. The test is now "does it open the Status tab", not "does it
    # notify" - a notification is exactly what did not work.
    import jcidashwindow as DW
    real_dash = DW.open_window
    calls, gate = [], threading.Event()

    def fake_dash(load, stats=None, open_url=None, tab="News", *a, **kw):
        calls.append({"tab": tab, "rows": list(load() or []),
                      "stats": stats() if stats else None})
        gate.wait(3)
        return None

    DW.open_window = fake_dash
    TR.state["watcher"] = FakeWatcher()
    TR.state["dash_open"] = False
    TR.state["news"] = os.path.join(tempfile.mkdtemp(), "news.csv")
    try:
        TR.do_status(icon, None)
        for _ in range(300):
            if calls:
                break
            time.sleep(0.01)
        check("Status opens the window on the Status tab",
              calls and calls[0]["tab"] == "Status", calls)
        check("and the window is handed the live source health",
              calls and "katadata" in (calls[0]["stats"] or {}).get("sources", {}),
              calls[0]["stats"] if calls else None)
        text = DW.status_text(calls[0]["stats"]) if calls else ""
        check("the text lists every source",
              "katadata" in text and "iqplus-stock" in text, text)
        check("and shows the failure reason",
              "Timeout" in text or "timed out" in text, text)
        TR.do_dashboard(icon, None)
        time.sleep(0.1)
        check("a second window is refused while one is open", len(calls) == 1,
              len(calls))
    finally:
        gate.set()
        time.sleep(0.1)
        DW.open_window = real_dash
    check("before the engine exists the status text says so, not raises",
          "not started" in DW.status_text(None))

    print("\n== the menu ==")
    menu = TR.build_menu()
    labels = [i.label() if isinstance(i, FakeMenuItem) else i
              for i in menu.items]
    for want in ("Check now", "Status", "Verify sources",
                 "News dashboard...", "Options...", "Quit"):
        check(f"has {want!r}", want in labels, labels)
    TR.state["watcher"] = FakeWatcher()
    dyn = [i for i in menu.items
           if isinstance(i, FakeMenuItem) and callable(i.text)]
    check("Pause/Resume is dynamic", dyn and dyn[0].label(icon) == "Pause",
          [d.label(icon) for d in dyn])
    TR.state["watcher"].paused = True
    check("and flips when paused", dyn[0].label(icon) == "Resume")
    check("the dashboard is first in the menu", labels[0].startswith("News"),
          labels[:3])
    check("'News dashboard...' is the default double-click action",
          any(getattr(i, "default", False) and "dashboard" in str(i.label())
              for i in menu.items if isinstance(i, FakeMenuItem)),
          [i.label() for i in menu.items
           if isinstance(i, FakeMenuItem) and getattr(i, "default", False)])

    print("\n== the dashboard can do everything the tray menu can ==")
    TR.state["watcher"] = FakeWatcher()
    acts = TR.tray_actions(icon)
    for want in ("Check now", "Verify sources", "Send test notification",
                 "Options...", "Open folder"):
        check(f"the window is handed {want!r}", want in acts, sorted(acts))
    check("Pause is offered by its current name",
          "Pause" in acts or "Resume" in acts, sorted(acts))
    check("and quitting is spelled out, not just 'Quit'",
          any(k.startswith("Quit") for k in acts), sorted(acts))
    check("every action is callable", all(callable(v) for v in acts.values()))

    print("\n== quit stops the engine before killing the icon ==")
    w = FakeWatcher()
    TR.state["watcher"] = w
    import jcipopup
    closed = {"n": 0}
    real_close = jcipopup.close_live
    jcipopup.close_live = lambda: closed.__setitem__("n", closed["n"] + 1)
    real_exit = TR.os._exit
    TR.os._exit = lambda code: None
    try:
        TR.do_quit(icon, None)
        check("the watcher is asked to stop", w.stops == 1, w.stops)
        check("the popup is closed so it cannot outlive the process",
              closed["n"] == 1)
        check("and the icon is stopped", icon.stopped is True)
    finally:
        jcipopup.close_live = real_close
        TR.os._exit = real_exit

    print("\n== saving Options re-compiles the rules, not just the dict ==")
    import jciwindow as JW0
    real_open0 = JW0.open_window
    real_load0 = TR.jci.load_config
    real_bf = TR.jci.build_filters
    seen_cfg = []

    class RecordingWatcher(FakeWatcher):
        def __init__(self):
            FakeWatcher.__init__(self)
            self.reconfigured = []
            self.sources = []

        def reconfigure(self, cfg, filters=None):
            self.reconfigured.append((dict(cfg), filters))

    rw = RecordingWatcher()
    TR.state["watcher"] = rw
    TR.state["options_open"] = False
    TR.jci.load_config = lambda *a, **k: ({"watchlist": ["BBCA"]}, False)
    TR.jci.build_filters = lambda cfg: seen_cfg.append(cfg) or "COMPILED"
    tmpcfg = os.path.join(tempfile.mkdtemp(), "config.json")
    real_path = TR.jci.CONFIG_PATH
    TR.jci.CONFIG_PATH = tmpcfg

    def save_now(cfg, save, sources=None, *a, **kw):
        save({"watchlist": [], "poll_seconds": 90})
        return None

    JW0.open_window = save_now
    try:
        TR.do_options(FakeIcon("JCIAlert"), None)
        for _ in range(300):
            if rw.reconfigured:
                break
            time.sleep(0.01)
        check("the watcher is reconfigured, not just cfg.update()d",
              len(rw.reconfigured) == 1, rw.reconfigured)
        check("with freshly compiled rules from the saved config",
              rw.reconfigured and rw.reconfigured[0][1] == "COMPILED",
              rw.reconfigured)
        check("compiled from what was SAVED, not what was loaded",
              seen_cfg and seen_cfg[0].get("watchlist") == [], seen_cfg)
        check("and the file on disk got it too",
              json.load(open(tmpcfg, encoding="utf-8"))["poll_seconds"] == 90)

    finally:
        JW0.open_window = real_open0
        TR.jci.load_config = real_load0
        TR.jci.build_filters = real_bf
        TR.jci.CONFIG_PATH = real_path
        TR.state["options_open"] = False

    # The save line counted new_cfg["rules"], a key that does not exist -
    # rules live under "filters". It printed "0 rule(s)" over a config that
    # had a working rule in it, every save, for a day. Count against a REAL
    # config, never an invented dict: see feedback-contracts.
    import jcioptions as _O
    real_cfg = _O.default_config()
    lines_s = []
    rw2 = RecordingWatcher()
    TR.state["watcher"] = rw2
    TR.state["options_open"] = False
    TR.jci.load_config = lambda *a, **k: (real_cfg, False)
    TR.jci.build_filters = lambda cfg: "COMPILED"
    TR.jci.CONFIG_PATH = os.path.join(tempfile.mkdtemp(), "config.json")
    real_log2 = TR.jci.log
    TR.jci.log = lines_s.append
    JW0.open_window = lambda cfg, save, sources=None, *a, **kw: save(real_cfg)
    try:
        TR.do_options(FakeIcon("JCIAlert"), None)
        for _ in range(300):
            if lines_s:
                break
            time.sleep(0.01)
        said = " ".join(lines_s)
        want = len(real_cfg["filters"]["rules"])
        check("the save line counts the rules that are really there",
              f"of {want} rule(s)" in said and want > 0, said)
        check("and does not report zero over a working config",
              "0 of 0" not in said, said)
    finally:
        TR.jci.log = real_log2
        JW0.open_window = real_open0
        TR.jci.load_config = real_load0
        TR.jci.build_filters = real_bf
        TR.jci.CONFIG_PATH = real_path
        TR.state["options_open"] = False
    print("\n== only one Options window, and it reads the file not memory ==")
    import jciwindow
    import threading as _th
    real_open = jciwindow.open_window
    real_load = TR.jci.load_config
    opened, gate = [], _th.Event()

    def fake_open(cfg, save, sources=None, *a, **kw):
        opened.append(dict(cfg))
        gate.wait(3)                       # hold the window open
        return None

    TR.state["cfg"] = {"watchlist": ["STALE"]}
    TR.state["watcher"] = None
    TR.state["options_open"] = False
    jciwindow.open_window = fake_open
    TR.jci.load_config = lambda *a, **k: ({"watchlist": ["ONDISK"]}, False)
    try:
        icon = FakeIcon("JCIAlert")
        TR.do_options(icon, None)
        for _ in range(300):               # let the thread reach fake_open
            if opened:
                break
            time.sleep(0.01)
        TR.do_options(icon, None)          # the second click
        time.sleep(0.1)
        check("a second click does not open a second window", len(opened) == 1,
              len(opened))
        check("and the user is told why",
              any("already open" in str(m) for m in icon.notifications),
              icon.notifications)
        check("the window is handed what is on disk, not the startup copy",
              opened and opened[0].get("watchlist") == ["ONDISK"],
              opened[0] if opened else None)
    finally:
        gate.set()
        time.sleep(0.1)
        jciwindow.open_window = real_open
        TR.jci.load_config = real_load
    check("and the flag clears so Options can be opened again",
          TR.state["options_open"] is False)

    print("\n== NOTHING may kill the notification pump ==")
    # Reported 2026-09-15: "notification not showing up after some time
    # running". The pump ran its body bare, so one exception out of
    # refresh_icon or show ended the only thread that draws anything - alerts
    # then queued for ever while the app looked perfectly alive.
    import jciview as _V
    lines = []
    real_log = TR.jci.log
    TR.jci.log = lines.append
    real_rows = _V.rows_for
    real_show = TR.jcipopup.show
    TR.state["cfg"] = {}
    TR.state["watcher"] = FakeWatcher()
    TR.state["pump_error"] = ""
    TR.state["shown"] = None
    drawn = []
    try:
        # (a) a payload that cannot be turned into rows
        _V.rows_for = lambda p: (_ for _ in ()).throw(ValueError("bad row"))
        check("show() survives a malformed payload instead of raising",
              TR.show(FakeIcon("JCIAlert"), [object()]) is False)
        check("and says which layer failed",
              any("could not build popup rows" in l for l in lines), lines)

        # (b) the loop itself, driven one cycle at a time
        _V.rows_for = real_rows
        TR.jcipopup.show = lambda rows, opts=None: drawn.append(rows)

        class Exploding(FakeIcon):
            """Windows takes the tray handle away and the next assignment
            throws. Armed AFTER construction, or __init__'s own assignment
            goes off in the test's face rather than the pump's."""
            armed = False

            @property
            def icon(self):
                return self._icon

            @icon.setter
            def icon(self, v):
                if self.armed:
                    raise RuntimeError("tray handle is gone")
                self._icon = v

        boom = Exploding("JCIAlert")
        boom.visible = True
        boom.armed = True
        lines.clear()
        t = threading.Thread(target=TR.pump, args=(boom,), daemon=True)
        t.start()
        time.sleep(1.6)
        check("the pump thread is STILL ALIVE after refresh_icon throws",
              t.is_alive())
        check("and it logged the failure once, not once per cycle",
              sum("pump failed" in l for l in lines) == 1, lines)
        check("naming the error rather than swallowing it",
              any("tray handle is gone" in l for l in lines), lines)
        check("and it stamps pump_at, so a watchdog can tell wedged from quiet",
              TR.state["pump_at"] > 0)
    finally:
        TR.jci.log = real_log
        _V.rows_for = real_rows
        TR.jcipopup.show = real_show

    print("\n== the tray icon is only written when it CHANGES ==")
    TR.state["shown"] = None
    quiet = FakeIcon("JCIAlert")
    check("the first call paints", TR.refresh_icon(quiet) is True)
    check("the second call does not", TR.refresh_icon(quiet) is False)
    TR.state["watcher"].paused = True
    check("but a status change does", TR.refresh_icon(quiet) is True)

    print("\n== a worker that dies anyway is restarted ==")
    # A guard cannot catch what kills the thread itself. The cost of being
    # wrong is total and silent, so it is made recoverable.
    lines2 = []
    TR.jci.log = lines2.append
    dead = FakeIcon("JCIAlert")
    ran = []
    try:
        TR.WORKERS.clear()
        TR.WORKERS["notification pump"] = lambda _i: ran.append(1)
        TR.state["threads"] = {}
        TR.state["watcher"] = FakeWatcher()
        TR.state["pump_at"] = time.time()
        TR.spawn("notification pump", dead)
        time.sleep(0.05)
        check("the worker ran and finished", ran == [1], ran)
        TR.watchdog(dead, interval=0.05, forever=False)
        time.sleep(0.05)
        check("the watchdog noticed it was gone and started it again",
              len(ran) == 2, ran)
        check("and said so in the log",
              any("had died - restarting" in l for l in lines2), lines2)

        TR.state["pump_at"] = time.time() - 900
        lines2.clear()
        TR.watchdog(dead, interval=0.05, forever=False)
        check("a pump that is alive but has not cycled in 15 min is reported",
              any("not completed a cycle" in l for l in lines2), lines2)

        lines2.clear()
        TR.state["watcher"]._stop = True
        TR.watchdog(dead, interval=0.05, forever=False)
        check("but nothing is resurrected once the app is quitting",
              lines2 == [], lines2)
    finally:
        TR.jci.log = real_log
        TR.WORKERS.clear()

    print("\n== the popup is told which corner to appear in ==")
    import jcipopup as _P
    real_show = _P.show
    seen_opts = []
    _P.show = lambda items, opts=None: seen_opts.append(dict(opts or {}))

    class _It:
        source, title, url, feed_category = "katadata", "TLKM naik", "", ""
        published = None
    alert = [E.Alert(_It(), "TLKM", "r", set(), 1, "Infrastruktur", [])]

    TR.state["cfg"] = {"max_visible": 4, "duration_seconds": 0,
                       "corner": "top left"}
    try:
        TR.show(FakeIcon("JCIAlert"), alert)
        check("the corner reaches the popup", seen_opts
              and seen_opts[0].get("corner") == "top left", seen_opts)
        TR.state["cfg"] = {}
        TR.show(FakeIcon("JCIAlert"), alert)
        check("and a config that has never seen the setting still works",
              seen_opts[-1].get("corner") == "bottom right", seen_opts[-1])
    finally:
        _P.show = real_show

    print("\n== the status colours are a shared language, not a theme ==")
    # Asked for and then corrected on 2026-09-10: the ACCENT is orange, but
    # green/amber/blue/red mean the same thing in both apps and must not be
    # restyled - a healthy tray that is not green reads as a warning.
    check("healthy is green", TR.ICONS["ok"] is not TR.ICONS["error"]
          and _px(TR.ICONS["ok"])[1] > _px(TR.ICONS["ok"])[0],
          _px(TR.ICONS["ok"]))
    check("failing is red", _px(TR.ICONS["error"])[0]
          > _px(TR.ICONS["error"])[1], _px(TR.ICONS["error"]))
    check("stale is blue", _px(TR.ICONS["stale"])[2]
          > _px(TR.ICONS["stale"])[0], _px(TR.ICONS["stale"]))
    check("paused is amber", _px(TR.ICONS["paused"])[0]
          > _px(TR.ICONS["paused"])[2], _px(TR.ICONS["paused"]))
    check("every status has its own colour",
          len({_px(i) for i in TR.ICONS.values()}) == 4)
    check("but the popup accent is orange, so the two apps look different",
          "#f5a94b" in open("jcipopup.py", encoding="utf-8").read())

    # The popup had green in three places and they were found one screenshot
    # at a time: the accent, then the headline, then the border. Sweep the
    # whole palette instead of naming them.
    import re as _re
    pal = _re.findall(r"#([0-9a-fA-F]{6})",
                      open("jcipopup.py", encoding="utf-8").read())
    greens = [h for h in pal
              if (lambda r, g, b: g > r + 25 and g > b + 15 and g > 80)(
                  int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))]
    check("and NOTHING in the popup palette is still green", greens == [],
          greens)
    check("the headline is a paler tint than the ticker above it, not the "
          "same value - two lines in one orange and the row has no hierarchy",
          "#f7cf9a" in open("jcipopup.py", encoding="utf-8").read())

    print("\n== the singleton mutex is JCIAlert's, not IDXAlert's ==")
    check("distinct mutex name so both apps can run side by side",
          "JCIAlertTray" in body_all() and "IDXAlert3Tray" not in body_all())
    check("distinct Run key name",
          'NAME = "JCIAlert"' in open("jcistartup.py", encoding="utf-8").read())

    print("\n%d checks failed" % len(failures) if failures else "\nall checks passed")
    return 1 if failures else 0


def _px(img):
    """The fill the badge was drawn with - the FIRST rounded_rectangle, which
    is the background plate. Read off the recorded draw ops rather than the
    source, so a colour changed anywhere in make_icon is still caught."""
    return tuple(img.ops[0][2][:3])


def body_all():
    return open(TR.__file__, encoding="utf-8").read()


if __name__ == "__main__":
    raise SystemExit(main())
