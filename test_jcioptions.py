#!/usr/bin/env python3
"""
test_jcioptions.py - the Options logic, and the chain it sits at the top of.

IDXAlert taught that unit tests can all pass while the feature is broken,
because what a user cares about spans four links:

    Options form -> config.json -> the engine reads it -> the wrong items drop

A break anywhere looks identical from outside. So the last section here drives
that whole chain: it puts values through apply_values, hands the result to a
real FilterSet and Pipeline, and asserts on what reaches the dispatcher.

The rest guards three things that are silent when wrong:
  · a round trip through the form stripping keys it has no widget for
  · a saved config that can never alert (every rule off)
  · a burst setting that can never group

Offline, no display. Run:  python test_jcioptions.py
"""

import copy

import jciengine as E
import jcifilter as F
import jcimatch as M
import jcioptions as O
import jcisource as S

from datetime import datetime, timezone

NOW = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)
failures = []


def check(name, cond, detail=None):
    if cond:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}" + (f"   -> {detail!r}" if detail is not None else ""))


def main():
    print("\n== round trip preserves what the form does not own ==")
    cfg = O.default_config()
    cfg["_comment_speed"] = "jangan diubah kecuali paham"
    cfg["_comment_filters"] = "daftar aturan"
    cfg["some_future_key"] = {"written_by": "a newer version"}
    out = O.apply_values(cfg, O.read_values(cfg))
    check("_comment_* documentation survives",
          out.get("_comment_speed") and out.get("_comment_filters"), sorted(out))
    check("an unknown key from a newer version survives",
          out.get("some_future_key") == {"written_by": "a newer version"},
          out.get("some_future_key"))
    check("the round trip is otherwise a no-op",
          O.read_values(out) == O.read_values(cfg))
    check("apply_values does not mutate the config it was given",
          "watchlist" in cfg and cfg["watchlist"] == [], cfg["watchlist"])

    print("\n== field coercion ==")
    v = O.read_values(cfg)
    v.update({"poll_seconds": "45", "max_visible": "5", "min_confidence": "0.7"})
    c2 = O.apply_values(cfg, v)
    check("numeric text is coerced to numbers",
          c2["poll_seconds"] == 45 and c2["max_visible"] == 5, c2["poll_seconds"])
    check("floats stay floats", abs(c2["min_confidence"] - 0.7) < 1e-9)
    v["poll_seconds"] = 99999
    check("out-of-range values are clamped, not written raw",
          O.apply_values(cfg, v)["poll_seconds"] == 3600,
          O.apply_values(cfg, v)["poll_seconds"])
    v["poll_seconds"] = "abc"
    check("unparseable text leaves the old value alone rather than corrupting it",
          O.apply_values(c2, v)["poll_seconds"] == 45,
          O.apply_values(c2, v)["poll_seconds"])
    v = O.read_values(cfg)
    v["watchlist"] = "bbca\n bbri \n\nBMRI\n"
    c3 = O.apply_values(cfg, v)
    check("the watchlist is split, trimmed, upper-cased and de-blanked",
          c3["watchlist"] == ["BBCA", "BBRI", "BMRI"], c3["watchlist"])

    print("\n== the rule list ==")
    rules = []
    rules = O.add_rule(rules)
    rules = O.add_rule(rules)
    check("adding twice does not create a duplicate name",
          [r["name"] for r in rules] == ["New rule", "New rule 2"],
          [r["name"] for r in rules])
    check("and so the config still validates",
          not any("duplicate" in e for e in
                  F.validate({"categories": F.DEFAULT_CATEGORIES, "rules": rules})),
          F.validate({"categories": F.DEFAULT_CATEGORIES, "rules": rules}))
    rules[0]["name"] = "coverage"
    rules[1]["name"] = "sector"
    check("move reorders", [r["name"] for r in O.move_rule(rules, 0, 1)]
          == ["sector", "coverage"])
    check("moving off either end is a no-op, never an error",
          O.move_rule(rules, 0, -1) == rules and O.move_rule(rules, 1, 1) == rules)
    check("remove removes", [r["name"] for r in O.remove_rule(rules, 0)] == ["sector"])
    check("removing a bad index is a no-op", O.remove_rule(rules, 9) == rules)
    off = O.set_enabled(rules, 0, False)
    check("disabling one rule leaves the other alone",
          off[0]["enabled"] is False and off[1]["enabled"] is True)
    check("set_enabled does not mutate the original list",
          rules[0]["enabled"] is True, rules[0]["enabled"])
    dup = O.duplicate_rule(rules, 0)
    check("duplicate lands next to its original with a fresh name",
          [r["name"] for r in dup] == ["coverage", "coverage 2", "sector"],
          [r["name"] for r in dup])
    lines = O.rule_lines(rules)
    check("the list widget gets state, name and a description per row",
          len(lines) == 2 and lines[0][0] == "on " and "any ticker" in lines[0][2],
          lines)

    print("\n== validate: the silent kills ==")
    good = O.read_values(O.default_config())
    check("the shipped defaults validate clean", O.validate(good) == [],
          O.validate(good))
    dead = copy.deepcopy(good)
    for r in dead["rules"]:
        r["enabled"] = False
    check("every rule disabled is refused",
          any("nothing can ever alert" in e for e in O.validate(dead)),
          O.validate(dead))
    empty = copy.deepcopy(good)
    empty["rules"] = []
    check("no rules at all is refused", O.validate(empty) != [])
    bad = copy.deepcopy(good)
    bad["burst_group_min"], bad["burst_threshold"] = 9, 4
    check("a burst setting that can never group is refused",
          any("burst_group_min" in e for e in O.validate(bad)), O.validate(bad))
    bad = copy.deepcopy(good)
    bad["watchlist"] = "BBCA\nBBCAX\nB1CA"
    errs = O.validate(bad)
    check("malformed tickers are named individually",
          sum("is not a 4-letter ticker" in e for e in errs) == 2, errs)
    bad = copy.deepcopy(good)
    bad["poll_seconds"] = 2
    check("a poll interval below the floor is refused",
          any("poll_seconds" in e for e in O.validate(bad)), O.validate(bad))
    # The Telegram and email settings were seven config keys, a whole Options
    # tab and two validation rules for a sender that was never written. They
    # went on 2026-09-23; a setting the app cannot act on is a promise the
    # window makes on its behalf.
    check("no unreachable notification settings are offered",
          not any("telegram" in f or "email" in f or "smtp" in f
                  for f in O.BOOL_FIELDS + O.TEXT_FIELDS),
          O.BOOL_FIELDS + O.TEXT_FIELDS)
    bad = copy.deepcopy(good)
    bad["sources"] = {"katadata": {"enabled": False}, "idxchannel": {"enabled": False}}
    check("turning every source off is refused",
          any("every source is disabled" in e for e in O.validate(bad)), O.validate(bad))
    bad = copy.deepcopy(good)
    bad["rules"] = [dict(O.new_rule(), categories=["tidak_ada"])]
    check("a rule naming a category that does not exist is refused",
          any("unknown category" in e for e in O.validate(bad)), O.validate(bad))

    print("\n== the Speed readout uses the values that get saved ==")
    srcs = [S.RssSource("a", "x"), S.RssSource("b", "y"),
            S.RssSource("c", "z", enabled=False)]
    b = O.budget_summary({"poll_seconds": 60}, srcs)
    check("disabled sources are not counted", b["sources"] == 2, b)
    check("requests per hour is per enabled source",
          b["requests_per_hour"] == 120, b)
    check("worst-case detection equals the poll interval",
          b["worst_case_seconds"] == 60, b)
    check("halving the interval doubles the requests",
          O.budget_summary({"poll_seconds": 30}, srcs)["requests_per_hour"] == 240)
    check("the readout text quotes the same numbers",
          "120" in b["text"] and "60s" in b["text"], b["text"])

    print("\n== THE CHAIN: form -> config -> engine -> dispatcher ==")
    emiten = {"BBCA": {"name": "PT Bank Central Asia Tbk.",
                       "aliases": ["bank central asia", "bca"], "sector": "Keuangan"},
              "BBRI": {"name": "PT Bank Rakyat Indonesia (Persero) Tbk",
                       "aliases": ["bri"], "sector": "Keuangan"}}
    table = M.Table(emiten)
    feed = [S.Item("katadata", "n1", "BBCA Tebar Dividen Interim Rp2 Triliun",
                   "http://x/1", NOW),
            S.Item("katadata", "n2", "BBRI Umumkan Buyback Saham",
                   "http://x/2", NOW)]

    def run(values):
        cfg = O.apply_values(O.default_config(), values)
        if O.validate(values):
            return None, O.validate(values)
        pipe = E.Pipeline(table, F.FilterSet(cfg["filters"]), E.Store(),
                          dict(cfg, seed_on_first_run=False), emiten)
        return pipe.process(feed, now=NOW), None

    v = O.read_values(O.default_config())
    v["rules"] = [dict(O.new_rule("coverage"), tickers=["BBCA"])]
    res, err = run(v)
    check("a ticker typed into the form reaches the engine",
          err is None and [a.ticker for a in res.alerts] == ["BBCA"],
          err or [a.ticker for a in res.alerts])
    v["rules"] = [dict(O.new_rule("coverage"), tickers=["BBCA", "BBRI"])]
    res, _ = run(v)
    check("adding the second ticker lets it through too",
          sorted(a.ticker for a in res.alerts) == ["BBCA", "BBRI"],
          [a.ticker for a in res.alerts])
    v["rules"] = [dict(O.new_rule("coverage"), tickers=["BBCA", "BBRI"],
                       exclude=["buyback"])]
    res, _ = run(v)
    check("an exclude term typed into the form drops the right one",
          [a.ticker for a in res.alerts] == ["BBCA"],
          [a.ticker for a in res.alerts])
    v["rules"] = [dict(O.new_rule("coverage"), tickers=[])]
    res, _ = run(v)
    check("clearing the ticker box restores the whole feed",
          len(res.alerts) == 2, [a.ticker for a in res.alerts])
    v["global_exclude"] = "tebar dividen"
    res, _ = run(v)
    check("the global exclude box beats the rules, as documented",
          [a.ticker for a in res.alerts] == ["BBRI"],
          [a.ticker for a in res.alerts])
    v["global_exclude"] = ""
    v["rules"] = [dict(O.new_rule("off"), enabled=False)]
    res, err = run(v)
    check("a config that can never alert never reaches the engine at all",
          res is None and err, err)

    print("\n== the popup corner is a closed set, not free text ==")
    import jcipopup as P
    base = O.default_config()
    check("it defaults to bottom right", base["corner"] == "bottom right",
          base["corner"])
    check("read_values passes it through", O.read_values(base)["corner"]
          == "bottom right")
    v = O.read_values(base)
    check("a valid corner is saved",
          O.apply_values(base, dict(v, corner="top left"))["corner"]
          == "top left")
    for spelling in ("TOP-Left", "top_left", "  Top   Left "):
        check(f"{spelling!r} is the same corner - a config file is hand-edited",
              O.apply_values(base, dict(v, corner=spelling))["corner"]
              == "top left")
    kept = O.apply_values(dict(base, corner="top right"),
                          dict(v, corner="sideways"))
    check("junk never overwrites a good value", kept["corner"] == "top right",
          kept["corner"])
    check("and validate() says so rather than silently ignoring it",
          any("corner must be one of" in e
              for e in O.validate(dict(v, corner="sideways"))),
          O.validate(dict(v, corner="sideways")))
    check("a config with no corner at all reads as the default",
          O.read_values({})["corner"] == "bottom right")

    print("\n== and the geometry it produces ==")
    check("bottom right sits in from both far edges",
          P.corner_geometry("bottom right", 430, 300, 1920, 1080)
          == (1920 - 430 - 24, 1080 - 300 - 72))
    check("top left sits in from both near edges",
          P.corner_geometry("top left", 430, 300, 1920, 1080) == (24, 72))
    check("bottom left", P.corner_geometry("bottom left", 430, 300, 1920, 1080)
          == (24, 1080 - 300 - 72))
    check("top right", P.corner_geometry("top right", 430, 300, 1920, 1080)
          == (1920 - 430 - 24, 72))
    check("an unknown corner falls back rather than throwing",
          P.corner_geometry("", 430, 300, 1920, 1080)
          == P.corner_geometry("bottom right", 430, 300, 1920, 1080))
    check("a popup taller than the screen is never placed off the top",
          P.corner_geometry("bottom right", 430, 4000, 1920, 1080)[1] == 0)
    check("every corner is offered to the window",
          list(O.CHOICE_FIELDS["corner"]) == list(P.CORNERS), P.CORNERS)

    print("\n%d checks failed" % len(failures) if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
