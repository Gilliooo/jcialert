#!/usr/bin/env python3
"""
jci - JCIAlert's entry point.

    python jci.py --once              poll every source once, show what it WOULD
                                      alert and why, write nothing
    python jci.py --once --commit     same, but record what it saw
    python jci.py --watch             run the loop in the console
    python jci.py --verify-sources    freshness invariant per source
    python jci.py --show-config       where config and data actually live

WHY --once EXISTS
-----------------
Testing the plumbing and tuning the rules are different jobs, and doing both at
once makes a plumbing bug indistinguishable from a noisy rule. --once makes
every decision visible: which source answered, which ticker was attributed by
which rule at what confidence, which filter rule accepted, and for everything
dropped, the reason. At a quiet hour you can check every line against the
source page by hand. That is how the class of bug that cost IDXAlert two
versions gets caught - output that is perfectly valid and quietly wrong.

It writes nothing unless --commit, so you can run it repeatedly and see the
same decisions instead of watching them vanish into the seen-set.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import jcidash
import jcidoctor
import jciengine
import jcifilter
import jcimatch
import jcinet
import jcioptions
import jcisource
import jciview

VERSION = "1.0.1"
APP = "JCIAlert " + VERSION
WIB = timezone(timedelta(hours=7))

def app_dir():
    """The folder the user thinks the app lives in.

    Under PyInstaller --onefile, `__file__` points into a TEMP EXTRACTION
    DIRECTORY that is deleted on exit. An exe using it would read a config
    that is not the one next to it, write settings nobody can find, and lose
    them at shutdown. When frozen, everything the user owns - config.json,
    emiten.json, seen.json and the logs folder hang off the executable
    instead.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


HERE = app_dir()
CONFIG_PATH = os.path.join(HERE, "config.json")
EMITEN_PATH = os.path.join(HERE, "emiten.json")

# A rule whose tickers are exactly this is filled in from cfg["watchlist"].
# Without it, setting a watchlist would do nothing: an empty ticker list means
# ANY ticker, so the default rule would keep alerting on the whole market and
# the watchlist box would look broken.
WATCHLIST_TOKEN = "@watchlist"


_logfile = {"dir": None}


def set_log_dir(path):
    """One file per calendar day, never auto-deleted - IDXAlert's rule. The
    log is how you reconstruct what the app did while you were not watching,
    so pruning it is exactly backwards."""
    _logfile["dir"] = path
    if path:
        os.makedirs(path, exist_ok=True)


def log(msg):
    stamp = datetime.now(WIB)
    line = f"[{stamp.strftime('%H:%M:%S')}] {msg}"
    print(line)
    d = _logfile["dir"]
    if not d:
        return
    try:
        with open(os.path.join(d, f"jci-{stamp:%Y-%m-%d}.log"), "a",
                  encoding="utf-8") as f:
            f.write(f"[{stamp:%Y-%m-%d %H:%M:%S}] {msg}\n")
    except OSError:
        pass                     # logging must never take the app down


# ------------------------------------------------------------------- config

def resolve_data_dir(cfg):
    """Where seen.json and the logs go.

    Deliberately does NOT require a probe file to delete cleanly. IDXAlert 2.x
    did, and on a filesystem that allows create but not unlink it silently
    exiled the whole app to LocalAppData - the data was fine, it was just
    somewhere nobody looked.
    """
    want = (cfg or {}).get("data_dir") or HERE
    want = os.path.abspath(os.path.expanduser(want))
    try:
        os.makedirs(want, exist_ok=True)
        probe = os.path.join(want, ".writable")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        try:
            os.remove(probe)
        except OSError:
            pass                       # created fine; that is what we needed
        return want
    except OSError:
        fallback = os.path.join(
            os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "JCIAlert")
        os.makedirs(fallback, exist_ok=True)
        return fallback


