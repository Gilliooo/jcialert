#!/usr/bin/env python3
"""
test_aliases.py - the alias table, plus a static undefined-name check.

WHY THE STATIC CHECK EXISTS
---------------------------
build_aliases.py cannot be exercised end to end without a residential
connection to idx.co.id, so its network path is untestable from anywhere the
code gets edited. That gap let a real bug ship twice in one afternoon:

  1. a hand-rolled header set that Cloudflare refused (403), and then
  2. a patch that added the correct client at the top of the file but left the
     OLD one further down, where it shadowed the new one and raised
     NameError: name 'HEADERS' is not defined - at call time, on Gill's PC,
     after a round trip.

Neither is a network problem. Both are visible in the source. So this suite
walks the AST of every module here and asserts that every name a function
reads actually resolves to a local, an argument, a module global or a builtin,
and that no function is defined twice at module level. That is cheap, offline,
and catches the shadowing class of failure before it costs a round trip.

The alias checks below use REAL rows from the registry (sampled 2026-09-05),
because the derivation only has to cope with how IDX actually writes names.

Offline. Run:  python test_aliases.py
"""

import ast
import builtins
import json
import os
import sys

import build_aliases as B

HERE = os.path.dirname(os.path.abspath(__file__))
MODULES = ["build_aliases.py", "jcimatch.py", "jcifilter.py", "jcisource.py",
           "jciengine.py", "jcinet.py", "jciview.py", "jcioptions.py",
           "jcipopup.py", "jci.py", "jcitray.py", "jcistartup.py",
           "jciwindow.py", "jcidash.py", "jcidashwindow.py", "jcidoctor.py",
           "probe_sources.py", "test_jcimatch.py", "test_jcifilter.py",
           "test_jcisource.py", "test_jciengine.py", "test_jcinet.py",
           "test_jciview.py", "test_jcioptions.py", "test_jci.py",
           "test_jcitray.py", "test_jciwindow.py", "test_jcidash.py",
           "test_jcidashwindow.py", "test_jcidoctor.py", "test_installer.py"]

failures = []


def check(name, cond, detail=None):
    if cond:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}" + (f"   -> {detail!r}" if detail is not None else ""))


# --------------------------------------------------------- static name checks

