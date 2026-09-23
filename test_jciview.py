#!/usr/bin/env python3
"""
test_jciview.py - do engine payloads become rows the popup can actually draw?

This layer is small but it is an INTEGRATION CONTRACT with code that is not
going to change: idx3popup is lifted verbatim from IDXAlert, so jciview has to
meet it exactly rather than the other way round. The sharp edge is the clock -
the popup renders it as `posted[-8:-3]`, which yields HH:MM only if the string
ends "HH:MM:SS". The test below asserts the SLICE, not the format string,
because the slice is the contract and the format is just how we satisfy it.

Offline. Run:  python test_jciview.py
"""

from datetime import datetime, timedelta, timezone

import jciengine as E
import jcimatch as M
import jcisource as S
import jciview as V

WIB = timezone(timedelta(hours=7))
T = datetime(2026, 9, 4, 9, 21, 5, tzinfo=timezone.utc)   # 16:21:05 WIB

failures = []


def check(name, cond, detail=None):
    if cond:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}" + (f"   -> {detail!r}" if detail is not None else ""))


def alert(ticker, title, sector="Keuangan", source="katadata", when=T,
          cid=1, sources=None, url=None):
    it = S.Item(source, f"i{ticker}{cid}", title, url or f"http://x/{ticker}", when)
    return E.Alert(it, ticker, "coverage", {"corporate_action"}, cid, sector,
                   [M.Hit(ticker, "alias", 0.9)],
                   sources if sources is not None else [(source, it.url, when)])