def explain_json_error(path, raw, exc):
    """A broken config must not produce a Python stack trace.

    config.json is hand-edited - it is one click away in the tray menu and the
    whole point of the Filters tab is that you can also just open the file. A
    missing pair of quotes is the single most likely mistake, and answering it
    with a traceback through json/decoder.py tells the person nothing about
    which line to fix. It also breaks IDXAlert's UI-copy rule: no internal
    jargon in anything the user sees, and config.json IS something they see.
    """
    lines = raw.splitlines()
    n = getattr(exc, "lineno", 0) or 0
    col = getattr(exc, "colno", 0) or 0
    out = [f"\n{path} is not valid JSON.",
           f"  {exc.msg} at line {n}, column {col}", ""]
    for i in range(max(1, n - 2), min(len(lines), n + 1) + 1):
        mark = ">" if i == n else " "
        out.append(f"  {mark} {i:>3} | {lines[i-1]}")
        if i == n:
            out.append("        | " + " " * max(0, col - 1) + "^")
    out += ["",
            "  Most often this is a missing pair of quotes. Every value must be",
            '  quoted:  "watchlist": ["BBCA", "BMRI"]   not   [BBCA, BMRI]',
            "",
            "  Fix that line, or delete the file and run again to get a fresh",
            "  default (your settings will be lost)."]
    return "\n".join(out)


def load_config(path=CONFIG_PATH, create=True):
    """Read config.json, writing a documented default on first launch.

    Exiting because there is no config would make the exe useless to anyone it
    is handed to - the same requirement IDXAlert has.
    """
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        try:
            return json.loads(raw), False
        except json.JSONDecodeError as exc:
            raise SystemExit(explain_json_error(path, raw, exc))
    cfg = jcioptions.default_config()
    cfg["filters"]["rules"] = [
        dict(jcioptions.new_rule("My coverage"), tickers=[WATCHLIST_TOKEN]),
    ]
    cfg["_comment"] = (
        "Set your tickers in 'watchlist'. A rule whose tickers are "
        f"[\"{WATCHLIST_TOKEN}\"] uses that list. An empty ticker list means "
        "ANY ticker, not none.")
    if create:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
    return cfg, True


def expand_watchlist(filters, watchlist):
    """Substitute the @watchlist token. Pure, so it is testable."""
    out = json.loads(json.dumps(filters))
    wl = [t.strip().upper() for t in (watchlist or []) if str(t).strip()]
    for rule in out.get("rules") or []:
        tk = rule.get("tickers") or []
        if any(str(t).strip().lower() == WATCHLIST_TOKEN for t in tk):
            rest = [t for t in tk if str(t).strip().lower() != WATCHLIST_TOKEN]
            rule["tickers"] = sorted(set(rest) | set(wl))
    return out


def load_emiten(path=EMITEN_PATH):
    if not os.path.exists(path):
        raise SystemExit(
            f"{path} is missing.\nRun:  python build_aliases.py --report\n"
            "(needs a connection that idx.co.id accepts - not a datacenter IP)")
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("emiten", {})


def build_filters(cfg):
    """The compiled rules for a config. Its own function because the rules are
    compiled ONCE at startup and must be RE-compiled whenever the config
    changes - editing the watchlist in Options was silently doing nothing
    until the next restart, because only this call reads it."""
    return jcifilter.FilterSet(
        expand_watchlist(cfg.get("filters") or jcifilter.DEFAULT_FILTERS,
                         cfg.get("watchlist")))


def build(cfg, store_path=None):
    emiten = load_emiten()
    table = jcimatch.Table(emiten)
    filters = build_filters(cfg)
    sources = jcisource.build_sources(set(emiten), cfg)
    store = jciengine.Store(store_path, cfg.get("seen_max", 5000)).load()
    pipeline = jciengine.Pipeline(table, filters, store, cfg, emiten)
    fetch = jcinet.make_fetch()
    return emiten, table, filters, sources, store, pipeline, fetch


# ---------------------------------------------------------------- dry run

def _age(dt, now):
    if not dt:
        return "     ?"
    m = (now - dt).total_seconds() / 60.0
    return f"{m:5.0f}m" if m < 120 else f"{m/60:5.1f}h"


