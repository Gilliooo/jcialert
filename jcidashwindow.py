#!/usr/bin/env python3
"""
JCIAlert - the dashboard window.

WHY THIS EXISTS
---------------
A popup is gone the moment it is dismissed, and "Status" used to be a Windows
balloon - which Windows 10/11 routinely swallows whole (Focus Assist, per-app
notification settings, or simply an unregistered AppUserModelID), and truncates
when it does show. A menu item that silently does nothing is worse than no menu
item. Both problems have the same answer: a real window that is still there
when you look at it.

It is also the app's main face now, so everything the tray menu can do has a
button here. The tray is a fallback, not the only way in.

TWO TABS, ONE RULE
------------------
News shows what came off the wire; Status shows whether the wire is alive.
The News tab defaults to the filtered view - what JCIAlert would have popped
up - and "Show everything" widens it to every headline recorded. The filters
are a VIEW here (see jcidash), so the switch costs nothing and answers the
only question tuning ever asks: what did I reject, and did I want it.

TWO THINGS THAT LOOK LIKE STYLE AND ARE NOT
-------------------------------------------
1. The button bar is packed side="bottom" BEFORE the notebook is packed. Do it
   the other way round and an expanding notebook eats the bar off the bottom of
   the screen.
2. Every Tk variable is created with master=root. A Var with no master binds to
   tkinter._default_root, which is a DEAD interpreter after a window has been
   closed with the X button - and then the widget shows blank for a value that
   is really there. That bug cost a day in the Options window; it is not
   allowed to happen twice.
"""

import jcidash

TITLE = "JCIAlert - news"
GEOMETRY = "1140x660"
MIN_SIZE = (820, 460)

# (heading, width, key). Time first, headline last and widest - the eye scans
# down the narrow columns and only stops to read the one that matters.
COLUMNS = [("Time", 140, "ts_wib"),
           ("Ticker", 70, "ticker"),
           ("Source", 120, "source"),
           ("Status", 70, "status"),
           ("Why / rule", 190, "_why"),
           ("Headline", 560, "title")]

ALL_SOURCES = "(every source)"
UNREAD_BG = "#fff4d6"          # a warm tint, not a colour that means "error"
UNREAD_FG = "#7a4b00"


def _why(row):
    return jcidash.why_of(row)


