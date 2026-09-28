# Spec: Macroeconomics alert tagging

Status: IMPLEMENTED — all success criteria verified, `run_tests.bat` green.

## Objective

JCIAlert alerts on headlines that name a ticker. It has no way to alert on
macroeconomic news that names no company at all — a BI rate decision, a Fed
move, an oil price swing, an inflation print, a Rupiah move. That is a real
miss: the trigger for this feature is a specific headline of that kind the
app was never built to catch.

Add a dedicated **Macroeconomics** section to Options: one master "(all)"
checkbox plus one checkbox per indicator (BI Rate, The Fed, Oil, Inflation,
Rupiah, more later). Checking an indicator lets keyword-matched headlines for
that indicator alert — no ticker required — tagged with that indicator's
name so it is filterable in the dashboard the same way `ticker` is today,
and so it shows as its own label in the popup instead of a generic bucket.

**User:** the app's own operator (Gill), same as every other Options control.

**Success looks like:** enabling "Oil" and nothing else, an oil-price
headline with no ticker alerts, the popup shows it labeled "OIL" (not
lumped under the generic "PASAR" bucket with unrelated ticker-less alerts),
and the dashboard's existing `categories` column lets you filter to just
that indicator's history.

## Grounding: what already exists (read before building)

This is smaller than it looks because the filter engine, not the UI, does
the hard part, and it already does everything except toggle-by-indicator and
label-by-indicator:

- **[jcifilter.py](jcifilter.py)**: `categories` are named keyword bundles
  (`DEFAULT_CATEGORIES`, ~line 115); a `rule` with `require_ticker: False`
  and `categories: ["some_category"]` already alerts on keyword match alone,
  no ticker, no confidence gate (`evaluate()`, ~line 472). `Decision.categories`
  already carries which category matched. `global_exclude` and phrase-boundary
  term matching (`compile_term`, ~line 303) are the existing noise controls —
  reuse them, do not build a second matcher.
- **[jciengine.py](jciengine.py):130-340**: `decision.categories` already
  flows into `Alert.categories` and into the drop/alert log calls. This is
  not a new pipe; it already exists end to end.
- **[jcidash.py](jcidash.py):34,59**: `categories` is **already a NEWS_COLUMNS
  column**, already populated (`" ".join(sorted(rec.categories or ()))`).
  Dashboard storage needs no schema change. `view()` (~line 189) currently
  filters by `ticker`/`source`/`status`/`query` but has no dedicated category
  filter param — add one, or the "filterable like ticker" success criterion
  isn't actually met (typing "oil" into free-text `query` today only matches
  title/ticker, not `categories` — confirm which is wanted, see Open Questions).
- **[jciview.py](jciview.py):128-201**: popup rows already carry `"categories"`
  (~line 139) but grouping (`rows_for`, ~line 195) uses `key = p.ticker or
  "PASAR"` — every ticker-less alert, regardless of category, collapses into
  one "PASAR" bucket today. **Required fix, confirmed:** each matched macro
  indicator gets its own grouping key/label. Oil alerts group under "OIL",
  BI Rate alerts group under "BI RATE" — never merged with each other, and
  never merged into a shared "PASAR" row. "PASAR" remains the fallback only
  for a ticker-less alert that matches no macro indicator (any other
  keyword-only rule someone hand-adds later via the generic Filters tab).
- **[jcipopup.py](jcipopup.py):339**: renders `it.get("ticker") or "PASAR"` as
  the row's bold label — reads only `ticker`, ignores the `categories` field
  that's already sitting in the row dict.
- **[jcioptions.py](jcioptions.py) / [jciwindow.py](jciwindow.py)**: pure-logic
  / thin-Tk-shell split (`read_values`/`apply_values`/`validate` in
  jcioptions.py, widgets in jciwindow.py). Filters tab (jciwindow.py:220-351)
  already lists `rules` with per-rule enable and a generic editor — this is
  the existing "reuse or extend" seam. See Open Questions for the decision
  this spec is making about it.

