#!/usr/bin/env python3
"""
The JCIAlert popup - the always-on-top list of new stories.

LIFTED FROM IDXAlert's idx3popup.py, changed as little as possible. Every
awkward line in it is load-bearing and was paid for in real debugging time;
the four notes below are IDXAlert's and still apply here verbatim. If you are
tempted to tidy something in this file, read them first.

What actually differs from the original, and why:
  * a row is a TICKER and its menu entries are that ticker's STORIES, where
    IDXAlert had one row per filing and attachments in the menu. Same widget,
    different meaning.
  * entry labels are trimmed at the TAIL, not the middle - see entry_label.
  * there is no single "the page" to fall back on when a row has no link. The notification STYLE is deliberately not negotiable:
winrt toasts and tray balloons are both suppressible by Do Not Disturb and by
per-app banner settings, and they fail SILENTLY - the call returns success and
nothing is drawn. A window we draw ourselves cannot be quietly swallowed.

Four fixes here cost real debugging time in 2.x. Do not undo them:

  * Tk does not bubble events to parents. A binding on the Toplevel alone is
    dead everywhere a child widget covers it, which is everywhere. Bind
    recursively (bind_deep).
  * Rows are NOT a fixed height - a title that wraps to two lines is taller.
    Sizing the canvas from summed winfo_reqheight() clips rows mid-way. Measure
    where the rows actually landed: built[max_visible].winfo_y().
  * The window must not close on Esc, on click-anywhere, or on a timer. Only
    the X and a right-click.
  * A left-click on a row opens its file and leaves the window open.

New stories merge into the popup already on screen and it scrolls back to
the top, so a busy batch window never leaves a pile of overlapping windows.
"""

import threading

TRAY_MODE = False          # jcitray sets this True: sticky popups must not block

_live = {"root": None, "add": None}
_lock = threading.Lock()


def _sort_newest_first(rows):
    return sorted(rows, key=lambda i: (i.get("posted") or ""), reverse=True)


def _row_key(item):
    return item.get("key") or (item.get("ticker", ""), item.get("title", ""))


CORNERS = ("bottom right", "bottom left", "top right", "top left")

# The taskbar is the reason these are not simply screen corners. Tk reports the
# FULL screen, not the work area, so a naive bottom-right lands the popup under
# a bottom taskbar. 72px clears a standard Windows taskbar with room to spare,
# and the same inset is used at the top for symmetry - a popup flush against
# the edge reads as a rendering glitch rather than a placement.
EDGE_X, EDGE_Y = 24, 72


def normalise_corner(corner):
    """ONE spelling rule, exported, because two modules were about to grow
    their own. `Top-Left`, `top_left` and `top left` are the same corner; a
    config file is hand-edited and should not care."""
    name = " ".join(str(corner or "").replace("-", " ").replace("_", " ")
                    .lower().split())
    return name if name in CORNERS else ""


def corner_geometry(corner, width, height, screen_w, screen_h):
    """-> (x, y) for the popup's top-left. Pure, so placement is testable
    without a display - which is the only way it can be tested at all here."""
    name = normalise_corner(corner) or CORNERS[0]
    x = EDGE_X if "left" in name else max(0, screen_w - width - EDGE_X)
    y = EDGE_Y if "top" in name else max(0, screen_h - height - EDGE_Y)
    return x, y


def _entry_key(f):
    return f.get("url") or (f.get("name") or "")