def cmd_once(cfg, args):
    now = datetime.now(timezone.utc)
    store_path = os.path.join(resolve_data_dir(cfg), "seen.json") if args.commit else None
    emiten, table, filters, sources, store, pipeline, fetch = build(cfg, store_path)

    print(f"\n{APP} - dry run"
          + ("" if args.commit else ", nothing will be written"))
    enabled = [s for s in sources if s.enabled]
    print(f"{len(enabled)} sources enabled · "
          f"{len(table.tickers)} tickers known · "
          f"{len(filters.rules)} filter rules active\n")

    print("sources")
    items = []
    for src in enabled:
        t0 = time.time()
        try:
            body = fetch(src)
        except Exception as exc:
            print(f"  {src.name:<18} {'ERR':>5} {'':>7}  "
                  f"{type(exc).__name__}: {str(exc)[:44]}")
            continue
        ms = int((time.time() - t0) * 1000)
        if body is None:
            print(f"  {src.name:<18} {'304':>5} {ms:>5}ms  not modified")
            continue
        try:
            got = src.parse(body)
        except Exception as exc:
            print(f"  {src.name:<18} {'BAD':>5} {ms:>5}ms  "
                  f"parse failed: {type(exc).__name__}: {exc}")
            continue
        newest, _mins, stale = src.freshness(got, now)
        flag = ("STALE" if stale
                else "quiet" if src.stale_after(now) is None else "ok")
        print(f"  {src.name:<18} {200:>5} {ms:>5}ms  {len(got):>3} items  "
              f"newest {_age(newest, now)}  {flag}")
        for it in got:
            items.append((src, it))

    if not items:
        print("\nnothing fetched.")
        return 1

    # seed=False on purpose: a dry run against a fresh store would otherwise
    # seed silently and show zero decisions, which looks like "nothing matched".
    res = pipeline.process([i for _s, i in items], now=now,
                           seed=False if not args.commit else None)

    print(f"\ndecisions   ({len(items)} items)")
    if not res.alerts and not res.groups:
        print("  nothing would alert")
    for g in res.groups:
        # len(g.alerts) is STORIES; g.tickers is the distinct set. Printing the
        # story count next to the ticker list read as "4 tickers: SSIA, TLKM",
        # which is visibly wrong and undersells what was collapsed.
        n_s, n_t = len(g.alerts), len(g.tickers)
        print(f"  GROUP  {g.sector} · {n_s} "
              f"{'story' if n_s == 1 else 'stories'} across {n_t} "
              f"{'ticker' if n_t == 1 else 'tickers'}: {', '.join(g.tickers)}")
    for a in res.alerts:
        h = a.hits[0] if a.hits else None
        print(f"  ALERT  {a.ticker or '-':<5} "
              f"{(h.rule + ' ' + format(h.confidence, '.2f')) if h else '':<12} "
              f"[{a.rule}]  {', '.join(sorted(a.categories)) or '-'}")
        print(f"         {a.item.title[:96]}")
        print(f"         {a.item.source} · {jciview.posted(a.item.published)} · "
              f"{a.item.url}")

    print("\nsummary")
    total_drop = sum(res.dropped.values())
    print(f"  {len(items)} fetched · {len(res.alerts)} would alert · "
          f"{len(res.groups)} grouped · {total_drop} dropped")
    for why, n in sorted(res.dropped.items(), key=lambda kv: -kv[1]):
        print(f"    {n:>5}  {why}")
    if not args.commit:
        print("\n  (nothing written - add --commit to record what was seen)")
    return 0


