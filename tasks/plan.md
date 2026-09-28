# Plan: v1.2.2 — Gold, Interest Rate, Unemployment Rate, GDP indicators

Spec: [SPEC-macro-tagging.md](../SPEC-macro-tagging.md) (v1.2 baseline) — this
extends it with four more indicators, same mechanism, no spec change needed
since the mechanism was already built generic (`MACRO_LABELS`/`MACRO_RULES`
driven, not a hardcoded five).

## Components and dependency order

```
[1] jcifilter.py data          (root - everything else already reads these
                                 generically, confirmed: jcioptions.py,
                                 jciwindow.py, jciview.py, jcidash.py,
                                 jcipopup.py all loop over MACRO_LABELS /
                                 MACRO_RULES, none hardcode "five")
        │
        ▼
[2] test fixture + count fixups (test_jcifilter.py generic loop extended;
    test_jciwindow.py's two hardcoded == 5 / == 6 assertions; wording-only
    "five" -> "nine" in test_jcioptions.py, test_jcifilter.py, jciwindow.py,
    jcifilter.py docstrings)
        │
        ▼
[3] version bump + docs (jci.py, installer/JCIAlert.iss, CHANGELOG.md,
    README.md)
        │
        ▼
[4] full-suite verification
```

## Risks

- **Keyword/headline collision.** New categories' terms were chosen to share
  no consecutive-word phrase with any existing macro category's terms, or
  with each other's — verified by hand against all nine keyword lists and
  all nine test headlines before writing code, so the existing "the OTHER
  indicators do not also fire on it" cross-check stays true without adding
  new false-positive guard tests (mirrors bi_rate/fed/inflation/rupiah,
  which needed none — only Oil's cooking-oil collision did).
- **Two non-generic hardcoded counts.** `test_jciwindow.py` asserts exact
  counts (`== 5`, `== 6`) rather than deriving them from `MACRO_LABELS` like
  every other check in that file does. Must bump both or the suite fails
  for a reason unrelated to correctness. Not fixing the test to be generic
  here — out of scope, it already works this way for the existing five.
