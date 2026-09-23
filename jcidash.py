#!/usr/bin/env python3
"""
JCIAlert - the news log, and the view over it.

THE FILTERS ARE A VIEW, NOT A GATE
----------------------------------
Everything that comes off the wire is written to news.csv. The filters decide
which of those rows the dashboard SHOWS by default; they never decide which
rows exist. That is the difference between a log you can tune against and a
log that only ever agrees with the settings that produced it - if a rule is
too tight, the only evidence is the headline it rejected, and a design that
throws those away can never show you the mistake.

So: `recorder(path)` writes every Record. `read(path)` reads them back.
`view(rows, ...)` is the filtering, and it is pure - the window calls it, the
tests call it, and neither needs the other.

Rows are plain dicts with string values, straight from csv.DictReader. No
row objects: the window renders strings, and a schema nobody can drift from
is worth more here than types. jciview's row contract was broken exactly once,
by inventing a key ("path" vs "url"), and that was enough.
"""

import csv
import json
import os
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))

# ts_wib first because the dashboard is sorted and read by time, and a human
# reading the raw CSV in Excel should not have to hunt for the clock.
NEWS_COLUMNS = ["ts_wib", "status", "ticker", "sector", "source", "title",
                "rule", "categories", "reason", "match_rule", "confidence",
                "feed_category", "cluster", "url", "ts_utc"]

STATUSES = ("alert", "dropped")


def _now():
    return datetime.now(timezone.utc)


def row_of(rec, now=None):
    """Record -> dict. The published time wins over the clock: a story's own
    timestamp is what a reader means by "when"."""
    now = now or _now()
    when = getattr(rec.item, "published", None) or now
    hit = rec.hits[0] if rec.hits else None
    return {
        "ts_wib": when.astimezone(WIB).strftime("%Y-%m-%d %H:%M:%S")
                  if getattr(when, "tzinfo", None) else "",
        "status": rec.status,
        "ticker": rec.ticker or "",
        "sector": rec.sector or "",
        "source": rec.item.source,
        "title": rec.item.title,
        "rule": rec.rule or "",
        "categories": " ".join(sorted(rec.categories or ())),
        "reason": rec.reason or "",
        "match_rule": hit.rule if hit else "",
        "confidence": f"{hit.confidence:.2f}" if hit else "",
        "feed_category": getattr(rec.item, "feed_category", "") or "",
        "cluster": rec.cluster_id or "",
        "url": rec.item.url or "",
        "ts_utc": now.isoformat(timespec="seconds"),
    }


# Every headline, not just the alerts, means roughly a megabyte a week. Left
# alone that is a hundred megabytes a year in the user's data folder, and a
# file no editor will open. One rotation keeps the recent past whole and the
# older past available, at a bounded cost - and it is a rename, not a rewrite,
# so it cannot lose a row halfway through.
# Sized against the ask: the dashboard offers a "This year" filter, so the log
# has to be able to answer it. At roughly half a megabyte of headlines a day,
# 64 MB is about eight months, and the one rotated copy doubles that. Raise it
# if a year of history matters more than the disk.
MAX_BYTES = 64 * 1024 * 1024


def rotate(path, max_bytes=MAX_BYTES):
    try:
        if os.path.getsize(path) < max_bytes:
            return False
        os.replace(path, path + ".1")
        return True
    except OSError:
        return False


def append(path, rows):
    """Append rows, writing the header only into a new file. Never raises:
    a full disk must not take the app down, the same rule csv_channel follows."""
    rows = [r for r in rows if r]
    if not rows:
        return 0
    rotate(path)
    try:
        new = not os.path.exists(path) or os.path.getsize(path) == 0
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, NEWS_COLUMNS, extrasaction="ignore")
            if new:
                w.writeheader()
            for r in rows:
                w.writerow(r)
        return len(rows)
    except OSError:
        return 0


def recorder(path):
    """-> a callable for Watcher.recorders."""
    def record(records):
        append(path, [row_of(r) for r in records])
    return record


def read(path, limit=20000):
    """Newest first, capped. The cap is a READING limit, not a retention one -
    the file keeps everything; this is how much of it the window is willing to
    hold in memory at once."""
    try:
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except (OSError, csv.Error):
        return []
    rows = [r for r in rows if r.get("title")]
    rows.reverse()                      # the file is append-ordered
    return rows[:limit] if limit else rows


# A Treeview redraws every row it holds, so the DRAWN count is what decides
# whether the window feels alive. Filtering happens over everything read; only
# the drawing is capped, and the status bar says when the cap bit.
DRAW_LIMIT = 2000


# ------------------------------------------------------------ read receipts

