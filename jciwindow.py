#!/usr/bin/env python3
"""
jciwindow - the Options window. A shell over jcioptions, which holds the logic.

Nothing here decides anything: every value goes through
`jcioptions.read_values` / `apply_values` / `validate`, so what the window can
express is exactly what the config can hold, and the tests for that behaviour
live where there is no display.

⚠️ PACK ORDER - the bug this file must not repeat
-------------------------------------------------
IDXAlert's Options window shipped with Save/Apply/Cancel OFF SCREEN. Cause:
`nb.pack(fill="both", expand=True)` ran BEFORE the button bar was packed, so
the notebook claimed all the space and squeezed the bar out. The only way to
reach the buttons was to drag the window bigger.

**Pack fixed chrome against its edge FIRST, then let the flexible widget take
the remainder.** It matters more here than it did there: the Filters tab holds
a scrolling rule list and is taller than any tab that app has.

AN UNEDITED WIDGET MUST NEVER ERASE A SETTING
---------------------------------------------
The watchlist came back EMPTY from a real save on 2026-09-10 - three tickers
gone - while the same code round-tripped perfectly against a stub. Whatever Tk
was doing (two interpreters live at once: the popup owns one, this window
another, and neither is on the main thread), the lesson is that reading widget
state back at save time is a single point of failure with no error and no log.

So every free-text box is tracked: the value is captured when the box is
built, `<KeyRelease>` marks it dirty, and at save time a box that was never
touched contributes its ORIGINAL value rather than whatever the widget
happens to return. A setting can now only be cleared by actually clearing it.

The same rule covers `sources`: an empty checkbox set means the window was
opened without a source list, not that every source should be forgotten.

THE FILTERS TAB IS A LIST, NOT A FORM
-------------------------------------
Rules are ordered, individually enabled, and each row shows
`jcifilter.describe()`. Every mutation goes through jcioptions' pure CRUD
helpers, which return NEW lists - so the widget owns no state that could drift
from the config, and Cancel is simply "do not write".
"""

import webbrowser

import jcifilter
import jcioptions

TITLE = "JCIAlert Options"
ABOUT_TEXT = "Made by Bill Grandy Tunjung"
ABOUT_URL = "https://www.linkedin.com/in/bill-tunjung/"
GEOMETRY = "700x660"
MIN_SIZE = (640, 580)

# (tab label, [(config key, human label, kind)])
SPEED = [("poll_seconds", "Check every (seconds)", "int"),
         ("max_item_age_minutes", "Ignore items older than (minutes)", "int"),
         ("min_confidence", "Minimum match confidence (0-1)", "float"),
         ("seen_max", "Remember this many items", "int")]
CLUSTER = [("cluster_window_minutes", "Treat as one story for (minutes)", "int"),
           ("cluster_threshold", "Story similarity threshold (0-1)", "float"),
           ("burst_threshold", "Group a burst above (alerts)", "int"),
           ("burst_group_min", "Minimum tickers to group", "int")]
ALERTS = [("max_visible", "Rows visible in the popup", "int"),
          ("duration_seconds", "Auto-close after (0 = never)", "int"),
          ("corner", "Popup appears in this corner", "choice")]
ADVANCED = [("seed_on_first_run", "Seed silently on first run", "bool"),
            ("start_with_windows", "Start with Windows", "bool"),
            ("data_dir", "Data folder (blank = app folder)", "text")]

RULE_TEXT = [("tickers", "Tickers (one per line, or @watchlist)"),
             ("categories", "Only these categories"),
             ("categories_exclude", "Never these categories"),
             ("require", "Must contain ALL of"),
             ("any_of", "Must contain ANY of"),
             ("exclude", "Must contain NONE of"),
             ("sources", "Only these sources")]


def _lines(v):
    return "\n".join(v or [])


def _split(text):
    return [x.strip() for x in str(text or "").splitlines() if x.strip()]


