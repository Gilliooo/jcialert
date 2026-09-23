# Changelog

Dates are when the change was made, not when it was released.

## 1.0.1 — 2026-09-23

### Removed

A single pass over the whole tree, deleting mechanisms that no longer had a
caller. Net −334 lines of source, −149 lines of batch, −165 MB of build
litter, with all 15 suites green before and after.

- **IQPlus, parser and all.** The source left the registry in 1.0; its
  machinery didn't. `IQPlusSource` was the only ALL-CAPS source and the only
  one putting tickers in its URL slugs, so removing it also removed the
  slug-upgrade step in `Pipeline.process`, `MACRO_FEED_CODES`, the `allcaps`
  parameter threaded through `Item`/`Source`/`find_tickers`, matching rule 4
  (leading-token scoring and the ambiguous-word penalty), and
  `trading_days_only`. No registered source is ALL-CAPS.
- **Telegram and email settings.** Seven config keys, an Options tab, two
  validation rules — for a sender that was never written. A setting the app
  cannot act on is a promise the window makes on its behalf.
- **`alerts.csv`.** It recorded a strict subset of `news.csv`, which already
  logs every headline *with* its status. `--report` now reads `news.csv`
  filtered on `status == "alert"`. Two writers of the same facts is one
  chance for them to disagree.
- **Cross-language clustering.** A ticker-and-time rule for suppressing an
  English retelling of an Indonesian story. The only English source ever
  measured (IDN Financials) refuses this client; every registered source is
  Indonesian.
- **`JCIAlert.spec`** — PyInstaller rewrites it on every build.
- **`installer/install.bat` and `uninstall.bat`** — superseded by
  `SETUP.bat`/`REMOVE.bat`. Two installers that can disagree is worse than
  one.

### Fixed

- **The portable zip shipped the developer's own history.** `seen.json`,
  `news.csv`, `opened.json` and `logs\` were inside it, because the folder
  was assembled by hand and nothing ever emptied it — so anyone unzipping it
  started mid-history, on someone else's story clusters, with someone else's
  logs. `make_portable.bat` now builds the folder from nothing every time,
  from an explicit ship list.
- **Notifications stopped after hours of running.** The notification pump ran
  unguarded, so one exception ended the only thread that could draw a popup,
  silently. Guarded, with a watchdog and a change-only icon refresh.
- `build.bat` and `run_tests.bat` carried identical suite lists kept in step
  by hand. `build.bat` now calls `run_tests.bat`.
- `probe_sources.py` had its own copy of the measured Chrome header set and
  ALPN-pinned TLS context. It imports jcinet's now — a probe answering "will
  the app fetch this" with a different client than the app uses answers the
  wrong question the moment the two drift.
- Dead `HOME` constant and its unreachable branch in `jcipopup`.

## 1.0 — 2026-09-14

- Watchlist filtering verified against a day of real alerts, with `--replay`
  to check a watchlist before committing to it.
- Popup corner is configurable: any of the four screen corners.
- Kompas added, scraped from `money.kompas.com` — no RSS exists any more.
  The publication time comes from the URL, to the second; the page itself
  only says "20 jam lalu", and a relative stamp cannot drive an age cutoff.
- IQPlus removed: unreachable since 2026-09-08, 45 seconds of timeout per
  attempt while dead.
- Bisnis.com investigated and *not* registered — 403 on eight paths with the
  full Chrome header set and a same-site Referer retry.
- Installer: Inno Setup script plus a portable zip for machines that block
  unsigned installers. Per-user, no admin, no UAC.
- `--doctor` and a Diagnostics menu item, because a `--noconsole` exe has no
  stderr: `raise SystemExit("...")` produces total silence.

## 0.9 — 2026-09-05 → 09-13

First working version and the bugs found by running it.

- News dashboard: every headline seen, and why each did or did not alert.
- Per-source cold-start seeding. A source that has never delivered has not
  been seeded, whatever the others did — a DNS-delayed startup once dumped
  170 stale headlines into a live pipeline.
- Five-state source status. `ok  iqplus-stock  0 items` was a source that had
  not answered in days.
- Refusals name the gate that rejected them; nine different conditions used
  to report the same "no rule accepted".
- Watchlist settings now persist (three separate bugs: two Tk roots racing,
  config never re-read, and a `FilterSet` compiled once at startup).
- Popup rows merge across ticks instead of stacking.
