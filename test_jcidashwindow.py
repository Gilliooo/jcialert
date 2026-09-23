#!/usr/bin/env python3
"""
The dashboard window, driven with a stub toolkit.

Same rule as test_jciwindow: a headless skip looks exactly like a pass, so the
toolkit is faked and the real code runs. Every fake widget is a CLASS, never a
lambda - a lambda has no attributes to read and Windows/3.13 reads them.

The Treeview stub is deliberately literal: it stores the value tuples it was
handed, so a test can assert on the TEXT a user would see rather than on the
row objects the code happens to be holding.
"""

import os
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jcidash as D                                        # noqa: E402
import jcidashwindow as DW                                 # noqa: E402

failures = []
log = []
WIB = timezone(timedelta(hours=7))


def check(name, cond, detail=None):
    if cond:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}" + (f"   -> {detail!r}" if detail is not None else ""))


class W:
    def __init__(self, master=None, **kw):
        self.master, self.kw = master, dict(kw)
        self.children, self.packs, self.bindings = [], [], {}
        self.text_content = ""
        log.append(("create", type(self).__name__, kw.get("text", "")))
        if isinstance(master, W):
            master.children.append(self)

    def pack(self, **kw):
        self.packs.append(kw)
        log.append(("pack", type(self).__name__, kw.get("side", ""),
                    kw.get("expand", False)))

    def bind(self, seq, fn):
        self.bindings[seq] = fn

    def config(self, **kw):
        self.kw.update(kw)

    configure = config

    def destroy(self):
        log.append(("destroy", type(self).__name__, ""))

    def title(self, t=None):
        self.kw["title"] = t

    def protocol(self, name, fn):
        self.bindings[name] = fn

    def geometry(self, g=None):
        self.kw["geometry"] = g

    def minsize(self, *a):
        self.kw["minsize"] = a

    def mainloop(self):
        log.append(("mainloop", "", ""))

    # Text
    def insert(self, index, value=None):
        self.text_content += str("" if value is None else value)

    def get(self, *a):
        return self.text_content

    def delete(self, *a):
        self.text_content = ""


class Button(W):
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self.command = kw.get("command")


class Treeview(W):
    """Item ids are STABLE and unique, exactly as Tk's are. An earlier version
    of this stub numbered them by position, so deleting row 0 renumbered every
    other row and the clear-then-redraw left half the table behind - a failure
    the real widget cannot have. A stub that is wrong in the direction of
    "harder than reality" is fine; this one was wrong in the other direction."""

    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self._by_id, self._order = {}, []
        self._tags = {}
        self._n = 0
        self.headings, self.commands, self.tag_styles = {}, {}, {}
        self._sel = ()

    @property
    def rows(self):
        return [self._by_id[i] for i in self._order]

    def heading(self, key, text="", command=None):
        self.headings[key] = text
        if command is not None:
            self.commands[key] = command

    def column(self, key, **kw):
        pass

    def insert(self, parent, where, values=(), tags=()):
        self._n += 1
        iid = f"I{self._n}"
        self._by_id[iid] = list(values)
        self._tags[iid] = tuple(tags)
        self._order.append(iid)
        return iid

    def tag_configure(self, name, **kw):
        self.tag_styles[name] = kw

    def tags_of(self, i):
        return self._tags[self._order[i]]

    def yview(self, *a):
        pass

    def get_children(self, *a):
        return list(self._order)

    def delete(self, *iids):
        for iid in iids:
            if iid in self._by_id:
                del self._by_id[iid]
                self._order.remove(iid)

    def selection(self):
        return self._sel

    def select(self, i):
        self._sel = (self._order[i],)


class Var:
    def __init__(self, v):
        self._v = v
        self._traces = []

    def get(self):
        return self._v

    def set(self, v):
        self._v = v
        for fn in self._traces:
            fn()

    def trace_add(self, _mode, fn):
        self._traces.append(lambda *a: fn())


