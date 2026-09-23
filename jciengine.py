#!/usr/bin/env python3
"""
jciengine - the poll loop, and everything that turns fetched bytes into alerts.

Two layers, split on purpose:

  Pipeline   PURE. items in, decisions out. No threads, no clock of its own,
             no disk unless you hand it a Store. Every interesting rule in this
             app is testable through it without starting anything.
  Watcher    threads, timing, retries, and the API the tray drives.

IDXAlert 3.0's structural decision carries over unchanged: **the engine owns
the loop.** The tray is a face - it starts a Watcher, appends a channel, and
turns menu clicks into requests. Nothing in the UI decides when to fetch, and
"Check now" never polls from the click thread. Two threads polling at once
double-alert.

WHAT MAKES A RESTART SAFE
-------------------------
Not the seen-set. An age cutoff.

The seen-set stops an item alerting twice, but it cannot stop a COLD start from
alerting on everything a feed happens to be serving - 200+ items across four
sources on first run, and whatever accumulated while the laptop was asleep on
every run after. RSS feeds also reorder and republish, so "not in seen.json" is
not the same as "new".

So: an item is never alerted if its `published` is older than
`max_item_age_minutes`, whatever the seen-set says. Restarts are then safe by
construction rather than by bookkeeping. Items with no timestamp fall back to
the seen-set alone, and on a first run nothing alerts at all - the store is
seeded silently.

BURSTS ARE GROUPED, NOT CAPPED
------------------------------
A macro story touching twelve Financials names is one event, not twelve. Above
`burst_threshold` alerts in a single tick, items sharing a sector collapse into
one grouped alert. Nothing is dropped - grouping changes presentation, a cap
would change what you learn.
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import jcifilter
import jcimatch

WIB = timezone(timedelta(hours=7))

DEFAULTS = {
    "poll_seconds": 60,
    "max_item_age_minutes": 180,
    "seed_on_first_run": True,
    "burst_threshold": 5,
    "burst_group_min": 3,
    "cluster_window_minutes": 360,
    "cluster_threshold": 0.6,
    "min_confidence": 0.5,
    "seen_max": 5000,
    "cooldown_seconds": 300,
    "cooldown_max_seconds": 3600,
}


def now_utc():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------- storage

class Store:
    """seen ids + live clusters, persisted atomically.

    Clusters are persisted because they are not a cache: losing them on restart
    means every outlet that already reported a story reports it again.
    """

    def __init__(self, path=None, seen_max=DEFAULTS["seen_max"]):
        self.path, self.seen_max = path, seen_max
        self.seen = {}                  # id -> iso first-seen
        self.clusters = []              # serialised Cluster rows
        self.seeded = {}                # source name -> iso, when its backlog
                                        # was absorbed. PER SOURCE, not per
                                        # store: see Pipeline.process.
        self.first_run = True

    def load(self):
        if not self.path or not os.path.exists(self.path):
            return self
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return self                 # corrupt store behaves as a fresh one
        self.seen = d.get("seen") or {}
        self.clusters = d.get("clusters") or []
        self.seeded = d.get("seeded") or {}
        self.first_run = not self.seen
        return self

    def save(self):
        if not self.path:
            return
        if len(self.seen) > self.seen_max:
            keep = sorted(self.seen.items(), key=lambda kv: kv[1])[-self.seen_max:]
            self.seen = dict(keep)
        tmp = self.path + ".tmp"
        payload = {"saved": now_utc().isoformat(timespec="seconds"),
                   "seen": self.seen, "clusters": self.clusters,
                   "seeded": self.seeded}
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, self.path)      # atomic; a killed process cannot truncate

    def mark(self, item_id):
        self.seen.setdefault(item_id, now_utc().isoformat(timespec="seconds"))

    def has(self, item_id):
        return item_id in self.seen


# ------------------------------------------------------------------- results

class Alert:
    __slots__ = ("item", "ticker", "rule", "categories", "cluster_id",
                 "sector", "hits", "cluster_sources")

    def __init__(self, item, ticker, rule, categories, cluster_id, sector, hits,
                 cluster_sources=()):
        self.item, self.ticker, self.rule = item, ticker, rule
        self.categories, self.cluster_id = categories, cluster_id
        self.sector, self.hits = sector, hits
        # Every outlet on this cluster at the moment of alerting - which is
        # exactly one, since a cluster alerts on its first sighting. The field
        # exists so the popup row has one shape whether or not JCIAlert later
        # re-emits when a second outlet attaches (the row key is already
        # "c<cluster_id>", so such an update could find its row).
        self.cluster_sources = list(cluster_sources)

    def __repr__(self):
        return f"Alert({self.ticker}, {self.item.title[:40]!r})"


class Record:
    """ONE ROW PER HEADLINE THAT CAME OFF THE WIRE, alerted or not.

    An Alert is the small filtered subset that earns a popup. A Record is
    everything - including the ones a rule rejected, the ones that were too
    old, and the ones the first run swallowed. The filters decide what gets
    SHOWN; they do not decide what gets KEPT.

    That separation is the whole point. A headline that never alerted is the
    only evidence that a rule is too tight, and under the old design it was
    thrown away at the moment it was judged - so "why didn't I see the BBCA
    story" had no answer beyond a counter saying six things were dropped.
    """

    __slots__ = ("item", "hits", "ticker", "sector", "categories", "rule",
                 "status", "reason", "cluster_id")

    def __init__(self, item, hits=(), ticker="", sector="", categories=(),
                 rule="", status="dropped", reason="", cluster_id=0):
        self.item, self.hits = item, list(hits)
        self.ticker, self.sector = ticker or "", sector or ""
        self.categories = set(categories or ())
        self.rule, self.status, self.reason = rule or "", status, reason or ""
        self.cluster_id = cluster_id

    def __repr__(self):
        return f"Record({self.status}, {self.ticker or '-'}, " \
               f"{self.item.title[:40]!r})"


class GroupedAlert:
    __slots__ = ("sector", "alerts")

    def __init__(self, sector, alerts):
        self.sector, self.alerts = sector, alerts

    @property
    def tickers(self):
        return sorted({a.ticker for a in self.alerts if a.ticker})

    def __repr__(self):
        return f"GroupedAlert({self.sector}, {len(self.alerts)} items)"


class TickResult:
    __slots__ = ("alerts", "groups", "seen_new", "dropped", "per_source",
                 "records")

    def __init__(self):
        self.alerts, self.groups = [], []
        self.seen_new = 0
        self.dropped = {}
        self.per_source = {}
        self.records = []          # every new headline; see Record

    def bump(self, why):
        self.dropped[why] = self.dropped.get(why, 0) + 1


# ------------------------------------------------------------------ pipeline

class Pipeline:
    """items -> decisions. Pure, given a Store and a clock."""

    def __init__(self, table, filters, store=None, cfg=None, emiten=None):
        self.cfg = dict(DEFAULTS, **(cfg or {}))
        self.table = table
        self.filters = filters
        self.store = store or Store()
        self.emiten = emiten or {}
        self.log = None        # set by whoever owns a log; None stays quiet
        self.clusterer = jcimatch.Clusterer(
            table,
            window_minutes=self.cfg["cluster_window_minutes"],
            threshold=self.cfg["cluster_threshold"],
        )
        # Hydrate from disk. Without this a restart re-alerts every story the
        # other outlets are still carrying, which is exactly the 6h window the
        # clusterer exists to cover.
        self.clusterer.import_state(self.store.clusters)

    def reconfigure(self, cfg, filters=None):
        """Adopt a changed config WITHOUT losing the clusters.

        `self.cfg` was frozen at construction and the compiled rules with it,
        so every Options save between restarts was a no-op for anything that
        mattered - the watchlist, the rules, the confidence floor, the age
        cutoff. The clusterer is updated in place rather than rebuilt: a new
        one would forget every live story and re-alert the lot.
        """
        self.cfg = dict(DEFAULTS, **(cfg or {}))
        if filters is not None:
            self.filters = filters
        c = self.clusterer
        c.window = timedelta(minutes=self.cfg["cluster_window_minutes"])
        c.threshold = self.cfg["cluster_threshold"]

    def persist(self):
        self.store.clusters = self.clusterer.export_state()
        self.store.save()

    def sector_of(self, ticker):
        return (self.emiten.get(ticker) or {}).get("sector", "") if ticker else ""

    def _too_old(self, item, now):
        if not item.published:
            return False                # cannot judge; the seen-set decides
        age = (now - item.published).total_seconds() / 60.0
        return age > self.cfg["max_item_age_minutes"]

    def process(self, items, now=None, seed=None):
        """One tick's worth of items -> TickResult.

        `seed` forces seeding mode; by default it is on for a first run.
        """
        now = now or now_utc()
        res = TickResult()
        seeded_now = {}

        def seeding_for(item):
            """SEEDING IS PER SOURCE, and this is why.

            2026-09-10, first launch after a reboot: DNS was not up yet, so
            four of six feeds failed with getaddrinfo and delivered nothing.
            Ten headlines arrived from the two that worked, the store declared
            itself no longer a first run, and sixty seconds later the other
            four came back and dumped a hundred and seventy stale headlines
            into a live pipeline. Nothing alerted, but only because the age
            cutoff happened to catch 120 of them - fifty went all the way
            through the filters on their merits.

            A source that has never delivered has not been seeded, whatever
            the other five did. BanksAlert already learned this; JCIAlert did
            not inherit it.
            """
            if seed is not None:
                return seed
            if not self.cfg["seed_on_first_run"]:
                return False
            return item.source not in self.store.seeded

        def drop(item, why, **kw):
            """Bump the counter AND keep the headline. The counter answers
            'how many'; only the row answers 'which one, and would I have
            wanted it'."""
            res.bump(why)
            res.records.append(Record(item, reason=why, status="dropped", **kw))

        for item in items:
            if self.store.has(item.id):
                res.bump("already seen")     # recorded on its first sighting
                continue
            self.store.mark(item.id)
            res.seen_new += 1

            if seeding_for(item):
                drop(item, "seeded on first run")
                seeded_now[item.source] = seeded_now.get(item.source, 0) + 1
                continue
            if self._too_old(item, now):
                drop(item, "older than the age cutoff")
                continue

            hits = jcimatch.find_tickers(
                item.title, self.table,
                min_confidence=self.cfg["min_confidence"])

            decision = self.filters.evaluate(item.title, hits, item.source)
            first = hits[0].ticker if hits else ""
            if not decision.alert:
                drop(item, decision.reason or "no rule accepted", hits=hits,
                     ticker=decision.ticker or first,
                     sector=self.sector_of(decision.ticker or first),
                     categories=decision.categories)
                continue

            ticker = decision.ticker or (hits[0].ticker if hits else None)
            cl = self.clusterer.add(
                ticker or "_none", item.title, item.source, item.url,
                item.published or now)
            if not cl.is_new:
                drop(item, "duplicate of a story already alerted", hits=hits,
                     ticker=ticker, sector=self.sector_of(ticker),
                     categories=decision.categories, rule=decision.rule,
                     cluster_id=cl.cluster.id)
                continue

            res.alerts.append(Alert(item, ticker, decision.rule,
                                    decision.categories, cl.cluster.id,
                                    self.sector_of(ticker), hits,
                                    list(cl.cluster.sources)))
            res.records.append(Record(item, hits, ticker,
                                      self.sector_of(ticker),
                                      decision.categories, decision.rule,
                                      "alert", "", cl.cluster.id))

        # Mark a source as absorbed only once it has actually DELIVERED. A
        # source that failed this tick delivered nothing and stays unseeded,
        # so its backlog is swallowed on the tick it finally answers.
        stamp = now.isoformat(timespec="seconds")
        for src in {it.source for it in items}:
            self.store.seeded.setdefault(src, stamp)
        if self.store.seen:
            self.store.first_run = False
        if seeded_now and self.log:
            where = ", ".join(f"{n} from {s}"
                              for s, n in sorted(seeded_now.items()))
            self.log(f"first sight of {len(seeded_now)} source(s) - {where}: "
                     f"recorded, not alerted; anything published from now on "
                     f"will alert")
        self._group(res)
        return res

    def _group(self, res):
        if len(res.alerts) < self.cfg["burst_threshold"]:
            return
        by_sector = {}
        for a in res.alerts:
            by_sector.setdefault(a.sector or "", []).append(a)
        kept, groups = [], []
        for sector, alerts in by_sector.items():
            if sector and len(alerts) >= self.cfg["burst_group_min"]:
                groups.append(GroupedAlert(sector, alerts))
            else:
                kept.extend(alerts)
        if groups:
            res.alerts, res.groups = kept, groups