def cmd_density(cfg, args):
    """Which sources actually carry emiten news? Measured, not eyeballed.

    "Does Katadata carry enough tickers to be worth polling" is a judgement
    call until you count it. This enables EVERY registered source - including
    the ones shipped disabled - fetches once, and runs the real matcher over
    every headline. The numbers that decide the launch source list are:

      fresh     items inside the age cutoff - the ONLY items a live poll can
                ever alert on. Everything older is dropped before matching.
      f-tick    of those fresh items, how many name a ticker.
      f-tick%   THE NUMBER THAT DECIDES. Share of what this source will
                actually contribute that is about a company.
      ticker%   the same over the WHOLE feed, stale items included. Kept
                because it is a bigger sample, but it can badly mislead: on
                2026-09-07 cnbc-market scored 22% over 100 items of which
                exactly ONE was fresh. A long backlog with good historical
                density is not the same as a source that publishes company
                news today.
      yours     fresh items naming a ticker on YOUR watchlist. The only
                column that becomes a popup.

    Run it during market hours on a weekday. A Saturday run measures nothing:
    the feeds are full of weekend filler.
    """
    now = datetime.now(timezone.utc)
    emiten = load_emiten()
    table = jcimatch.Table(emiten)
    sources = jcisource.build_sources(set(emiten), cfg)
    for src in sources:
        src.enabled = True                     # measure the candidates too
    fetch = jcinet.make_fetch()
    watch = {t.strip().upper() for t in (cfg.get("watchlist") or [])}
    cutoff = cfg.get("max_item_age_minutes", 180)
    minconf = cfg.get("min_confidence", 0.5)

    print(f"\n{APP} - source density, {datetime.now(WIB):%a %d %b %H:%M} WIB")
    if datetime.now(WIB).weekday() >= 5:
        print("  WARNING: it is the weekend. These numbers mean very little.")
    print(f"  watchlist: {', '.join(sorted(watch)) or '(empty)'}"
          f"   age cutoff: {cutoff}m\n")
    print(f"  {'source':<18} {'items':>5} {'fresh':>5} {'f-tick':>6} "
          f"{'f-tick%':>8} {'ticker%':>8} {'yours':>6}   top tickers")
    print("  " + "-" * 96)

    rows = []
    for src in sources:
        try:
            body = fetch(src)
            got = src.parse(body) if body is not None else []
        except Exception as exc:
            why = (f"timed out after {src.timeout}s" if isinstance(exc, TimeoutError)
                   else f"{type(exc).__name__}: {str(exc)[:40]}")
            print(f"  {src.name:<18} {'ERR':>5}   {why}")
            continue
        seen_t, with_t, yours, fresh, fresh_t = {}, 0, 0, 0, 0
        for it in got:
            hits = jcimatch.find_tickers(it.title, table,
                                         min_confidence=minconf)
            tick = {h.ticker for h in hits}
            is_fresh = bool(it.published) and (
                now - it.published).total_seconds() / 60 <= cutoff
            if tick:
                with_t += 1
                for t in tick:
                    seen_t[t] = seen_t.get(t, 0) + 1
            if is_fresh:
                fresh += 1
                if tick:
                    fresh_t += 1
                if tick & watch:
                    yours += 1
        pct = (100.0 * with_t / len(got)) if got else 0.0
        fpct = (100.0 * fresh_t / fresh) if fresh else 0.0
        top = ", ".join(f"{t}({n})" for t, n in
                        sorted(seen_t.items(), key=lambda kv: -kv[1])[:4])
        rows.append((src.name, len(got), with_t, pct, yours, fresh, fresh_t, fpct))
        print(f"  {src.name:<18} {len(got):>5} {fresh:>5} {fresh_t:>6} "
              f"{fpct:>7.0f}% {pct:>7.0f}% {yours:>6}   {top}")

    print("\n  reading it")
    MIN_FRESH = 3          # below this the percentage is noise, not a measure
    judged = [r for r in rows if r[5] >= MIN_FRESH]
    thin = [r for r in rows if r[1] and r[5] < MIN_FRESH]
    keep = [r for r in judged if r[7] >= 15]
    drop = [r for r in judged if r[7] < 15]
    if keep:
        print("    worth polling:  " + ", ".join(
            f"{r[0]} ({r[7]:.0f}%)" for r in sorted(keep, key=lambda r: -r[7])))
    if drop:
        print("    mostly macro:   " + ", ".join(
            f"{r[0]} ({r[7]:.0f}%)" for r in sorted(drop, key=lambda r: -r[7])))
    if thin:
        print("    too few fresh items to judge: " + ", ".join(
            f"{r[0]} ({r[5]})" for r in thin))
    print("\n    Judge on f-tick%, not ticker%. A feed can carry a long backlog")
    print("    with good historical density and still publish almost nothing")
    print("    new - only fresh items can ever become an alert.")
    print("    'yours' is the only column that becomes a popup at all.")
    return 0


def cmd_verify(cfg, args):
    """A healthy feed's newest item is never old. Same guard IDXAlert added
    after indexFrom=1 spent two versions reading the wrong page."""
    now = datetime.now(timezone.utc)
    _e, _t, _f, sources, _s, _p, fetch = build(cfg)
    bad = 0
    print(f"\n{APP} - source freshness\n")
    for src in [s for s in sources if s.enabled]:
        try:
            body = fetch(src)
            got = src.parse(body) if body is not None else []
        except Exception as exc:
            print(f"  FAIL  {src.name:<18} {type(exc).__name__}: {exc}")
            bad += 1
            continue
        newest, mins, stale = src.freshness(got, now)
        limit = src.stale_after(now)
        verdict = "STALE" if stale else "quiet" if limit is None else "ok"
        if stale:
            bad += 1
        shown = f"(limit {limit}m)" if limit is not None else "(outside publishing hours)"
        print(f"  {verdict:<5} {src.name:<18} {len(got):>3} items  "
              f"newest {_age(newest, now)}  {shown}")
    print(f"\n  {bad} source(s) need attention" if bad else "\n  all sources fresh")
    return 1 if bad else 0