SCOPED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _own_nodes(node):
    """Nodes belonging to THIS scope. Nested functions/lambdas/classes are
    yielded but not descended into - they are scanned separately, with this
    scope as their enclosing one. Getting that wrong is what made the first
    version of this check flag every closure and lambda argument."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, SCOPED):
            stack.extend(ast.iter_child_nodes(n))


def _binds(node):
    """Names this scope binds, ignoring nested scopes' internals."""
    out = set()
    a = getattr(node, "args", None)
    if isinstance(a, ast.arguments):
        for grp in (a.posonlyargs, a.args, a.kwonlyargs):
            out.update(x.arg for x in grp)
        if a.vararg:
            out.add(a.vararg.arg)
        if a.kwarg:
            out.add(a.kwarg.arg)
    for n in _own_nodes(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            out.update(n.names)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                out.add((al.asname or al.name).split(".")[0])
    return out


def _scan(node, enclosing, bad, where):
    visible = enclosing | _binds(node)
    for n in _own_nodes(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            if n.id not in visible:
                bad.append((where, n.id, n.lineno))
    for n in _own_nodes(node):
        if isinstance(n, SCOPED):
            label = getattr(n, "name", None) or f"<lambda @ {n.lineno}>"
            _scan(n, visible, bad, f"{where}.{label}" if where else label)


def undefined_names(path):
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    base = set(dir(builtins)) | {"__name__", "__file__", "__doc__", "__spec__"}
    bad = []
    _scan(tree, base, bad, "")
    return bad


def duplicate_defs(path):
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    names = [n.name for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    return sorted({n for n in names if names.count(n) > 1})


# ------------------------------------------------------------------- fixtures

REGISTRY = [
    ("AADI", "PT Adaro Andalan Indonesia Tbk"),
    ("ADHI", "PT Adhi Karya (Persero) Tbk."),
    ("ADRO", "Alamtri Resources Indonesia Tbk"),
    ("ANTM", "PT ANTAM (Persero) Tbk"),
    ("ARTO", "PT Bank Jago Tbk."),
    ("BBCA", "PT Bank Central Asia Tbk."),
    ("BBRI", "PT Bank Rakyat Indonesia (Persero) Tbk"),
    ("BMRI", "PT Bank Mandiri (Persero) Tbk"),
    ("BUKA", "PT Bukalapak.com Tbk"),
    ("CUAN", "PT Petrindo Jaya Kreasi Tbk"),
    ("ISAT", "PT Indosat Tbk"),
    ("KLBF", "Kalbe Farma Tbk"),
    ("KLGS", "KLGS"),
    ("PTPP", "PP (Persero) Tbk"),
    ("TLKM", "PT Telkom Indonesia (Persero) Tbk"),
    ("WTON", "Wijaya Karya Beton"),
    ("TESTENAK", "Saham Baru Testing 29 Aug"),
    ("TESTSBKD", "TESTSBKD"),
]
ROWS = [{"KodeEmiten": k, "NamaEmiten": n} for k, n in REGISTRY]


def main():
    print("\n== static: no undefined names ==")
    for m in MODULES:
        p = os.path.join(HERE, m)
        if not os.path.exists(p):
            continue
        bad = undefined_names(p)
        check(f"{m} reads no undefined name",
              not bad, [f"{f}(): {n} @ line {ln}" for f, n, ln in bad[:6]])

    print("\n== static: no invalid escape sequences ==")
    # `logs\ - hangs off...` in a plain docstring printed a SyntaxWarning on
    # EVERY run of every suite and every PyInstaller pass. Noise in a build log
    # is not harmless: it is where a real warning goes to hide. Python 3.12
    # made these warnings, and a future version makes them errors.
    import warnings as _warnings
    for m in MODULES:
        path = os.path.join(HERE, m)
        if not os.path.exists(path):
            continue
        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            try:
                compile(open(path, encoding="utf-8").read(), m, "exec")
            except SyntaxError as exc:
                check(f"{m} compiles", False, str(exc))
                continue
        bad = [str(x.message) for x in caught
               if issubclass(x.category, SyntaxWarning)]
        check(f"{m} has no escape-sequence warnings", not bad, bad[:2])

    print("\n== static: nothing defined twice at module level ==")
    for m in MODULES:
        p = os.path.join(HERE, m)
        if not os.path.exists(p):
            continue
        dups = duplicate_defs(p)
        check(f"{m} defines each function once", not dups, dups)

    print("\n== name normalisation ==")
    check("'PT Bank Mandiri (Persero) Tbk' -> 'bank mandiri'",
          B.normalize_name("PT Bank Mandiri (Persero) Tbk") == "bank mandiri",
          B.normalize_name("PT Bank Mandiri (Persero) Tbk"))
    check("a trailing 'Tbk.' with a full stop is stripped",
          B.normalize_name("PT Bank Jago Tbk.") == "bank jago",
          B.normalize_name("PT Bank Jago Tbk."))
    check("a name with no PT and no Tbk survives intact",
          B.normalize_name("Wijaya Karya Beton") == "wijaya karya beton",
          B.normalize_name("Wijaya Karya Beton"))

    print("\n== alias derivation ==")
    table, skipped, dropped, resolved = B.build(ROWS, {})
    al = {c: e["aliases"] for c, e in table.items()}

    check("registry test rows are dropped on the 4-letter rule",
          "TESTENAK" not in table and "TESTSBKD" not in table, sorted(table))
    check("a company whose name is just its ticker yields no alias",
          al.get("KLGS") == [], al.get("KLGS"))
    check("'PP (Persero) Tbk' is too short to derive - needs a manual override",
          al.get("PTPP") == [], al.get("PTPP"))
    check("a leading generic word is shortened past only when 2 tokens remain",
          "jago" in al["ARTO"] and "asia" not in al["BBCA"], (al["ARTO"], al["BBCA"]))
    check("a single-token name survives if it is long enough",
          al["ANTM"] == ["antam"], al["ANTM"])
    check("the press name is NOT derivable for ADRO (hence aliases_manual)",
          not any("adaro" in a for a in al["ADRO"]), al["ADRO"])

    print("\n== manual overlay ==")
    manual_path = os.path.join(HERE, "aliases_manual.json")
    raw = json.load(open(manual_path, encoding="utf-8"))
    manual = {k.upper(): v for k, v in raw.items() if not k.startswith("_")}
    check("aliases_manual.json is valid and non-trivial", len(manual) > 50, len(manual))
    check("keys starting with _ are treated as comments, not tickers",
          all(not k.startswith("_") for k in manual))

    table2, _s2, _d2, _r2 = B.build(ROWS, manual)
    check("the overlay supplies what derivation could not (ADRO -> adaro)",
          "adaro" in table2["ADRO"]["aliases"], table2["ADRO"]["aliases"])
    check("the overlay rescues PTPP", table2["PTPP"]["aliases"] != [],
          table2["PTPP"]["aliases"])
    check("the overlay does not clobber derived aliases",
          "bank mandiri" in table2["BMRI"]["aliases"], table2["BMRI"]["aliases"])

    print("\n== contested aliases ==")

    # Nobody owns it: two companies with the identical registered name.
    clash = [{"KodeEmiten": "AAAA", "NamaEmiten": "PT Contoh Sekali Tbk"},
             {"KodeEmiten": "BBBB", "NamaEmiten": "PT Contoh Sekali Tbk"}]
    t3, _s3, d3, r3 = B.build(clash, {})
    check("an alias nobody can claim is dropped from both",
          t3["AAAA"]["aliases"] == [] and t3["BBBB"]["aliases"] == [],
          (t3["AAAA"], t3["BBBB"]))
    check("and is reported so it can get a manual override",
          "contoh sekali" in d3 and not r3, (dict(d3), dict(r3)))

    # THE DUTI CASE, from the real registry. DPNS's name shortens to DUTI's
    # full name. Blanket-dropping left DUTI matchable only by its ticker.
    duti = [{"KodeEmiten": "DPNS", "NamaEmiten": "Duta Pertiwi Nusantara Tbk"},
            {"KodeEmiten": "DUTI", "NamaEmiten": "Duta Pertiwi Tbk"}]
    t4, _s4, d4, r4 = B.build(duti, {})
    check("a truncation never steals another company's full name",
          "duta pertiwi" in t4["DUTI"]["aliases"], t4["DUTI"]["aliases"])
    check("the company it was truncated from loses it",
          "duta pertiwi" not in t4["DPNS"]["aliases"], t4["DPNS"]["aliases"])
    check("the contest is reported as awarded, not dropped",
          r4.get("duta pertiwi", (None,))[0] == "DUTI" and not d4,
          (dict(r4), dict(d4)))
    check("DPNS keeps its own longer name",
          "duta pertiwi nusantara" in t4["DPNS"]["aliases"], t4["DPNS"]["aliases"])

    # A hand-written alias is asserted intent and wins over a truncation.
    t5, _s5, d5, r5 = B.build(
        [{"KodeEmiten": "AAAA", "NamaEmiten": "PT Contoh Sekali Tbk"},
         {"KodeEmiten": "BBBB", "NamaEmiten": "PT Contoh Sekali Tbk"}],
        {"BBBB": ["contoh sekali"]})
    check("a hand-written alias wins a contest nobody else can claim",
          r5.get("contoh sekali", (None,))[0] == "BBBB", dict(r5))

    print("\n== single-word alias guards ==")

    def al_of(code, name, man=None):
        t, _s, _d, _r = B.build([{"KodeEmiten": code, "NamaEmiten": name}], man or {})
        return t[code]["aliases"]

    check("'Bank Sinarmas' does not derive the group brand 'sinarmas'",
          "sinarmas" not in al_of("BSIM", "PT Bank Sinarmas Tbk"),
          al_of("BSIM", "PT Bank Sinarmas Tbk"))
    check("'Bank Mega' does not derive the ordinary word 'mega'",
          "mega" not in al_of("MEGA", "PT Bank Mega Tbk"),
          al_of("MEGA", "PT Bank Mega Tbk"))
    check("'Bank Jago' still derives the distinctive 'jago'",
          "jago" in al_of("ARTO", "PT Bank Jago Tbk."),
          al_of("ARTO", "PT Bank Jago Tbk."))
    check("'Nusantara Infrastructure' does not derive the sector noun",
          "infrastructure" not in al_of("META", "Nusantara Infrastructure Tbk"),
          al_of("META", "Nusantara Infrastructure Tbk"))
    check("'Mitra Pemuda' does not derive the ordinary noun 'pemuda'",
          "pemuda" not in al_of("MTRA", "PT Mitra Pemuda Tbk."),
          al_of("MTRA", "PT Mitra Pemuda Tbk."))
    check("'bukalapak.com' is two tokens to the matcher, not a one-word alias",
          not B._one_token("bukalapak.com") and B._one_token("indosat"))
    check("a hand-written alias still overrides the guard deliberately",
          "astra" in al_of("ASII", "Astra International Tbk", {"ASII": ["astra"]}),
          al_of("ASII", "Astra International Tbk", {"ASII": ["astra"]}))

    # The seed file may override the guard, but only on purpose. Each of these
    # is a judgement call, checked against the live registry 2026-09-05:
    #   astra    -> ASII   4 Astra companies (AALI ASGR ASII AUTO), but press
    #                      "saham Astra" almost always means ASII
    #   indofood -> INDF   2 (ICBP, INDF); bare "Indofood" means the parent
    #   timah    -> TINS   2 names contain it (NIKL "Pelat Timah Nusantara",
    #                      TINS), and it is also the metal. Kept because tin
    #                      stories are usually TINS-relevant - revisit if it
    #                      turns out to be noisy in practice.
    # "ciputra" was flagged here and then CLEARED: only CTRA carries the name
    # now, so it was removed from GROUP_NAMES rather than allowlisted.
    DELIBERATE = {"astra", "indofood", "timah"}
    asserted = {a.strip().lower() for v in manual.values() for a in v
                if " " not in a.strip()}
    unreviewed = sorted((asserted & (B.GROUP_NAMES | B._COMMON_WORDS)) - DELIBERATE)
    check("no UNREVIEWED group brand or common word is hand-asserted",
          not unreviewed, unreviewed)

    print("\n== IDX-IC taxonomy is carried through ==")
    rich = B.build([{"KodeEmiten": "ADRO", "NamaEmiten": "Alamtri Resources Indonesia Tbk",
                     "Sektor": "Energi", "SubSektor": "Minyak, Gas & Batu Bara",
                     "Industri": "Batu Bara", "PapanPencatatan": "Utama"}], {})[0]["ADRO"]
    check("sector, subsector, industry and board reach emiten.json",
          (rich["sector"] == "Energi" and rich["subsector"].startswith("Minyak")
           and rich["industry"] == "Batu Bara" and rich["board"] == "Utama"), rich)
    check("the fields exist even when the registry omits them",
          all(k in table["ADHI"] for k in ("sector", "subsector", "industry", "board")),
          sorted(table["ADHI"]))
    check("adding them did not disturb name or aliases",
          rich["name"].startswith("Alamtri") and isinstance(rich["aliases"], list))

    print("\n== the matcher can consume what this builds ==")
    import jcimatch as M
    t6, _s6, _d6, _r6 = B.build(ROWS, manual)
    tbl = M.Table(t6)
    hits = M.find_tickers("Bos Bank Mandiri Jelaskan Alasan Tebar Dividen", tbl)
    check("a table straight from build_aliases matches a real headline",
          [h.ticker for h in hits] == ["BMRI"], hits)

    print("\n%d checks failed" % len(failures) if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
