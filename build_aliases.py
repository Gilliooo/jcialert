#!/usr/bin/env python3
"""
build_aliases.py - build the ticker/company alias table JCIAlert matches against.

Pulls IDX's own listed-company registry and turns it into emiten.json:

    {"BMRI": {"name": "PT Bank Mandiri (Persero) Tbk",
              "aliases": ["bank mandiri", "mandiri"]}, ...}

Run on a machine that can reach idx.co.id (residential/office - datacenter
IPs are refused). Re-run occasionally; new listings appear continuously.

    python build_aliases.py                 # writes emiten.json
    python build_aliases.py --report        # also print what it could NOT resolve


ENDPOINT NOTES (measured 2026-09-05 from inside the IDX page)
------------------------------------------------------------
  /primary/ListedCompany/GetCompanyProfiles?start=0&length=N
    -> {"draw":0,"recordsTotal":964,"recordsFiltered":964,"data":[...]}
    Fields used: KodeEmiten, NamaEmiten. Also carries Sektor/SubSektor/
    PapanPencatatan if sector filtering is ever wanted.

  *** start IS A ROW OFFSET here. ***
  This is the OPPOSITE of GetAnnouncement, where indexFrom is a 0-based PAGE
  number - the bug that had two versions of IDXAlert reading page two. Two
  endpoints on the same host disagree, so the invariant is checked at runtime
  below (_assert_offset_semantics) rather than trusted. Verified: start=0&len=5
  concat start=5&len=5 is byte-identical to start=0&len=10, overlap zero.

  Registry junk to expect: two test rows with non-4-letter codes (TESTENAK,
  TESTSBKD) and at least one company whose NamaEmiten is just its own ticker
  (KLGS). Both are filtered.
"""

import argparse
import gzip
import http.client
import io
import json
import os
import re
import ssl
import sys
import time
import zlib
from datetime import datetime, timezone

HOST = "www.idx.co.id"
PATH = "/primary/ListedCompany/GetCompanyProfiles"
PAGE = "https://www.idx.co.id/id/perusahaan-tercatat/profil-perusahaan-tercatat/"

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "emiten.json")
MANUAL = os.path.join(HERE, "aliases_manual.json")

# THE HEADER SET IS NOT NEGOTIABLE (learned 2026-09-05, the hard way)
# ------------------------------------------------------------------
# The first version of this file hand-rolled a "clean" header set - User-Agent,
# Accept, Accept-Language, Accept-Encoding, Referer, Origin, Connection: close.
# It got a flat 403 from Gill's PC, from the same machine and the same IP where
# IDXAlert talks to GetAnnouncement all day long.
#
# What was missing: sec-ch-ua / sec-ch-ua-mobile / sec-ch-ua-platform,
# Sec-Fetch-Dest / Mode / Site, X-Requested-With, Cache-Control, Pragma - and
# keep-alive instead of close. idx3net.py says in a comment "Do not tidy this",
# and that comment is load-bearing.
#
# So this no longer maintains its own client. It imports idx3net and uses the
# clients that have a measured track record, which also buys the 403-failover
# for free: if one client is refused, the next is tried inside the same run.

_V3 = os.path.join(os.path.dirname(HERE), "v3-alpha")
if os.path.isdir(_V3) and _V3 not in sys.path:
    sys.path.insert(0, _V3)

try:
    import idx3net
except ImportError:                                   # pragma: no cover
    idx3net = None


# Fallback only, for a copy of this folder that has no v3-alpha beside it.
# Copied VERBATIM from idx3net.HEADERS - if you are editing this, you are
# probably making the same mistake described above.
FALLBACK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Referer": PAGE,
    "Origin": "https://www.idx.co.id",
    "sec-ch-ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "X-Requested-With": "XMLHttpRequest",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache, no-store, max-age=0",
    "Pragma": "no-cache",
}