**Architectural decision made here:** each indicator checkbox maps to one
pre-defined `DEFAULT_RULES` entry (`require_ticker: False`, `categories:
["macro_<name>"]`, `enabled: False`), reusing the exact mechanism the Filters
tab already edits. The new Macroeconomics section does **not** duplicate the
rule engine — it is a purpose-built, simplified checkbox view over five
specific rules' `enabled` flags, because the generic Filters-tab editor
requires understanding categories/require_ticker mechanics that a quick
on/off toggle shouldn't. The "(all)" checkbox is UI-only derived state
(checked iff all five sub-rules are enabled) with no new config key.

## Commands

```
Tests:  python test_jcifilter.py && python test_jcioptions.py &&
        python test_jciview.py && python test_jcidash.py &&
        python test_jcitray.py
Full suite: run_tests.bat
Build:  build.bat   (runs the full suite first, then PyInstaller)
```

## Project Structure (files this touches)

```
jcifilter.py     -> DEFAULT_CATEGORIES: 5 new "macro_*" keyword bundles.
                     DEFAULT_RULES: 5 new require_ticker=False rules, enabled=False.
jcioptions.py     -> pure helpers: which rules are "macro" rules, bulk
                     enable/disable them, "(all)" derived-state helper.
jciwindow.py      -> new "Macroeconomics" checkbox list in the Options
                     window, modeled on the existing Sources-tab pattern.
jciview.py        -> rows_for()/row_for_ticker(): grouping key falls back to
                     the matched macro indicator's label, not flat "PASAR",
                     when ticker is empty.
jcipopup.py       -> render(): label uses the resolved group key/label
                     (already computed by jciview), not raw "ticker" alone.
jcidash.py        -> view(): add a category filter param alongside
                     ticker/source/status/query.
test_jcifilter.py, test_jcioptions.py, test_jciview.py, test_jcidash.py,
test_jcitray.py  -> coverage for all of the above, same suites already own
                     this code today.
CHANGELOG.md      -> user-facing entry once implemented.
```

No new files. No new dependencies. No new config top-level keys — this rides
entirely on the existing `categories` and `rules` shape in config.json.

## Code Style

Match what's already here — this project has a strong, consistent voice.
One real example, from `jcifilter.py`'s own pattern for a new category:

```python
DEFAULT_CATEGORIES = {
    ...
    # Macro indicators: no ticker to anchor on, so precision leans entirely
    # on phrase-boundary keyword matching + global_exclude. Starter terms
    # only - needs tuning against real headlines, same as every other
    # category here was (see corporate_action's 2026-09-09 note).
    "macro_bi_rate": [
        "bi rate", "suku bunga acuan", "bi-rate", "rdg bi",
        "rapat dewan gubernur",
    ],
    "macro_fed": [
        "the fed", "federal reserve", "fomc", "powell", "suku bunga AS",
    ],
    "macro_oil": [
        "harga minyak", "minyak mentah", "brent", "wti", "opec",
    ],
    "macro_inflation": [
        "inflasi", "deflasi", "ihk", "indeks harga konsumen",
    ],
    "macro_rupiah": [
        "rupiah melemah", "rupiah menguat", "kurs rupiah", "nilai tukar",
    ],
}
```

Rules follow the existing `DEFAULT_RULES` shape exactly — no new fields:

```python
{
    "name": "Macroeconomics: BI Rate",
    "enabled": False,
    "tickers": [], "categories": ["macro_bi_rate"],
    "require": [], "any_of": [], "exclude": [],
    "sources": [],
    "min_confidence": 0.0,
    "require_ticker": False,
},
```

Comments explain *why*, not *what* (project convention throughout) — see
every existing docstring in `jcifilter.py` for the bar to match.

## Testing Strategy

Same suites, same style: pure functions tested directly, no mocking beyond
what's already used (e.g. `test_jcidoctor.py`'s ctypes stub pattern for
anything that would otherwise touch the OS).

