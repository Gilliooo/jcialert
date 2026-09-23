#!/usr/bin/env python3
"""
jcioptions - the Options window's logic, with no Tk in it.

Same split IDXAlert 3.x uses and for the same reason: `read_values`,
`apply_values`, `validate` and `budget_summary` are pure functions over dicts,
so the thing a user actually cares about - "I typed tickers into Options, does
the popup now show only those?" - is testable without a display. The Tk window
is a thin shell over these.

THREE RULES THIS MODULE EXISTS TO ENFORCE
-----------------------------------------
1. **apply_values must preserve what it does not understand.** config.json is
   one click away in the tray menu and is full of `_comment_*` documentation.
   A round trip through the form must not strip a key it has no widget for -
   including keys a NEWER version wrote, if someone downgrades.

2. **Refuse to save a config that can never alert.** Every rule disabled, or
   no rules at all, is a silent kill: nothing errors, nothing logs, the popup
   simply never appears. `validate()` returns that as an error, and the window
   must not save through it.

3. **The Filters tab is a LIST, not a form.** Rules are ordered, individually
   enabled, and each shows `jcifilter.describe()`. The CRUD helpers below are
   pure and return new lists, so undo is trivial and the widget owns nothing.

⚠️ PACK-ORDER, for whoever writes the Tk shell: pack the Save/Apply/Cancel bar
against the bottom edge BEFORE the notebook expands, or the buttons go
off-screen. That bug cost real time in IDXAlert, and a rule list is TALLER than
any tab that app has.
"""

import copy

import jciengine
import jcifilter
import jcipopup

# Only these are surfaced as form fields. Anything else in config.json is
# carried through untouched - see rule 1 above.
SPEED_FIELDS = {
    "poll_seconds": (int, 15, 3600),
    "max_item_age_minutes": (int, 5, 10080),
    "cluster_window_minutes": (int, 10, 10080),
    "burst_threshold": (int, 2, 100),
    "burst_group_min": (int, 2, 100),
    "min_confidence": (float, 0.0, 1.0),
    "cluster_threshold": (float, 0.0, 1.0),
    "seen_max": (int, 100, 200000),
}

ALERT_FIELDS = {
    "max_visible": (int, 1, 20),
    "duration_seconds": (int, 0, 3600),
}

BOOL_FIELDS = ("seed_on_first_run", "start_with_windows")

TEXT_FIELDS = ("data_dir",)

# name -> allowed values. A closed set, so the window renders a dropdown and
# validate() can reject a hand-typed value instead of the popup quietly
# landing in the default corner and looking like the setting did nothing.
CHOICE_FIELDS = {"corner": jcipopup.CORNERS}


def default_config():
    cfg = dict(jciengine.DEFAULTS)
    cfg.update({
        "max_visible": 3,
        "duration_seconds": 0,          # only the X closes it. Not negotiable.
        "watchlist": [],
        "sources": {},
        "filters": copy.deepcopy(jcifilter.DEFAULT_FILTERS),
        "start_with_windows": True,
        "data_dir": "",
        "corner": jcipopup.CORNERS[0],
    })
    return cfg


# ------------------------------------------------------------ form <-> config

def read_values(cfg):
    """config -> a flat dict of form values. Lists become newline-joined text."""
    cfg = cfg or {}
    base = default_config()
    out = {}
    for name in list(SPEED_FIELDS) + list(ALERT_FIELDS):
        out[name] = cfg.get(name, base[name])
    for name in BOOL_FIELDS:
        out[name] = bool(cfg.get(name, base.get(name, False)))
    for name in TEXT_FIELDS:
        out[name] = str(cfg.get(name, base.get(name, "")) or "")
    for name in CHOICE_FIELDS:
        out[name] = jcipopup.normalise_corner(cfg.get(name)) or base[name]
    out["watchlist"] = "\n".join(cfg.get("watchlist") or [])
    out["global_exclude"] = "\n".join(
        (cfg.get("filters") or {}).get("global_exclude") or [])
    out["rules"] = copy.deepcopy((cfg.get("filters") or {}).get("rules")
                                 or base["filters"]["rules"])
    out["categories"] = copy.deepcopy((cfg.get("filters") or {}).get("categories")
                                      or base["filters"]["categories"])
    out["sources"] = copy.deepcopy(cfg.get("sources") or {})
    return out


def _lines(text):
    if isinstance(text, (list, tuple)):
        return [str(x).strip() for x in text if str(x).strip()]
    return [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]


def _coerce(value, spec):
    typ, lo, hi = spec
    v = typ(value)
    return max(lo, min(hi, v))


def apply_values(cfg, values):
    """form values -> a NEW config, preserving every key it does not own."""
    out = copy.deepcopy(cfg or {})
    for name, spec in list(SPEED_FIELDS.items()) + list(ALERT_FIELDS.items()):
        if name in values:
            try:
                out[name] = _coerce(values[name], spec)
            except (TypeError, ValueError):
                pass                    # validate() reports it; do not corrupt
    for name in BOOL_FIELDS:
        if name in values:
            out[name] = bool(values[name])
    for name in TEXT_FIELDS:
        if name in values:
            out[name] = str(values[name] or "")
    for name in CHOICE_FIELDS:
        if name in values:
            got = jcipopup.normalise_corner(values[name])
            if got:
                out[name] = got
    if "watchlist" in values:
        out["watchlist"] = [t.strip().upper() for t in _lines(values["watchlist"])]
    if "sources" in values:
        # MERGE, do not replace. The Sources tab is a column of checkboxes, so
        # the form can only ever say `enabled` - while config.json carries
        # `_f_tick` density measurements per source that _sources_note tells
        # you to decide enable/disable by. A wholesale replace ate all nine of
        # them on a single Save (2026-09-23). Rule 1 applies one level down too.
        # Consequence, and the right trade: a source that no longer exists
        # keeps its config entry. A stale key is harmless; a lost measurement
        # is not.
        srcs = copy.deepcopy(out.get("sources") or {})
        for name, got in (values["sources"] or {}).items():
            srcs[name] = {**(srcs.get(name) or {}), **copy.deepcopy(got)}
        out["sources"] = srcs

    filters = dict(out.get("filters") or {})
    if "global_exclude" in values:
        filters["global_exclude"] = _lines(values["global_exclude"])
    if "rules" in values:
        filters["rules"] = copy.deepcopy(values["rules"])
    if "categories" in values:
        filters["categories"] = copy.deepcopy(values["categories"])
    out["filters"] = filters
    return out