def _fallback_get(path):
    ctx = ssl.create_default_context()
    ctx.set_alpn_protocols(["http/1.1"])   # omit this and Cloudflare 403s
    conn = http.client.HTTPSConnection(HOST, timeout=20, context=ctx)
    try:
        conn.request("GET", path, headers=FALLBACK_HEADERS)
        r = conn.getresponse()
        body = r.read()
        enc = (r.getheader("Content-Encoding") or "").lower()
        if "gzip" in enc:
            body = gzip.GzipFile(fileobj=io.BytesIO(body)).read()
        elif "deflate" in enc:
            body = zlib.decompress(body, -zlib.MAX_WBITS)
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        return body.decode("utf-8", "replace")
    finally:
        conn.close()


_CLIENTS = []
_TRIED = []


def _clients():
    """idx3net's clients in the order its own probe measured them fastest."""
    global _CLIENTS
    if _CLIENTS or idx3net is None:
        return _CLIENTS
    for cls in ("KeepAliveClient", "UrllibClient", "CurlCffiClient", "CurlClient"):
        c = getattr(idx3net, cls, None)
        if c is None:
            continue
        try:
            _CLIENTS.append(c())
        except Exception:
            pass
    return _CLIENTS


def _get(path):
    """Fetch one JSON path, falling through clients on refusal.

    Note the Referer that idx3net sends points at the disclosure page rather
    than the company-profile page. Both are same-origin on www.idx.co.id and
    it is accepted; do not "fix" it without measuring.
    """
    errors = []
    for client in _clients():
        try:
            body = client.get(path)[0]
            if client.name not in _TRIED:
                _TRIED.append(client.name)
            return json.loads(body)
        except Exception as exc:
            errors.append(f"{client.name}: {type(exc).__name__} {exc}")
    try:
        return json.loads(_fallback_get(path))
    except Exception as exc:
        errors.append(f"fallback: {type(exc).__name__} {exc}")
    raise RuntimeError("every client refused " + path + "\n   " + "\n   ".join(errors))


# ---------------------------------------------------------------- name -> alias

# Corporate scaffolding that carries no identifying information.
_STRIP_PATTERNS = [
    r"^\s*pt\.?\s+",                       # leading "PT "
    r"\s*\(persero(?:da)?\)\s*",           # (Persero), (Perseroda)
    r"\s*tbk\.?\s*$",                      # trailing Tbk / Tbk.
    r"\s*\(?tbk\)?\.?\s*$",
]

# Words too generic to identify a company on their own. A single-token alias
# from this set would match nearly every banking headline in the feed.
# Brand names shared by several listed companies. The two-token shortening
# below turns "Bank Sinarmas" into "sinarmas", "Bank Panin" into "panin" -
# manufacturing a GROUP name that in the press almost never means the specific
# company it was derived from. Awarding "sinarmas" to Bank Sinarmas (BSIM)
# would fire on every Sinar Mas story: SMMA, SMAR, INKP, TKIM, BSDE...
#
# This blocks DERIVATION only. A hand-written alias in aliases_manual.json
# still wins, which is the right layering: "astra" -> ASII is a deliberate
# call Gill can make, but the machine should not make it for him.
# Counted against the live registry 2026-09-05 (964 rows), not guessed:
#   astra    4 - AALI, ASGR, ASII, AUTO
#   panin    4 - PANS, PNBS, PNIN, PNLF
#   sinarmas 3 - BSIM, SMAR, SMMA   (registered as "Sinar Mas", one word in the press)
#   indofood 2 - ICBP, INDF
#   ciputra  1 - CTRA only. CTRS and CTRP are long gone, so this is NOT a
#                shared brand and is deliberately absent from the set below.
# Recount with the browser pane before adding or removing anything here.
GROUP_NAMES = {
    "sinarmas", "sinar mas", "lippo", "bakrie", "panin", "barito", "salim",
    "mayapada", "sampoerna", "djarum", "triputra", "mnc", "astra",
    "indofood", "sinar", "gudang garam", "agung sedayu", "alfa", "wings",
}