def cmd_watch(cfg, args):
    data_dir = resolve_data_dir(cfg)
    set_log_dir(os.path.join(data_dir, "logs"))
    _e, _t, _f, sources, _s, pipeline, fetch = build(
        cfg, os.path.join(data_dir, "seen.json"))
    disp = jciengine.Dispatcher(log=log)

    def console(payload):
        for row in jciview.rows_for([payload]):
            log(f"{row['ticker']:<6} {row['title'][:88]}")
            for f in row["files"]:
                log(f"        {f['name'][:88]}")

    disp.channels.append(console)
    watcher = jciengine.Watcher(sources, pipeline, fetch, disp, cfg, log)
    watcher.recorders.append(
        jcidash.recorder(os.path.join(data_dir, "news.csv")))
    log(f"{APP} watching {len([s for s in sources if s.enabled])} sources, "
        f"every {cfg.get('poll_seconds', 60)}s. Ctrl-C to stop.")
    log(f"data: {data_dir}")
    try:
        watcher.run()
    except KeyboardInterrupt:
        log("stopped.")
    return 0


def cmd_report(cfg, args):
    """What actually happened, from news.csv. Evidence, not an impression.

    This used to read a second file, alerts.csv, which recorded a strict
    subset of what news.csv already holds - the dashboard log records every
    headline WITH its status, so the alerts were one filter away the whole
    time. Two writers of the same facts is one chance for them to disagree.
    """
    import collections
    path = os.path.join(resolve_data_dir(cfg), "news.csv")
    if not os.path.exists(path):
        print(f"\nnothing recorded yet ({path})")
        print("Run the tray, or 'python jci.py --watch', and check back.")
        return 1
    day = args.day or datetime.now(WIB).strftime("%Y-%m-%d")
    rows = [r for r in jcidash.read(path, limit=0)
            if r.get("status") == "alert" and r.get("ts_wib", "").startswith(day)]
    print(f"\n{APP} - alerts on {day}")
    if not rows:
        print("  nothing recorded for that day.")
        return 0

    def tally(field, n=8):
        c = collections.Counter(r[field] or "(none)" for r in rows)
        return c.most_common(n), len(c)

    print(f"  {len(rows)} alerts · "
          f"{len({r['ticker'] for r in rows})} distinct tickers\n")
    for label, field in (("by source", "source"), ("by ticker", "ticker"),
                         ("how it matched", "match_rule"),
                         ("which rule accepted", "rule"),
                         ("category", "categories")):
        top, total = tally(field)
        print(f"  {label}  ({total} distinct)")
        for k, n in top:
            bar = "#" * min(40, n)
            print(f"    {n:>4}  {k[:34]:<34} {bar}")
        print()

    if args.recategorize:
        # news.csv records what the categories were AT ALERT TIME. After
        # editing filters.categories the old rows do not change - correctly,
        # they are history - but that makes it impossible to tell whether an
        # expansion worked. This re-runs the CURRENT bundles and filters over
        # the recorded titles, which validates a change against every headline
        # of a real day instead of the handful you happened to eyeball.
        import jcifilter as _F
        import jcimatch as _M
        emiten = load_emiten()
        table = _M.Table(emiten)
        fs = _F.FilterSet(expand_watchlist(
            cfg.get("filters") or _F.DEFAULT_FILTERS, cfg.get("watchlist")))
        was_none = sum(1 for r in rows if not r["categories"])
        now_none, would_drop, changed = [], [], 0
        for r in rows:
            cats = _F.categories_of(r["title"], fs.categories)
            hits = _M.find_tickers(r["title"], table,
                                   min_confidence=cfg.get("min_confidence", 0.5))
            if r["ticker"] and not any(h.ticker == r["ticker"] for h in hits):
                hits.insert(0, _M.Hit(r["ticker"], r["match_rule"] or "slug",
                                      float(r["confidence"] or 1.0)))
            d = fs.evaluate(r["title"], hits, r["source"])
            if not d.alert:
                would_drop.append((r, d.reason))
            # An item that would be DROPPED is not a category gap - it never
            # reaches a popup, so its lack of a category is irrelevant. Listing
            # the two together made correctly-rejected roundups look like
            # missing vocabulary.
            if not cats and d.alert:
                now_none.append(r)
            if cats != set((r["categories"] or "").split()) - {""}:
                changed += 1
        print(f"  re-running today's rules over {len(rows)} recorded alerts\n")
        print(f"  uncategorised:  {was_none} when they fired  ->  "
              f"{len(now_none)} now   ({100*(was_none-len(now_none))//max(1,was_none)}% closed)")
        print(f"  categories changed on {changed} alerts")
        print(f"  would now be DROPPED: {len(would_drop)} of {len(rows)}\n")
        if would_drop:
            reasons = collections.Counter(w[1] for w in would_drop)
            for why, n in reasons.most_common():
                print(f"    {n:>4}  {why}")
            for r, why in would_drop[:10]:
                print(f"      {r['ticker']:<5} {r['title'][:66]}")
            print()
        if now_none:
            print(f"  still uncategorised AND still alerting ({len(now_none)})"
                  f" - the real remaining gap:")
            for r in now_none[:25]:
                print(f"    {r['ticker']:<5} {r['title'][:70]}")
        return 0

    if args.list:
        want = args.list.lower()
        pick = {"uncategorized": lambda r: not r["categories"],
                "weak": lambda r: r["match_rule"] == "token",
                "all": lambda r: True}.get(want)
        if not pick:
            print(f"  --list must be uncategorized, weak or all (got {want!r})")
            return 1
        sel = [r for r in rows if pick(r)]
        print(f"  {len(sel)} {want} alerts\n")
        for r in sel:
            print(f"  {r['ts_wib'][-8:-3]} {r['ticker']:<5} "
                  f"{r['match_rule']:<6} {r['source']:<17} {r['title'][:72]}")
        print("\n  Feed the recurring vocabulary here back into "
              "filters.categories -")
        print("  a category that misses real corporate words is why 'no "
              "category' is")
        print("  common, and that is a gap in the bundles, not a bad alert.")
        return 0

    weak = [r for r in rows if r["match_rule"] == "token"]
    nocat = [r for r in rows if not r["categories"]]
    print(f"  {len(weak)} matched only by a bare ticker (the roundup shape)")
    print(f"  {len(nocat)} matched no category at all")
    both = [r for r in rows if r["match_rule"] == "token" and not r["categories"]]
    if both:
        print(f"\n  {len(both)} were BOTH - these are the ones to eyeball:")
        for r in both[:8]:
            print(f"    {r['ts_wib'][-8:-3]} {r['ticker']:<5} {r['title'][:74]}")
        print("\n  If they are lists rather than stories, add the marker they")
        print("  slipped past to global_exclude, or turn on require_event.")
    return 0