def merge_rows(existing, incoming):
    """-> (rows, fresh_keys). Pure, so the merge can be tested without Tk.

    THE ROW KEY IS THE TICKER, so a second story about a ticker already on
    screen arrives with a key that is ALREADY THERE. The first version treated
    that as "seen it" and threw the whole row away - so the popup's own headline
    feature, one row per ticker with an N-stories menu, only ever worked inside
    a single tick. A story that arrived a minute later simply vanished: not
    merged, not added, not logged.

    So a repeat key MERGES: its stories join the row's menu, the row takes the
    newer headline and timestamp, and it counts as fresh so it is highlighted
    and floats to the top. A story already in the menu (same url) is still
    dropped - that is the case the "seen it" check was actually for.
    """
    # Rows are COPIED before they are changed. A "pure" function that edits
    # the list it was handed is only pure until someone reuses the argument,
    # and the caller here is a Tk callback holding the live popup's state.
    rows = [dict(r) for r in existing]
    by_key = {_row_key(r): r for r in rows}
    fresh = set()

    for item in incoming:
        key = _row_key(item)
        row = by_key.get(key)
        if row is None:
            row = dict(item)
            by_key[key] = row
            rows.append(row)
            fresh.add(key)
            continue

        have = {_entry_key(f) for f in row.get("files") or []}
        added = [f for f in item.get("files") or []
                 if _entry_key(f) not in have]
        newer = (item.get("posted") or "") > (row.get("posted") or "")
        if not added and not newer:
            continue                        # genuinely nothing new

        if added:
            row["files"] = added + list(row.get("files") or [])
            row["count"] = len(row["files"])
        if newer:
            # The row leads with its newest story, which is what the reader
            # means by "what just happened to TLKM".
            row["title"] = item.get("title", row.get("title", ""))
            row["posted"] = item.get("posted") or row.get("posted")
        for extra in ("rule", "sector"):
            if not row.get(extra) and item.get(extra):
                row[extra] = item[extra]
        cats = set(row.get("categories") or ()) | set(item.get("categories") or ())
        if cats:
            row["categories"] = sorted(cats)
        fresh.add(key)

    return _sort_newest_first(rows), fresh


def entry_label(name, limit=62):
    """OPPOSITE trimming rule to IDXAlert's, on purpose.

    IDX named attachments "20260831_ZP_Pemberitahuan..._32143667_lamp2.pdf",
    where the distinguishing part (lamp2) sits at the END - so that version
    trimmed the middle and kept the tail.

    JCIAlert entries are "16:21 · IQPlus · BTPS BERENCANA GELAR BUYBACK": the
    distinguishing part is at the FRONT and the tail is the least important
    words of a headline. So trim the tail."""
    name = (name or "story").strip()
    if len(name) <= limit:
        return name
    return name[:limit - 1].rstrip() + "…"


def close_live():
    """Used by the tray on quit so the window does not outlive the process."""
    with _lock:
        root = _live.get("root")
    if root is not None:
        try:
            root.after(0, root.destroy)
        except Exception:
            pass


