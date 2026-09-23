#!/usr/bin/env python3
"""
test_jciwindow.py - the Options window, built with a FAKE toolkit.

A headless test that dies at `import tkinter` reports success, and a skipped
test looks identical to a passing one. So the stub below records every widget,
pack call and binding, and the REAL open_window() runs against it.

Two bugs this suite exists to prevent, both of which cost real time in
IDXAlert:

  1. PACK ORDER. Save/Apply/Cancel shipped off-screen because the notebook
     was packed with expand=True before the button bar. Asserted here by
     CREATION ORDER, not by geometry - the fake has no geometry manager.
  2. Edits vanishing on selection change. The rule editor writes back into the
     list only when store_rule() runs; miss one path and the user's typing is
     silently discarded, which looks like the app ignoring them.

Run:  python test_jciwindow.py
"""

import sys
import types

import jcifilter as F
import jcioptions as O

failures = []
log = []


def check(name, cond, detail=None):
    if cond:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}" + (f"   -> {detail!r}" if detail is not None else ""))


class W:
    """One fake widget class for everything. A CLASS, not a lambda - urllib
    and friends read attributes off the class on Windows/3.13."""

    def __init__(self, master=None, **kw):
        self.master, self.kw = master, dict(kw)
        self.children, self.packs, self.bindings = [], [], {}
        self.text_content = ""
        self.items = []
        self.selection = ()
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
        if a and a[0] == 0:
            self.items = []
        else:
            self.text_content = ""

    # Listbox
    def curselection(self):
        return self.selection

    def selection_set(self, i):
        self.selection = (i,)


class Listbox(W):
    """A separate class, because Listbox.insert and Text.insert take the same
    arguments and mean completely different things. Folding them into one fake
    made the rule list look empty and cost four false failures."""

    def insert(self, index, value=None):
        self.items.append(str(value))

    def delete(self, *a):
        self.items = []


class Button(W):
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self.command = kw.get("command")


class Var:
    def __init__(self, value=None):
        self._v = value

    def get(self):
        return self._v

    def set(self, v):
        self._v = v


def make_tk():
    tk = types.SimpleNamespace()
    for name in ("Tk", "Frame", "Label", "Entry", "Text", "Checkbutton"):
        setattr(tk, name, type(name, (W,), {}))
    tk.Listbox = Listbox
    tk.Button = Button
    # A Var with no master binds to tkinter._default_root - which, after a
    # window is closed with the X button, is a DEAD interpreter. That is the
    # blank-Options bug, and it is invisible to a stub unless the stub insists
    # on the master the real toolkit silently does without. So it insists.
    def _var(cast):
        def make(master=None, value=None):
            if master is None:
                raise AssertionError(
                    "Tk variable created with no master: it will bind to "
                    "_default_root and read blank after a window is closed "
                    "with the X button")
            return Var(cast(value))
        return make

    tk.StringVar = _var(lambda v: "" if v is None else v)
    tk.BooleanVar = _var(bool)
    ttk = types.SimpleNamespace()

    class Notebook(W):
        def add(self, child, text=""):
            self.items.append(text)
            log.append(("tab", text, ""))
    ttk.Notebook = Notebook
    ttk.Combobox = type("Combobox", (W,), {})
    shown = []
    mb = types.SimpleNamespace(showerror=lambda t, m: shown.append((t, m)))
    return tk, ttk, mb, shown


import jciwindow as JW                                     # noqa: E402


def build(cfg=None, sources=None):
    log.clear()
    tk, ttk, mb, shown = make_tk()
    saved = []
    cfg = cfg or O.default_config()
    root = JW.open_window(cfg, saved.append, sources or [], tk, ttk, mb)
    return root, saved, shown


def widgets(node, cls, found=None):
    found = [] if found is None else found
    for c in node.children:
        if isinstance(c, cls):
            found.append(c)
        widgets(c, cls, found)
    return found


def buttons(node, found=None):
    found = [] if found is None else found
    for c in node.children:
        if isinstance(c, Button):
            found.append(c)
        buttons(c, found)
    return found


