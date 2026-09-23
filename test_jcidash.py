#!/usr/bin/env python3
"""
The news log and the view over it.

The claim under test is the one Gill made: *the filters decide what is SHOWN,
not what is KEPT*. So the interesting cases are all about the rows that never
alerted - that they are written at all, that they survive a round trip through
the CSV, and that widening the view brings them back.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jcidash as D                                        # noqa: E402
import jciengine as E                                      # noqa: E402
import jcimatch as M                                       # noqa: E402
import jcisource as S                                      # noqa: E402

failures = []
NOW = datetime(2026, 9, 10, 3, 0, tzinfo=timezone.utc)      # 10:00 WIB


def check(name, cond, detail=None):
    if cond:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}" + (f"   -> {detail!r}" if detail is not None else ""))


def item(tid, title, when=NOW, source="katadata", cat=""):
    it = S.Item(source, tid, title, f"http://x/{tid}", when, cat)
    return it


def rec(title, status="alert", reason="", ticker="BBCA", rule="My coverage",
        source="katadata", hits=(), when=NOW):
    return E.Record(item(title[:8], title, when, source), hits, ticker,
                    "Financials", {"earnings"}, rule, status, reason, 7)


def main():
    print("\n== a Record becomes a row, dropped ones included ==")
    r = D.row_of(rec("BBCA cetak laba", "dropped", "no rule accepted"), NOW)
    check("the reason survives", r["reason"] == "no rule accepted", r)
    check("so does the status", r["status"] == "dropped", r)
    check("the clock is WIB, not UTC", r["ts_wib"] == "2026-09-10 10:00:00",
          r["ts_wib"])
    check("the story's own time wins over ours",
          D.row_of(rec("x", when=NOW - timedelta(hours=2)), NOW)["ts_wib"]
          == "2026-09-10 08:00:00")
    hit = M.Hit("BBCA", "paren", 1.0)
    r2 = D.row_of(rec("BBCA (BBCA) naik", hits=[hit]), NOW)
    check("how it matched is kept, not just that it did",
          r2["match_rule"] == "paren" and r2["confidence"] == "1.00", r2)

    print("\n== every column the reader has is a column the writer writes ==")
    check("no key drifts between row_of and NEWS_COLUMNS",
          set(D.row_of(rec("x"), NOW)) == set(D.NEWS_COLUMNS),
          set(D.row_of(rec("x"), NOW)) ^ set(D.NEWS_COLUMNS))

    print("\n== round trip through the file ==")
    tmp = os.path.join(tempfile.mkdtemp(), "news.csv")
    D.append(tmp, [D.row_of(rec("BBCA laba naik"), NOW),
                   D.row_of(rec("Promo CSR BMRI", "dropped", "excluded: csr",
                                ticker="BMRI"), NOW)])
    D.append(tmp, [D.row_of(rec("ASII rights issue", ticker="ASII"), NOW)])
    body = open(tmp, encoding="utf-8").read()
    check("the header is written once, not once per append",
          body.count("ts_wib,status") == 1, body.count("ts_wib,status"))
    rows = D.read(tmp)
    check("everything comes back", len(rows) == 3, len(rows))
    check("newest first", rows[0]["title"] == "ASII rights issue", rows[0])
    check("including the one that never alerted",
          any(x["title"] == "Promo CSR BMRI" for x in rows))
    check("a missing file is empty, not an exception",
          D.read(tmp + ".nope") == [])
    check("an unwritable path is swallowed - recording never kills the app",
          D.append(os.path.join(tmp, "no", "such", "f.csv"), [{"title": "x"}])
          == 0)

    print("\n== the log rotates instead of growing without end ==")
    big = os.path.join(tempfile.mkdtemp(), "news.csv")
    D.append(big, [D.row_of(rec("first headline ever"), NOW)])
    check("a small file is left alone", D.rotate(big, 10 ** 9) is False)
    check("and its rows are still there", len(D.read(big)) == 1)
    D.rotate(big, 1)
    check("an oversized file is renamed, not truncated",
          os.path.exists(big + ".1") and not os.path.exists(big))
    check("the rotated copy still holds the rows",
          "first headline ever" in open(big + ".1", encoding="utf-8").read())
    D.append(big, [D.row_of(rec("life goes on"), NOW)])
    check("and the new file gets its own header",
          open(big, encoding="utf-8").read().count("ts_wib,status") == 1)
    check("rotating a file that is not there is not an error",
          D.rotate(big + ".nope") is False)

    print("\n== the view: filtered by default, everything on request ==")
    check("the default face is what the filters let through",
          [x["title"] for x in D.view(rows)]
          == ["ASII rights issue", "BBCA laba naik"],
          [x["title"] for x in D.view(rows)])
    check("show_all brings back the rejected headline",
          len(D.view(rows, show_all=True)) == 3)
    check("and it is the SAME row, reason and all",
          [x for x in D.view(rows, show_all=True)
           if x["status"] == "dropped"][0]["reason"] == "excluded: csr")

    print("\n== the narrowing arguments ==")
    check("by ticker", [x["ticker"] for x in
                        D.view(rows, show_all=True, ticker="bmri")] == ["BMRI"])
    check("by source", len(D.view(rows, show_all=True, source="nowhere")) == 0)
    check("search matches the headline",
          len(D.view(rows, show_all=True, query="rights")) == 1)
    check("and matches the ticker too, which is not in the headline",
          len(D.view(rows, show_all=True, query="bmri")) == 1)
    check("status narrows without widening",
          len(D.view(rows, show_all=True, status="alert")) == 2)

    print("\n== the age window keeps undated rows ==")
    old = D.row_of(rec("stale story", when=NOW - timedelta(hours=30)), NOW)
    blank = dict(old, ts_wib="", title="no clock at all")
    both = [old, blank]
    kept = [x["title"] for x in D.view(both, show_all=True, hours=6, now=NOW)]
    check("a 30h-old row is outside a 6h window", "stale story" not in kept, kept)
    check("but a row with no timestamp is kept, not silently vanished",
          "no clock at all" in kept, kept)

    print("\n== periods are calendar boundaries, not rolling windows ==")
    now = datetime(2026, 9, 10, 15, 0, tzinfo=D.WIB)   # a Thursday
    check("Today starts at WIB midnight, not 24h ago",
          D.period_start("Today", now).hour == 0
          and D.period_start("Today", now).day == 10)
    check("This week starts on Monday", D.period_start("This week", now).day == 7)
    check("This month starts on the 1st",
          D.period_start("This month", now).day == 1)
    check("This year starts in January",
          D.period_start("This year", now).month == 1)
    check("All has no start at all", D.period_start("All", now) is None)
    late = dict(D.row_of(rec("late last night"), NOW),
                ts_wib="2026-09-09 23:30:00")
    check("11pm yesterday is yesterday's news, not 'within 24 hours'",
          D.view([late], show_all=True, period="Today", now=now) == [])

    print("\n== read receipts ==")
    rp = os.path.join(tempfile.mkdtemp(), "opened.json")
    check("a missing file is no receipts, not a crash", D.load_opened(rp) == {})
    D.save_opened(rp, {"http://x/1": "t"})
    check("and it round-trips", D.load_opened(rp) == {"http://x/1": "t"})
    D.save_opened(rp, {f"u{i}": str(i).zfill(5) for i in range(4200)}, keep=100)
    kept = D.load_opened(rp)
    check("the file is capped so it cannot grow for ever", len(kept) == 100,
          len(kept))
    check("and it keeps the NEWEST, not an arbitrary hundred", "u4199" in kept)
    D.save_opened(os.path.join(rp, "no", "where.json"), {"a": "b"})
    check("an unwritable path is swallowed", True)

    rows2 = [dict(D.row_of(rec("read one"), NOW), url="http://x/1"),
             dict(D.row_of(rec("unread one"), NOW), url="http://x/2")]
    check("unread_only keeps what has not been opened",
          [r["title"] for r in D.view(rows2, show_all=True, unread_only=True,
                                      opened={"http://x/1": "t"})]
          == ["unread one"])

    print("\n== sorting is stable, and blanks sink ==")
    three = [dict(rows2[0], ticker="TLKM"), dict(rows2[1], ticker=""),
             dict(rows2[0], ticker="ASII")]
    check("ascending", [r["ticker"] for r in D.sort_rows(three, "ticker")]
          == ["ASII", "TLKM", ""])
    check("descending keeps the blank at the bottom",
          [r["ticker"] for r in D.sort_rows(three, "ticker", True)]
          == ["TLKM", "ASII", ""])
    check("the Why column sorts by the string the table draws",
          D.sort_rows([{"status": "alert", "rule": "zed"},
                       {"status": "dropped", "reason": "abc"}], "_why")[0]
          ["reason"] == "abc")

    print("\n== the summaries the status bar shows ==")
    s = D.summary(rows)
    check("counts split alerted from not",
          (s["total"], s["alerts"], s["dropped"]) == (3, 2, 1), s)
    check("sources are counted once each", s["sources"] == 1, s)
    why = D.why_summary(rows)
    check("and the rejections are tallied by reason",
          why == [("excluded: csr", 1)], why)
    check("alerts contribute nothing to the rejection tally",
          D.why_summary(D.view(rows)) == [])

    print("\n%d checks failed" % len(failures) if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
