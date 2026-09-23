#!/usr/bin/env python3
"""
test_jci.py - the entry point's wiring, especially the parts that fail QUIETLY.

Three of these guard against an app that looks like it is working:

  · --once against a fresh store would SEED silently and print "nothing would
    alert", which reads exactly like "no news matched". It must force seed=False.
  · a watchlist that is never substituted into the rules does nothing at all,
    because an empty ticker list means ANY ticker - so the box would look
    broken while the app cheerfully alerted on the whole market.
  · a first launch with no config.json must write one and carry on, not exit.
    The exe gets handed to colleagues.

Offline: nothing here touches the network or idx.co.id.
Run:  python test_jci.py
"""

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import jci
import jciengine as E
import jcifilter as F
import jcimatch as M
import jcioptions as O
import jcisource as S

NOW = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)
failures = []


def check(name, cond, detail=None):
    if cond:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}" + (f"   -> {detail!r}" if detail is not None else ""))


def main():
    print("\n== the @watchlist token ==")
    filters = {"categories": F.DEFAULT_CATEGORIES,
               "rules": [dict(O.new_rule("coverage"), tickers=["@watchlist"]),
                         dict(O.new_rule("market"), tickers=[])]}
    out = jci.expand_watchlist(filters, ["bbca", " bbri ", "BMRI"])
    check("the token becomes the watchlist, upper-cased and trimmed",
          out["rules"][0]["tickers"] == ["BBCA", "BBRI", "BMRI"],
          out["rules"][0]["tickers"])
    check("a rule that did not ask for it is untouched",
          out["rules"][1]["tickers"] == [], out["rules"][1]["tickers"])
    mixed = jci.expand_watchlist(
        {"rules": [dict(O.new_rule("x"), tickers=["@watchlist", "TLKM"])]},
        ["BBCA"])
    check("explicit tickers survive alongside the token",
          mixed["rules"][0]["tickers"] == ["BBCA", "TLKM"],
          mixed["rules"][0]["tickers"])
    empty = jci.expand_watchlist(filters, [])
    check("an empty watchlist leaves the rule matching ANY ticker, as documented",
          empty["rules"][0]["tickers"] == [], empty["rules"][0]["tickers"])
    check("expand_watchlist does not mutate what it was given",
          filters["rules"][0]["tickers"] == ["@watchlist"],
          filters["rules"][0]["tickers"])
    check("the token passes validation rather than being reported as a bad ticker",
          not any("not a 4-letter" in e for e in F.validate(filters)),
          F.validate(filters))
    check("and passes the Options validator too",
          not any("not a 4-letter" in e
                  for e in O.validate({"watchlist": "@watchlist",
                                       "rules": filters["rules"],
                                       "categories": F.DEFAULT_CATEGORIES})))

    print("\n== a dry run must not silently seed ==")
    emiten = {"BBCA": {"name": "PT Bank Central Asia Tbk.",
                       "aliases": ["bank central asia"], "sector": "Keuangan"}}
    table = M.Table(emiten)
    fs = F.FilterSet({"categories": F.DEFAULT_CATEGORIES,
                      "rules": [O.new_rule("everything")], "global_exclude": []})
    feed = [S.Item("katadata", "n1", "BBCA Tebar Dividen Interim",
                   "http://x/1", NOW)]
    fresh = E.Pipeline(table, fs, E.Store(), {}, emiten)
    check("a fresh store WOULD seed and show nothing - this is the trap",
          fresh.process(feed, now=NOW).alerts == [])
    fresh2 = E.Pipeline(table, fs, E.Store(), {}, emiten)
    check("so --once passes seed=False and the decision becomes visible",
          len(fresh2.process(feed, now=NOW, seed=False).alerts) == 1)

    print("\n== config on first launch ==")
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "config.json")
        cfg, created = jci.load_config(path)
        check("a missing config is created, not a reason to exit",
              created is True and os.path.exists(path))
        check("the default rule references the watchlist rather than hard-coding",
              cfg["filters"]["rules"][0]["tickers"] == ["@watchlist"],
              cfg["filters"]["rules"][0]["tickers"])
        check("the written file explains the empty-list trap",
              "ANY ticker, not none" in cfg["_comment"], cfg.get("_comment"))
        check("what it writes validates clean",
              O.validate(O.read_values(cfg)) == [], O.validate(O.read_values(cfg)))
        cfg2, created2 = jci.load_config(path)
        check("a second launch reads it back instead of overwriting",
              created2 is False and cfg2["filters"] == cfg["filters"])
        with open(path, encoding="utf-8") as f:
            check("and what landed on disk is what was returned",
                  json.load(f)["watchlist"] == cfg["watchlist"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n== a broken config must not produce a stack trace ==")
    # The real mistake, made on the first hand-edit: "watchlist": [BBCA, BMRI]
    # with no quotes. json.load raised through decoder.py, which tells the
    # person nothing about which line to fix - and config.json is meant to be
    # hand-edited, so this is a user-facing surface.
    tmp = tempfile.mkdtemp()
    try:
        bad = os.path.join(tmp, "config.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write('{\n "poll_seconds": 60,\n "watchlist": [BBCA, BMRI],\n'
                    ' "sources": {}\n}\n')
        try:
            jci.load_config(bad)
            msg = None
        except SystemExit as exc:
            msg = str(exc)
        check("it exits cleanly instead of raising JSONDecodeError",
              msg is not None, msg)
        check("the message names the file", "config.json" in (msg or ""), msg)
        check("and the line number", "line 3" in (msg or ""), msg)
        check("it shows the offending line itself",
              "watchlist" in (msg or "") and ">" in (msg or ""), msg)
        check("with a caret under the column",
              "^" in (msg or ""), msg)
        check("and names the likeliest cause in plain words",
              "quote" in (msg or "").lower(), msg)
        check("no module paths or exception class names leak into it",
              not any(x in (msg or "")
                      for x in ("Traceback", "json.decoder", "JSONDecodeError",
                                ".py\", line")), msg)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n== news.csv is history, not live state ==")
    # The confusion this guards: after expanding filters.categories, --report
    # still showed the same titles as uncategorised. That is CORRECT - the
    # categories column records what was true when the alert fired. But it
    # made the expansion look like it had done nothing, so --recategorize
    # re-runs today's rules over the recorded titles.
    src2 = open(os.path.join(jci.HERE, "jci.py"), encoding="utf-8").read()
    check("--recategorize exists", "--recategorize" in src2)
    check("it re-reads the CURRENT categories rather than the CSV column",
          "categories_of(r[" in src2 and "fs.categories" in src2)
    check("and reports how much of the gap closed",
          "closed" in src2 and "still uncategorised" in src2)

    print("\n== data dir resolution ==")
    tmp = tempfile.mkdtemp()
    try:
        d = jci.resolve_data_dir({"data_dir": os.path.join(tmp, "nested", "dir")})
        check("a missing data dir is created", os.path.isdir(d), d)
        check("and it is the one that was asked for",
              os.path.basename(d) == "dir", d)
        check("the write probe does not leave litter behind",
              ".writable" not in os.listdir(d), os.listdir(d))
        check("no data_dir falls back to the app folder",
              jci.resolve_data_dir({}) == jci.HERE, jci.resolve_data_dir({}))
        check("~ is expanded rather than taken literally",
              "~" not in jci.resolve_data_dir({"data_dir": "~/JCITest"}))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n== a frozen exe must own its own folder ==")
    # Under PyInstaller --onefile, __file__ points into a TEMP EXTRACTION
    # DIRECTORY that is deleted on exit. An exe using it reads a config that
    # is not the one beside it, writes settings nobody can find, and loses
    # them at shutdown. Nothing about that fails loudly.
    real_frozen = getattr(sys, "frozen", False)
    real_exe = sys.executable
    try:
        sys.frozen = True
        sys.executable = os.path.join(tempfile.gettempdir(), "app", "JCIAlert.exe")
        check("frozen: paths hang off the EXECUTABLE, not __file__",
              jci.app_dir() == os.path.dirname(os.path.abspath(sys.executable)),
              jci.app_dir())
        check("and not the extraction dir",
              "_MEI" not in jci.app_dir(), jci.app_dir())
    finally:
        if real_frozen:
            sys.frozen = real_frozen
        else:
            del sys.frozen
        sys.executable = real_exe
    check("not frozen: it is the source folder",
          jci.app_dir() == os.path.dirname(os.path.abspath(jci.__file__)))
    src3 = open(os.path.join(jci.HERE, "jci.py"), encoding="utf-8").read()
    check("config, emiten and data all derive from it",
          src3.count("os.path.join(HERE,") >= 2 and "HERE = app_dir()" in src3)

    print("\n== the build refuses to ship a broken app ==")
    bat = os.path.join(jci.HERE, "build.bat")
    if os.path.exists(bat):
        b = open(bat, encoding="utf-8").read()
        call = next(ln for ln in b.splitlines() if "call run_tests.bat" in ln)
        check("the suites run before PyInstaller is even installed",
              b.index(call) < b.index("pip install"))
        check("and any failure aborts the build",
              "goto :fail" in call, call)
        check("a missing emiten.json stops it too",
              'if not exist "emiten.json"' in b)
        check("emiten.json is copied beside the exe, not frozen into it",
              "dist\\emiten.json" in b and "--add-data" not in b)
        check("an already-tuned dist config is never clobbered",
              'if exist "dist\\config.json"' in b)

    print("\n== the build checks the cheap, fatal things FIRST ==")
    # 2026-09-17: build.bat ran all 15 suites, installed dependencies and spent
    # ~30 seconds in PyInstaller analysis before dying on
    #   PermissionError: [WinError 5] Access is denied: dist\JCIAlert.exe
    # because the app was running and Windows locks a running exe. The check
    # costs two seconds. Ordering is the whole fix.
    bb = open(os.path.join(jci.HERE, "build.bat"), encoding="utf-8").read()
    check("the build refuses to run while JCIAlert.exe is running",
          "tasklist" in bb.lower() and "JCIAlert.exe" in bb)
    lock = bb.lower().index("tasklist")
    first_suite = bb.index("call run_tests.bat")
    check("and it checks BEFORE the suites, not after the build",
          lock < first_suite, (lock, first_suite))
    check("and there is ONE list of suites - build.bat calls run_tests.bat "
          "instead of keeping a second copy in step by hand",
          "python test_" not in bb, bb[bb.index("python test_"):][:60]
          if "python test_" in bb else None)
    check("the message says how to quit it, not just that it failed",
          "Quit" in bb and "taskkill" in bb)
    check("and mentions the chevron, since that is where the icon usually is",
          "chevron" in bb)

    print("\n== a pre-build suite must not need the build's own output ==")
    # test_installer.py asserted dist\JCIAlert.exe existed while running at
    # build.bat line 42, with PyInstaller at line 55. From a clean tree a green
    # build was impossible; it passed once only because a stale exe was lying
    # around. Same shape as the running-exe bug an hour earlier: a check that
    # depends on work which happens later.
    import re as _re2
    rt = open(os.path.join(jci.HERE, "run_tests.bat"), encoding="utf-8").read()
    suites = _re2.findall(r"python\s+(test_\w+\.py)", rt)
    check("run_tests.bat runs every suite in the folder",
          set(suites) == {f for f in os.listdir(jci.HERE)
                          if f.startswith("test_") and f.endswith(".py")},
          sorted(set(f for f in os.listdir(jci.HERE)
                     if f.startswith("test_") and f.endswith(".py"))
                 ^ set(suites)))
    lines = bb.splitlines()
    call_at = next(i for i, l in enumerate(lines) if "call run_tests.bat" in l)
    build_at = next(i for i, l in enumerate(lines) if "-m PyInstaller" in l)
    check("build.bat does run the suites before PyInstaller",
          call_at < build_at, (call_at, build_at))
    for name in sorted(suites):
        src = open(os.path.join(jci.HERE, name), encoding="utf-8").read()
        # `dist` followed by a separator - not "distinctive", which is what a
        # bare substring test flagged on the first run of this very check.
        refers = _re2.search(r"dist[\\/]", src) is not None
        check(f"{name} does not require a built artefact to pass",
              not refers or "not built yet" in src, name)

    print("\n== version is a single source of truth ==")
    check("APP is built from VERSION", jci.APP == "JCIAlert " + jci.VERSION)
    src = open(os.path.join(jci.HERE, "jci.py"), encoding="utf-8").read()
    import re as _re
    hard = [m for m in _re.findall(r'"JCIAlert \d+\.\d+"', src)]
    check("no window title or string hard-codes a version number",
          not hard, hard)

    print("\n== argparse surface ==")
    check("--version prints and exits clean", jci.main(["--version"]) == 0)
    for flag in ("--once", "--watch", "--verify-sources", "--show-config",
                 "--commit", "--density", "--report", "--day",
                 "--recategorize", "--list"):
        check(f"{flag} is accepted", flag in src)

    print("\n%d checks failed" % len(failures) if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