def main():
    print("\n== the window builds at all ==")
    root, saved, shown = build()
    check("it ran to mainloop", ("mainloop", "", "") in log)
    tabs = [e[1] for e in log if e[0] == "tab"]
    for want in ("Speed", "Alerts", "Filters", "Watchlist", "Sources"):
        check(f"has a {want} tab", want in tabs, tabs)

    print("\n== PACK ORDER: the bug that shipped in IDXAlert ==")
    packs = [e for e in log if e[0] == "pack"]
    bottom = next((i for i, e in enumerate(packs) if e[2] == "bottom"), None)
    expand = next((i for i, e in enumerate(packs)
                   if e[1] == "Notebook" and e[3]), None)
    check("the button bar is packed against an edge", bottom is not None)
    check("the notebook expands", expand is not None)
    check("and the bar is packed BEFORE the notebook takes the space",
          bottom is not None and expand is not None and bottom < expand,
          (bottom, expand))
    labels = [b.kw.get("text") for b in buttons(root)]
    for want in ("Save", "Apply", "Cancel"):
        check(f"{want} exists", want in labels, labels)

    print("\n== the Filters tab is a list ==")
    for want in ("Add", "Copy", "Remove", "Up", "Down"):
        check(f"{want} button", want in labels, labels)
    lb = None
    def find(node):
        nonlocal lb
        for c in node.children:
            if type(c).__name__ == "Listbox":
                lb = c
            find(c)
    find(root)
    check("there is a rule listbox", lb is not None)
    check("it is populated from jcioptions.rule_lines",
          lb and lb.items and "[on ]" in lb.items[0], lb.items if lb else None)
    check("each row carries the description, not just a name",
          lb and any("ticker" in i for i in lb.items), lb.items if lb else None)

    print("\n== Add / Remove drive the pure helpers ==")
    by = {b.kw.get("text"): b for b in buttons(root)}
    before = len(lb.items)
    by["Add"].command()
    check("Add appends a rule", len(lb.items) == before + 1, lb.items)
    check("and gives it a unique name so validation cannot trip",
          len({i.split(" - ")[0] for i in lb.items}) == len(lb.items), lb.items)
    by["Remove"].command()
    check("Remove takes it away", len(lb.items) == before, lb.items)
    by["Remove"].command()
    by["Remove"].command()
    check("removing past the end does not raise", True)
    by["Add"].command()
    check("and Add still works afterwards", len(lb.items) >= 1)

    print("\n== Save goes through validate, and refuses bad configs ==")
    root, saved, shown = build()
    by = {b.kw.get("text"): b for b in buttons(root)}
    by["Save"].command()
    check("a clean config saves", len(saved) == 1, (len(saved), shown))
    check("what it saves is a config dict, not form values",
          saved and "filters" in saved[0], sorted(saved[0]) if saved else None)
    check("the watchlist round-trips as a list",
          isinstance(saved[0].get("watchlist"), list), saved[0].get("watchlist"))

    bad = O.default_config()
    bad["filters"]["rules"] = [dict(O.new_rule("x"), enabled=False)]
    root, saved, shown = build(bad)
    by = {b.kw.get("text"): b for b in buttons(root)}
    by["Save"].command()
    check("a config that can never alert is REFUSED, not written",
          saved == [], saved)
    check("and the user is told why", shown and "alert" in shown[0][1].lower(),
          shown)

    print("\n== Apply keeps the window open, Cancel writes nothing ==")
    root, saved, shown = build()
    by = {b.kw.get("text"): b for b in buttons(root)}
    by["Apply"].command()
    check("Apply saves", len(saved) == 1)
    check("without destroying the window",
          ("destroy", "Tk", "") not in log[log.index(("mainloop", "", "")):]
          if ("mainloop", "", "") in log else True)
    root, saved, shown = build()
    by = {b.kw.get("text"): b for b in buttons(root)}
    by["Cancel"].command()
    check("Cancel saves nothing at all", saved == [], saved)

    print("\n== an UNEDITED box cannot erase a setting ==")
    # The real failure, 2026-09-10: a save from the exe came back with an
    # empty watchlist - three tickers gone - while the same code round-tripped
    # perfectly against this stub. Reading widget state back at save time is a
    # single point of failure with no error and no log, so an untouched box now
    # contributes its ORIGINAL value.
    cfg = O.default_config()
    cfg["watchlist"] = ["BBCA", "BMRI", "BBRI"]
    root, saved, shown = build(cfg)
    by = {b.kw.get("text"): b for b in buttons(root)}
    texts = []

    def collect_texts(node):
        for c in node.children:
            if type(c).__name__ == "Text":
                texts.append(c)
            collect_texts(c)
    collect_texts(root)
    texts[0].text_content = ""            # simulate the widget reading back blank
    by["Save"].command()
    check("a blank read from an untouched box keeps the saved tickers",
          saved and saved[0]["watchlist"] == ["BBCA", "BMRI", "BBRI"],
          saved[0].get("watchlist") if saved else None)

    # The 2026-09-10 bug in one line. <KeyRelease> is not the only way text
    # gets into a Text widget - an IME commit, a right-click Paste, a drag and
    # drop and a programmatic insert all miss it. So a box that HAS content is
    # believed no matter how it got there; only a blank read needs the alibi.
    root, saved, shown = build(cfg)
    by = {b.kw.get("text"): b for b in buttons(root)}
    texts = []
    collect_texts(root)
    texts[0].text_content = "TLKM\nASII\n"    # no binding fired
    by["Save"].command()
    check("text typed by a route that fires no binding is still saved",
          saved and saved[0]["watchlist"] == ["TLKM", "ASII"],
          saved[0].get("watchlist") if saved else None)

    root, saved, shown = build(cfg)
    by = {b.kw.get("text"): b for b in buttons(root)}
    texts = []
    collect_texts(root)
    texts[0].bindings["<KeyRelease>"]()   # the user actually types
    texts[0].text_content = "TLKM\nASII\n"
    by["Save"].command()
    check("but a box the user edited is honoured",
          saved and saved[0]["watchlist"] == ["TLKM", "ASII"],
          saved[0].get("watchlist") if saved else None)

    root, saved, shown = build(cfg)
    by = {b.kw.get("text"): b for b in buttons(root)}
    texts = []
    collect_texts(root)
    texts[0].bindings["<KeyRelease>"]()
    texts[0].text_content = ""
    by["Save"].command()
    check("including deliberately clearing it",
          saved and saved[0]["watchlist"] == [], saved[0].get("watchlist"))

    print("\n== reopening the window shows the saved values, not blanks ==")
    # Reported 2026-09-10: "if i close the options window and open it up after
    # some time it shows all the values as blank even if in reality theres
    # saved value inside". The cause is a Var with no master; the stub above
    # now refuses to build one, so this section is really asserting that the
    # window survives being opened, closed with the X, and opened again.
    cfg3 = O.default_config()
    cfg3["watchlist"] = ["BBCA", "TLKM"]
    cfg3["poll_seconds"] = 45
    root, saved, shown = build(cfg3)
    close = root.bindings.get("WM_DELETE_WINDOW")
    check("the X button routes through Python's destroy, not just the WM",
          close is not None)
    close()
    root, saved, shown = build(cfg3)      # open it again, same process
    by = {b.kw.get("text"): b for b in buttons(root)}
    by["Save"].command()
    check("the second window still has the watchlist",
          saved and saved[0]["watchlist"] == ["BBCA", "TLKM"],
          saved[0].get("watchlist") if saved else None)
    check("and the scalar fields too",
          saved and saved[0]["poll_seconds"] == 45,
          saved[0].get("poll_seconds") if saved else None)

    print("\n== an empty Sources tab must not wipe the source map ==")
    cfg2 = O.default_config()
    cfg2["sources"] = {"katadata": {"enabled": True},
                       "cnbc-market": {"enabled": False}}
    root, saved, shown = build(cfg2)        # built with sources=[]
    by = {b.kw.get("text"): b for b in buttons(root)}
    by["Save"].command()
    check("no checkboxes means 'unknown', not 'all off'",
          saved and saved[0].get("sources") == cfg2["sources"],
          saved[0].get("sources") if saved else None)

    print("\n== unknown config keys survive the window ==")
    cfg = O.default_config()
    cfg["_comment_keep"] = "documentation"
    cfg["future_key"] = {"from": "a newer version"}
    root, saved, shown = build(cfg)
    by = {b.kw.get("text"): b for b in buttons(root)}
    by["Save"].command()
    check("_comment_* documentation is not eaten",
          saved and saved[0].get("_comment_keep") == "documentation")
    check("nor is a key this version has no widget for",
          saved and saved[0].get("future_key") == {"from": "a newer version"})

    print("\n== the corner is a dropdown, and it round-trips ==")
    cfgc = O.default_config()
    cfgc["corner"] = "top left"
    root, saved, shown = build(cfgc)
    combos = [w for w in widgets(root, W)
              if type(w).__name__ == "Combobox" and "textvariable" in w.kw]
    check("it is rendered as a Combobox, not an Entry", len(combos) == 1,
          len(combos))
    check("readonly, so a typo cannot reach the config",
          combos and combos[0].kw.get("state") == "readonly", combos[0].kw)
    check("offering exactly the four corners",
          combos and list(combos[0].kw.get("values")) == list(O.CHOICE_FIELDS["corner"]),
          combos[0].kw.get("values") if combos else None)
    check("showing the saved value, not the default",
          combos and combos[0].kw["textvariable"].get() == "top left",
          combos[0].kw["textvariable"].get() if combos else None)
    by = {b.kw.get("text"): b for b in buttons(root)}
    combos[0].kw["textvariable"].set("bottom left")
    by["Save"].command()
    check("changing it saves the new corner",
          saved and saved[0]["corner"] == "bottom left",
          saved[0].get("corner") if saved else None)

    print("\n%d checks failed" % len(failures) if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