# Ordinary words that a single-token derived alias must never collapse to.
# "Bank Mega" -> "mega" would fire on "mega proyek"; "PT Timah" -> "timah" on
# any story about the metal. Multi-word aliases are safe - it is the one-word
# ones that match ordinary prose. Same idea as jcimatch.AMBIGUOUS_WORDS, but
# that set is about four-letter TICKER codes; this one is about alias strings.
_COMMON_WORDS = {
    "mega", "jaya", "raya", "mulia", "bumi", "sinar", "emas", "timah", "batu",
    "kayu", "anak", "baru", "tiga", "lima", "dana", "jasa", "kota", "masa",
    "pasar", "harga", "modal", "laba", "naik", "buka", "cuan", "raja", "roti",
    "mina", "ikan", "muda", "arah", "sari", "putra", "putri", "bintang",
    "star", "gold", "king", "hero", "good", "best", "real", "true", "city",
    "land", "life", "home", "food", "tech", "news", "plan", "port", "zone",
    "safe", "care", "luck", "mark", "nice", "pack", "idea", "halo", "indo",
    "hope", "help", "more", "rock", "live", "beer", "wifi", "bird", "taxi",
    "wood", "fast", "same", "rise", "nasa", "gems", "tour", "baby", "solusi",
    # Sector nouns. These arrive via the trailing-generic shortening:
    # "Nusantara Infrastructure Tbk" -> "infrastructure" (META), which would
    # fire on a large fraction of Indonesian business headlines. Found by
    # auditing the derived single-word list on the 2026-09-05 build.
    "infrastructure", "infrastruktur", "properti", "property", "teknologi",
    "technology", "digital", "industri", "industry", "industries", "media",
    "telekomunikasi", "komunikasi", "transportasi", "transport", "logistik",
    "logistics", "sekuritas", "asuransi", "farmasi", "kesehatan", "pangan",
    "niaga", "dagang", "perdagangan", "keuangan", "investasi", "pembangunan",
    "konstruksi", "pertambangan", "perkebunan", "manufaktur", "otomotif",
    # Ordinary Indonesian nouns that turned up as derived aliases:
    # "Mitra Pemuda Tbk" -> "pemuda" ("youth").
    "pemuda", "wanita", "karya", "usaha", "niat", "harapan", "berkah",
}

_TOO_GENERIC = {
    "bank", "indonesia", "asia", "jaya", "sukses", "makmur", "sejahtera",
    "mandiri", "utama", "abadi", "sentosa", "nusantara", "persada", "prima",
    "internasional", "international", "energy", "energi", "resources",
    "group", "grup", "tbk", "indo", "putra", "putri", "karya", "pacific",
    "global", "multi", "cipta", "agung", "artha", "graha", "mitra", "central",
}


def _one_token(alias):
    """Is this alias a single token AS THE MATCHER SEES IT?

    Not the same as "has no space": jcimatch tokenises on non-alphanumerics,
    so "bukalapak.com" is two tokens and is not the risky one-word kind. The
    audit report got this wrong at first and listed it as single-word.
    """
    return len(re.findall(r"[a-z0-9]+", alias)) == 1


def normalize_name(raw):
    """'PT Bank Mandiri (Persero) Tbk' -> 'bank mandiri'."""
    s = " " + (raw or "").strip() + " "
    s = s.replace(" ", " ")
    low = s.lower()
    for pat in _STRIP_PATTERNS:
        low = re.sub(pat, " ", low)
    low = re.sub(r"[^a-z0-9&.\- ]+", " ", low)
    low = re.sub(r"\s+", " ", low).strip()
    return low


