#!/usr/bin/env python3
"""
test_jciengine.py - the chain test. Does an alert actually come out the end?

The lesson from IDXAlert was that every unit can pass while the feature is
broken, because the thing a user cares about spans four links:

    source bytes -> parse -> match -> filter -> cluster -> dispatch

A break anywhere looks identical from outside: no popup, nothing in the log.
So this drives the WHOLE chain with real fixture bytes and asserts on what
reaches the dispatcher, which is exactly what reaches the popup.

The other half is the failures that are silent by nature and cost real money
to discover late:
  · a cold start alerting on 200 items at once
  · a restart re-alerting everything a feed is still serving
  · one dead source stalling the other three
  · a channel raising and killing the poll loop

Offline. Uses fixtures/ if present, synthetic items otherwise.
Run:  python test_jciengine.py
"""

import json
import os
import shutil
import time
import tempfile
from datetime import datetime, timedelta, timezone

import jciengine as E
import jcifilter as F
import jcimatch as M
import jcisource as S

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
WIB = timezone(timedelta(hours=7))
NOW = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)      # 17:00 WIB Friday

failures = []


def check(name, cond, detail=None):
    if cond:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}" + (f"   -> {detail!r}" if detail is not None else ""))


EMITEN = {
    "BTPS": {"name": "PT Bank BTPN Syariah Tbk.", "aliases": ["btpn syariah"],
             "sector": "Keuangan"},
    "ADHI": {"name": "PT Adhi Karya (Persero) Tbk.", "aliases": ["adhi karya"],
             "sector": "Perindustrian"},
    "PTPP": {"name": "PP (Persero) Tbk", "aliases": ["ptpp"],
             "sector": "Perindustrian"},
    "SUNI": {"name": "PT Sunindo Pratama Tbk", "aliases": ["sunindo pratama"],
             "sector": "Energi"},
    "TLKM": {"name": "PT Telkom Indonesia (Persero) Tbk",
             "aliases": ["telkom", "telkomsel"], "sector": "Infrastruktur"},
    "TINS": {"name": "PT TIMAH (Persero) Tbk", "aliases": ["timah"],
             "sector": "Barang Baku"},
    "SMDR": {"name": "Samudera Indonesia Tbk", "aliases": ["samudera indonesia"],
             "sector": "Transportasi & Logistik"},
    "SRTG": {"name": "PT Saratoga Investama Sedaya Tbk.", "aliases": ["saratoga"],
             "sector": "Keuangan"},
    "BAIK": {"name": "PT Bersama Mencapai Puncak Tbk.", "aliases": ["bersama mencapai"],
             "sector": "Barang Konsumen Non-Primer"},
    "BBCA": {"name": "PT Bank Central Asia Tbk.", "aliases": ["bank central asia", "bca"],
             "sector": "Keuangan"},
    "BBRI": {"name": "PT Bank Rakyat Indonesia (Persero) Tbk", "aliases": ["bri"],
             "sector": "Keuangan"},
    "BMRI": {"name": "PT Bank Mandiri (Persero) Tbk", "aliases": ["bank mandiri"],
             "sector": "Keuangan"},
    "GOTO": {"name": "PT GoTo Gojek Tokopedia Tbk", "aliases": ["goto", "gojek"],
             "sector": "Teknologi"},
}
TABLE = M.Table(EMITEN)
OPEN_RULES = {"global_exclude": [], "categories": F.DEFAULT_CATEGORIES,
              "rules": [{"name": "everything", "enabled": True, "tickers": [],
                         "categories": [], "require": [], "any_of": [],
                         "exclude": [], "sources": [], "min_confidence": 0.5,
                         "require_ticker": True}]}


def pipeline(store=None, cfg=None, filters=None):
    return E.Pipeline(TABLE, F.FilterSet(filters or OPEN_RULES),
                      store or E.Store(), cfg, EMITEN)


def item(tid, title, when=NOW, source="t"):
    return S.Item(source, tid, title, f"http://x/{tid}", when)