def make_tk():
    tk = types.SimpleNamespace()
    for name in ("Tk", "Frame", "Label", "Entry", "Text", "Checkbutton"):
        setattr(tk, name, type(name, (W,), {}))
    tk.Button = Button
    # A Var with no master binds to tkinter._default_root - dead after a
    # window is closed with the X. That is the blank-Options bug; the stub
    # refuses to reproduce the conditions for it.
    def _var(cast):
        def make(master=None, value=None):
            if master is None:
                raise AssertionError("Tk variable created with no master")
            return Var(cast(value))
        return make

    tk.StringVar = _var(lambda v: "" if v is None else v)
    tk.BooleanVar = _var(bool)
    ttk = types.SimpleNamespace()

    class Notebook(W):
        def __init__(self, master=None, **kw):
            super().__init__(master, **kw)
            self.tabs, self.selected = [], None

        def add(self, child, text=""):
            self.tabs.append(text)
            self._by_widget = getattr(self, "_by_widget", {})
            self._by_widget[id(child)] = text

        def select(self, child):
            self.selected = self._by_widget[id(child)]

    ttk.Notebook = Notebook
    ttk.Treeview = Treeview
    ttk.Combobox = type("Combobox", (W,), {})
    ttk.Scrollbar = type("Scrollbar", (W,), {"set": lambda self, *a: None})
    return tk, ttk


def widgets(node, cls, found=None):
    found = [] if found is None else found
    for c in node.children:
        if isinstance(c, cls):
            found.append(c)
        widgets(c, cls, found)
    return found


def row(title, status="alert", ticker="BBCA", source="katadata", reason="",
        rule="My coverage", when="2026-09-10 10:00:00", url="http://x/1"):
    return {"ts_wib": when, "status": status, "ticker": ticker,
            "sector": "Financials", "source": source, "title": title,
            "rule": rule, "categories": "earnings", "reason": reason,
            "match_rule": "paren", "confidence": "1.00", "feed_category": "",
            "cluster": "7", "url": url, "ts_utc": ""}


ROWS = [row("ASII rights issue", ticker="ASII", source="kontan-investasi",
            url="http://x/asii"),
        row("BBCA laba naik", url="http://x/bbca"),
        row("Promo CSR BMRI", "dropped", "BMRI", reason="excluded: csr",
            rule=""),
        row("Daftar 10 saham cuan", "dropped", "", reason="looks like a roundup",
            rule="")]


def build(rows=None, stats=None, tab="News", opened=None, actions=None,
          opened_path=None):
    log.clear()
    tk, ttk = make_tk()
    root = DW.open_window(lambda: list(rows if rows is not None else ROWS),
                          (lambda: stats) if stats is not None else None,
                          (opened.append if opened is not None else None),
                          tab, actions, opened_path, tk, ttk)
    return root


def tree_of(root):
    return widgets(root, Treeview)[0]


def var_named(root, label):
    """The Var behind the control whose neighbouring Label says `label`."""
    kids = widgets(root, W)
    for i, w in enumerate(kids):
        if str(w.kw.get("text")) == label:
            for later in kids[i + 1:]:
                if "textvariable" in later.kw:
                    return later.kw["textvariable"]
    return None


def checkbox(root, label):
    for w in widgets(root, W):
        if label in str(w.kw.get("text")) and "variable" in w.kw:
            return w.kw["variable"]
    return None