def derive_aliases(code, raw_name):
    """Return candidate aliases (casefolded) for one company.

    Deliberately conservative. A wrong alias is worse than a missing one:
    a missing alias costs recall on one source, a wrong alias fires the popup
    on an unrelated company and teaches you to distrust the app.
    """
    base = normalize_name(raw_name)
    if not base:
        return []
    # NamaEmiten sometimes just repeats the ticker (e.g. KLGS). No information.
    if base.replace(" ", "").upper() == code.upper():
        return []

    out = [base]

    toks = base.split()
    # Drop a trailing geography word: "chandra asri pacific" -> "chandra asri".
    if len(toks) >= 3 and toks[-1] in _TOO_GENERIC:
        short = " ".join(toks[:-1])
        if short not in GROUP_NAMES:
            out.append(short)
    # "bank central asia" -> "central asia" is wrong, but "bank jago" -> "jago"
    # is right, so only shorten past a leading generic when 2 tokens remain -
    # and never down to a shared group brand.
    if (len(toks) == 2 and toks[0] in _TOO_GENERIC
            and toks[1] not in _TOO_GENERIC and toks[1] not in GROUP_NAMES
            and toks[1] not in _COMMON_WORDS):
        out.append(toks[1])

    seen, uniq = set(), []
    for a in out:
        a = a.strip()
        if len(a) < 4:
            continue
        if a in _TOO_GENERIC:
            continue
        # One-word aliases are the ones that match ordinary prose, so they get
        # the extra screening. Multi-word aliases are inherently specific.
        if " " not in a and (a in _COMMON_WORDS or a in GROUP_NAMES):
            continue
        if a not in seen:
            seen.add(a)
            uniq.append(a)
    return uniq


# ---------------------------------------------------------------------- fetch
# The client lives at the top of this file, on top of idx3net. There is
# deliberately no second one here: an earlier revision had a hand-rolled _get()
# in this spot that shadowed the real one and 403'd. See the header note above.

def _url(start, length):
    return f"{PATH}?start={start}&length={length}&_={int(time.time()*1000)}"


def _assert_offset_semantics():
    """Prove `start` is a row offset before trusting a single-shot pull.

    The same guard idx3.py --verify-feed applies to GetAnnouncement. Two
    endpoints on this host use different paging semantics; assume nothing.
    """
    a = [r.get("KodeEmiten") for r in _get(_url(0, 5)).get("data", [])]
    b = [r.get("KodeEmiten") for r in _get(_url(5, 5)).get("data", [])]
    c = [r.get("KodeEmiten") for r in _get(_url(0, 10)).get("data", [])]
    if len(a) != 5 or len(c) != 10:
        raise RuntimeError(f"unexpected page sizes: {len(a)}, {len(c)}")
    if set(a) & set(b):
        raise RuntimeError("pages 0 and 1 overlap - `start` is NOT a row offset")
    if a + b != c:
        raise RuntimeError("start=0..5 + start=5..5 != start=0..10 - paging changed")


def fetch_all(verify=True):
    if verify:
        _assert_offset_semantics()
    first = _get(_url(0, 1000))
    total = int(first.get("recordsTotal") or 0)
    rows = list(first.get("data") or [])
    while total and len(rows) < total:
        chunk = _get(_url(len(rows), 1000)).get("data") or []
        if not chunk:
            break
        rows.extend(chunk)
    return rows, total


# ---------------------------------------------------------------------- build