def open_window(load, stats=None, open_url=None, tab="News", actions=None,
                opened_path=None, tk=None, ttk=None):
    """Build and run the window.

    `load()` -> rows, re-read on every Refresh so the window is never a stale
    snapshot of a file the engine is still appending to.
    `actions` -> {label: callable} for the tray's own menu items, rendered as
    buttons. The window never reaches into the tray; the tray hands it the
    callables, so this module stays testable with none of them.
    """
    if tk is None:
        import tkinter as tk
        from tkinter import ttk

    rows = list(load() or [])
    shown = []                       # parallel to the tree's row ids
    opened = jcidash.load_opened(opened_path) if opened_path else {}
    sort = {"key": "ts_wib", "desc": True}

    root = tk.Tk()
    root.title(TITLE)
    root.geometry(GEOMETRY)
    try:
        root.minsize(*MIN_SIZE)
    except Exception:
        pass
    try:
        root.protocol("WM_DELETE_WINDOW", root.destroy)
    except Exception:
        pass

    # ---- BUTTON BAR FIRST. See the module docstring.
    bar = tk.Frame(root)
    bar.pack(side="bottom", fill="x", padx=10, pady=8)
    status_line = tk.Label(bar, text="", anchor="w")
    status_line.pack(side="left", fill="x", expand=True)

    nb = ttk.Notebook(root)
    nb.pack(side="top", fill="both", expand=True, padx=10, pady=(10, 0))

    # ------------------------------------------------------------- News tab
    ntab = tk.Frame(nb)
    nb.add(ntab, text="News")

    controls = tk.Frame(ntab)
    controls.pack(fill="x", pady=(4, 6))

    tk.Label(controls, text="Search").pack(side="left")
    q = tk.StringVar(master=root, value="")
    tk.Entry(controls, textvariable=q, width=24).pack(side="left", padx=(4, 10))

    tk.Label(controls, text="Ticker").pack(side="left")
    tick = tk.StringVar(master=root, value="")
    tk.Entry(controls, textvariable=tick, width=8).pack(side="left", padx=(4, 10))

    tk.Label(controls, text="Source").pack(side="left")
    src = tk.StringVar(master=root, value=ALL_SOURCES)
    src_box = ttk.Combobox(controls, textvariable=src, width=17,
                           values=[ALL_SOURCES] + jcidash.sources_in(rows))
    src_box.pack(side="left", padx=(4, 10))

    tk.Label(controls, text="When").pack(side="left")
    period = tk.StringVar(master=root, value="All")
    ttk.Combobox(controls, textvariable=period, width=11,
                 values=jcidash.PERIODS).pack(side="left", padx=(4, 10))

    every = tk.BooleanVar(master=root, value=False)
    tk.Checkbutton(controls, text="Show everything", variable=every).pack(
        side="left")
    unread = tk.BooleanVar(master=root, value=False)
    tk.Checkbutton(controls, text="Unopened only", variable=unread).pack(
        side="left")

    # ---- table + scrollbar, in their own frame so the bar hugs the table
    holder = tk.Frame(ntab)
    holder.pack(fill="both", expand=True)
    tree = ttk.Treeview(holder, columns=[c[2] for c in COLUMNS],
                        show="headings", selectmode="browse")
    yscroll = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=yscroll.set)
    yscroll.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)

    # Unopened stories are tinted rather than bolded: a colour survives being
    # glanced at sideways, and the row is still perfectly readable once read.
    try:
        tree.tag_configure("unread", background=UNREAD_BG, foreground=UNREAD_FG)
    except Exception:
        pass

    def sort_by(key):
        def handler():
            sort["desc"] = not sort["desc"] if sort["key"] == key else False
            sort["key"] = key
            redraw()
        return handler

    for head, width, key in COLUMNS:
        tree.heading(key, text=head, command=sort_by(key))
        tree.column(key, width=width, anchor="w")

    why_line = tk.Label(ntab, text="", anchor="w")
    why_line.pack(fill="x", pady=(4, 0))

    def cell(row, key):
        return _why(row) if key == "_why" else str(row.get(key, "") or "")

    def heading_text(head, key):
        # The arrow is on the column being sorted and nowhere else, so the
        # table always says which order you are looking at.
        if sort["key"] != key:
            return head
        return head + ("  v" if sort["desc"] else "  ^")

    def redraw():
        del shown[:]
        kids = tree.get_children()
        if kids:
            tree.delete(*kids)      # one call: deleting inside the loop over
                                    # get_children() is the classic way to
                                    # leave half a table on screen
        chosen = jcidash.view(
            rows,
            show_all=bool(every.get()),
            query=q.get(),
            ticker=tick.get(),
            source="" if src.get() in ("", ALL_SOURCES) else src.get(),
            period="" if period.get() == "All" else period.get(),
            unread_only=bool(unread.get()),
            opened=opened)
        chosen = jcidash.sort_rows(chosen, sort["key"], sort["desc"])
        capped = len(chosen) > jcidash.DRAW_LIMIT
        chosen = chosen[:jcidash.DRAW_LIMIT]
        for row in chosen:
            url = row.get("url") or ""
            tags = ("unread",) if url and url not in opened else ()
            tree.insert("", "end", tags=tags,
                        values=[cell(row, key) for _h, _w, key in COLUMNS])
            shown.append(row)
        for head, _w, key in COLUMNS:
            tree.heading(key, text=heading_text(head, key), command=sort_by(key))
        s = jcidash.summary(rows)
        new = sum(1 for r in chosen
                  if r.get("url") and r["url"] not in opened)
        status_line.config(
            text=(f"showing {'the first ' if capped else ''}{len(chosen)} of "
                  f"{s['total']} headlines - {new} unopened, "
                  f"{s['alerts']} alerted, {s['dropped']} not, "
                  f"{s['sources']} sources, {s['tickers']} tickers")
                 + ("  -  narrow the filter to see the rest" if capped else ""))
        why = jcidash.why_summary(chosen if every.get() else rows)
        why_line.config(text="not alerted: " + ", ".join(
            f"{n}x {reason}" for reason, n in why) if why else "")

    def refresh():
        del rows[:]
        rows.extend(load() or [])
        try:
            src_box.configure(values=[ALL_SOURCES] + jcidash.sources_in(rows))
        except Exception:
            pass
        redraw()

    for var in (q, tick, src, every, period, unread):
        try:
            var.trace_add("write", lambda *_a: redraw())
        except Exception:
            pass          # a toolkit without traces still has the buttons

    def open_selected(_event=None):
        sel = tree.selection()
        if not sel:
            return
        try:
            i = tree.get_children().index(sel[0])
        except (ValueError, AttributeError):
            return
        if not (0 <= i < len(shown)):
            return
        url = shown[i].get("url")
        if not url:
            return
        if open_url:
            open_url(url)
        # Mark read AFTER opening, and persist immediately - the process can
        # be killed from the tray at any moment and a read receipt that only
        # lives in memory is not a read receipt.
        opened[url] = shown[i].get("ts_utc") or shown[i].get("ts_wib") or ""
        if opened_path:
            jcidash.save_opened(opened_path, opened)
        redraw()

    tree.bind("<Double-1>", open_selected)
    tree.bind("<Return>", open_selected)

    # ----------------------------------------------------------- Status tab
    stab = tk.Frame(nb)
    nb.add(stab, text="Status")
    stext = tk.Text(stab, height=20)
    stext.pack(fill="both", expand=True, pady=4)

    def redraw_status():
        try:
            stext.delete("1.0", "end")
            stext.insert("1.0", status_text(stats() if stats else None))
        except Exception:
            pass

    # ---------------------------------------------------------- Actions tab
    # Everything the tray menu does, as buttons. The tray is a 16-pixel target
    # behind a chevron; this is not.
    atab = tk.Frame(nb)
    nb.add(atab, text="Actions")
    tk.Label(atab, text="Everything the tray menu can do:", anchor="w").pack(
        fill="x", pady=(6, 4))

    def wrap(fn):
        def handler():
            try:
                fn()
            except Exception as exc:
                status_line.config(text=f"that failed: {exc}")
                return
            refresh()
            redraw_status()
        return handler

    for label, fn in (actions or {}).items():
        tk.Button(atab, text=label, command=wrap(fn), width=28).pack(
            anchor="w", pady=2)

    # ------------------------------------------------------------- buttons
    tk.Button(bar, text="Close", command=root.destroy).pack(side="right", padx=4)

    def do_refresh():
        refresh()
        redraw_status()

    tk.Button(bar, text="Refresh", command=do_refresh).pack(side="right")
    tk.Button(bar, text="Open story", command=open_selected).pack(side="right",
                                                                  padx=4)

    def mark_all_read():
        for r in shown:
            if r.get("url"):
                opened[r["url"]] = r.get("ts_utc") or ""
        if opened_path:
            jcidash.save_opened(opened_path, opened)
        redraw()

    tk.Button(bar, text="Mark all opened", command=mark_all_read).pack(
        side="right", padx=4)

    redraw()
    redraw_status()
    if tab == "Status":
        try:
            nb.select(stab)
        except Exception:
            pass          # a wrong tab is a nuisance; a crash is not
    root.mainloop()
    return root