- `test_jcifilter.py`: each new category's keyword list fires on at least
  one realistic headline and does not fire on an adjacent false-positive
  (mirrors the existing "real headline" style of this suite's fixtures).
  A `require_ticker: False` macro rule alerts with `ticker=None`.
- `test_jcioptions.py`: bulk-enable/disable helper flips exactly the five
  macro rules' `enabled` flags and nothing else; "(all)" derived state is
  correct at 0/some/all-enabled.
- `test_jciview.py`: two alerts with different macro categories and no
  ticker group into two separate popup rows, not one "PASAR" row.
- `test_jcidash.py`: new category filter param narrows `view()` output
  correctly; existing `categories` column behavior is unchanged.
- `test_jcitray.py`: existing "wired as the tray calls it" style smoke test
  extended to cover a ticker-less macro alert reaching the popup queue.

No new test file — extend the five that already own this code, per this
project's own convention (one suite per module, not one suite per feature).

## Boundaries

- **Always:** run `run_tests.bat` before any commit touching these files;
  keep `require_ticker: False` rules' semantics unchanged for existing
  keyword-only rules (e.g. "Never miss a suspension") — this must be
  additive, not a rewrite of `evaluate()`.
- **Ask first:** changing `RULE_FIELDS`/config schema in any way that isn't
  additive; changing default-enabled state of any *existing* rule; renaming
  the `categories` CSV column (external tooling/spreadsheets may depend on
  its current name).
- **Never:** ship any macro rule `enabled: True` by default (confirmed
  constraint — opt-in only); build a per-indicator watchlist/alias entity
  (out of scope, confirmed); add a UI for users to hand-author new indicators
  in this pass (out of scope, confirmed — "and others" is a future list
  addition by the developer, not a v1 editor).

## Success Criteria

- [x] Options has a "Macroeconomics" section: "(all)" + BI Rate, The Fed,
      Oil, Inflation, Rupiah, all unchecked by default.
- [x] Checking one indicator and nothing else: a matching, ticker-less
      headline alerts; an unrelated ticker-less headline does not.
- [x] "(all)" checked enables all five; unchecked disables all five; it does
      not gate a child that's independently checked (confirmed).
- [x] Popup shows each fired indicator under its own label (e.g. "OIL",
      "BI RATE") — two different indicators never merge into one row, and
      neither merges into a generic "PASAR" bucket. An Oil alert and a BI
      Rate alert firing together produce two separate popup rows.
- [x] Dashboard's `categories` column shows the indicator name; a new filter
      lets you narrow the view to just that indicator (mirrors `ticker`
      filtering).
- [x] `run_tests.bat` passes, including the new/extended assertions above.
- [x] `config.json` written by an old version still loads (no new required
      keys) — an upgrade must not need a config migration.

## Open Questions

1. **Category filter UI in the dashboard:** dedicated dropdown/field (like
   the existing `ticker=` filter box), or fold it into the existing
   free-text `query` so typing "oil" also searches `categories`? The spec
   assumes a dedicated param in `view()` either way; the *window* affordance
   (new widget vs. extending `query`'s reach) is undecided — smaller diff if
   `query` is extended, more discoverable if it's its own field.
2. **Keyword list accuracy:** the lists above are starting guesses, not
   tuned against real headlines the way every existing category was (each
   carries a "N real alerts" provenance note in `jcifilter.py`). Recommend
   capturing a day or two of real macro headlines via `probe_sources.py`
   before finalizing terms, same process used for `corporate_action`/`ma`/etc.
3. **"More later" indicators:** confirmed as future, developer-added, not
   user-editable in v1 — but should the five ship as one PR, or land
   incrementally (BI Rate first, prove the mechanism, then add the rest)?
   Lean-build argues for shipping the full set at once here, since it's one
   category dict + one rule list + one checkbox loop, not five separate
   subsystems — but flagging since it's a real scope choice.
4. **Rule ordering / interaction with existing rules:** these five new
   `DEFAULT_RULES` entries are OR'd with every other rule per existing
   engine semantics (any rule accepting alerts). Confirm no existing rule's
   `categories_exclude` needs to list the new `macro_*` names (none does
   today, since they didn't exist) — just noted so it's not missed during
   implementation.