def build(rows, manual=None):
    """-> (table, skipped, dropped, resolved)

    COLLISION HANDLING (rewritten 2026-09-05 after the DUTI case)
    ------------------------------------------------------------
    The first version dropped a contested alias from every claimant. That is
    right when nobody owns it and badly wrong when somebody does:

        DPNS  "Duta Pertiwi Nusantara Tbk"  -> shortened to "duta pertiwi"
        DUTI  "Duta Pertiwi Tbk"            -> IS        "duta pertiwi"

    Blanket-dropping left DUTI with no alias at all, matchable only by its
    ticker - and it was a truncation of DPNS's name that cost it. So a contest
    is now awarded when exactly one claimant has a real claim:

      1. the alias is that company's FULL registered name (not a truncation)
      2. failing that, exactly one claimant declared it by hand in
         aliases_manual.json - a hand-written alias is asserted intent
      3. otherwise nobody gets it, and it is reported

    "sinarmas" is the case rule 3 is for: Bank Sinarmas (BSIM) and Sinarmas
    Multiartha (SMMA) both truncate to it, and in the press it usually means
    the group rather than either company. Nobody should get that one.
    """
    manual = manual or {}
    table, skipped = {}, []
    primary, hand = {}, {}

    for r in rows:
        code = (r.get("KodeEmiten") or "").strip().upper()
        name = (r.get("NamaEmiten") or "").strip()
        # Indonesian tickers are exactly four letters. Anything else is
        # registry junk (TESTENAK, TESTSBKD) - and the 4-letter rule is what
        # the matcher's regexes depend on, so enforce it here.
        if not re.fullmatch(r"[A-Z]{4}", code):
            skipped.append((code, name, "code is not 4 letters"))
            continue
        aliases = derive_aliases(code, name)
        primary[code] = normalize_name(name)
        hand[code] = set()
        for extra in manual.get(code, []):
            a = extra.strip().lower()
            if not a:
                continue
            hand[code].add(a)
            if a not in aliases:
                aliases.append(a)
        if not aliases:
            skipped.append((code, name, "no usable alias derived"))
        # IDX-IC taxonomy, carried straight through. Free here, and the basis
        # for grouping a burst of alerts by shared sector rather than capping
        # it. Measured 2026-09-05: 11 Sektor, 34 SubSektor, 60 Industri.
        # PapanPencatatan matters on its own - 155 companies sit on
        # "Pemantauan Khusus", which is a standing watch-list for free.
        table[code] = {
            "name": name,
            "aliases": aliases,
            "sector": (r.get("Sektor") or "").strip(),
            "subsector": (r.get("SubSektor") or "").strip(),
            "industry": (r.get("Industri") or "").strip(),
            "board": (r.get("PapanPencatatan") or "").strip(),
        }

    owners = {}
    for code, ent in table.items():
        for a in ent["aliases"]:
            owners.setdefault(a, []).append(code)

    dropped, resolved = {}, {}
    for a, codes in sorted(owners.items()):
        if len(codes) < 2:
            continue
        claim = [c for c in codes if primary.get(c) == a]
        if len(claim) != 1:
            claim = [c for c in codes if a in hand.get(c, ())]
        if len(claim) == 1:
            keeper = claim[0]
            resolved[a] = (keeper, [c for c in codes if c != keeper])
            losers = [c for c in codes if c != keeper]
        else:
            dropped[a] = codes
            losers = codes
        for code in losers:
            table[code]["aliases"] = [x for x in table[code]["aliases"] if x != a]

    return table, skipped, dropped, resolved