# ------------------------------------------------------------------ dispatch

class Dispatcher:
    """Fan out to channels. A channel that raises must never kill the loop."""

    def __init__(self, log=None):
        self.channels = []
        self.log = log or (lambda *a: None)

    def send(self, payload):
        for ch in list(self.channels):
            try:
                ch(payload)
            except Exception as exc:
                self.log(f"channel {getattr(ch, '__name__', ch)!r} failed: "
                         f"{type(exc).__name__}: {exc}")


# ------------------------------------------------------------------- watcher

class SourceState:
    __slots__ = ("name", "ok", "last_ok_at", "last_error", "items",
                 "newest", "stale", "consecutive_failures", "cooldown_until",
                 "reported", "since", "attempts")

    def __init__(self, name):
        self.name, self.ok, self.last_ok_at = name, True, None
        # NEVER TRIED IS NOT OK. `ok` starts True so a fresh source does not
        # paint the tray red before it has had a turn - but that made a source
        # nobody had fetched yet read as healthy in Status, which is how
        # iqplus-stock showed "ok, 0 items" while it had not answered in days.
        # Count the attempts and the two states stop being the same one.
        self.attempts = 0
        self.last_error, self.items, self.newest = "", 0, None
        self.stale, self.consecutive_failures, self.cooldown_until = False, 0, 0.0
        # What condition we have already told the user about, so a persistent
        # problem is ONE line and not one per tick. Gill's rule from IDXAlert:
        # the log must stay readable, so no line per routine check.
        self.reported = ""
        self.since = None