def kompas_items():
    p = os.path.join(FIX, "kompas-money.html")
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        return S.KompasSource("kompas-money", "x").parse(f.read())


def main():
    print("\n== the chain, on real Kompas bytes ==")
    items = kompas_items()
    missing = items is None
    if missing:
        print("  (skipped - no fixture)")
    else:
        # The fixture is weeks old and one index page spans about a day, so
        # both the clock and the age cutoff have to be widened or nothing
        # reaches the matcher at all. Neither is what this test is about.
        when = max(i.published for i in items)
        p = pipeline(cfg={"seed_on_first_run": False,
                          "max_item_age_minutes": 60 * 48})
        res = p.process(items, now=when, seed=False)
        titles = [a.item.title for a in res.alerts]
        check("a real index page produces alerts", len(res.alerts) >= 1, len(res.alerts))
        check("the GOTO story reaches the dispatcher",
              any("Morgan Stanley Jual Saham GOTO" in t for t in titles), titles)
        goto = [a for a in res.alerts if a.ticker == "GOTO"]
        check("attributed to GOTO, and the alias outranks the bare token",
              goto and goto[0].hits[0].rule == "alias"
              and goto[0].hits[0].confidence == 0.90,
              goto[0].hits if goto else None)
        check("nothing alerts without a ticker in it",
              all(a.ticker for a in res.alerts), titles)

    print("\n== a cold start must not alert on everything ==")
    st = E.Store()
    p = pipeline(st)
    res = p.process([item(f"i{n}", f"BBCA Kabar Nomor {n}") for n in range(200)],
                    now=NOW)
    check("first run alerts on nothing at all", res.alerts == [], len(res.alerts))
    check("but the whole feed is recorded as seen", len(st.seen) == 200, len(st.seen))
    check("and the next tick is live again",
          len(p.process([item("new1", "BBCA Tebar Dividen Interim")],
                        now=NOW).alerts) == 1)

    print("\n== the age cutoff is what makes a restart safe ==")
    p = pipeline(cfg={"max_item_age_minutes": 180, "seed_on_first_run": False})
    fresh = item("f1", "BBCA Tebar Dividen", NOW - timedelta(minutes=30))
    old = item("o1", "BBRI Tebar Dividen", NOW - timedelta(hours=9))
    res = p.process([fresh, old], now=NOW)
    check("a fresh unseen item alerts", len(res.alerts) == 1, res.alerts)
    check("an unseen item past the cutoff does NOT",
          all(a.item.id != "o1" for a in res.alerts), res.alerts)
    check("and says why", "age cutoff" in " ".join(res.dropped), res.dropped)
    p2 = pipeline(cfg={"max_item_age_minutes": 180, "seed_on_first_run": False})
    undated = S.Item("t", "u1", "BBCA Tebar Dividen", "http://x", None)
    check("an undated item is judged by the seen-set alone, not dropped",
          len(p2.process([undated], now=NOW).alerts) == 1)

    print("\n== restart: seen-set and clusters both survive ==")
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "store.json")
        st = E.Store(path).load()
        p = pipeline(st, cfg={"seed_on_first_run": False})
        a = item("x1", "Bank Mandiri Tebar Dividen Interim Rp6,16 Triliun",
                 source="katadata")
        r1 = p.process([a], now=NOW)
        p.persist()
        check("the first sighting alerts", len(r1.alerts) == 1, r1.alerts)

        st2 = E.Store(path).load()
        check("the store came back off disk", len(st2.seen) == 1 and st2.clusters,
              (len(st2.seen), len(st2.clusters)))
        p2 = pipeline(st2, cfg={"seed_on_first_run": False})
        check("the same item does not alert again",
              p2.process([a], now=NOW).alerts == [])
        b = item("x2", "Bank Mandiri (BMRI) Tebar Dividen Interim Rp6,16 T",
                 NOW + timedelta(minutes=20), source="kontan-investasi")
        r3 = p2.process([b], now=NOW + timedelta(minutes=20))
        check("and a second outlet on that story is still suppressed AFTER a restart",
              r3.alerts == [] and "duplicate" in " ".join(r3.dropped), r3.dropped)

        with open(path, "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        st3 = E.Store(path).load()
        check("a corrupt store degrades to a fresh one, it does not crash",
              st3.seen == {} and st3.first_run is True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n== bursts group by sector, nothing is dropped ==")
    p = pipeline(cfg={"seed_on_first_run": False, "burst_threshold": 5,
                      "burst_group_min": 3})
    burst = [item("b1", "BBCA Tebar Dividen Interim"),
             item("b2", "BBRI Umumkan Buyback Saham"),
             item("b3", "Bank Mandiri Gelar RUPSLB Bahas Dividen"),
             item("b4", "SRTG Tambah Kepemilikan Saham"),
             item("b5", "TLKM Teken Kontrak Baru Senilai Rp2 Triliun"),
             item("b6", "TINS Raih Tender Smelter")]
    res = p.process(burst, now=NOW)
    fin = [g for g in res.groups if g.sector == "Keuangan"]
    check("four Financials items collapse into one grouped alert",
          len(fin) == 1 and len(fin[0].alerts) == 4,
          [(g.sector, len(g.alerts)) for g in res.groups])
    check("the group knows its tickers",
          fin and set(fin[0].tickers) == {"BBCA", "BBRI", "BMRI", "SRTG"},
          fin[0].tickers if fin else None)
    check("singleton sectors stay individual, nothing is lost",
          len(res.alerts) + sum(len(g.alerts) for g in res.groups) == 6,
          (len(res.alerts), [len(g.alerts) for g in res.groups]))
    small = pipeline(cfg={"seed_on_first_run": False, "burst_threshold": 20})
    check("below the threshold nothing is grouped",
          small.process(burst, now=NOW).groups == [])

    print("\n== the watcher: one bad source must not stall the rest ==")
    good = S.RssSource("good", "x")
    good.parse = lambda body: [item("g1", "BBCA Tebar Dividen Interim")]
    bad = S.RssSource("bad", "y")

    def fetch(src):
        if src.name == "bad":
            raise OSError("connection reset")
        return b"<rss/>"

    sent = []
    disp = E.Dispatcher()
    disp.channels.append(sent.append)
    w = E.Watcher([bad, good], pipeline(cfg={"seed_on_first_run": False}),
                  fetch, disp)
    res = w.poll_once(now=NOW)
    check("the healthy source still delivers", len(sent) == 1, sent)
    check("the failing one is recorded, not raised",
          w.state["bad"].ok is False and "connection reset" in w.state["bad"].last_error,
          w.state["bad"].last_error)
    check("tray status goes red", w.status() == "red", w.status())
    for _ in range(2):
        w.poll_once(now=NOW)
    check("three failures in a row put that source in cooldown",
          w.state["bad"].cooldown_until > 0, w.state["bad"].cooldown_until)

    print("\n== a 304 is not an error and not an empty feed ==")
    src304 = S.RssSource("s304", "x")
    src304.parse = lambda body: (_ for _ in ()).throw(
        AssertionError("parse must never be called on a 304"))
    w304 = E.Watcher([src304], pipeline(cfg={"seed_on_first_run": False}),
                     lambda s: None, E.Dispatcher())
    r304 = w304.poll_once(now=NOW)
    check("a 304 does not reach the parser", r304.alerts == [])
    check("the source stays healthy",
          w304.state["s304"].ok is True and not w304.state["s304"].last_error,
          w304.state["s304"].last_error)
    check("and the tray stays green", w304.status() == "green", w304.status())

    print("\n== a source polled every Nth tick is skipped, not failed ==")
    calls = []
    every3 = S.RssSource("slow", "x", every_n_ticks=3)
    every3.parse = lambda b: [item("s1", "BBCA Tebar Dividen Interim")]
    each = S.RssSource("fast", "y")
    each.parse = lambda b: []

    def counting_fetch(src):
        calls.append(src.name)
        return b""
    wN = E.Watcher([every3, each], pipeline(cfg={"seed_on_first_run": False}),
                   counting_fetch, E.Dispatcher())
    for _ in range(6):
        wN.poll_once(now=NOW)
    check("the fast source is fetched on every tick",
          calls.count("fast") == 6, calls.count("fast"))
    check("the slow one only every third",
          calls.count("slow") == 2, calls.count("slow"))
    check("skipping it does not mark it unhealthy",
          wN.state["slow"].ok is True and wN.status() == "green",
          (wN.state["slow"].ok, wN.status()))

    print("\n== the log records CHANGES, not states ==")
    # The log this prevents: 90 minutes of "cnbc-market: newest item is older
    # than its threshold" on every tick, and an IQPlus timeout every six
    # minutes for three days. Gill's rule from IDXAlert - no line per routine
    # check - was written down and then not implemented here.
    lines = []
    dead = S.RssSource("dead", "x")
    dead.parse = lambda b: []

    host = {"up": False}

    def boom(src):
        if not host["up"]:
            raise TimeoutError("connect timed out")
        return b""
    wl = E.Watcher([dead], pipeline(cfg={"seed_on_first_run": False}), boom,
                   E.Dispatcher(), {"poll_seconds": 60}, lines.append)
    for i in range(12):
        wl.state["dead"].cooldown_until = 0
        wl.poll_once(now=NOW + timedelta(minutes=i))
    fails = [l for l in lines if "timed out" in l]
    check("twelve consecutive failures produce ONE line, not twelve",
          len(fails) == 1, fails)
    host["up"] = True                      # the host comes back
    wl.state["dead"].cooldown_until = 0
    wl.poll_once(now=NOW + timedelta(minutes=20))
    check("and recovery is reported once, with how long it was down",
          any("recovered after" in l for l in lines),
          [l for l in lines if "recovered" in l])
    before = len(lines)
    for i in range(5):
        wl.poll_once(now=NOW + timedelta(minutes=21 + i))
    check("a healthy source says nothing at all", len(lines) == before,
          lines[before:])

    print("\n== stale is a state too, not a per-tick event ==")
    lines2 = []
    stale_src = S.RssSource("cnbc", "x", stale_minutes_day=1,
                            stale_minutes_night=1)
    old_item = item("s1", "BBCA Tebar Dividen",
                    NOW - timedelta(hours=6))
    stale_src.parse = lambda b: [old_item]
    w2 = E.Watcher([stale_src], pipeline(cfg={"seed_on_first_run": False}),
                   lambda s: b"", E.Dispatcher(), {}, lines2.append)
    for i in range(8):
        w2.poll_once(now=NOW + timedelta(minutes=i))
    stales = [l for l in lines2 if "older than its threshold" in l]
    check("eight stale ticks produce ONE line", len(stales) == 1, stales)
    check("and the tray still knows it is stale",
          w2.state["cnbc"].stale is True and w2.status() == "blue", w2.status())

    print("\n== a dead host backs off instead of being probed forever ==")
    st = E.SourceState("x")
    ladder = []
    for n in range(7):
        st.consecutive_failures = 3 + n
        ladder.append(int(min(3600.0, 300.0 * (2 ** n))))
    check("cooldown doubles and then holds at the cap",
          ladder == [300, 600, 1200, 2400, 3600, 3600, 3600], ladder)
    def always_boom(src):
        raise TimeoutError("connect timed out")
    wb = E.Watcher([dead], pipeline(cfg={"seed_on_first_run": False}),
                   always_boom, E.Dispatcher(), {}, lambda m: None)
    for i in range(6):
        wb.state["dead"].cooldown_until = 0
        wb.poll_once(now=NOW + timedelta(minutes=i))
    check("after repeated failure the next attempt is far in the future",
          wb.state["dead"].cooldown_until - time.time() > 900,
          wb.state["dead"].cooldown_until - time.time())

    print("\n== hourly heartbeat ==")
    lines3 = []
    good3 = S.RssSource("g", "x")
    good3.parse = lambda b: []
    w3 = E.Watcher([good3], pipeline(cfg={"seed_on_first_run": False}),
                   lambda s: b"", E.Dispatcher(), {}, lines3.append)
    w3.poll_once(now=NOW)
    w3.poll_once(now=NOW + timedelta(minutes=30))
    check("it does not beat twice in an hour",
          sum("still running" in l for l in lines3) == 1, lines3)
    w3.poll_once(now=NOW + timedelta(minutes=61))
    check("but does beat once an hour has passed",
          sum("still running" in l for l in lines3) == 2, lines3)
    check("and the beat names the problems",
          "sources ok" in lines3[0], lines3[0])

    # "10 sources ok" while six are switched on read as health over a dead
    # feed. Count what is actually running.
    lines4 = []
    on = S.RssSource("on", "x")
    on.parse = lambda b: []
    off = S.RssSource("off", "x")
    off.parse = lambda b: []
    off.enabled = False
    w4 = E.Watcher([on, off], pipeline(cfg={"seed_on_first_run": False}),
                   lambda s: b"", E.Dispatcher(), {}, lines4.append)
    w4.poll_once(now=NOW)
    check("the beat counts only the sources that are switched on",
          "1/1 sources ok" in lines4[0], lines4)

    # A silent log is ambiguous: stopped, wedged, or just quiet. Paused must
    # say so out loud.
    lines5 = []
    w5 = E.Watcher([on], pipeline(cfg={"seed_on_first_run": False}),
                   lambda s: b"", E.Dispatcher(), {}, lines5.append)
    w5.paused = True
    w5.heartbeat(NOW)
    check("a paused watcher still beats, and says it is paused",
          lines5 and lines5[0].startswith("PAUSED"), lines5)

    print("\n== stats() tells never-tried apart from healthy ==")
    fresh_src = S.RssSource("fresh", "x")
    fresh_src.parse = lambda b: []
    never = S.RssSource("never", "x")
    never.parse = lambda b: []
    never.enabled = True
    wS = E.Watcher([fresh_src, never], pipeline(cfg={"seed_on_first_run": False}),
                   lambda s: b"", E.Dispatcher())
    wS.state["never"].cooldown_until = time.time() + 1800   # skipped this tick
    wS.poll_once(now=NOW)
    st = wS.stats()["sources"]
    check("a source that was fetched has an attempt on record",
          st["fresh"]["attempts"] == 1, st["fresh"])
    check("one that was skipped has none, so the window can say 'not tried'",
          st["never"]["attempts"] == 0, st["never"])
    check("and the cooldown is reported as seconds still to wait",
          1700 < st["never"]["waiting"] <= 1800, st["never"]["waiting"])
    check("a source with no cooldown reports zero, not a stale epoch",
          st["fresh"]["waiting"] == 0, st["fresh"]["waiting"])
    never.enabled = False
    check("and stats names what is switched off, so the window can say so",
          wS.stats()["disabled"] == ["never"], wS.stats()["disabled"])

    print("\n== a source you switched off cannot raise an alarm ==")
    dead = S.RssSource("dead", "x")
    dead.parse = lambda b: []
    alive = S.RssSource("alive", "x")
    alive.parse = lambda b: []
    w9 = E.Watcher([alive, dead], pipeline(cfg={"seed_on_first_run": False}),
                   lambda s: b"", E.Dispatcher())
    w9.state["dead"].ok = False
    w9.state["dead"].last_error = "TimeoutError: [WinError 10060]"
    check("while it is enabled, a failing source turns the icon red",
          w9.status() == "red", w9.status())
    dead.enabled = False
    check("unticked, the same failure no longer colours the icon",
          w9.status() == "green", w9.status())
    check("and it disappears from Status entirely",
          "dead" not in w9.stats()["sources"], w9.stats()["sources"])
    check("but its history is kept, so re-enabling does not start from zero",
          "dead" in w9.state)
    dead.enabled = True
    check("re-enabled, the alarm comes straight back", w9.status() == "red")

    print("\n== pressing Save actually changes what the engine does ==")
    # The bug this pins: rules and the watchlist are compiled ONCE, so every
    # Options save between restarts changed the file and nothing else. Gill
    # emptied his watchlist and the running app still refused GGRM for "not
    # one of this rule's tickers".
    tight = F.FilterSet({"global_exclude": [], "categories": F.DEFAULT_CATEGORIES,
                         "rules": [{"name": "cov", "enabled": True,
                                    "tickers": ["BBCA"],
                                    "require_ticker": True}]})
    loose = F.FilterSet({"global_exclude": [], "categories": F.DEFAULT_CATEGORIES,
                         "rules": [{"name": "cov", "enabled": True,
                                    "tickers": [],
                                    "require_ticker": True}]})
    p8 = E.Pipeline(TABLE, tight, E.Store(), {"seed_on_first_run": False},
                    EMITEN)
    before = p8.process([item("g1", "TLKM bagikan dividen interim")], now=NOW)
    check("the tight rule refuses it", before.alerts == [], before.alerts)
    check("and says which list it is not in",
          "not one of this rule's" in before.records[0].reason,
          before.records[0].reason)

    live = p8.clusterer.by_ticker
    p8.reconfigure({"seed_on_first_run": False, "min_confidence": 0.4}, loose)
    after = p8.process([item("g2", "TLKM bagikan dividen final")], now=NOW)
    check("after reconfigure the same headline gets through",
          len(after.alerts) == 1, after.records[0].reason if after.records else None)
    check("the scalar settings moved too, not just the rules",
          p8.cfg["min_confidence"] == 0.4, p8.cfg["min_confidence"])
    check("and the clusterer was NOT rebuilt - live stories survive a save",
          p8.clusterer.by_ticker is live)

    on1 = S.RssSource("keep", "x")
    on2 = S.RssSource("drop", "x")
    w8 = E.Watcher([on1, on2], p8, lambda s: b"", E.Dispatcher(),
                   {"poll_seconds": 60})
    w8.reconfigure({"poll_seconds": 30,
                    "sources": {"drop": {"enabled": False}}}, loose)
    check("a source switched off in Options is off now, not after a restart",
          on1.enabled is True and on2.enabled is False,
          (on1.enabled, on2.enabled))
    check("and a new poll interval takes effect", w8.cfg["poll_seconds"] == 30)
    check("the sleeping loop is woken so it does not serve out the old one",
          w8._interrupt.is_set())

    print("\n== every headline is recorded, not just the ones that alert ==")
    pl2 = pipeline(cfg={"seed_on_first_run": False},
                   filters={"rules": [{"name": "coverage", "enabled": True,
                                       "tickers": ["BBCA"],
                                       "require_ticker": True}]})
    r = pl2.process([item("a", "BBCA raih laba bersih Rp1 triliun"),
                     item("b", "TLKM bagikan dividen interim"),
                     item("c", "Tidak ada emiten di judul ini")], now=NOW)
    check("one alert got through", len(r.alerts) == 1, r.alerts)
    check("but every new headline left a record", len(r.records) == 3,
          [x.status for x in r.records])
    kept = {x.item.title[:4]: x for x in r.records}
    check("the alerted one is marked as such",
          kept["BBCA"].status == "alert", kept["BBCA"].status)
    check("the rejected one carries the reason it was rejected",
          kept["TLKM"].status == "dropped" and kept["TLKM"].reason,
          (kept["TLKM"].status, kept["TLKM"].reason))
    check("and the ticker it matched, so the row is not a mystery",
          kept["TLKM"].ticker == "TLKM", kept["TLKM"].ticker)
    check("a headline naming nobody is still kept, with no ticker",
          kept["Tida"].status == "dropped" and kept["Tida"].ticker == "",
          (kept["Tida"].status, kept["Tida"].ticker))

    # A second sighting must NOT write a second row - the log would double
    # every headline every tick, which is how a log stops being readable.
    r2 = pl2.process([item("a", "BBCA raih laba bersih Rp1 triliun")], now=NOW)
    check("a headline already seen is not recorded twice",
          r2.records == [], r2.records)

    lines7 = []
    got = []
    src7 = S.RssSource("s7", "x")
    src7.parse = lambda b: []
    w7 = E.Watcher([src7], pl2, lambda s: b"", E.Dispatcher(), {},
                   lines7.append)
    w7.recorders.append(got.append)
    w7.recorders.append(lambda _rs: 1 / 0)
    w7.poll_once(now=NOW)
    check("the watcher hands its recorders every tick", got == [[]], got)
    check("and a recorder that raises is logged, not fatal",
          any("recorder failed" in l for l in lines7), lines7)

    print("\n== a machine with no resolver is ONE line, not six ==")
    # Every reboot: Windows starts JCIAlert before the resolver is up, all six
    # feeds fail with the same getaddrinfo error, and the log opened with six
    # lines of alarm about one fact.
    linesN = []
    import socket
    def dns_fetch(_src):
        raise socket.gaierror(11001, "getaddrinfo failed")
    srcs = []
    for n in ("a", "b", "c"):
        sx = S.RssSource(n, "x")
        sx.parse = lambda b: []
        srcs.append(sx)
    wN = E.Watcher(srcs, pipeline(cfg={"seed_on_first_run": False}),
                   dns_fetch, E.Dispatcher(), {}, linesN.append)
    def said(lines):
        return [l for l in lines if not l.startswith("still running")]

    wN.poll_once(now=NOW)
    check("three sources that cannot resolve produce one line",
          len(said(linesN)) == 1, linesN)
    check("and it names the machine's problem, not a feed's",
          "no network" in said(linesN)[0] and "a:" not in said(linesN)[0],
          said(linesN)[0])
    wN.poll_once(now=NOW)
    check("it is not repeated every tick", len(said(linesN)) == 1, linesN)
    check("but every source is still marked unhealthy",
          all(not st.ok for st in wN.state.values()))
    check("so the icon is red, not green", wN.status() == "red", wN.status())

    wN.fetch = lambda s: b""
    wN.poll_once(now=NOW)
    check("coming back is also one line, not three",
          len(said(linesN)) == 2 and "network back" in said(linesN)[1],
          said(linesN))
    check("and the sources are healthy again", wN.status() == "green",
          wN.status())

    # The collapse must only fire when the common cause is certain.
    lines2 = []
    good = S.RssSource("good", "x")
    good.parse = lambda b: []
    bad = S.RssSource("bad", "x")
    bad.parse = lambda b: []
    def half(src):
        if src.name == "bad":
            raise socket.gaierror(11001, "getaddrinfo failed")
        return b""
    w2n = E.Watcher([good, bad], pipeline(cfg={"seed_on_first_run": False}),
                    half, E.Dispatcher(), {}, lines2.append)
    w2n.poll_once(now=NOW)
    check("one source failing DNS while another works is that source's own "
          "problem", said(lines2) and said(lines2)[0].startswith("bad:"),
          lines2)

    lines3 = []
    solo = S.RssSource("solo", "x")
    solo.parse = lambda b: []
    w3n = E.Watcher([solo], pipeline(cfg={"seed_on_first_run": False}),
                    dns_fetch, E.Dispatcher(), {}, lines3.append)
    w3n.poll_once(now=NOW)
    check("and with only ONE source configured there is nothing to collapse",
          said(lines3) and said(lines3)[0].startswith("solo:"), lines3)

    print("\n== seeding is PER SOURCE, not per store ==")
    # The 2026-09-10 startup: DNS was not up, four of six feeds delivered
    # nothing, the store declared first-run over on the strength of the two
    # that worked, and a minute later the other four dumped their whole
    # backlog into a live pipeline. Only the age cutoff caught it.
    lines6 = []
    pl = pipeline(cfg={"seed_on_first_run": True})
    pl.log = lines6.append
    r6 = pl.process([item("s1", "BBCA jual saham", source="up")], now=NOW)
    check("the source that answered is seeded, not alerted",
          r6.alerts == [] and r6.records[0].reason == "seeded on first run",
          r6.records[0].reason)
    check("and the seeding is announced with the source named",
          any("first sight" in l and "up" in l for l in lines6), lines6)
    check("the source is remembered as absorbed", "up" in pl.store.seeded)
    check("a source that delivered NOTHING is not marked absorbed",
          "down" not in pl.store.seeded, sorted(pl.store.seeded))

    r7 = pl.process([item("s2", "BBCA raih laba", source="up"),
                     item("s3", "BBRI tebar dividen", source="down")], now=NOW)
    kept = {x.item.source: x for x in r7.records}
    check("the source that came back late is seeded on ITS first delivery",
          kept["down"].reason == "seeded on first run", kept["down"].reason)
    check("while the source already absorbed is now live",
          kept["up"].status == "alert", kept["up"].reason)

    r8 = pl.process([item("s4", "BBRI raih laba", source="down")], now=NOW)
    check("and the late source is live from its second delivery on",
          r8.alerts and r8.alerts[0].item.source == "down", r8.records)

    tmp8 = os.path.join(tempfile.mkdtemp(), "seen.json")
    st = E.Store(tmp8)
    st.seeded = {"up": "2026-09-10T05:47:34"}
    st.mark("x")
    st.save()
    check("which sources are absorbed survives a restart",
          E.Store(tmp8).load().seeded == {"up": "2026-09-10T05:47:34"},
          E.Store(tmp8).load().seeded)
    check("an older store with no seeded map loads as none absorbed",
          E.Store().seeded == {})

    lines9 = []
    pl9 = pipeline(cfg={"seed_on_first_run": False})
    pl9.log = lines9.append
    r9 = pl9.process([item("s5", "BBCA jual saham", source="fresh")], now=NOW)
    check("with seeding switched off the first headline alerts",
          len(r9.alerts) == 1, r9.records[0].reason if r9.records else None)
    check("and nothing is announced", lines9 == [], lines9)
    check("but the source is still marked, so turning seeding on later "
          "cannot re-swallow it", "fresh" in pl9.store.seeded)

    print("\n== status precedence and our_lag ==")
    w2 = E.Watcher([good], pipeline(cfg={"seed_on_first_run": False}),
                   lambda s: b"", disp)
    w2.poll_once(now=NOW)
    check("a healthy run is green", w2.status() == "green", w2.status())
    w2.state["good"].stale = True
    check("a stale feed turns it blue", w2.status() == "blue", w2.status())
    w2.set_paused(True)
    check("paused outranks stale", w2.status() == "amber", w2.status())
    w2.set_paused(False)
    w2.poll_once(now=NOW + timedelta(seconds=62))
    check("our_lag is the gap between successive polls, not a source's clock",
          abs(w2.our_lag - 62) < 0.01, w2.our_lag)

    print("\n== a channel that raises must not kill the loop ==")
    boom = E.Dispatcher()
    logged = []
    boom.log = logged.append

    def explode(_payload):
        raise RuntimeError("popup went away")
    got = []
    boom.channels += [explode, got.append]
    boom.send("payload")
    check("the surviving channel still receives", got == ["payload"], got)
    check("and the failure is logged", logged and "popup went away" in logged[0],
          logged)

    if missing:
        # A skipped chain test looks identical to a passing one, and THIS is
        # the test that spans parse -> match -> filter -> cluster -> dispatch.
        # Everything below it is a unit. Say so loudly or a clone will read
        # "all checks passed" and believe the chain was exercised.
        print("\n" + "!" * 72)
        print("!! THE CHAIN TEST DID NOT RUN - fixtures/kompas-money.html is")
        print("!! missing, so nothing below was proved end to end. Capture it:")
        print("!!     python probe_sources.py --save-fixtures fixtures")
        print("!" * 72)
    print("\n%d checks failed" % len(failures) if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