def main():
    print("\n== the window builds, bottom bar first ==")
    root = build()
    check("it ran to mainloop", ("mainloop", "", "") in log)
    packs = [e for e in log if e[0] == "pack"]
    bar = next(i for i, e in enumerate(packs) if e[2] == "bottom")
    grow = next(i for i, e in enumerate(packs) if e[3] is True)
    check("the button bar is packed before anything that expands",
          bar < grow, (bar, grow))
    tree = widgets(root, Treeview)[0]
    check("both tabs exist",
          widgets(root, W) and any("News" in nbt for nbt in _tabs(root)), _tabs(root))
    check("and Status is one of them", "Status" in _tabs(root), _tabs(root))

    print("\n== the default face is the filtered one ==")
    titles = [r[5] for r in tree.rows]
    check("only the alerted headlines are drawn",
          titles == ["ASII rights issue", "BBCA laba naik"], titles)
    bars = [w for w in widgets(root, W) if "showing" in str(w.kw.get("text"))]
    check("the count says what is hidden as well as what is shown",
          bars and "showing 2 of 4" in bars[0].kw["text"],
          bars[0].kw.get("text") if bars else None)

    print("\n== show everything is one click, and brings the rest back ==")
    boxes = [w for w in widgets(root, W)
             if "Show everything" in str(w.kw.get("text"))]
    check("the switch is on the toolbar, not in a config file", len(boxes) == 1)
    boxes[0].kw["variable"].set(True)
    titles = [r[5] for r in tree.rows]
    check("every recorded headline is now drawn", len(titles) == 4, titles)
    check("including one that named no ticker at all",
          "Daftar 10 saham cuan" in titles, titles)

    print("\n== the Why column means something in both states ==")
    by_title = {r[5]: r for r in tree.rows}
    check("an alert shows the rule that accepted it",
          by_title["BBCA laba naik"][4] == "My coverage",
          by_title["BBCA laba naik"])
    check("a rejection shows the reason, in the same column",
          by_title["Promo CSR BMRI"][4] == "excluded: csr",
          by_title["Promo CSR BMRI"])
    check("and the tally under the table names the big rejections",
          any("looks like a roundup" in str(w.kw.get("text"))
              for w in widgets(root, W)))

    print("\n== searching narrows the drawn rows, live ==")
    root = build()
    tree = widgets(root, Treeview)[0]
    entries = [w for w in widgets(root, W) if "textvariable" in w.kw]
    q = entries[0].kw["textvariable"]
    q.set("rights")
    check("typing redraws without a button press",
          [r[5] for r in tree.rows] == ["ASII rights issue"],
          [r[5] for r in tree.rows])
    q.set("")
    check("and clearing it puts them back", len(tree.rows) == 2, tree.rows)

    print("\n== double-clicking a row opens that story, not another ==")
    opened = []
    root = build(opened=opened)
    tree = widgets(root, Treeview)[0]
    tree.select(1)                      # the second drawn row
    tree.bindings["<Double-1>"]()
    check("the URL opened is the selected row's",
          opened == ["http://x/bbca"], opened)
    check("Return does the same thing as a double click",
          "<Return>" in tree.bindings)

    print("\n== Refresh re-reads the file, it does not redraw a snapshot ==")
    live = list(ROWS)
    root = build(rows=live)
    tree = widgets(root, Treeview)[0]
    before = len(tree.rows)
    live.append(row("New story lands", ticker="TLKM"))
    btn = {b.kw.get("text"): b for b in widgets(root, Button)}
    btn["Refresh"].command()
    check("a headline appended after the window opened shows up",
          len(tree.rows) == before + 1, (before, len(tree.rows)))

    print("\n== the Status tab says what the balloon could not ==")
    stats = {"paused": False, "status": "blue", "our_lag": 3.0,
             "last_poll_at": datetime(2026, 9, 10, 3, 0, tzinfo=timezone.utc),
             "sources": {
                 "katadata": {"ok": True, "items": 12, "stale": False,
                              "newest": "2026-09-10T09:55:00", "error": ""},
                 "iqplus-stock": {"ok": False, "items": 0, "stale": False,
                                  "newest": None,
                                  "error": "TimeoutError: [WinError 10060]"}}}
    text = DW.status_text(stats)
    check("every source is named", "katadata" in text and "iqplus-stock" in text)
    check("the failing one is marked, not just listed", "FAILING" in text, text)
    check("with the whole error, untruncated",
          "WinError 10060" in text, text)
    check("the last check is shown in WIB", "10:00:00 WIB" in text, text)
    check("a paused engine says so first",
          DW.status_text(dict(stats, paused=True)).startswith("PAUSED"))
    check("and before the engine exists it explains itself",
          "not started" in DW.status_text(None))

    print("\n== NEVER TRIED is not OK, and three other states ==")
    # Reported 2026-09-10: Status showed `ok  iqplus-stock  0 items  newest -`
    # for a feed that had not answered in three days. SourceState.ok starts
    # True so an unpolled source does not paint the tray red - which made
    # "healthy" and "has not had its turn" the same word.
    five = {"paused": False, "our_lag": 3.0, "last_poll_at": None,
            "disabled": ["liputan6-bisnis"],
            "sources": {
                "fine": {"ok": True, "items": 25, "stale": False,
                         "newest": "2026-09-10T14:51:00+07:00", "error": "",
                         "attempts": 5, "failures": 0, "waiting": 0},
                "untried": {"ok": True, "items": 0, "stale": False,
                            "newest": None, "error": "", "attempts": 0,
                            "failures": 0, "waiting": 0},
                "old": {"ok": True, "items": 9, "stale": True,
                        "newest": "2026-07-04T09:00:00+07:00", "error": "",
                        "attempts": 5, "failures": 0, "waiting": 0},
                "broken": {"ok": False, "items": 0, "stale": False,
                           "newest": None, "error": "TimeoutError: read",
                           "attempts": 5, "failures": 1, "waiting": 0},
                "resting": {"ok": False, "items": 0, "stale": False,
                            "newest": None, "error": "TimeoutError: connect",
                            "attempts": 9, "failures": 6, "waiting": 2820}}}
    t5 = DW.status_text(five)
    rows = {l.split()[1]: l.split()[0] + " " + (l.split()[1] if l.split()[0]
            in ("not", "BACKED") else "") for l in t5.splitlines() if "items" in l}
    check("a source nobody has fetched says so", "not tried   untried" in t5, t5)
    check("and is NOT called ok",
          "ok          untried" not in t5, t5)
    check("a source that answered is ok", "ok          fine" in t5, t5)
    check("one with nothing fresh is STALE", "STALE       old" in t5, t5)
    check("one that errored is FAILING", "FAILING     broken" in t5, t5)
    check("one in cooldown is BACKED OFF, not merely failing",
          "BACKED OFF  resting" in t5, t5)
    check("and says how long the wait is, in minutes not epoch seconds",
          "next try in 47 min" in t5, t5)
    check("with the failure count that earned it",
          "after 6 failures" in t5, t5)

    print("\n== a healthy engine with nothing reaching the screen ==")
    # Every source green while the pump is dead is exactly the 2026-09-15 bug,
    # and until now Status had no reading for it at all.
    stuck = dict(five, queued=14, pump_age=900)
    t6 = DW.status_text(stuck)
    check("a wedged pump is called out, in minutes",
          "!!" in t6 and "15 min" in t6,
          [l for l in t6.splitlines() if "!!" in l])
    check("with the backlog it is sitting on", "14 alert(s)" in t6, t6)
    check("a healthy pump with a small queue just says so",
          "1 alert(s) waiting to be drawn"
          in DW.status_text(dict(five, queued=1, pump_age=3)),
          DW.status_text(dict(five, queued=1, pump_age=3)))
    check("and a healthy pump with an empty queue says nothing about it",
          "waiting" not in DW.status_text(dict(five, queued=0, pump_age=3)))
    check("a stats dict from before this existed still renders",
          "Running" in DW.status_text(five), five.get("queued"))

    print("\n== the header says what the colour MEANS ==")
    check("the internal status word never reaches the reader",
          "Running - red" not in t5 and "- red" not in t5, t5.splitlines()[0])
    check("it counts the sources that are not answering",
          t5.splitlines()[0] == "Running - 2 of 5 sources are not answering",
          t5.splitlines()[0])
    allgood = {"sources": {"a": {"ok": True, "items": 3, "attempts": 1,
                                 "newest": "2026-09-10T14:00:00+07:00"}}}
    check("a healthy engine says so in words",
          "all 1 sources answered" in DW.status_text(allgood),
          DW.status_text(allgood).splitlines()[0])
    check("'pending' is replaced by what it is waiting FOR",
          "second poll" in DW.status_text(allgood),
          DW.status_text(allgood))

    print("\n== the timestamps are wall clocks, not machine strings ==")
    check("the +07:00 suffix and the T are gone",
          "2026-09-10 14:51" in t5 and "T14:51" not in t5, t5)
    check("a source with no timestamp shows a dash, not None",
          "newest -" in t5, t5)

    print("\n== unticked sources are named, not just alluded to ==")
    check("the note lists them", "liputan6-bisnis" in t5, t5)
    check("and the note is absent when nothing is switched off",
          "Switched off" not in DW.status_text(allgood))

    root = build(stats=stats, tab="Status")
    nb = [w for w in widgets(root, W) if hasattr(w, "tabs")][0]
    check("the Status menu item lands on the Status tab",
          nb.selected == "Status", nb.selected)
    texts = [w for w in widgets(root, W) if "iqplus-stock" in w.text_content]
    check("and the tab is actually filled in", len(texts) == 1)

    print("\n== the table scrolls ==")
    root = build()
    bars = [w for w in widgets(root, W)
            if type(w).__name__ == "Scrollbar"]
    check("a vertical scrollbar exists", len(bars) == 1, len(bars))
    check("and it is packed against the right edge, filling y",
          bars and bars[0].packs[0].get("side") == "right"
          and bars[0].packs[0].get("fill") == "y", bars[0].packs if bars else None)

    print("\n== clicking a heading sorts, clicking again reverses ==")
    root = build()
    tree = tree_of(root)
    check("it opens newest-first without being asked",
          [r[0] for r in tree.rows] == sorted([r[0] for r in tree.rows],
                                              reverse=True))
    tree.commands["ticker"]()
    check("clicking Ticker sorts ascending first",
          [r[1] for r in tree.rows] == ["ASII", "BBCA"],
          [r[1] for r in tree.rows])
    check("and the heading says which way", "^" in tree.headings["ticker"],
          tree.headings["ticker"])
    tree.commands["ticker"]()
    check("clicking the same heading reverses it",
          [r[1] for r in tree.rows] == ["BBCA", "ASII"],
          [r[1] for r in tree.rows])
    check("the arrow flips too", "v" in tree.headings["ticker"],
          tree.headings["ticker"])
    check("and no other heading carries an arrow",
          "^" not in tree.headings["title"] and "v" not in tree.headings["title"],
          tree.headings["title"])
    tree.commands["title"]()
    check("moving to another column starts ascending, not reversed",
          "^" in tree.headings["title"] and "v" not in tree.headings["ticker"],
          (tree.headings["title"], tree.headings["ticker"]))

    print("\n== blank cells sink to the bottom either way ==")
    mixed = [row("has one", ticker="TLKM"), row("has none", ticker=""),
             row("has two", ticker="ASII")]
    root = build(rows=mixed)
    tree = tree_of(root)
    tree.commands["ticker"]()
    check("ascending puts the blank last",
          [r[1] for r in tree.rows] == ["ASII", "TLKM", ""],
          [r[1] for r in tree.rows])
    tree.commands["ticker"]()
    check("and so does descending - a blank is 'no answer', not 'first'",
          [r[1] for r in tree.rows] == ["TLKM", "ASII", ""],
          [r[1] for r in tree.rows])

    print("\n== unopened stories are marked, and stop being marked ==")
    tmp = os.path.join(tempfile.mkdtemp(), "opened.json")
    urls = []
    root = build(opened=urls, opened_path=tmp)
    tree = tree_of(root)
    check("every row starts unread", all("unread" in tree.tags_of(i)
                                         for i in range(len(tree.rows))))
    check("the tag is actually styled, not just named",
          "unread" in tree.tag_styles)
    tree.select(0)
    tree.bindings["<Double-1>"]()
    check("opening a story opens its URL", len(urls) == 1, urls)
    check("and clears the highlight on that row only",
          [("unread" in tree.tags_of(i)) for i in range(len(tree.rows))]
          == [False, True],
          [tree.tags_of(i) for i in range(len(tree.rows))])
    check("the receipt is written immediately, not at close",
          urls[0] in D.load_opened(tmp), D.load_opened(tmp))

    root = build(opened_path=tmp)       # a fresh window, same file
    tree = tree_of(root)
    check("and it survives closing the window",
          sum("unread" in tree.tags_of(i) for i in range(len(tree.rows))) == 1)
    btn = {b.kw.get("text"): b for b in widgets(root, Button)}
    btn["Mark all opened"].command()
    check("'Mark all opened' clears the lot",
          not any("unread" in tree.tags_of(i) for i in range(len(tree.rows))))

    print("\n== the Unopened-only switch ==")
    root = build(opened_path=tmp)
    tree = tree_of(root)
    before = len(tree.rows)
    checkbox(root, "Unopened only").set(True)
    check("nothing is left once everything has been opened",
          before == 2 and len(tree.rows) == 0, (before, len(tree.rows)))

    print("\n== the When filter ==")
    now = datetime(2026, 9, 10, 15, 0, tzinfo=WIB)
    old = row("last year's news", when="2025-11-03 09:00:00")
    today = row("this morning", when="2026-09-10 08:00:00")
    check("Today keeps only today",
          [r["title"] for r in D.view([old, today], show_all=True,
                                            period="Today", now=now)]
          == ["this morning"])
    check("This year keeps this year",
          len(D.view([old, today], show_all=True, period="This year",
                           now=now)) == 1)
    check("All keeps everything",
          len(D.view([old, today], show_all=True, period="All",
                           now=now)) == 2)
    root = build(rows=[old, today])
    check("and the dropdown offers exactly the five periods",
          D.PERIODS == ["Today", "This week", "This month", "This year",
                              "All"])

    print("\n== every tray action gets a button ==")
    fired = []
    acts = {"Check now": lambda: fired.append("check"),
            "Quit JCIAlert": lambda: fired.append("quit")}
    root = build(actions=acts)
    check("Actions is a tab", "Actions" in _tabs(root), _tabs(root))
    btn = {b.kw.get("text"): b for b in widgets(root, Button)}
    check("each action is a button", "Check now" in btn and "Quit JCIAlert" in btn,
          sorted(btn))
    btn["Check now"].command()
    check("and pressing it calls the tray's own function", fired == ["check"],
          fired)
    boom = build(actions={"Explode": lambda: 1 / 0})
    bt = {b.kw.get("text"): b for b in widgets(boom, Button)}
    bt["Explode"].command()
    check("an action that raises is reported, not fatal",
          any("that failed" in str(w.kw.get("text")) for w in widgets(boom, W)))

    print("\n== the window can be opened, closed and opened again ==")
    root = build()
    close = root.bindings.get("WM_DELETE_WINDOW")
    check("the X routes through Python's destroy", close is not None)
    close()
    root = build()
    check("and the second window still has its rows", len(tree_of(root).rows) == 2)

    print("\n== a huge log does not freeze the window ==")
    many = [row(f"headline number {i}", when=f"2026-09-10 {i % 24:02d}:00:00",
                url=f"http://x/{i}") for i in range(D.DRAW_LIMIT + 500)]
    root = build(rows=many)
    tree = tree_of(root)
    check("only the draw limit is rendered", len(tree.rows) == D.DRAW_LIMIT,
          len(tree.rows))
    check("and the bar admits it rather than lying about the count",
          any("narrow the filter" in str(w.kw.get("text"))
              for w in widgets(root, W)))

    print("\n== an empty log is a normal state, not a crash ==")
    root = build(rows=[])
    tree = widgets(root, Treeview)[0]
    check("no rows, no exception", tree.rows == [])
    check("and the bar says zero rather than nothing",
          any("showing 0 of 0" in str(w.kw.get("text"))
              for w in widgets(root, W)))

    print("\n%d checks failed" % len(failures) if failures else "\nall checks passed")
    return 1 if failures else 0


def _tabs(root):
    for w in widgets(root, W):
        if hasattr(w, "tabs"):
            return w.tabs
    return []


if __name__ == "__main__":
    raise SystemExit(main())