# ------------------------------------------------------------- the rule list

def new_rule(name="New rule"):
    return {"name": name, "enabled": True, "tickers": [], "categories": [],
            "require": [], "any_of": [], "exclude": [], "sources": [],
            "min_confidence": 0.5, "require_ticker": True}


def _unique_name(rules, wanted):
    names = {r.get("name") for r in rules}
    if wanted not in names:
        return wanted
    n = 2
    while f"{wanted} {n}" in names:
        n += 1
    return f"{wanted} {n}"


def add_rule(rules, rule=None, at=None):
    """Append or insert. Names are made unique so validate() cannot trip on a
    duplicate the user never chose - clicking Add twice is not an error."""
    rules = list(rules or [])
    r = copy.deepcopy(rule or new_rule())
    r["name"] = _unique_name(rules, r.get("name") or "New rule")
    rules.insert(len(rules) if at is None else max(0, min(at, len(rules))), r)
    return rules


def remove_rule(rules, index):
    rules = list(rules or [])
    if 0 <= index < len(rules):
        rules.pop(index)
    return rules


def move_rule(rules, index, delta):
    """Reorder. Order only decides which rule NAME is reported on an alert -
    rules are OR'd - so this is cosmetic and must never fail loudly."""
    rules = list(rules or [])
    j = index + delta
    if 0 <= index < len(rules) and 0 <= j < len(rules):
        rules[index], rules[j] = rules[j], rules[index]
    return rules


def set_enabled(rules, index, value):
    rules = copy.deepcopy(list(rules or []))
    if 0 <= index < len(rules):
        rules[index]["enabled"] = bool(value)
    return rules


def duplicate_rule(rules, index):
    if not (0 <= index < len(rules or [])):
        return list(rules or [])
    copy_of = copy.deepcopy(rules[index])
    copy_of["name"] = _unique_name(rules, copy_of.get("name", "Rule"))
    return add_rule(rules, copy_of, at=index + 1)


def describe(rule):
    return jcifilter.describe(rule)


def rule_lines(rules):
    """What the Filters list widget renders, one tuple per row."""
    return [(("on " if r.get("enabled", True) else "off"),
             r.get("name") or "(unnamed)", describe(r))
            for r in rules or []]


# --------------------------------------------------------------- the numbers

def budget_summary(cfg, sources=None):
    """Live readout for the Speed tab, computed from the SAME values that get
    saved - so the readout and the validator can never disagree."""
    cfg = cfg or {}
    poll = int(cfg.get("poll_seconds") or jciengine.DEFAULTS["poll_seconds"])
    poll = max(1, poll)
    enabled = [s for s in (sources or []) if getattr(s, "enabled", True)]
    n = len(enabled) or 1
    per_hour = int(3600 / poll) * n
    return {
        "sources": len(enabled),
        "requests_per_hour": per_hour,
        "requests_per_day": per_hour * 24,
        "worst_case_seconds": poll,
        "text": (f"{len(enabled)} sources · one round every {poll}s · "
                 f"{per_hour:,} requests/hour · worst-case detection {poll}s"),
    }


# --------------------------------------------------------------- validation

def validate(values, cfg=None):
    """Every reason the window must refuse to save. Human-readable strings."""
    errs = []
    for name, spec in list(SPEED_FIELDS.items()) + list(ALERT_FIELDS.items()):
        if name not in values:
            continue
        typ, lo, hi = spec
        try:
            v = typ(values[name])
        except (TypeError, ValueError):
            errs.append(f"{name} must be a number")
            continue
        if not lo <= v <= hi:
            errs.append(f"{name} must be between {lo} and {hi}")

    for name in CHOICE_FIELDS:
        if name in values and not jcipopup.normalise_corner(values[name]):
            errs.append(f"{name} must be one of: "
                        + ", ".join(CHOICE_FIELDS[name]))

    for t in _lines(values.get("watchlist", "")):
        if t.strip().lower() == "@watchlist":
            continue
        if not t.strip().isalpha() or len(t.strip()) != 4:
            errs.append(f"{t!r} is not a 4-letter ticker")

    if "burst_group_min" in values and "burst_threshold" in values:
        try:
            if int(values["burst_group_min"]) > int(values["burst_threshold"]):
                errs.append("burst_group_min is larger than burst_threshold, "
                            "so nothing could ever be grouped")
        except (TypeError, ValueError):
            pass

    # The silent kill: a config that looks fine and can never alert.
    errs.extend(jcifilter.validate({
        "categories": values.get("categories") or jcifilter.DEFAULT_CATEGORIES,
        "rules": values.get("rules") or [],
        "global_exclude": _lines(values.get("global_exclude", "")),
    }))

    srcs = values.get("sources") or {}
    if srcs and all(not v.get("enabled", True) for v in srcs.values()):
        errs.append("every source is disabled, so there is nothing to watch")
    return errs