def cmd_show_config(cfg, args):
    print(f"\n{APP}")
    print(f"  config     {CONFIG_PATH}")
    print(f"  data       {resolve_data_dir(cfg)}")
    print(f"  emiten     {EMITEN_PATH} "
          f"({'present' if os.path.exists(EMITEN_PATH) else 'MISSING'})")
    print(f"  watchlist  {cfg.get('watchlist') or '(empty - every ticker passes)'}")
    filters = expand_watchlist(cfg.get("filters") or {}, cfg.get("watchlist"))
    for r in filters.get("rules") or []:
        state = "on " if r.get("enabled", True) else "off"
        print(f"    [{state}] {r.get('name')}: {jcifilter.describe(r)}")
    errs = jcioptions.validate(jcioptions.read_values(cfg))
    print("\n  config is valid" if not errs else "\n  PROBLEMS:")
    for e in errs:
        print(f"    - {e}")
    return 1 if errs else 0


def cmd_replay(cfg, args):
    """Re-run TODAY'S rules over every headline the app has ever recorded.

    The question this answers is "does my watchlist actually do anything", and
    it is not a question a test can settle: the tests prove the code behaves,
    news.csv proves it behaved on Gill's own feeds. Because the log keeps the
    headlines a rule REJECTED (see jcidash), a watchlist can be tried against
    real history before it is saved - `--replay --watchlist BBCA,BMRI` says
    what the last fortnight would have looked like under it.

    Deliberately NOT applied here: the age cutoff and the clusterer. Both
    depend on when a poll happened, and replaying them would answer a question
    about last week's timing rather than about the rules. So the counts are
    higher than the day produced, and the command says so.
    """
    path = os.path.join(resolve_data_dir(cfg), "news.csv")
    rows = jcidash.read(path, limit=0)
    if not rows:
        print(f"no history yet - {path} appears once the app has run.")
        return 1

    emiten = load_emiten()
    table = jcimatch.Table(emiten)
    floor = cfg.get("min_confidence", 0.0)

    wanted = [t.strip().upper() for t in (args.watchlist or "").split(",")
              if t.strip()]
    trial = dict(cfg)
    if args.watchlist:
        trial["watchlist"] = wanted
    filters = build_filters(trial)
    shown = trial.get("watchlist") or []

    print(f"\n{APP} - replaying {len(rows)} recorded headlines")
    print(f"watchlist: {', '.join(shown) if shown else '(empty = EVERY ticker)'}")
    print("the age cutoff and story clustering are NOT applied, so these "
          "counts run\nhigher than the day actually produced.\n")

    passed, by_ticker, off_list = 0, {}, {}
    for r in rows:
        title = r.get("title") or ""
        hits = jcimatch.find_tickers(title, table, min_confidence=floor)
        d = filters.evaluate(title, hits, r.get("source", ""))
        if not d.alert:
            continue
        passed += 1
        t = d.ticker or (hits[0].ticker if hits else "-")
        by_ticker[t] = by_ticker.get(t, 0) + 1
        if shown and t not in shown:
            off_list[t] = off_list.get(t, 0) + 1

    print(f"{passed} of {len(rows)} would alert, "
          f"across {len(by_ticker)} tickers")
    for t, n in sorted(by_ticker.items(), key=lambda kv: (-kv[1], kv[0]))[:20]:
        print(f"    {t:<6} {n:>4}")
    if len(by_ticker) > 20:
        print(f"    ... and {len(by_ticker) - 20} more")

    if shown:
        # The whole point of the command. A watchlist that lets anything else
        # through is broken, and this is the line that would say so.
        if off_list:
            print("\n*** LEAK: these are not on the watchlist and got "
                  "through anyway ***")
            for t, n in sorted(off_list.items()):
                print(f"    {t:<6} {n:>4}")
            return 1
        print(f"\nevery one of the {passed} is on the watchlist - the filter "
              f"is doing its job.")
        missing = [t for t in shown if t not in by_ticker]
        if missing:
            print(f"nothing was recorded about: {', '.join(missing)}")
    return 0


