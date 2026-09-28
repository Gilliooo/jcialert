# Tasks: v1.2.2 — Gold, Interest Rate, Unemployment Rate, GDP indicators

Plan: [plan.md](plan.md)

- [x] Task 1: Add the four indicators to `jcifilter.py`
  - Acceptance: `DEFAULT_CATEGORIES` gains `macro_gold`, `macro_interest_rate`,
    `macro_unemployment`, `macro_gdp`. `MACRO_RULES` gains one rule per
    indicator (`enabled: False`, `require_ticker: False`, single category,
    name `"Macroeconomics: <Label>"`). `MACRO_LABELS` gains the four
    id->label entries. `ensure_macro_rules`/`ensure_macro_categories`
    docstrings say "nine", not "five".
  - Verify: `python test_jcifilter.py` (after Task 2's fixture extension).
  - Files: `jcifilter.py`

- [x] Task 2: Extend test fixtures and fix hardcoded counts
  - Acceptance: `test_jcifilter.py`'s macro headline loop covers all nine
    indicators (one real-style ticker-less headline each, no cross-category
    false fire). `test_jciwindow.py`'s "(all)" save assertions read `== 9`
    / `== 10` instead of `== 5` / `== 6`. Wording-only "five" -> "nine" in
    `test_jcioptions.py`, `test_jcifilter.py`, `jciwindow.py`.
  - Verify: `python test_jcifilter.py && python test_jcioptions.py &&
    python test_jciview.py && python test_jcidash.py && python
    test_jcitray.py && python test_jciwindow.py`
  - Files: `test_jcifilter.py`, `test_jciwindow.py`, `test_jcioptions.py`,
    `jciwindow.py`

- [x] Task 3: Version bump + docs
  - Acceptance: `jci.py` VERSION and `installer/JCIAlert.iss` AppVersion
    both `1.2.2`. `CHANGELOG.md` has a new `## 1.2.2` `### Added` entry
    naming the four indicators. `README.md` "What's new" mentions all nine.
  - Verify: grep for `1.2.2` in all four files.
  - Files: `jci.py`, `installer/JCIAlert.iss`, `CHANGELOG.md`, `README.md`

- [x] Task 4: Close out
  - Acceptance: `run_tests.bat` clean.
  - Verify: `run_tests.bat`
  - Files: none (verification only)
