#!/usr/bin/env python3
"""
test_jcisource.py - do the adapters parse what these sites ACTUALLY serve?

Every assertion runs against fixtures/, real bytes captured from Gill's PC on
2026-09-05 by `probe_sources.py --save-fixtures`. Nothing here touches the
network, which is the point: none of these sites are reachable from where this
code is edited, so a parser written against imagined markup would be untestable
until it failed in production.

Two of these checks exist because the captured bytes contradicted an
assumption:
  · Kontan ships no <guid> at all
  · Liputan6 repeats <title> inside <item> for its media metadata

Run:  python test_jcisource.py
"""

import json
import os
from datetime import datetime, timezone

import jcisource as S

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")

failures = []
skipped = []


def check(name, cond, detail=None):
    if cond:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}" + (f"   -> {detail!r}" if detail is not None else ""))


def banner():
    """A skipped test looks identical to a passing one. This is the whole
    reason for the noise: fixtures/ is not in the repo (captured third-party
    pages), so a fresh clone runs this suite with the parsing half switched
    off, and nothing else on screen would say so."""
    if not skipped:
        return
    print("\n" + "!" * 72)
    print(f"!! {len(set(skipped))} FIXTURES MISSING - THE PARSERS WERE NOT TESTED.")
    print("!! " + ", ".join(sorted(set(skipped))))
    print("!! Everything above is the logic that needs no bytes. To test the")
    print("!! parsers you must capture the real pages first:")
    print("!!     python probe_sources.py --save-fixtures fixtures")
    print("!" * 72)


def raw(fn):
    p = os.path.join(FIX, fn)
    if not os.path.exists(p):
        skipped.append(fn)
        return None
    with open(p, "rb") as f:
        return f.read()


def tickers():
    p = os.path.join(HERE, "emiten.json")
    if not os.path.exists(p):
        return {"SUNI", "BTPS", "ADHI", "TLKM", "TINS", "SMDR", "SRTG", "BAIK"}
    with open(p, encoding="utf-8") as f:
        return set(json.load(f).get("emiten", {}))