def load_opened(path):
    """URLs the user has already clicked, so the dashboard can show what is
    NEW at a glance. A separate small file rather than a column in news.csv:
    the log is append-only by design (that is what makes it cheap and
    crash-safe), and marking a row read is an update."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def save_opened(path, opened, keep=4000):
    if len(opened) > keep:               # newest by the stamp we wrote
        opened = dict(sorted(opened.items(), key=lambda kv: kv[1])[-keep:])
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(opened, f)
        os.replace(tmp, path)
    except OSError:
        pass
    return opened


# ------------------------------------------------------------------ the view

# Period names in the order the dropdown shows them. "Today" means since
# midnight WIB, not the last 24 hours - a story from 11pm yesterday is
# yesterday's news to a reader, whatever the clock arithmetic says.
PERIODS = ["Today", "This week", "This month", "This year", "All"]


def period_start(name, now=None):
    """-> the WIB datetime a period begins at, or None for 'All'."""
    n = (now or _now()).astimezone(WIB)
    midnight = n.replace(hour=0, minute=0, second=0, microsecond=0)
    if name == "Today":
        return midnight
    if name == "This week":              # Monday, the Indonesian trading week
        return midnight - timedelta(days=midnight.weekday())
    if name == "This month":
        return midnight.replace(day=1)
    if name == "This year":
        return midnight.replace(month=1, day=1)
    return None

def view(rows, show_all=False, query="", source="", ticker="", status="",
         hours=0, period="", unread_only=False, opened=None, now=None):
    """Pure. Every argument narrows; none of them reorder.

    show_all=False is the DEFAULT and means "what the filters let through" -
    that is the dashboard's normal face. Turning it on is how you audit the
    filters, which is why the switch is one click and not a config key.
    """
    out = list(rows)
    if not show_all:
        out = [r for r in out if r.get("status") == "alert"]
    if status:
        out = [r for r in out if r.get("status") == status]
    if source:
        out = [r for r in out if r.get("source") == source]
    if ticker:
        t = ticker.strip().upper()
        out = [r for r in out if (r.get("ticker") or "").upper() == t]
    q = (query or "").strip().lower()
    if q:
        # Title AND ticker: typing "bbca" should find the headline that names
        # the bank in prose as readily as the one tagged with it.
        out = [r for r in out
               if q in (r.get("title") or "").lower()
               or q in (r.get("ticker") or "").lower()]
    if hours:
        cut = (now or _now()).astimezone(WIB) - timedelta(hours=hours)
        out = [r for r in out if _within(r.get("ts_wib"), cut)]
    if period:
        start = period_start(period, now)
        if start:
            out = [r for r in out if _within(r.get("ts_wib"), start)]
    if unread_only:
        seen = opened or {}
        out = [r for r in out if r.get("url") and r["url"] not in seen]
    return out


def _within(stamp, cut):
    try:
        when = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=WIB)
    except (TypeError, ValueError):
        return True             # undated rows are kept; dropping them silently
    return when >= cut          # is how a source with no clock disappears


def why_of(row):
    """The one cell that means something in both states: the rule that accepted
    it, or the reason it did not. Lives here, not in the window, because the
    sort has to order by the same string the table draws."""
    if row.get("status") == "alert":
        return row.get("rule") or "alerted"
    return row.get("reason") or ""


def sort_rows(rows, key, descending=False):
    """Stable sort by one column. Pure.

    Time sorts as text on purpose: the stamps are zero-padded
    `YYYY-MM-DD HH:MM:SS`, so lexical order IS chronological order, and a row
    with no timestamp sorts to one end instead of raising. Blanks always sink
    to the bottom whichever way the arrow points - an empty ticker is not
    "before A", it is "no answer", and burying it is what a reader wants from
    both clicks.
    """
    def cell(r):
        return str((why_of(r) if key == "_why" else r.get(key, "")) or "")

    ranked = sorted(rows, key=lambda r: (cell(r) == "", cell(r)),
                    reverse=descending)
    if descending:                       # keep the blanks at the bottom
        blank = [r for r in ranked if cell(r) == ""]
        ranked = [r for r in ranked if cell(r) != ""] + blank
    return ranked


def sources_in(rows):
    return sorted({r.get("source", "") for r in rows if r.get("source")})


def summary(rows):
    """One line for the window's status bar."""
    alerts = sum(1 for r in rows if r.get("status") == "alert")
    return {"total": len(rows), "alerts": alerts,
            "dropped": len(rows) - alerts,
            "sources": len(sources_in(rows)),
            "tickers": len({r.get("ticker") for r in rows if r.get("ticker")})}


def why_summary(rows, top=8):
    """The counts that tuning actually needs: what is being rejected, and by
    what. Sorted by weight, because the long tail is never the problem."""
    tally = {}
    for r in rows:
        if r.get("status") == "alert":
            continue
        why = r.get("reason") or "unknown"
        tally[why] = tally.get(why, 0) + 1
    return sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