def cmd_doctor(cfg, args):
    """Why it will not start, in words. Run from a console this prints; the
    tray runs the same checks at startup and puts them in a dialog, because a
    --noconsole exe has nowhere to print to."""
    findings = jcidoctor.run(HERE, resolve_data_dir(cfg), already_running=False)
    print()
    print(jcidoctor.report(findings, HERE, APP))
    print()
    return 1 if jcidoctor.fatal(findings) else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jci", description=APP)
    ap.add_argument("--once", action="store_true",
                    help="poll once, show every decision, write nothing")
    ap.add_argument("--commit", action="store_true",
                    help="with --once: record what was seen")
    ap.add_argument("--watch", action="store_true", help="run the loop here")
    ap.add_argument("--verify-sources", action="store_true",
                    help="check each source's newest item is not stale")
    ap.add_argument("--density", action="store_true",
                    help="measure which sources actually carry emiten news")
    ap.add_argument("--show-config", action="store_true")
    ap.add_argument("--report", action="store_true",
                    help="summarise a day of alerts from news.csv")
    ap.add_argument("--day", default="", help="with --report: YYYY-MM-DD")
    ap.add_argument("--recategorize", action="store_true",
                    help="with --report: re-run today's rules over old titles")
    ap.add_argument("--list", default="",
                    help="with --report: uncategorized | weak | all")
    ap.add_argument("--replay", action="store_true",
                    help="re-run today's rules over every recorded headline")
    ap.add_argument("--watchlist", default="",
                    help="with --replay: try this list instead, comma-separated")
    ap.add_argument("--doctor", action="store_true",
                    help="check every reason the app can fail to start")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)

    if args.version:
        print(APP)
        return 0

    cfg, created = load_config()
    if created:
        print(f"wrote a default config to {CONFIG_PATH}")
        print("set your tickers in 'watchlist', then run --show-config")

    if args.doctor:
        return cmd_doctor(cfg, args)
    if args.replay:
        return cmd_replay(cfg, args)
    if args.report:
        return cmd_report(cfg, args)
    if args.density:
        return cmd_density(cfg, args)
    if args.verify_sources:
        return cmd_verify(cfg, args)
    if args.show_config:
        return cmd_show_config(cfg, args)
    if args.watch:
        return cmd_watch(cfg, args)
    if args.once or True:            # --once is the default: it changes nothing
        return cmd_once(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
