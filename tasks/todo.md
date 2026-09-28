# Tasks: Macroeconomics alert tagging

Spec: [SPEC-macro-tagging.md](../SPEC-macro-tagging.md) · Plan: [plan.md](plan.md)

- [x] Task 1: Add macro categories and rules to `jcifilter.py`
  - Acceptance: `DEFAULT_CATEGORIES` gains `macro_bi_rate`, `macro_fed`,
    `macro_oil`, `macro_inflation`, `macro_rupiah` keyword bundles.
    `DEFAULT_RULES` gains one rule per indicator: `enabled: False`,
    `require_ticker: False`, `categories: ["macro_<name>"]`. All pass
    `jcifilter.validate()`.
  - Verify: `python test_jcifilter.py` (extended with a realistic-headline
    hit per new category and one adjacent non-match); `python test_jci.py`.
  - Files: `jcifilter.py`, `test_jcifilter.py`

- [x] Task 2: Popup groups ticker-less alerts by matched indicator, not "PASAR"
  (jcipopup.py needed no change, confirmed - it already reads whatever key
  row_for_ticker returns)
  - Acceptance: `jciview.rows_for`/`row_for_ticker` grouping key resolves to
    the matched macro category's display label (e.g. "OIL") when ticker is
    empty; falls back to "PASAR" only when no macro category matched either.
    Two different indicators firing together produce two popup rows, never
    merged. Check whether `jcipopup.py` needs any change at all before
    editing it (see plan's risk note) — likely none, since it already reads
    whatever key `row_for_ticker` returns.
  - Verify: `python test_jciview.py` (two categories -> two rows; unknown
    ticker-less category -> "PASAR"); `python test_jcitray.py`.
  - Files: `jciview.py`, `test_jciview.py`, possibly `jcipopup.py` (verify
    first), possibly `test_jcitray.py`

- [x] Task 3: Dashboard can filter by indicator/category
  - Acceptance: `jcidash.view()` search (`query`) also matches against a
    row's `categories` field, not just title/ticker (per plan's OQ1
    decision). Existing ticker/source/status filtering unchanged.
  - Verify: `python test_jcidash.py` (query "oil" finds a macro_oil row by
    category even when title doesn't contain "oil" literally, or documents
    why not if the term IS the headline word — pick a fixture that proves
    the categories field specifically is being searched).
  - Files: `jcidash.py`, `test_jcidash.py`

- [x] Task 4: Pure helpers for the Macroeconomics checkbox group
  (also found and fixed: rule names "Macroeconomics - X" collided with
  jciwindow's own "{name} - {desc}" listbox format; renamed to
  "Macroeconomics: X". Also found: read_values() needed to seed the five
  macro rules for BOTH fresh and pre-existing config.json, since jci.py's
  load_config never merges defaults into an existing file - see
  jcifilter.ensure_macro_rules)
  - Acceptance: a way to identify which `rules` entries are the five macro
    ones (by name or a stable marker), bulk-enable/bulk-disable them, and
    compute "(all)" checkbox state (checked / unchecked / mixed) from their
    current `enabled` flags. Pure functions, no Tk, following
    `jcioptions.py`'s existing `read_values`/`apply_values` split.
  - Verify: `python test_jcioptions.py` (0/some/all enabled -> correct "(all)"
    state; bulk-toggle flips exactly the five macro rules and nothing else).
  - Files: `jcioptions.py`, `test_jcioptions.py`

- [x] Task 5: "Macroeconomics" section in the Options window
  - Acceptance: new checkbox list in Options — "(all)" + BI Rate, The Fed,
    Oil, Inflation, Rupiah — wired to Task 4's helpers. Matches the visual
    pattern of the existing Sources tab (flat checkboxes). Save persists
    through `apply_values` per existing round-trip rules (rule 1 in
    `jcioptions.py`'s docstring: preserve what it doesn't understand).
  - Verify: manual open of the Options window (Tk, not unit-tested per this
    project's convention) + `python test_jcitray.py` if it exercises Options
    wiring; `python test_jciwindow.py` if applicable field-level checks exist.
  - Files: `jciwindow.py`, `test_jciwindow.py` (if applicable)

- [x] Task 6: Close out
  - Acceptance: every Success Criteria checkbox in the spec is true. CHANGELOG
    has a user-facing entry (Added, not Fixed — this is new capability).
  - Verify: `run_tests.bat` clean; re-read spec Success Criteria line by line.
  - Files: `CHANGELOG.md`
