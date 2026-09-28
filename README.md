# JCIAlert

Windows tray app. Watches Indonesian corporate news feeds, pops up when a
headline names a ticker on your watchlist. Python stdlib only, plus `pystray`
+ `pillow` for the tray icon, tkinter for windows.

Sibling to IDXAlert (same interface, different feed: corporate news vs IDX
disclosures). Runs side by side with it — separate config, `seen.json`, Run
key, mutex, tray icon. Don't share any of that between the two.

## What's new — 1.2

**Macroeconomics alerting, no ticker needed:** BI Rate, The Fed, Oil,
Inflation, Rupiah. Off by default — check one in the Macroeconomics tab in
Options and a keyword-matched headline for that indicator alerts even though
no company is named in it. Fires under its own popup label ("OIL", "BI RATE",
...), never lumped into the generic ticker-less bucket, and is filterable in
the dashboard the same way `ticker` already is. Keyword lists are a starting
point, not yet tuned against real headlines the way the corporate-action/M&A
categories were — see `SPEC-macro-tagging.md`.

Also: the portable zip no longer installs anything (unzip, run
`JCIAlert.exe`, done — `SETUP.bat`/`REMOVE.bat` are gone), and `build.bat`
no longer pops a real Windows dialog partway through the test run. Full
list: `CHANGELOG.md`.

---

## Setup (build from source)

```
git clone <this repo>
cd jcialert
python -m pip install pystray pillow pyinstaller

run_tests.bat             15 suites, offline, ~10s
build.bat                 tests, then dist\JCIAlert.exe
make_portable.bat         dist -> installer\JCIAlert-<version>-portable.zip
make_installer.bat        all of the above, then the Inno Setup .exe
```

Needs Python 3.10+ on Windows. `run_tests.bat` needs nothing but Python.
Building the exe needs the three pip packages above.

**Not in the repo:**

| missing | effect | fix |
|---|---|---|
| `fixtures/` | parser/end-to-end tests are skipped (banner, not silent) | `python probe_sources.py --save-fixtures fixtures` — from Indonesia, weekday, market hours |
| `dist/`, portable zip, `installer/Output/` | nothing to install yet | `make_installer.bat` |

`emiten.json` (ticker/company names) **is** in the repo — app won't start
without it. Refresh with `python build_aliases.py --report`.

`config.json` is in the repo with tuned categories and sources. Its
watchlist is empty, which means **every ticker, not none**.

## Installation

```
make_installer.bat        -> installer\Output\JCIAlert-Setup-<version>.exe
```

Needs [Inno Setup 6](https://jrsoftware.org/isdl.php). Installs per-user to
`%LOCALAPPDATA%\Programs\JCIAlert`, no admin, no UAC. An upgrade replaces
only `JCIAlert.exe` and `emiten.json` — your `config.json`, `seen.json`,
`news.csv`, `logs\` stay untouched. Uninstaller asks before deleting them.

**No Inno Setup / blocked installers:** unzip
`installer\JCIAlert-<version>-portable.zip` and run `JCIAlert.exe` — nothing
to install. Settings and history are written next to the exe, so the whole
folder is the app; delete the folder to remove it.

Neither the installer nor the portable exe is signed — SmartScreen will warn
once ("More info" → "Run anyway").

## How to use it

```
python jci.py --watch     run the polling loop in this console
python jci.py --once      poll once, show every decision, write nothing
dist\JCIAlert.exe         the tray app, after build.bat
```

First run is quiet by design: each source's backlog is recorded but not
alerted, so you don't get a hundred old headlines at startup.

**Two windows**, from the tray icon:

- **News dashboard** (double-click default) — every headline seen, not just
  alerts. `Show everything` widens to rejected ones; `Why / rule` column
  says which rule accepted or which gate rejected. Search/ticker/source/date
  filters. Click a column to sort. **Status** tab shows per-source health.
- **Options** — speed, grouping, alerts, watchlist, sources, filters,
  macroeconomics (BI Rate/Fed/Oil/Inflation/Rupiah, no ticker needed, off
  by default). Refuses to save a config that could never alert.

**Key settings:**

| setting | what it does |
|---|---|
| `watchlist` | tickers to alert on. **Empty = EVERY ticker.** |
| `corner` | popup corner |
| `max_item_age_minutes` | nothing older ever alerts, regardless of seen-set — makes restart safe |
| `sources` | per source `enabled` + optional `url` override |

**Check a watchlist before committing to it:**

```
jci.py --replay --watchlist BBCA,BMRI,BBRI
```

Re-runs current rules over every headline in `news.csv`. Anything that gets
through and isn't on the list prints `*** LEAK ***` and exits non-zero.

Other commands: `--doctor` (diagnose a failed start), `--report` (summarize
a day), `--verify-sources`, `--show-config`.

## Other important details

**Matching:** tickers are 4 letters. Three rules: `(BMRI)` parenthetical
(confidence 1.00), alias like "Bank Mandiri" → BMRI (0.90-0.95, depends on
`emiten.json`), bare 4-letter token (0.60).

**Filters** are OR'd rule lists; within one rule every filled field must
pass (`tickers`, `categories`, `require`, `exclude`, `min_confidence`, ...).
An empty field always means ANY, never NONE — same as an empty watchlist.
Term syntax: `dividen` (exact), `akuisisi*` / `*akuisisi` (prefix/suffix),
`tebar dividen` (phrase). Known false positive: `rugi` in loser-list
headlines matches `earnings` category by keyword alone — handled by
`exclude` + `max_tickers`, both pinned by tests.

**Clustering:** one story reaches you once; later outlets/stories about the
same ticker join the existing popup row instead of re-alerting. Doesn't
cross languages (works on Indonesian sources only).

**What it writes:** `news.csv` (every headline + why), `opened.json`,
`seen.json`, `logs\jci-YYYY-MM-DD.log`.

**Sources:** RSS aggregator + a few HTML scrapes, never headless browser —
bot protection is on article pages, not feeds. `kompas-money` is registered
but OFF (scraped, not RSS). Bisnis.com is blocked (403 everywhere) and not
registered. IQPlus was removed (unreachable since 2026-09-08); restore from
git history if it comes back online.

**If no tray icon appears:** check under the `^` chevron first (Windows 11
hides new tray icons there). Then `JCIAlert.exe --doctor`, or check
`%LOCALAPPDATA%\JCIAlert\startup-error.txt`. Then confirm `emiten.json` is
next to the exe — it's not bundled in, so a lone copied exe won't start.

## Tests

```
run_tests.bat
```

15 suites, offline, run by `build.bat` before it builds. A suite that can't
run (e.g. `fixtures/` missing) prints a banner and still exits 0.

## Contributing

Open an issue before a large change — this is a personal tool with unusual
constraints (stdlib-only, no network where it's edited, `--noconsole` exe
with no stderr). Patches: `run_tests.bat` must be green with a new check for
new behavior; no new runtime dependency for what stdlib already does.