def show(items, opts=None):
    """Draw (or merge into) the popup. Raises if tkinter cannot start."""
    opts = opts or {}
    secs = int(opts.get("duration_seconds", 0) or 0)
    max_visible = max(1, int(opts.get("max_visible", 3)))
    corner = opts.get("corner") or CORNERS[0]
    rows = _sort_newest_first(list(items or []))
    if not rows:
        return

    # --- merge into the popup already on screen, if there is one ---
    with _lock:
        root = _live.get("root")
        add = _live.get("add")
    if root is not None and add is not None:
        try:
            root.after(0, lambda batch=rows: add(batch))
            return
        except Exception:
            with _lock:                       # window died between checks
                _live["root"] = None
                _live["add"] = None

    err = []

    def run():
        try:
            import tkinter as tk
            import webbrowser

            root = tk.Tk()
            root.withdraw()
            w = tk.Toplevel(root)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            try:
                w.attributes("-alpha", 0.97)
            except Exception:
                pass

            # ACCENT is orange. The tray STATUS colours are a shared
            # language - green means healthy in both apps and stays green -
            # but the accent inside the window is decoration, and orange is
            # what tells this popup apart from IDXAlert's at a glance.
            BG, FG, ACCENT, MUTED = "#1f2430", "#e8ecf1", "#f5a94b", "#8b93a1"
            ROW_BG, ROW_HOVER, NEW_BG = "#252b3a", "#2e3648", "#3a3226"
            # The headline is the thing you actually came to read, so it gets
            # the accent - but a paler tint of it, not the same value as the
            # ticker label above it. Two lines in the identical orange and the
            # row loses its hierarchy: everything shouts and nothing leads.
            TITLE_FG = "#f7cf9a"
            BORDER = "#c07d1e"
            W = 430

            live = {"rows": list(rows), "timer": None, "fresh": set()}

            outer = tk.Frame(w, bg=BG, highlightbackground=BORDER,
                             highlightthickness=2)
            outer.pack(fill="both", expand=True)

            def close(_event=None):
                with _lock:
                    if _live.get("root") is root:
                        _live["root"] = None
                        _live["add"] = None
                try:
                    root.destroy()
                except Exception:
                    pass

            # ---- header
            head = tk.Frame(outer, bg=BG)
            head.pack(fill="x", padx=14, pady=(10, 6))
            heading = tk.Label(head, text="", bg=BG, fg=ACCENT, anchor="w",
                               font=("Segoe UI", 11, "bold"))
            heading.pack(side="left")
            counter = tk.Label(head, text="", bg=BG, fg=MUTED,
                               font=("Segoe UI", 8))
            counter.pack(side="left", padx=(6, 0), pady=(3, 0))
            x = tk.Label(head, text="✕", bg=BG, fg=MUTED,
                         font=("Segoe UI", 12), cursor="hand2")
            x.pack(side="right")
            x.bind("<Button-1>", close)
            x.bind("<Enter>", lambda _e: x.config(fg="#ff6b6b"))
            x.bind("<Leave>", lambda _e: x.config(fg=MUTED))

            # ---- scrollable list
            body_f = tk.Frame(outer, bg=BG)
            body_f.pack(fill="both", expand=True, padx=10)
            canvas = tk.Canvas(body_f, bg=BG, highlightthickness=0, width=W - 24)
            canvas.pack(side="left", fill="both", expand=True)
            inner = tk.Frame(canvas, bg=BG)
            window_id = canvas.create_window((0, 0), window=inner, anchor="nw",
                                             width=W - 24)
            scrollbar = tk.Scrollbar(
                body_f, orient="vertical", command=canvas.yview,
                width=13, borderwidth=0, relief="flat", highlightthickness=0,
                elementborderwidth=0, troughcolor="#141821", bg="#5b6478",
                activebackground=ACCENT)
            canvas.configure(yscrollcommand=scrollbar.set)

            hint = tk.Label(outer, text="", bg=BG, fg="#6b7280", anchor="w",
                            font=("Segoe UI", 8))
            hint.pack(fill="x", padx=14, pady=(4, 8))

            def on_wheel(event):
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

            def bind_deep(widget, seq, fn):
                """Tk does not bubble. Bind every descendant or the binding is
                dead wherever a child covers the parent."""
                widget.bind(seq, fn)
                for child in widget.winfo_children():
                    bind_deep(child, seq, fn)

            def open_item(item):
                def handler(event=None):
                    # IDXAlert could fall back to the disclosure page here.
                    # JCIAlert reads from a dozen outlets, so a row with no
                    # link does nothing rather than opening something
                    # arbitrary.
                    files = item.get("files") or []
                    if not files:
                        return
                    if len(files) == 1:
                        webbrowser.open(files[0]["url"])
                        return
                    menu = tk.Menu(w, tearoff=0, bg=ROW_BG, fg=FG,
                                   activebackground=ROW_HOVER,
                                   activeforeground=ACCENT,
                                   font=("Segoe UI", 9), borderwidth=0)
                    for f in files:
                        menu.add_command(label=entry_label(f.get("name")),
                                         command=lambda u=f["url"]: webbrowser.open(u))
                    menu.add_separator()
                    menu.add_command(
                        label="Open all %d" % len(files),
                        command=lambda fs=files: [webbrowser.open(f["url"]) for f in fs])
                    try:
                        if event is not None:
                            menu.tk_popup(event.x_root, event.y_root)
                        else:
                            menu.tk_popup(w.winfo_rootx() + 40, w.winfo_rooty() + 40)
                    finally:
                        menu.grab_release()
                return handler

            def paint(frame, colour):
                def h(_e):
                    frame.config(bg=colour)
                    for c in frame.winfo_children():
                        try:
                            c.config(bg=colour)
                            for sc in c.winfo_children():
                                sc.config(bg=colour)
                        except Exception:
                            pass
                return h

            def render():
                for child in inner.winfo_children():
                    child.destroy()
                data = live["rows"]
                built = []
                for it in data:
                    base = NEW_BG if _row_key(it) in live["fresh"] else ROW_BG
                    r = tk.Frame(inner, bg=base, cursor="hand2")
                    r.pack(fill="x", pady=(0, 4))
                    line = tk.Frame(r, bg=base)
                    line.pack(fill="x", padx=10, pady=(6, 0))
                    tk.Label(line, text=(it.get("ticker") or "PASAR"), bg=base,
                             fg=ACCENT, font=("Segoe UI", 9, "bold")).pack(side="left")
                    stamp = (it.get("posted") or "")[-8:-3]
                    if stamp:
                        tk.Label(line, text="  " + stamp, bg=base, fg=MUTED,
                                 font=("Segoe UI", 8)).pack(side="left")
                    n = len(it.get("files") or [])
                    if n == 1:
                        tk.Label(line, text="1 story", bg=base, fg=MUTED,
                                 font=("Segoe UI", 8)).pack(side="right")
                    elif n > 1:
                        tk.Label(line, text="%d stories ▾" % n, bg=base, fg=ACCENT,
                                 font=("Segoe UI", 8, "bold")).pack(side="right")
                    tk.Label(r, text=it.get("title", ""), bg=base, fg=TITLE_FG,
                             justify="left", anchor="w", font=("Segoe UI", 9),
                             wraplength=W - 70).pack(fill="x", padx=10, pady=(1, 7))
                    bind_deep(r, "<Button-1>", open_item(it))
                    r.bind("<Enter>", paint(r, ROW_HOVER))
                    r.bind("<Leave>", paint(r, base))
                    built.append(r)

                total = len(data)
                heading.config(text="NEWS: %s" % (
                    (data[0].get("ticker") or "new story") if total == 1
                    else "%d tickers" % total))
                scrolls = total > max_visible
                counter.config(text="showing %d of %d" % (max_visible, total)
                               if scrolls else "")
                if scrolls and not scrollbar.winfo_ismapped():
                    scrollbar.pack(side="right", fill="y", padx=(4, 0))
                    canvas.itemconfig(window_id, width=W - 46)
                elif not scrolls and scrollbar.winfo_ismapped():
                    scrollbar.pack_forget()
                    canvas.itemconfig(window_id, width=W - 24)

                hint.config(text=(
                    ("scroll for %d more  ·  " % (total - max_visible)) if scrolls else "")
                    + "left-click a row to open its file  ·  right-click or ✕ to close")

                # Rows are NOT a fixed height - a wrapped title is taller.
                # Summing requested heights clips rows mid-way, so measure
                # where the rows actually landed instead.
                inner.update_idletasks()
                total_h = inner.winfo_reqheight()
                if len(built) > max_visible:
                    visible_h = built[max_visible].winfo_y()   # top of the first hidden row
                else:
                    visible_h = total_h
                visible_h = max(60, min(visible_h, canvas.winfo_screenheight() - 260))
                canvas.configure(height=visible_h,
                                 scrollregion=(0, 0, W, max(total_h, visible_h)))
                bind_deep(w, "<Button-3>", close)
                if scrolls:
                    bind_deep(w, "<MouseWheel>", on_wheel)

                w.update_idletasks()
                H = w.winfo_reqheight()
                sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
                x, y = corner_geometry(corner, W, H, sw, sh)
                w.geometry("%dx%d+%d+%d" % (W, H, x, y))
                canvas.update_idletasks()      # after layout settles
                canvas.yview_moveto(0)         # newest at the top

            def arm_timer():
                if live["timer"] is not None:
                    try:
                        w.after_cancel(live["timer"])
                    except Exception:
                        pass
                    live["timer"] = None
                if secs > 0:
                    live["timer"] = w.after(int(secs * 1000), close)

            def add_items(batch):
                """Merge a new batch in at the top and restart the timer."""
                rows, fresh = merge_rows(live["rows"], batch)
                if not fresh:
                    return
                live["fresh"] = fresh
                live["rows"] = rows
                render()
                arm_timer()
                try:
                    w.attributes("-topmost", True)
                    w.lift()
                except Exception:
                    pass

            with _lock:
                _live["root"] = root
                _live["add"] = add_items

            render()
            arm_timer()
            w.focus_force()
            root.mainloop()
        except Exception as exc:
            err.append(exc)
            with _lock:
                _live["root"] = None
                _live["add"] = None

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=(secs + 4) if secs > 0 else 2.5)
    if err:
        raise err[0]
    if secs <= 0 and not TRAY_MODE:
        # A sticky popup lives on a daemon thread. In one-shot mode the process
        # would exit and take the window with it, so block until it closes.
        # The tray keeps running anyway, so it returns immediately there.
        t.join()