def open_window(cfg, save, sources=None, tk=None, ttk=None, messagebox=None):
    """Build and run the window. The toolkit is injected so the tests can
    drive the real code path with a stub instead of skipping headlessly - a
    skipped test looks exactly like a passing one."""
    if tk is None:
        import tkinter as tk
        from tkinter import ttk, messagebox

    values = jcioptions.read_values(cfg)
    rules = list(values["rules"])
    sel = {"i": 0 if rules else -1}
    vars_ = {}
    touched = set()          # names of text boxes the user actually edited

    def track(box, name):
        """A box nobody typed in cannot contribute an empty value."""
        box.bind("<KeyRelease>", lambda _e=None: touched.add(name))
        box.bind("<<Paste>>", lambda _e=None: touched.add(name))
        return box

    def read_box(box, name, original):
        """Read what is actually in the box. An EMPTY read is the dangerous
        one - it is indistinguishable from "the user cleared this field" - so
        an empty result only counts when the user demonstrably typed in the
        box. A non-empty read is always the truth and always wins."""
        try:
            got = box.get("1.0", "end")
        except Exception:
            return original
        if str(got).strip():
            return got
        return got if name in touched else original

    root = tk.Tk()
    root.title(TITLE)
    root.geometry(GEOMETRY)
    try:
        root.minsize(*MIN_SIZE)
    except Exception:
        pass
    # EVERY FIELD CAME BACK BLANK THE SECOND TIME. Closing the window with the
    # X button destroys it through the window manager, so Python's Tk.destroy()
    # never runs and tkinter._default_root keeps pointing at the dead
    # interpreter. A Var built with no master binds to _default_root - so on
    # the next open, every StringVar lived in the corpse of the last window
    # while the Entry showing it lived in the new one, and the widget read a
    # variable that did not exist there: blank, for values that were on disk
    # the whole time. Two defences, both cheap:
    #   1. every Var below is created with master=root, so it can never bind
    #      to a stale default; and
    #   2. the X button routes through Python's destroy, so _default_root is
    #      cleared for anything else that forgets rule 1.
    try:
        root.protocol("WM_DELETE_WINDOW", root.destroy)
    except Exception:
        pass

    # ---- BUTTON BAR FIRST. See the module docstring. Do not move this below
    # the notebook, however tidy that would look.
    bar = tk.Frame(root)
    bar.pack(side="bottom", fill="x", padx=10, pady=8)

    # Bottom-left About: a link, not a dialog. A browser that refuses to open
    # must not take the Options window down with it - and `webbrowser.open`
    # signals that failure by RETURNING FALSE, only raising in the narrower
    # no-browser-registered case. Catching the exception alone would leave the
    # likely failure silent, so both paths fall through to showing the URL.
    def about():
        try:
            if webbrowser.open(ABOUT_URL):
                return
        except Exception:
            pass
        status.config(text=ABOUT_URL)

    tk.Button(bar, text=ABOUT_TEXT, command=about,
              relief="flat", fg="#0a66c2", cursor="hand2").pack(side="left")
    status = tk.Label(bar, text="", anchor="w")
    status.pack(side="left", fill="x", expand=True, padx=(8, 0))

    nb = ttk.Notebook(root)
    nb.pack(side="top", fill="both", expand=True, padx=10, pady=(10, 0))

    def add_fields(parent, spec):
        for key, label, kind in spec:
            row = tk.Frame(parent)
            row.pack(fill="x", pady=3)
            if kind == "bool":
                v = tk.BooleanVar(master=root, value=bool(values.get(key)))
                tk.Checkbutton(row, text=label, variable=v).pack(side="left")
            elif kind == "choice":
                # A closed set gets a dropdown. A free-text box here means a
                # typo lands the popup in the default corner and looks like
                # the setting was ignored.
                tk.Label(row, text=label, width=34, anchor="w").pack(side="left")
                v = tk.StringVar(master=root, value=str(values.get(key, "")))
                ttk.Combobox(row, textvariable=v, state="readonly",
                             values=list(jcioptions.CHOICE_FIELDS[key])).pack(
                    side="left", fill="x", expand=True)
            else:
                tk.Label(row, text=label, width=34, anchor="w").pack(side="left")
                v = tk.StringVar(master=root, value=str(values.get(key, "")))
                tk.Entry(row, textvariable=v).pack(side="left", fill="x",
                                                   expand=True)
            vars_[key] = v

    for label, spec in (("Speed", SPEED), ("Grouping", CLUSTER),
                        ("Alerts", ALERTS),
                        ("Advanced", ADVANCED)):
        tab = tk.Frame(nb)
        nb.add(tab, text=label)
        add_fields(tab, spec)

    # ---- Watchlist
    wtab = tk.Frame(nb)
    nb.add(wtab, text="Watchlist")
    tk.Label(wtab, text="One 4-letter ticker per line. Empty means EVERY "
                        "ticker, not none.", anchor="w").pack(fill="x")
    wl = track(tk.Text(wtab, height=14), "watchlist")
    wl.insert("1.0", values.get("watchlist", ""))
    wl.pack(fill="both", expand=True, pady=4)

    # ---- Sources
    stab = tk.Frame(nb)
    nb.add(stab, text="Sources")
    src_vars = {}
    for s in (sources or []):
        v = tk.BooleanVar(master=root, value=bool(s.enabled))
        tk.Checkbutton(stab, text=s.name, variable=v).pack(anchor="w")
        src_vars[s.name] = v

    # ---- Filters: a LIST, not a form
    ftab = tk.Frame(nb)
    nb.add(ftab, text="Filters")
    left = tk.Frame(ftab)
    left.pack(side="left", fill="y", padx=(0, 8))
    listbox = tk.Listbox(left, width=34, exportselection=False)
    listbox.pack(fill="y", expand=True)
    btns = tk.Frame(left)
    btns.pack(fill="x", pady=4)

    right = tk.Frame(ftab)
    right.pack(side="right", fill="both", expand=True)
    rule_vars = {}
    for key, label in RULE_TEXT:
        tk.Label(right, text=label, anchor="w").pack(fill="x")
        box = track(tk.Text(right, height=3), f"rule:{key}")
        box.pack(fill="x", pady=(0, 4))
        rule_vars[key] = box
    misc = tk.Frame(right)
    misc.pack(fill="x")
    rule_vars["name"] = tk.StringVar(master=root)
    tk.Label(misc, text="Name", anchor="w").pack(side="left")
    tk.Entry(misc, textvariable=rule_vars["name"]).pack(side="left", fill="x",
                                                        expand=True)
    rule_vars["min_confidence"] = tk.StringVar(master=root)
    tk.Label(misc, text="Min conf").pack(side="left")
    tk.Entry(misc, textvariable=rule_vars["min_confidence"],
             width=6).pack(side="left")
    rule_vars["max_tickers"] = tk.StringVar(master=root)
    tk.Label(misc, text="Max tickers").pack(side="left")
    tk.Entry(misc, textvariable=rule_vars["max_tickers"],
             width=5).pack(side="left")
    flags = tk.Frame(right)
    flags.pack(fill="x", pady=4)
    for key, label in (("enabled", "Enabled"),
                       ("require_ticker", "Needs a ticker"),
                       ("require_event", "Events only")):
        rule_vars[key] = tk.BooleanVar(master=root)
        tk.Checkbutton(flags, text=label, variable=rule_vars[key]).pack(side="left")

    def refresh_list():
        listbox.delete(0, "end")
        for state, name, desc in jcioptions.rule_lines(rules):
            listbox.insert("end", f"[{state}] {name} - {desc}"[:70])
        if 0 <= sel["i"] < len(rules):
            listbox.selection_set(sel["i"])

    def load_rule():
        if not (0 <= sel["i"] < len(rules)):
            return
        r = rules[sel["i"]]
        for key, _ in RULE_TEXT:
            rule_vars[key].delete("1.0", "end")
            rule_vars[key].insert("1.0", _lines(r.get(key)))
        rule_vars["name"].set(r.get("name", ""))
        rule_vars["min_confidence"].set(str(r.get("min_confidence", 0.5)))
        rule_vars["max_tickers"].set(str(r.get("max_tickers", 0)))
        for key in ("enabled", "require_ticker", "require_event"):
            rule_vars[key].set(bool(r.get(key, key != "require_event")))

    def store_rule():
        """Write the editor back into the list. Called before ANY action that
        changes the selection, or edits vanish silently."""
        if not (0 <= sel["i"] < len(rules)):
            return
        r = dict(rules[sel["i"]])
        for key, _ in RULE_TEXT:
            if f"rule:{key}" not in touched:
                continue                    # untouched box keeps the old value
            r[key] = _split(rule_vars[key].get("1.0", "end"))
        r["name"] = rule_vars["name"].get().strip() or r.get("name", "rule")
        try:
            r["min_confidence"] = float(rule_vars["min_confidence"].get())
        except ValueError:
            pass
        try:
            r["max_tickers"] = int(rule_vars["max_tickers"].get() or 0)
        except ValueError:
            pass
        for key in ("enabled", "require_ticker", "require_event"):
            r[key] = bool(rule_vars[key].get())
        rules[sel["i"]] = r

    def on_select(_event=None):
        cur = listbox.curselection()
        if not cur:
            return
        store_rule()
        sel["i"] = cur[0]
        load_rule()
        # A different rule is now in the boxes, so edits to the previous one
        # are already stored and the new contents are untouched again.
        for key, _ in RULE_TEXT:
            touched.discard(f"rule:{key}")
        refresh_list()

    listbox.bind("<<ListboxSelect>>", on_select)

    def act(fn):
        def handler():
            store_rule()
            rules[:] = fn(rules, sel["i"])
            sel["i"] = max(0, min(sel["i"], len(rules) - 1))
            load_rule()
            refresh_list()
        return handler

    def act_wrapped(fn):
        h = act(fn)

        def handler():
            h()
            for key, _ in RULE_TEXT:
                touched.discard(f"rule:{key}")
        return handler

    for label, fn in (
            ("Add", lambda rs, i: jcioptions.add_rule(rs)),
            ("Copy", lambda rs, i: jcioptions.duplicate_rule(rs, i)),
            ("Remove", lambda rs, i: jcioptions.remove_rule(rs, i)),
            ("Up", lambda rs, i: jcioptions.move_rule(rs, i, -1)),
            ("Down", lambda rs, i: jcioptions.move_rule(rs, i, 1))):
        tk.Button(btns, text=label, command=act_wrapped(fn)).pack(side="left")

    # ---- collect / validate / save
    def collect():
        store_rule()
        out = dict(values)
        for key, v in vars_.items():
            out[key] = v.get()
        out["watchlist"] = read_box(wl, "watchlist", values.get("watchlist", ""))
        out["rules"] = list(rules)
        # No checkboxes means the window was opened without a source list -
        # NOT that every source should be forgotten. Writing {} here wiped a
        # configured ten-source map in testing.
        if src_vars:
            out["sources"] = {n: {"enabled": bool(v.get())}
                              for n, v in src_vars.items()}
        return out

    def apply(close):
        vals = collect()
        errs = jcioptions.validate(vals)
        if errs:
            status.config(text=f"{len(errs)} problem(s) - nothing saved")
            messagebox.showerror(TITLE, "\n".join(errs[:12]))
            return False
        save(jcioptions.apply_values(cfg, vals))
        status.config(text="Saved. Changes apply within a minute.")
        if close:
            root.destroy()
        return True

    tk.Button(bar, text="Cancel", command=root.destroy).pack(side="right", padx=4)
    tk.Button(bar, text="Apply", command=lambda: apply(False)).pack(side="right")
    tk.Button(bar, text="Save", command=lambda: apply(True)).pack(side="right",
                                                                 padx=4)

    load_rule()
    refresh_list()
    root.mainloop()
    return root
