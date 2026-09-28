# Plan: Macroeconomics alert tagging

Spec: [SPEC-macro-tagging.md](../SPEC-macro-tagging.md) — approved.

## Components and dependency order

```
[1] jcifilter.py data          (root — everything else names these)
        │
   ┌────┴────┬──────────────┐
   ▼         ▼              │
[2] jciview.py         [3] jcidash.py     (parallel, both leaf-only
    (+jcipopup.py           view() filter  consumers of [1]'s names)
    if needed)
   │
   ▼
[4] jcioptions.py pure helpers  (needs [1]'s rule names to select by)
   │
   ▼
[5] jciwindow.py Macroeconomics UI  (needs [4]'s helpers)
   │
   ▼
[6] CHANGELOG.md + full-suite verification
```

[2] and [3] have no dependency on each other or on [4]/[5] — either order,
or both at once, is fine. [4] and [5] are sequential: the UI is a thin shell
over the pure helpers, per this project's own established split
(`jcioptions.py`'s own docstring).

## Risks

- **Keyword accuracy (flagged in spec, Open Question 2).** Starter terms are
  guesses. Risk is contained by: opt-in default-off (a bad category only
  fires for someone who turned it on), `global_exclude` still applies, and
  the existing "tune after real headlines" precedent this project already
  follows for every other category. Not a blocker to landing the mechanism.
- **`jcipopup.py` may need no change at all.** If [2]'s fix computes the
  right label into the same `"ticker"` key `row_for_ticker` already returns,
  `jcipopup.py`'s `render()` reads that key as-is and needs nothing touched.
  Task 2 includes verifying this before editing `jcipopup.py` — don't change
  a file the fix doesn't actually require.
- **Rule/category naming collision.** `macro_*` prefix on both category and
  rule names avoids colliding with existing `corporate_action`/`ma`/etc. —
  confirm no existing config in the wild already uses a `macro_*` name
  (unlikely; not user-facing today).

## Verification checkpoints

- After [1]: `python test_jcifilter.py` — new categories/rules validate
  (`jcifilter.validate()`), existing suite unaffected.
- After [2]+[3]: `python test_jciview.py && python test_jcidash.py`
- After [4]+[5]: `python test_jcioptions.py && python test_jcitray.py`,
  plus a manual Options-window open (Tk isn't unit-tested here — this
  project's own convention, see `jcioptions.py`'s pure/shell split rationale).
- Before done: `run_tests.bat` clean, then re-read Success Criteria in the
  spec line by line.

## Out of scope for this plan

Everything the spec already marked out of scope (per-indicator watchlist
entity, user-editable indicator list). Also not doing here: resolving Open
Question 1 (dashboard filter as dropdown vs. extending `query`) or Open
Question 3 (ship all five at once vs. incrementally) — those are picked at
task-breakdown time below, not re-litigated per component.

**Decisions for the two open questions, made here so tasks aren't blocked:**
- OQ1: extend `query` to also search `categories` — smaller diff, and the
  existing free-text box already does double duty (title + ticker) so this
  is consistent, not a new interaction pattern.
- OQ3: ship all five indicators in one pass — it's one dict, one list, one
  checkbox loop, not five subsystems (per the spec's own lean-build note).
