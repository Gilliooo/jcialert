#!/usr/bin/env python3
"""
jciview - turn engine output into the rows the popup draws.

Pure. No Tk, no threads. The popup itself is IDXAlert's, lifted unchanged
because every awkward line in it is load-bearing (see that module's docstring:
Tk does not bubble events, rows are not a fixed height, only the X closes it).
What JCIAlert has to supply is the row schema it expects:

    {"key": ..., "ticker": ..., "title": ..., "posted": ...,
     "files": [{"name": ..., "url": ...}]}

The menu entry key is "url", NOT "path". jcipopup does `webbrowser.open(f["url"])`
directly, with no .get() and no fallback, so getting this wrong is a KeyError on
click - and no test that invents its own schema can catch it. It was wrong here
until the popup was actually read.

ONE ROW PER TICKER, NOT PER STORY
---------------------------------
A row is a TICKER and its menu is that ticker's stories. The alternative -
one row per story, with the outlets that carried it in the menu - loses badly
in a three-row window: a busy BBCA morning becomes four BBCA rows and pushes
everything else off screen, while the thing you actually want to know is
"BBCA, four items, newest is X".

So the row shows the NEWEST headline (informative on its own) and the menu
carries every story for that ticker, each stamped with its time and outlet.
The popup already renders the entry count, so nothing is hidden by this.

IDXAlert's "several attachments -> a menu to pick one" widget does the work
unchanged; only what the entries MEAN changes.

A GroupedAlert - the sector collapse - is the layer above: one row for the
sector, whose menu entries are the individual stories. Sector -> ticker ->
story, and a burst never occupies twelve rows.

One menu entry per alert means one per STORY, not one per outlet: a cluster
only alerts on its first sighting, so the later outlets that attach to it
never produce a second alert to list.

THE FORMAT OF `posted` IS NOT FREE
----------------------------------
idx3popup renders the clock as `(it.get("posted") or "")[-8:-3]`. That slice
only yields HH:MM if the string ends "... HH:MM:SS". So `posted` is formatted
"%Y-%m-%d %H:%M:%S" in WIB, and there is a test pinning the slice itself rather
than the format string - the format is a means, the slice is the contract.
"""

from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))

# Short labels for the sector badge. IDX-IC's names are far too long for the
# small ticker chip the popup draws (it fits about six characters).
SECTOR_SHORT = {
    "Keuangan": "KEU",
    "Energi": "ENERGI",
    "Barang Baku": "BASIC",
    "Perindustrian": "INDUS",
    "Barang Konsumen Primer": "CONS-P",
    "Barang Konsumen Non-Primer": "CONS-N",
    "Kesehatan": "HEALTH",
    "Properti & Real Estat": "PROP",
    "Teknologi": "TECH",
    "Infrastruktur": "INFRA",
    "Transportasi & Logistik": "TRANS",
}

# What the outlets are called in the menu. The engine's source names are
# identifiers; these are what a person reads at 9am.
SOURCE_LABEL = {
    "katadata": "Katadata",
    "kontan-investasi": "Kontan Investasi",
    "kontan-keuangan": "Kontan Keuangan",
    "idxchannel": "IDX Channel",
    "cnbc-market": "CNBC Indonesia",
    "detik-finance": "Detik Finance",
    "liputan6-bisnis": "Liputan6",
    "wartaekonomi": "Warta Ekonomi",
    "kompas-money": "Kompas Money",
}

POSTED_FMT = "%Y-%m-%d %H:%M:%S"


def source_label(name):
    return SOURCE_LABEL.get(name, (name or "sumber").replace("-", " ").title())


def sector_badge(sector):
    return SECTOR_SHORT.get(sector, (sector or "PASAR")[:6].upper())


def posted(dt):
    """WIB, in the exact shape idx3popup's [-8:-3] clock slice needs."""
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=WIB)
    return dt.astimezone(WIB).strftime(POSTED_FMT)


def trim(text, limit=70):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _clock(dt):
    p = posted(dt)
    return p[-8:-3] if p else "--:--"


def story_entry(alert):
    """One menu line for one story: when, who carried it, what it said."""
    return {"name": f"{_clock(alert.item.published)} · "
                    f"{source_label(alert.item.source)} · "
                    f"{trim(alert.item.title, 54)}",
            "url": alert.item.url,
            "source": alert.item.source,
            "when": posted(alert.item.published),
            "cluster": alert.cluster_id}


def _newest_first(alerts):
    return sorted(alerts, key=lambda a: posted(a.item.published), reverse=True)


def row_for_ticker(alerts):
    """One row for one ticker. `alerts` must all share a ticker."""
    ordered = _newest_first(alerts)
    top = ordered[0]
    return {
        "key": f"t:{top.ticker or 'PASAR'}",
        "ticker": top.ticker or "PASAR",
        "title": top.item.title,
        "posted": posted(top.item.published),
        "files": [story_entry(a) for a in ordered],
        "rule": top.rule,
        "categories": sorted(set().union(*(a.categories or set()
                                           for a in ordered)) or ()),
        "sector": top.sector,
        "count": len(ordered),
        "grouped": False,
    }


def row_for_alert(alert):
    """A single alert is just the one-element case of a ticker row."""
    return row_for_ticker([alert])


def group_title(group):
    tickers = group.tickers
    shown = ", ".join(tickers[:6])
    if len(tickers) > 6:
        shown += f" +{len(tickers) - 6}"
    n = len(tickers) or len(group.alerts)
    return f"{n} {group.sector} names: {shown}"


def row_for_group(group):
    newest = max((a.item.published for a in group.alerts if a.item.published),
                 default=None)
    files = []
    for a in _newest_first(group.alerts):
        files.append({"name": f"{a.ticker or '—'} · {trim(a.item.title, 58)}",
                      "url": a.item.url, "source": a.item.source,
                      "when": posted(a.item.published),
                      "cluster": a.cluster_id})
    return {
        "key": f"g{group.sector}:{newest.isoformat() if newest else ''}",
        "ticker": sector_badge(group.sector),
        "title": group_title(group),
        "posted": posted(newest),
        "files": files,
        "rule": "",
        "categories": [],
        "sector": group.sector,
        "grouped": True,
    }


def rows_for(payloads):
    """Engine payloads -> popup rows, newest first.

    Accepts Alert and GroupedAlert in any order, which is what the dispatcher
    emits; groups are sent before individual alerts but the popup sorts by
    time, so ordering here is not relied upon.
    """
    rows, by_ticker, order = [], {}, []
    for p in payloads or ():
        if hasattr(p, "alerts"):
            rows.append(row_for_group(p))       # sector rows stay whole
            continue
        key = p.ticker or "PASAR"
        if key not in by_ticker:
            by_ticker[key] = []
            order.append(key)
        by_ticker[key].append(p)
    for key in order:
        rows.append(row_for_ticker(by_ticker[key]))
    return sorted(rows, key=lambda r: r.get("posted") or "", reverse=True)


def summary(rows):
    """The one-line heading, e.g. '3 new stories' or a single ticker."""
    if not rows:
        return ""
    if len(rows) == 1:
        r = rows[0]
        if r["grouped"]:
            return r["sector"]
        n = r.get("count", 1)
        return r["ticker"] if n == 1 else f"{r['ticker']} · {n} stories"
    total = sum(r.get("count", len(r["files"])) for r in rows)
    return f"{total} new stories"