def status_text(s):
    """The text the old balloon could not fit. Pure, so the test can read it.

    FIVE STATES, NOT TWO. `ok` on a SourceState starts True so that a source
    which has not had its turn yet does not paint the tray red - which meant
    Status showed `ok  iqplus-stock  0 items  newest -` for a feed that had
    not answered in three days. "Healthy", "not tried yet" and "backed off
    after failing" are three different facts and a reader acts differently on
    each, so they get three different words. BanksAlert learned the same thing
    about never-tried / tried-and-failed / genuinely dateless.
    """
    if not s:
        return "The engine has not started yet."

    lines = []
    srcs = s.get("sources") or {}
    marks = {n: _mark(d) for n, d in srcs.items()}
    counts = {}
    for m in marks.values():
        counts[m] = counts.get(m, 0) + 1

    if s.get("paused"):
        lines.append("PAUSED - nothing is being fetched")
    else:
        lines.append("Running - " + _headline(counts, len(srcs)))

    lag = s.get("our_lag")
    lines.append(f"Delay we added to the last poll: {lag:.0f}s" if lag
                 else "Delay we added to the last poll: not measurable until "
                      "the second poll")
    when = s.get("last_poll_at")
    if when is not None:
        try:
            lines.append("Last check: "
                         + when.astimezone(jcidash.WIB)
                               .strftime("%Y-%m-%d %H:%M:%S") + " WIB")
        except Exception:
            lines.append(f"Last check: {when}")

    # The notification pump, which is a different thing from the engine. Every
    # source can be healthy while nothing reaches the screen, and that gap had
    # no reading anywhere until it bit.
    queued, age = s.get("queued"), s.get("pump_age")
    if age is not None and age > 120:
        lines.append(f"!! The notification pump has not run in {_mins(age)}"
                     + (f" - {queued} alert(s) waiting" if queued else ""))
    elif queued:
        lines.append(f"{queued} alert(s) waiting to be drawn")
    lines.append("")

    if not srcs:
        lines.append("No sources are switched on.")
    for name, d in sorted(srcs.items()):
        newest = _clock(d.get("newest"))
        lines.append(f"{marks[name]:<11} {name:<20} {d.get('items', 0):>4} items"
                     f"   newest {newest}")
        wait = int(d.get("waiting") or 0)
        if wait:
            lines.append(f"            backed off after "
                         f"{d.get('failures', 0)} failures; next try in "
                         f"{_mins(wait)}")
        if not d.get("ok") and d.get("error"):
            lines.append(f"            {d['error']}")

    off = s.get("disabled") or []
    if off:
        lines.append("")
        lines.append(f"Switched off in Options, so not listed above and unable "
                     f"to colour the tray icon: {', '.join(off)}.")
    return "\n".join(lines)