def main():
    TK = tickers()

    print("\n== RSS: the four launch feeds ==")
    for fn, name, n in (("katadata.xml", "katadata", 25),
                        ("kontan-investasi.xml", "kontan-investasi", 25),
                        ("idx-channel.xml", "idxchannel", 10)):
        b = raw(fn)
        if not b:
            continue
        items = S.RssSource(name, "x").parse(b)
        check(f"{name}: parses all {n} items", len(items) == n, len(items))
        check(f"{name}: every item has a title and a url",
              all(i.title and i.url for i in items))
        check(f"{name}: ids are unique",
              len({i.id for i in items}) == len(items))
        check(f"{name}: pubDate parses, tz-aware",
              all(i.published and i.published.tzinfo for i in items),
              [i.published for i in items[:2]])

    print("\n== Kontan ships NO guid - the id fallback is load-bearing ==")
    b = raw("kontan-investasi.xml")
    if b:
        from xml.etree import ElementTree as ET
        root = ET.fromstring(b)
        guids = [(i.findtext("guid") or "").strip() for i in root.findall(".//item")]
        check("the fixture really has no guids", not any(guids), guids[:3])
        items = S.RssSource("kontan-investasi", "x").parse(b)
        check("ids still come out unique",
              len({i.id for i in items}) == len(items) == 25, len({i.id for i in items}))
        check("and fall back to the link",
              all(i.id.startswith("kontan-investasi:http") for i in items),
              [i.id for i in items[:2]])

    print("\n== IDX Channel's <idnews> beats its <guid> ==")
    b = raw("idx-channel.xml")
    if b:
        items = S.RssSource("idxchannel", "x").parse(b)
        check("the numeric idnews is used as the id",
              all(i.id.split(":")[-1].isdigit() for i in items),
              [i.id for i in items[:3]])
        check("feed category is carried through",
              any(i.feed_category for i in items),
              [i.feed_category for i in items[:3]])

    print("\n== Liputan6 repeats <title> inside <item> ==")
    b = raw("liputan6-bisnis-alt.xml")
    if b:
        items = S.RssSource("liputan6", "x").parse(b)
        check("the ARTICLE title wins, not the image caption",
              items[0].title.startswith("Antrean Ketapang"), items[0].title)
        check("all 50 items parse", len(items) == 50, len(items))
        check("its numeric guid is used",
              items[0].id.split(":")[-1].isdigit(), items[0].id)

    print("\n== a slow source is polled less often, not dropped ==")
    slow = S.KompasSource("kompas-money", "x", every_n_ticks=3)
    fast = S.RssSource("katadata", "y")
    check("a normal source is due every tick",
          [fast.due(t) for t in range(6)] == [True] * 6)
    check("a slow one is due every third",
          [slow.due(t) for t in range(6)] == [True, False, False, True, False, False],
          [slow.due(t) for t in range(6)])
    check("every_n_ticks below 1 is clamped, not a divide by zero",
          S.RssSource("z", "z", every_n_ticks=0).due(0) is True)
    reg = {s.name: s for s in S.build_sources(set())}
    # These knobs exist for a source that is slow or heavy, not for IQPlus in
    # particular - kompas-money is the one using them now: a 198 KB index page
    # every third tick instead of a quarter of a gigabyte a day.
    heavy = reg["kompas-money"]
    check("the heavy HTML source is polled every third tick",
          heavy.every_n_ticks == 3, heavy.every_n_ticks)
    check("a source can be given a longer timeout than the 15s default",
          S.RssSource("z", "z", timeout=45).timeout == 45)
    check("the RSS feeds stay on every tick",
          all(reg[n].every_n_ticks == 1
              for n in ("katadata", "kontan-investasi", "idxchannel")))

    print("\n== freshness is time-of-day aware (24/7 operation) ==")
    src = S.RssSource("x", "y", stale_minutes_day=120, stale_minutes_night=600)
    wib_11 = datetime(2026, 9, 7, 4, 0, tzinfo=timezone.utc)    # 11:00 WIB Monday
    wib_03 = datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc)   # 03:00 WIB Tuesday
    check("a weekday mid-morning uses the tight threshold",
          src.stale_after(wib_11) == 120, src.stale_after(wib_11))
    check("the small hours use the loose one",
          src.stale_after(wib_03) == 600, src.stale_after(wib_03))
    sat = datetime(2026, 9, 5, 4, 0, tzinfo=timezone.utc)       # 11:00 WIB Saturday
    check("the weekend uses the loose one too",
          src.stale_after(sat) == 600, src.stale_after(sat))

    # trading_days_only, which made a source ABSTAIN outside publishing hours,
    # went with IQPlus on 2026-09-23. It existed for a corporate wire whose
    # newest item is legitimately 27h old on a Saturday evening; every
    # registered source now publishes at the weekend, and the night threshold
    # already covers them.
    check("no source abstains from the staleness judgement any more",
          all(x.stale_after(sat) == x.stale_minutes_night
              for x in S.build_sources(set())),
          [(x.name, x.stale_after(sat)) for x in S.build_sources(set())])

    old = [S.Item("x", "1", "t", "u", datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc))]
    _n, mins, stale = src.freshness(old, now=wib_11)
    check("a 4h-old newest item is stale at 11:00 WIB", stale is True, mins)
    _n, _m, stale = src.freshness(old, now=datetime(2026, 9, 7, 1, 0, tzinfo=timezone.utc))
    check("and fresh an hour after publication", stale is False)
    _n, _m, stale = src.freshness([S.Item("x", "1", "t", "u", None)], now=wib_11)
    check("undated items are never called stale (cannot judge)", stale is False)

    print("\n== iqplus is gone, parser and all ==")
    names = [x.name for x in S.build_sources(set(), {})]
    check("no iqplus source is registered",
          not any("iqplus" in n for n in names), names)
    check("and IQPlusSource itself is gone - it was the only ALL-CAPS source "
          "and the only one with tickers in its slugs, and keeping it kept "
          "four other mechanisms alive with it",
          not hasattr(S, "IQPlusSource"))
    check("and the launch set is not empty", len(names) >= 5, names)

    print("\n== a feed url can be fixed from config, without a rebuild ==")
    moved = S.build_sources(set(), {"sources": {
        "katadata": {"enabled": True, "url": "https://katadata.co.id/feed"}}})
    got = [x for x in moved if x.name == "katadata"][0]
    check("the override is applied", got.url == "https://katadata.co.id/feed",
          got.url)
    check("an empty url string is ignored, not applied as a blank",
          [x for x in S.build_sources(set(), {"sources": {
              "katadata": {"url": "  "}}}) if x.name == "katadata"][0].url
          == "https://katadata.co.id/rss")
    check("a source not mentioned keeps its built-in url",
          [x for x in moved if x.name == "idxchannel"][0].url
          == "https://www.idxchannel.com/rss")
    check("enabled still works on its own",
          [x for x in S.build_sources(set(), {"sources": {
              "detik-finance": {"enabled": True}}})
           if x.name == "detik-finance"][0].enabled is True)

    print("\n== Kompas: no RSS exists, so the index page is parsed ==")
    # Measured on Gill's PC 2026-09-14: every Kompas RSS path 404s or 403s
    # (rss.kompas.com is a partner API that answers "Welcome to API Feed
    # Social"), while money.kompas.com serves 198 KB of plain HTML.
    kb = raw("kompas-money.html")
    if kb:
        items = S.KompasSource("kompas-money", "x").parse(kb)
        check("the index yields articles", len(items) == 26, len(items))
        check("every one is dated - the page only says '20 jam lalu', so the "
              "time comes from the URL", all(i.published for i in items))
        first = [i for i in items if i.id.endswith("154628026")][0]
        check("and it is read to the second, in WIB",
              first.published.hour == 15 and first.published.minute == 46
              and first.published.second == 28, first.published)
        check("the 9-digit id is the dedup key",
              len({i.id for i in items}) == len(items))
        check("the most-read sidebar is included, not skipped - it carried a "
              "real emiten headline",
              any("Morgan Stanley Jual Saham GOTO" in i.title for i in items),
              [i.title[:40] for i in items[:3]])
        check("a 2019 self-promo tile in the footer is NOT an article",
              not any("/read/2019/" in i.url for i in items))
        check("titles are clean of markup and entities",
              all("<" not in i.title and "&amp;" not in i.title for i in items))
        check("urls are absolute, so a click works",
              all(i.url.startswith("https://") for i in items))
        stamps = sorted(i.published for i in items)
        check("the newest is newer than the oldest by about a day, which is "
              "what one index page covers",
              1 < (stamps[-1] - stamps[0]).total_seconds() / 3600 < 48,
              (stamps[0], stamps[-1]))
    # NOT a failing check. A fixture this repo does not ship cannot be a
    # broken parser - see the SKIPPED banner at the end.

    check("a page with no articles parses to nothing, not an exception",
          S.KompasSource("k", "x").parse(b"<html><body>nope</body></html>") == [])
    check("a link with no heading inside it is skipped",
          S.KompasSource("k", "x").parse(
              b'<a href="https://money.kompas.com/read/2026/09/14/113756426/x">'
              b'<img src="y"></a>') == [])

    print("\n== the registry after the 2026-09-14 probe ==")
    reg2 = {x.name: x for x in S.build_sources(set(), {})}
    check("kompas-money is registered", "kompas-money" in reg2, sorted(reg2))
    check("pointing at the HTML index, since no feed exists",
          reg2["kompas-money"].url == "https://money.kompas.com/",
          reg2["kompas-money"].url)
    check("off by default - Gill decides whether its density earns the noise",
          reg2["kompas-money"].enabled is False)
    check("and polled every third tick, because 198 KB every 60s is a quarter "
          "of a gigabyte a day", reg2["kompas-money"].every_n_ticks == 3,
          reg2["kompas-money"].every_n_ticks)
    check("bisnis is NOT registered - 8 paths 403 with headers and a Referer, "
          "and an entry that can never be ticked on is clutter",
          "bisnis-market" not in reg2
          and not any(".bisnis.com" in x.url for x in reg2.values()),
          sorted(reg2))
    check("turning kompas on is all it takes",
          [x for x in S.build_sources(set(), {"sources": {
              "kompas-money": {"enabled": True}}})
           if x.name == "kompas-money"][0].enabled is True)
    check("the default launch set is still the three that were MEASURED",
          sorted(n for n, x in reg2.items() if x.enabled)
          == ["idxchannel", "katadata", "kontan-investasi"],
          sorted(n for n, x in reg2.items() if x.enabled))

    banner()
    print("\n%d checks failed" % len(failures) if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