class Watcher:
    """Owns the loop. The tray drives it; it never calls into the tray."""

    def __init__(self, sources, pipeline, fetch, dispatcher=None,
                 cfg=None, log=None):
        self.sources = list(sources)
        self.pipeline = pipeline
        self.fetch = fetch                     # (source) -> bytes
        self.dispatcher = dispatcher or Dispatcher()
        self.cfg = dict(DEFAULTS, **(cfg or {}))
        self.log = log or (lambda *a: None)
        self.pipeline.log = self.log
        # Recorders get EVERY headline, dispatcher channels get the filtered
        # few. Keeping them separate is what lets the filters be a view.
        self.recorders = []
        self.state = {s.name: SourceState(s.name) for s in self.sources}
        self.paused = False
        self.last_error = ""
        self._netdown = None           # when the resolver went away, if it did
        self.last_poll_at = None
        self.our_lag = None
        self._interrupt = threading.Event()
        self._stop = False
        self.wake = False
        self.tick = 0
        self.alerts = 0
        self._last_beat = None

    # ---- API the UI drives
    def request_check(self):
        self.wake = True
        self._interrupt.set()

    def request_stop(self):
        self._stop = True
        self._interrupt.set()

    def reconfigure(self, cfg, filters=None):
        """One place that answers 'the user just pressed Save'. Rules, speed,
        and which sources are switched on - all of them were baked in at
        startup before this existed."""
        self.cfg = dict(DEFAULTS, **(cfg or {}))
        self.pipeline.reconfigure(cfg, filters)
        over = (cfg or {}).get("sources") or {}
        for src in self.sources:
            if src.name in over:
                src.enabled = bool(over[src.name].get("enabled", src.enabled))
        self._interrupt.set()          # apply a new poll interval now, not
                                       # after the old one finishes elapsing

    def set_paused(self, value):
        self.paused = bool(value)

    def live_state(self):
        """Only the sources that are switched ON.

        `self.state` is keyed by every REGISTERED source, because a source can
        be re-enabled and should not lose its history when it is. But a source
        the user has deliberately unticked must not colour the icon or appear
        in Status - iqplus-stock has been unreachable for days, and a red tray
        icon over a feed you have already turned off is a false alarm that
        trains you to ignore the real one.
        """
        on = {s.name for s in self.sources if s.enabled}
        return {n: st for n, st in self.state.items() if n in on}

    def status(self):
        """amber / red / blue / green, with IDXAlert's precedence."""
        if self.paused:
            return "amber"
        live = self.live_state()
        if any(not st.ok for st in live.values()):
            return "red"
        if any(st.stale for st in live.values()):
            return "blue"
        return "green"

    def stats(self):
        return {
            "paused": self.paused,
            "last_poll_at": self.last_poll_at,
            "our_lag": self.our_lag,
            "status": self.status(),
            "sources": {n: {"ok": s.ok, "items": s.items, "stale": s.stale,
                            "newest": s.newest.isoformat() if s.newest else None,
                            "error": s.last_error,
                            "attempts": s.attempts,
                            "failures": s.consecutive_failures,
                            # Seconds until the next attempt, not a wall clock:
                            # the window renders it as "waiting 47 min", which
                            # needs no agreement about whose clock is whose.
                            "waiting": max(0, int(s.cooldown_until - time.time()))
                            if s.cooldown_until else 0}
                        for n, s in self.live_state().items()},
            "disabled": sorted(s.name for s in self.sources if not s.enabled),
        }

    def _report_network(self, dns_failed, tried):
        """EVERY source that was tried failed to RESOLVE ITS HOST. That is one
        fact about the machine, not N facts about the feeds, and it deserves
        one line - reported per source it was six lines of alarm at every
        reboot, because Windows starts JCIAlert before it has a resolver.

        If only SOME failed, the resolver is fine and each of those really is
        its own problem, so they report individually. The whole point of the
        collapse is that it can only fire when the common cause is certain.
        """
        down = bool(dns_failed) and len(dns_failed) == len(tried) > 1
        if down:
            if not self._netdown:
                self._netdown = now_utc()
                self.log(f"no network yet - none of the {len(tried)} sources "
                         f"could resolve their host")
            for st in dns_failed:
                st.reported, st.since = "no-network", st.since or self._netdown
            return True
        for st in dns_failed:                  # a genuine per-source failure
            self._report(st, "failing", f"{st.last_error[:90]}")
        if self._netdown:
            mins = (now_utc() - self._netdown).total_seconds() / 60.0
            self.log(f"network back after {mins:.0f} min")
            self._netdown = None
            # Clear the group condition silently: four "recovered" lines for
            # one resolver coming back is the noise we just removed.
            for st in self.state.values():
                if st.reported == "no-network":
                    st.reported, st.since = "", None
        return False

    def _report(self, st, condition, detail=""):
        """Log a source's condition ONLY when it changes.

        The log this replaces had `cnbc-market: newest item is older than its
        threshold` on every tick for ninety minutes, and an IQPlus timeout
        every six. That is not a log, it is a denial of service on the reader -
        and it breaks the rule IDXAlert already follows: no line per routine
        check, state changes and an hourly heartbeat only.
        """
        if condition == st.reported:
            return
        if condition:
            st.since = now_utc()
            self.log(f"{st.name}: {detail or condition}")
        elif st.reported:
            mins = ((now_utc() - st.since).total_seconds() / 60.0
                    if st.since else 0)
            self.log(f"{st.name}: recovered after {mins:.0f} min")
            st.since = None
        st.reported = condition

    def heartbeat(self, now):
        """Hourly: one line saying everything, instead of nothing or a flood.

        It beats while PAUSED too. A silent log is ambiguous - stopped, wedged,
        or just quiet - and the difference is the whole question when someone
        says "no news is showing up"."""
        if self._last_beat and (now - self._last_beat).total_seconds() < 3600:
            return
        self._last_beat = now
        # Only sources that are actually switched on. Counting all ten while
        # six are enabled read as "everything is fine" over a dead feed.
        live = self.live_state()
        ok = [n for n, s in live.items() if s.ok and not s.stale]
        bad = [f"{n}({s.reported})" for n, s in live.items() if s.reported]
        self.log(("PAUSED - " if self.paused else "still running - ")
                 + f"{self.tick} checks, {self.alerts} alerts, "
                 f"{len(ok)}/{len(live)} sources ok"
                 + (f", problems: {', '.join(bad)}" if bad else ""))

    # ---- one cycle
    # Windows starts JCIAlert before it has a working resolver, so the first
    # tick after every reboot fails EVERY source with the same error. Reported
    # one per source that is six lines of alarm for one fact: the machine is
    # not on the network yet.
    DNS_MARKERS = ("gaierror", "getaddrinfo", "Name or service not known",
                   "Temporary failure in name resolution", "[Errno 11001]")

    @staticmethod
    def _is_dns(text):
        return any(m in (text or "") for m in Watcher.DNS_MARKERS)

    def poll_once(self, now=None):
        now = now or now_utc()
        self.tick += 1
        items = []
        tried, dns_failed = [], []
        for src in self.sources:
            if not src.enabled:
                continue
            st = self.state[src.name]
            if st.cooldown_until and time.time() < st.cooldown_until:
                continue
            if not src.due(self.tick):
                continue
            tried.append(src.name)
            st.attempts += 1
            try:
                body = self.fetch(src)
                # None means the server answered 304: nothing changed. That is
                # not an error and not an empty feed, so the source keeps its
                # health, its item count and its freshness state untouched and
                # simply contributes nothing this tick.
                if body is None:
                    st.ok, st.consecutive_failures = True, 0
                    st.last_error = ""
                    continue
                got = src.parse(body)
            except Exception as exc:
                st.ok = False
                st.consecutive_failures += 1
                st.last_error = f"{type(exc).__name__}: {exc}"
                # One source failing must never stall the others, and a source
                # that keeps failing must not be retried every tick.
                if self._is_dns(st.last_error):
                    dns_failed.append(st)      # reported as one line below
                else:
                    self._report(st, "failing", f"{st.last_error[:90]}")
                if st.consecutive_failures >= 3:
                    # Escalating backoff. A flat 300s meant IQPlus - dead for
                    # three days - was still being probed every six minutes,
                    # each attempt burning a 45s timeout on the tick. Doubles
                    # to cooldown_max_seconds, then holds.
                    base = float(self.cfg.get("cooldown_seconds", 300))
                    cap = float(self.cfg.get("cooldown_max_seconds", 3600))
                    n = st.consecutive_failures - 3
                    st.cooldown_until = time.time() + min(cap, base * (2 ** n))
                continue
            if st.reported == "no-network":
                # The group line announces the resolver coming back. Clearing
                # this silently is what stops six "recovered" lines for one
                # machine rejoining the network.
                st.reported, st.since = "", None
            st.ok, st.consecutive_failures, st.cooldown_until = True, 0, 0.0
            st.last_error, st.items, st.last_ok_at = "", len(got), now
            st.newest, _mins, st.stale = src.freshness(got, now)
            self._report(st, "stale" if st.stale else "",
                         "newest item is older than its threshold")
            for it in got:
                items.append((src, it))

        self._report_network(dns_failed, tried)

        res = self.pipeline.process([i for _s, i in items], now=now)
        for src, _it in items:
            res.per_source[src.name] = res.per_source.get(src.name, 0) + 1

        prev = self.last_poll_at
        self.last_poll_at = now
        # The accountable latency number: absent one poll ago, present now, so
        # we delayed it by at most this. Needs no trust in anyone's clock.
        self.our_lag = (now - prev).total_seconds() if prev else None
        self.pipeline.persist()

        # Record before dispatching. A channel that throws must not cost us
        # the evidence, and the dashboard should show a headline even if the
        # popup failed to draw it.
        for rec in self.recorders:
            try:
                rec(res.records)
            except Exception as exc:
                self.log(f"recorder failed: {type(exc).__name__}: {exc}")

        self.alerts += len(res.alerts) + sum(len(g.alerts) for g in res.groups)
        self.heartbeat(now)
        for group in res.groups:
            self.dispatcher.send(group)
        for alert in res.alerts:
            self.dispatcher.send(alert)
        return res

    def run(self):
        while not self._stop:
            if not self.paused:
                try:
                    self.poll_once()
                    self.last_error = ""
                except Exception as exc:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    self.log(f"poll failed: {self.last_error}")
            else:
                self.heartbeat(now_utc())
            self.wake = False
            self._interrupt.wait(self.cfg["poll_seconds"])
            self._interrupt.clear()