def _mark(d):
    """One word per source, and they mean different things."""
    if not d.get("ok"):
        return "FAILING" if not d.get("waiting") else "BACKED OFF"
    if not d.get("attempts"):
        return "not tried"
    if d.get("stale"):
        return "STALE"
    return "ok"


def _headline(counts, total):
    """What the colour means, in words. The status word itself ("red") is an
    internal token and was leaking into the window as `Running - red`."""
    bad = counts.get("FAILING", 0) + counts.get("BACKED OFF", 0)
    if bad:
        return (f"{bad} of {total} sources are not answering"
                if bad > 1 else f"1 of {total} sources is not answering")
    if counts.get("STALE"):
        n = counts["STALE"]
        return (f"{n} sources have nothing newer than their threshold"
                if n > 1 else "1 source has nothing newer than its threshold")
    if counts.get("not tried"):
        return "waiting for the first check on some sources"
    return f"all {total} sources answered"


def _clock(iso):
    """`2026-09-10T14:40:10+07:00` is a machine's answer to "how fresh". The
    reader wants a wall clock, and every one of these is already WIB."""
    if not iso:
        return "-"
    text = str(iso).replace("T", " ")
    return text[:16] if len(text) >= 16 else text


def _mins(seconds):
    m = seconds / 60.0
    if m < 1:
        return f"{int(seconds)}s"
    if m < 90:
        return f"{int(round(m))} min"
    return f"{m / 60:.1f} hours"