def main():
    ap = argparse.ArgumentParser(description="Build the JCIAlert emiten alias table")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--manual", default=MANUAL)
    ap.add_argument("--watchlist", default="",
                    help="comma-separated tickers; scopes --report's risk audit "
                         "to names that can actually fire an alert")
    ap.add_argument("--report", action="store_true",
                    help="print companies with no usable alias, and ambiguous aliases")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the paging-semantics check (do not use routinely)")
    args = ap.parse_args()

    manual = {}
    if os.path.exists(args.manual):
        with open(args.manual, "r", encoding="utf-8") as f:
            raw = json.load(f)
        manual = {k.strip().upper(): v for k, v in raw.items()
                  if not k.startswith("_")}
        print(f"manual overrides: {len(manual)} tickers")

    print("fetching IDX listed-company registry ...")
    if idx3net is None:
        print("  (idx3net not importable - using the built-in fallback client)")
    rows, total = fetch_all(verify=not args.no_verify)
    print(f"  {len(rows)} rows (recordsTotal={total})"
          + (f" via {'/'.join(_TRIED)}" if _TRIED else ""))

    table, skipped, dropped, resolved = build(rows, manual)
    n_alias = sum(len(e["aliases"]) for e in table.values())
    no_alias = [c for c, e in table.items() if not e["aliases"]]

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": f"https://{HOST}{PATH}",
        "count": len(table),
        "emiten": dict(sorted(table.items())),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=False)

    print(f"wrote {args.out}")
    print(f"  {len(table)} tickers · {n_alias} aliases · "
          f"{len(no_alias)} with none · {len(resolved)} contests awarded · "
          f"{len(dropped)} dropped")
    sec = {}
    for e in table.values():
        sec[e["sector"] or "(blank)"] = sec.get(e["sector"] or "(blank)", 0) + 1
    board = {}
    for e in table.values():
        board[e["board"] or "(blank)"] = board.get(e["board"] or "(blank)", 0) + 1
    print("  sectors: " + " · ".join(f"{k} {v}" for k, v in
                                     sorted(sec.items(), key=lambda x: -x[1])))
    print("  boards:  " + " · ".join(f"{k} {v}" for k, v in
                                     sorted(board.items(), key=lambda x: -x[1])))

    if args.report:
        print("\n-- no usable alias (matchable only by ticker or parenthetical) --")
        for c in sorted(no_alias):
            print(f"   {c}  {table[c]['name']}")
        print("\n-- contested aliases AWARDED (the winner owns it as a full name) --")
        for a, (keeper, losers) in sorted(resolved.items()):
            print(f"   {a!r} -> {keeper}, taken from {', '.join(sorted(losers))}")
        print("\n-- contested aliases DROPPED (nobody has a clear claim) --")
        for a, cs in sorted(dropped.items()):
            print(f"   {a!r} claimed by {', '.join(sorted(cs))}")
        wl = {t.strip().upper() for t in args.watchlist.split(",") if t.strip()}
        singles = sorted(
            (a, c) for c, e in table.items() for a in e["aliases"]
            if _one_token(a) and a not in {x.lower() for x in manual.get(c, [])}
            and (not wl or c in wl))
        scope = f" for your {len(wl)} watchlist tickers" if wl else ""
        print(f"\n-- derived SINGLE-WORD aliases ({len(singles)}){scope} --")
        print("   A one-word alias is the highest-risk kind: it fires on any")
        print("   headline containing that word.")
        if not wl:
            print("   NOTE: the watchlist is applied AFTER matching, so a loose alias")
            print("   on a company you do not cover can never reach the popup. Pass")
            print("   --watchlist BBCA,BBRI,... to audit only the ones that matter.")
        for a, c in singles:
            print(f"   {a:<24} {c}  ({table[c]['name']})")
        risky = sorted(
            (a, c) for c, e in table.items() for a in e["aliases"]
            if _one_token(a) and (a in _COMMON_WORDS or a in GROUP_NAMES))
        print(f"\n-- hand-asserted aliases that OVERRIDE the safety guard ({len(risky)}) --")
        print("   These are one-word aliases the derivation refuses to make on")
        print("   its own - an ordinary word, or a brand several companies share.")
        print("   Each is a deliberate call in aliases_manual.json. Re-read them:")
        for a, c in risky:
            why = "group brand" if a in GROUP_NAMES else "ordinary word"
            print(f"   {a:<24} {c}  ({why})")
        if skipped:
            print("\n-- skipped rows --")
            for c, n, why in skipped:
                print(f"   {c!r} {n!r}: {why}")

    print("\nReminder: IDX's registered name is often NOT what the press calls a")
    print("company (ADRO is 'Alamtri Resources', the papers say 'Adaro'). Anything")
    print("you cover should get a line in aliases_manual.json.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