def main():
    print("\n== the clock contract with idx3popup ==")
    r = V.row_for_alert(alert("BBCA", "BBCA Tebar Dividen Interim"))
    check("posted[-8:-3] yields HH:MM, which is what the popup slices",
          r["posted"][-8:-3] == "16:21", (r["posted"], r["posted"][-8:-3]))
    check("the timestamp is rendered in WIB, not UTC",
          r["posted"].startswith("2026-09-04 16:21"), r["posted"])
    naive = V.posted(datetime(2026, 9, 4, 16, 21, 5))
    check("a naive datetime is assumed to be WIB already, not shifted",
          naive[-8:-3] == "16:21", naive)
    check("a missing timestamp is empty, not a crash", V.posted(None) == "")

    print("\n== a single alert (the one-element ticker row) ==")
    check("the row carries every field the popup reads",
          all(k in r for k in ("key", "ticker", "title", "posted", "files")),
          sorted(r))
    check("the ticker is the badge", r["ticker"] == "BBCA", r["ticker"])
    check("one story means one menu entry, so a click opens it directly",
          len(r["files"]) == 1, r["files"])
    check("the entry is stamped with time, outlet and headline",
          r["files"][0]["name"].startswith("16:21 · Katadata · BBCA Tebar"),
          r["files"][0]["name"])
    check("and the link is under the key the popup actually reads",
          r["files"][0]["url"] == "http://x/BBCA", r["files"][0])
    check("the key is the ticker, so a later story joins this row",
          r["key"] == "t:BBCA", r["key"])
    km = V.row_for_alert(alert("GOTO", "Morgan Stanley Jual Saham GOTO",
                               source="kompas-money"))
    check("a mapped source gets its human label",
          "Kompas" in km["files"][0]["name"], km["files"][0]["name"])
    check("an unmapped source degrades to a title-cased name",
          V.source_label("some-new-wire") == "Some New Wire",
          V.source_label("some-new-wire"))
    noticker = V.row_for_alert(alert(None, "IHSG Ditutup Menguat"))
    check("an alert with no ticker still gets a badge",
          noticker["ticker"] == "PASAR", noticker["ticker"])

    print("\n== several stories about one ticker share ONE row ==")
    three = [alert("BMRI", "Bank Mandiri Tebar Dividen Interim", cid=7),
             alert("BMRI", "Bank Mandiri Tunjuk Direktur Utama Baru",
                   when=T + timedelta(minutes=40), source="kontan-investasi", cid=8),
             alert("BMRI", "Bank Mandiri Gelar RUPSLB Akhir Bulan",
                   when=T + timedelta(minutes=20), source="idxchannel", cid=9)]
    rm = V.row_for_ticker(three)
    check("three BMRI stories collapse into a single row",
          rm["key"] == "t:BMRI" and rm["count"] == 3, (rm["key"], rm["count"]))
    check("the row shows the NEWEST headline, not a count string",
          rm["title"] == "Bank Mandiri Tunjuk Direktur Utama Baru", rm["title"])
    check("and takes its clock from that newest story",
          rm["posted"][-8:-3] == "17:01", rm["posted"])
    check("every story is reachable from the menu", len(rm["files"]) == 3)
    check("menu entries run newest first",
          [f["name"][:5] for f in rm["files"]] == ["17:01", "16:41", "16:21"],
          [f["name"][:5] for f in rm["files"]])
    check("each entry names its own outlet, not the row's",
          [f["source"] for f in rm["files"]]
          == ["kontan-investasi", "idxchannel", "katadata"],
          [f["source"] for f in rm["files"]])
    check("categories are unioned across the stories",
          rm["categories"] == ["corporate_action"], rm["categories"])
    check("each entry keeps its cluster id",
          {f["cluster"] for f in rm["files"]} == {7, 8, 9},
          [f["cluster"] for f in rm["files"]])

    print("\n== a group's story count and ticker count are different numbers ==")
    # From the 18:26 run: "Infrastruktur · 4 tickers: SSIA, TLKM" - four
    # stories across two tickers. Conflating them reads as a bug in the output.
    two_t = E.GroupedAlert("Infrastruktur", [
        alert("SSIA", "SSIA Teken Kontrak Baru", "Infrastruktur", cid=51),
        alert("SSIA", "SSIA Rilis Kinerja Kuartal", "Infrastruktur",
              when=T + timedelta(minutes=5), cid=52),
        alert("TLKM", "TLKM Umumkan Belanja Modal", "Infrastruktur",
              when=T + timedelta(minutes=8), cid=53),
        alert("TLKM", "TLKM Tunjuk Direktur Baru", "Infrastruktur",
              when=T + timedelta(minutes=11), cid=54)])
    check("four stories across two tickers really is two tickers",
          two_t.tickers == ["SSIA", "TLKM"], two_t.tickers)
    check("while the group still holds all four stories",
          len(two_t.alerts) == 4, len(two_t.alerts))
    rt = V.row_for_group(two_t)
    check("the row title counts TICKERS, not stories",
          rt["title"] == "2 Infrastruktur names: SSIA, TLKM", rt["title"])
    check("and the menu still reaches every story", len(rt["files"]) == 4)

    print("\n== rows_for groups alerts by ticker ==")
    mixed = V.rows_for(three + [alert("TLKM", "TLKM Teken Kontrak",
                                      "Infrastruktur",
                                      when=T + timedelta(minutes=10), cid=30)])
    check("four alerts across two tickers become two rows",
          len(mixed) == 2, [(r["ticker"], r["count"]) for r in mixed])
    check("the BMRI row carries all three of its stories",
          next(r for r in mixed if r["ticker"] == "BMRI")["count"] == 3,
          [(r["ticker"], r["count"]) for r in mixed])
    check("rows sort by their newest story",
          [r["ticker"] for r in mixed] == ["BMRI", "TLKM"],
          [(r["ticker"], r["posted"]) for r in mixed])
    check("nothing is lost in the collapse",
          sum(len(r["files"]) for r in mixed) == 4,
          [len(r["files"]) for r in mixed])
    unticked = V.rows_for([alert(None, "IHSG Ditutup Menguat", cid=41),
                           alert(None, "Rupiah Melemah", cid=42)])
    check("tickerless alerts share the PASAR row rather than multiplying",
          len(unticked) == 1 and unticked[0]["count"] == 2,
          [(r["ticker"], r["count"]) for r in unticked])

    print("\n== a sector burst becomes ONE row ==")
    g = E.GroupedAlert("Keuangan", [
        alert("BBCA", "BBCA Tebar Dividen Interim", cid=11),
        alert("BBRI", "BBRI Umumkan Buyback Saham", when=T + timedelta(minutes=3), cid=12),
        alert("BMRI", "Bank Mandiri Gelar RUPSLB", when=T + timedelta(minutes=6), cid=13),
        alert("SRTG", "SRTG Tambah Kepemilikan", when=T + timedelta(minutes=9), cid=14)])
    rg_group = g
    rg = V.row_for_group(g)
    check("the badge is the sector, shortened to fit the chip",
          rg["ticker"] == "KEU", rg["ticker"])
    check("the title names the count, sector and tickers",
          rg["title"] == "4 Keuangan names: BBCA, BBRI, BMRI, SRTG", rg["title"])
    check("the burst is still fully readable in the menu",
          len(rg["files"]) == 4, len(rg["files"]))
    check("menu entries lead with the ticker",
          rg["files"][0]["name"].startswith("SRTG · "), rg["files"][0]["name"])
    check("the newest member supplies the row's clock",
          rg["posted"][-8:-3] == "16:30", rg["posted"])
    check("the row is flagged as a group", rg["grouped"] is True)
    big = E.GroupedAlert("Energi", [alert(f"T{n:03d}", f"headline {n}", "Energi",
                                          cid=100 + n) for n in range(9)])
    tb = V.row_for_group(big)["title"]
    check("a very wide burst truncates the ticker list rather than overflowing",
          "+3" in tb and tb.startswith("9 Energi names:"), tb)
    check("an unmapped sector still yields a short badge",
          V.sector_badge("Sektor Yang Sangat Panjang") == "SEKTOR",
          V.sector_badge("Sektor Yang Sangat Panjang"))
    check("no sector at all degrades to PASAR", V.sector_badge("") == "PASAR")

    print("\n== rows_for: mixed payloads, newest first ==")
    rows = V.rows_for([g, alert("TLKM", "TLKM Teken Kontrak",
                                "Infrastruktur", when=T + timedelta(hours=1),
                                cid=21)])
    check("both kinds of payload are accepted", len(rows) == 2, rows)
    check("a sector group stays one row, it is not split by ticker",
          any(r["grouped"] and len(r["files"]) == 4 for r in rows),
          [(r["ticker"], r["grouped"], len(r["files"])) for r in rows])
    check("the newest row sorts first",
          rows[0]["ticker"] == "TLKM", [r["ticker"] for r in rows])
    check("sorting matches how the popup itself sorts (by posted, desc)",
          rows == sorted(rows, key=lambda r: r["posted"], reverse=True))
    check("an empty batch is empty, not an error", V.rows_for([]) == [])
    check("titles are never mangled by the row builder",
          rows[0]["title"] == "TLKM Teken Kontrak", rows[0]["title"])

    print("\n== headings and trimming ==")
    check("one alert is headed by its ticker",
          V.summary([V.row_for_alert(alert("BBCA", "x"))]) == "BBCA")
    check("one ticker with several stories says how many",
          V.summary([rm]) == "BMRI · 3 stories", V.summary([rm]))
    check("one group is headed by its sector",
          V.summary([rg]) == "Keuangan", V.summary([rg]))
    check("the heading counts STORIES, not rows",
          V.summary(mixed) == "4 new stories", V.summary(mixed))
    check("nothing is an empty heading", V.summary([]) == "")
    long = "A" * 90
    check("long text is trimmed with an ellipsis",
          V.trim(long, 70).endswith("…") and len(V.trim(long, 70)) == 70,
          len(V.trim(long, 70)))
    check("short text is left exactly alone", V.trim("BBCA naik") == "BBCA naik")
    check("whitespace is collapsed, including newlines",
          V.trim("BBCA  \n  naik") == "BBCA naik", V.trim("BBCA  \n  naik"))

    print("\n== the schema contract, read out of jcipopup itself ==")
    # The bug this exists for: jciview emitted "path" while jcipopup does
    # webbrowser.open(f["url"]) with no .get() and no fallback - a KeyError on
    # click that no test inventing its own schema could ever catch. So the
    # contract is now READ FROM THE OTHER MODULE'S SOURCE rather than assumed.
    import os as _os
    import re as _re
    pop = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "jcipopup.py")
    if _os.path.exists(pop):
        src = open(pop, encoding="utf-8").read()
        subscripted = set(_re.findall(r'\bf\["([a-z_]+)"\]', src))
        subscripted |= set(_re.findall(r'files\[0\]\["([a-z_]+)"\]', src))
        entry = V.row_for_alert(alert("BBCA", "x"))["files"][0]
        check("jcipopup subscripts at least one entry key (the regex still matches)",
              bool(subscripted), subscripted)
        check("every key jcipopup subscripts without a fallback is emitted",
              subscripted <= set(entry), (sorted(subscripted), sorted(entry)))
        gentry = V.row_for_group(rg_group)["files"][0]
        check("group entries satisfy the same contract",
              subscripted <= set(gentry), (sorted(subscripted), sorted(gentry)))
        rowkeys = set(_re.findall(r'it\.get\("([a-z_]+)"', src))
        rowkeys |= set(_re.findall(r'item\.get\("([a-z_]+)"', src))
        rowkeys |= set(_re.findall(r'data\[0\]\.get\("([a-z_]+)"', src))
        row = V.row_for_alert(alert("BBCA", "x"))
        check("every row key jcipopup reads is present too",
              rowkeys <= set(row), (sorted(rowkeys - set(row)), sorted(row)))
    else:
        print("  (jcipopup.py not present - contract check skipped)")

    print("\n== a SECOND story about a ticker already on screen ==")
    # The bug, reported 2026-09-10: "the news ticker grouping after new news
    # pops up isnt working". The row key is the ticker, so a later story about
    # TLKM arrives with a key that is already on screen - and add_items treated
    # a known key as "seen it" and dropped the whole row. One row per ticker
    # with an N-stories menu therefore only ever worked WITHIN one tick; a
    # story a minute later vanished silently. These pin the merge.
    import jcipopup as P

    def rw(key, title, posted, url, ticker="TLKM"):
        return {"key": key, "ticker": ticker, "title": title, "posted": posted,
                "files": [{"name": title, "url": url}], "count": 1,
                "categories": [], "rule": "r", "sector": "Infrastruktur"}

    first = rw("t:TLKM", "Telkom genjot infrastruktur", "2026-09-10 12:58:00",
               "u1")
    later = rw("t:TLKM", "Telkom rights issue", "2026-09-10 13:10:00", "u2")
    rows, fresh = P.merge_rows([first], [later])
    check("the ticker still has exactly one row", len(rows) == 1, len(rows))
    check("and the menu now carries BOTH stories",
          [f["url"] for f in rows[0]["files"]] == ["u2", "u1"], rows[0]["files"])
    check("the count the popup renders goes to 2",
          len(rows[0]["files"]) == 2)
    check("the row leads with the NEWER headline",
          rows[0]["title"] == "Telkom rights issue", rows[0]["title"])
    check("and takes the newer timestamp",
          rows[0]["posted"] == "2026-09-10 13:10:00", rows[0]["posted"])
    check("it counts as fresh, so it is highlighted and floats up",
          fresh == {"t:TLKM"}, fresh)

    check("the same story arriving twice changes nothing",
          P.merge_rows(rows, [later])[1] == set(),
          P.merge_rows(rows, [later])[1])

    older = rw("t:TLKM", "an older Telkom story", "2026-09-10 11:00:00", "u0")
    rows2, fresh2 = P.merge_rows(rows, [older])
    check("an older story still joins the menu",
          len(rows2[0]["files"]) == 3, rows2[0]["files"])
    check("but does not steal the headline",
          rows2[0]["title"] == "Telkom rights issue", rows2[0]["title"])

    other = rw("t:BBCA", "BBCA laba naik", "2026-09-10 13:20:00", "u3",
               ticker="BBCA")
    rows3, fresh3 = P.merge_rows(rows2, [other])
    check("a different ticker is still its own row", len(rows3) == 2)
    check("and the newest row is first",
          rows3[0]["ticker"] == "BBCA", [r["ticker"] for r in rows3])

    before = dict(first)
    P.merge_rows([first], [later])
    check("merging does not mutate the rows it was handed", first == before)

    check("add_items goes through merge_rows, not a known-key filter",
          "merge_rows(live[" in open("jcipopup.py", encoding="utf-8").read(),
          None)

    print("\n%d checks failed" % len(failures) if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
