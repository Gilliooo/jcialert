#!/usr/bin/env python3
"""
jcifilter - decide whether a matched headline is worth interrupting Gill for.

`jcimatch` answers "which tickers is this about". This answers "do I care",
and it is the part the Options window drives.

THE MODEL: A LIST OF RULES, OR'd TOGETHER
-----------------------------------------
The obvious design is one global filter - a ticker box, a category box, a
keyword box, all ANDed. It breaks the first day you use it, because an analyst
does not want the same sensitivity for every name:

    "anything at all about BBRI"                     - a name you own
    "only buybacks and M&A across your whole sector" - names you watch
    "any suspension, any ticker"                     - a thing you never miss

Those cannot be expressed as one AND. So a filter is a LIST of rules and a
headline alerts if ANY rule accepts it. One rule reproduces the simple case
exactly, so nothing is lost.

WITHIN one rule, every populated field must pass (AND):

    tickers      hit.ticker must be in this set          [] = any ticker
    categories   the headline must match one of these    [] = any category
    categories_exclude  drop if it matches ANY of these   [] = nothing excluded
    require      EVERY term must appear                  [] = no requirement
    any_of       AT LEAST ONE term must appear           [] = no requirement
    exclude      NO term may appear                      [] = nothing excluded
    sources      the item must come from one of these    [] = any source
    min_confidence   the ticker hit's confidence floor
    require_event    drop bare mentions - see below           False = off
    max_tickers      drop headlines naming more than this      0 = no limit

REQUIRE_EVENT: THE ROUNDUP PROBLEM
----------------------------------
The first live weekday run produced three alerts and all three were the same
false positive:

    "IHSG Berpotensi Turun ... Saham JPFA hingga PTRO Jadi Rekomendasi"
    "Asing Ramai Borong Saham Big Banks BBRI, BBCA dan BMRI Sepekan Terakhir"
    "Daftar Saham PER Terendah & Tertinggi LQ45 ..., JPFA dan CUAN Disorot"

Every one is a LIST - a recommendation, a flow roundup, a screening table. The
tickers appear because the article enumerates them, not because anything
happened to those companies. And all three shared a fingerprint:

    attributed ONLY by a bare token (0.60), and matching NO category.

That is the signature of a mention rather than an event. Real corporate news
almost always carries an event word (dividen, buyback, RUPS, kontrak, laba)
or names the company properly - "Bank Mandiri (BMRI)", or IQPlus's slug.

So `require_event` accepts an item only if EITHER it matched a category, OR
the ticker was attributed by something stronger than a bare token.

It is off by default because it has a real recall cost: "GOTO PHK 500
Karyawan" is genuine news with a bare ticker and no category keyword, and this
would drop it. The remedy for that is to ADD the missing words to a category,
which is a better place for the knowledge than a filter exception.

**An empty list means "no constraint", never "match nothing".** That is the
one semantic everybody gets wrong when hand-editing the config, so it is
stated on every field in the example file and pinned by its own tests.

CATEGORIES are just named keyword bundles, editable in Options. They exist so
the common case ("corporate actions on my coverage") is two clicks instead of
twenty keywords, not because they are a different mechanism.

WHY SO MANY TERMS END IN `*`
---------------------------
Indonesian suffixes defeat exact matching constantly, and the misses are
invisible: "Simak Prospeknya" does not match "prospek", "kepemilikannya" does
not match "kepemilikan". Measured over 162 real alerts, converting the
single-word event terms to prefix form closed a chunk of the remaining gap on
its own. Prefer `prospek*` to `prospek` for any term a headline might inflect.

TERM SYNTAX
-----------
Terms are matched on token boundaries, never as raw substrings - "laba" must
not fire on "labatannya". Indonesian affixation makes plain equality too
strict, though ("akuisisi" / "mengakuisisi" / "diakuisisi"), so:

    dividen        exact token
    akuisisi*      prefix   - akuisisi, akuisisinya
    *akuisisi      suffix   - mengakuisisi
    *akuisisi*     anywhere in a token
    tebar dividen  a phrase: consecutive tokens, wildcards allowed per token
    borong * saham a GAP: up to 3 arbitrary tokens between the two words

THE GAP OPERATOR, and why it had to exist
-----------------------------------------
Indonesian headlines insert quantities into the middle of the phrase that
names the event:

    "Borong 635 Juta Saham BUKA"      vs  borong saham
    "Tawar 62% Saham Bayan"           vs  tawar saham

Strictly consecutive matching misses every one of these, and they are not
edge cases - they are how ownership stories are written. A bare `*` BETWEEN
two words now matches up to three arbitrary tokens. A `*` on its own is still
deliberately inert, so a stray asterisk cannot match the whole feed.

Standard library only.
"""

import re

from jcimatch import tokens

# --------------------------------------------------------------- categories

# Editable in Options. These are starting points from real IDX/IQPlus wording,
# not a taxonomy anyone has to keep.
DEFAULT_CATEGORIES = {
    # Expanded 2026-09-09 from 40 real uncategorised alerts. The bundles
    # shipped on Friday were guessed; these are the words Indonesian financial
    # media actually used over one trading day.
    "corporate_action": [
        "buyback", "buy back", "beli kembali", "dividen", "tebar dividen",
        "stock split", "reverse stock", "right issue", "rights issue", "hmetd",
        "saham bonus", "tender offer", "obligasi", "sukuk", "kupon",
    ],
    # 10 of the 40 - the single biggest gap. Insider and major-holder dealing
    # is exactly what an analyst wants and it matched nothing at all.
    "ownership": [
        "kepemilikan saham", "kurangi kepemilikan", "tambah kepemilikan",
        "borong saham", "borong * saham", "lepas * saham", "jual * saham",
        "akumulasi saham", "pemegang saham pengendali", "divestasi saham",
        "kepemilikan*", "caplok saham", "saham treasuri", "free float",
        "lepas * saham", "jual * saham", "pegang * saham", "borong * juta",
    ],
    "ma": [
        "akuisisi*", "*akuisisi*", "merger", "caplok", "ambil alih",
        "divestasi", "pengendali baru", "pemegang saham pengendali",
        "*akuisisi", "joint venture", "usaha patungan", "tawar * saham",
        "dikabarkan tawar", "incar * saham",
    ],
    # "INDY DIRIKAN ANAK USAHA BARU", "SMRA KURANGI MODAL KE ANAK USAHANYA"
    "structure": [
        "anak usaha", "anak usahanya", "entitas anak", "dirikan", "spin off",
        "spin-off", "restrukturisasi", "kurangi modal", "tambah modal",
        "konsolidasi", "induk usaha", "holding", "transaksi afiliasi",
        "unit bisnis", "pencatatan saham", "kub",
    ],
    # "FITCH TETAPKAN PERINGKAT AAA UNTUK INDOSAT", "RAIH KREDIT DARI OCBC"
    "rating_credit": [
        "peringkat*", "rating", "fitch", "pefindo", "moody", "s&p", "sdanp",
        "raih kredit", "fasilitas kredit", "pinjaman", "refinancing",
        "outlook stabil", "gagal bayar", "wanprestasi",
    ],
    "earnings": [
        "laba", "rugi", "kinerja", "pendapatan", "penjualan", "margin",
        "transaksi*", "pertumbuhan", "tumbuh",
        "laporan keuangan", "kuartal", "semester", "*tahunan", "ebitda",
        "top line", "bottom line", "triwulan",
        # banking metrics - three of the 40 were these
        "dpk", "dana pihak ketiga", "npl", "nim", "car", "penyaluran",
        "royalti", "beban", "biaya pengiriman", "kredit", "penurunan",
    ],
    "governance": [
        "rups", "rupslb", "rupst", "direktur", "direksi", "komisaris",
        "dirut", "mundur", "resign", "tunjuk", "rombak", "pengunduran diri",
    ],
    "capital": [
        "ipo", "private placement", "penambahan modal", "pmhmetd", "pmthmetd",
        "rights issue", "penawaran umum", "book building", "listing perdana",
    ],
    "regulatory": [
        "suspensi", "suspend", "notasi khusus", "gembok", "delisting",
        "denda", "sanksi", "ojk", "bei", "idx", "pailit", "pkpu",
        "fraud", "penipuan", "penyelewengan", "audit",
    ],
    "operations": [
        "kontrak*", "tender*", "proyek*", "ekspansi", "pabrik", "produksi*",
        "kapasitas*", "akuisisi lahan", "smelter", "tambang",
        "penyelesaian", "lrt", "mrt",
        # expansion/ops vocabulary the first bundle missed entirely
        "operasional", "gudang", "armada", "perluas", "ekosistem",
        "manufaktur", "pasokan", "andalkan", "perkuat", "pacu", "tkdn",
        "jalan tol", "kilang", "pelabuhan", "distribusi", "ekspor", "impor",
        "digitalisasi", "transformasi", "beroperasi", "jaringan", "5g",
        "inovasi", "penuhi kebutuhan", "pembangunan", "aset", "sewa",
        "optimalisasi", "nikel", "mineral", "payroll", "pasar global",
        "data center", "seksi", "tol", "esg", "berkelanjutan", "rental",
    ],
    "guidance": [
        "target*", "proyeksi", "outlook", "guidance", "capex", "rencana*",
        "optimistis", "prospek*", "peluang investasi", "peluang*",
    ],
    # NOT news. Six of the 40 were corporate PR: "PT TIMAH DUKUNG BIAYA
    # PENGOBATAN WARGA", "SIDO MUNCUL DORONG JAMU MASUK MENU ANGKRINGAN".
    # Kept as a CATEGORY rather than a global exclude so it is Gill's choice
    # whether to see it - put "csr" in a rule's exclude list to drop it.
    "csr": [
        "csr", "umkm", "pemberdayaan", "bakti", "donasi", "beasiswa",
        "sosial", "desa", "warga", "pengobatan", "angkringan", "simulasi",
        "santunan", "khitanan", "posyandu", "lingkungan hidup", "masker",
        "masyarakat", "edukasi", "literasi",
        "obat-obatan", "erupsi", "terdampak", "award", "gratis", "bantuan",
    ],
}

DEFAULT_RULES = [
    {
        "name": "My coverage - everything",
        "enabled": True,
        "tickers": [],                 # fill from the watchlist
        "categories": [],
        "require": [], "any_of": [], "exclude": [],
        "sources": [],
        "min_confidence": 0.5,
        "require_ticker": True,
    },
    {
        "name": "Market-wide corporate actions",
        "enabled": False,
        "tickers": [],
        "categories": ["corporate_action", "ma"],
        "require": [], "any_of": [], "exclude": [],
        "sources": [],
        "min_confidence": 0.85,        # market-wide, so demand a strong hit
        "require_ticker": True,
    },
    {
        "name": "Never miss a suspension",
        "enabled": False,
        "tickers": [],
        "categories": ["regulatory"],
        "require": [], "any_of": ["suspensi", "suspend", "gembok", "delisting"],
        "exclude": [],
        "sources": [],
        "min_confidence": 0.5,
        "require_ticker": True,
    },
]

RULE_FIELDS = {
    "name": str, "enabled": bool, "tickers": list, "categories": list,
    "require": list, "any_of": list, "exclude": list, "sources": list,
    "min_confidence": float, "require_ticker": bool, "require_event": bool,
    "max_tickers": int, "categories_exclude": list,
}

# MAX_TICKERS: the structural roundup guard, from 69 real alerts on 2026-09-09.
# A headline naming three or more companies is enumerating them, not reporting
# on them: "IHSG Terkoreksi ke Level 6.647, Saham Kesehatan MEDS, KAEF hingga
# PYFA...". Two is legitimate and common - "MERGER ADHI KARYA DAN PTPP" - so
# the useful threshold is 2, meaning drop at three or more.
#
# Keyword markers cannot catch these reliably: the giveaway word is "hingga"
# ("up to"), which is ordinary Indonesian and would block real headlines. The
# COUNT is the signal, not the vocabulary.

# Attribution rules that are only a MENTION. "token" is a bare four-letter
# capital in a mixed-case headline - exactly what a roundup produces. paren,
# alias, slug and lead all mean the article is ABOUT that company.
WEAK_RULES = {"token"}

# Words that mark an article as a LIST rather than a story. These rarely
# appear in genuine single-company news, and each one was seen producing a
# false positive on the first live weekday run.
ROUNDUP_MARKERS = [
    "rekomendasi", "daftar saham", "sepekan", "top losers", "top gainers",
    "top gainer", "top loser", "disorot", "rangkuman", "berpotensi",
    "prediksi", "proyeksi ihsg", "saham pilihan", "jajaran", "deretan",
    # Foreign-flow roundups, seen 2026-09-09: "Investor Asing Catat Net
    # Foreign Sell Rp577,21 Miliar, BBCA Jadi Saham Pal..."
    "net foreign sell", "net foreign buy", "net sell", "net buy",
    "foreign sell", "foreign buy", "asing catat", "wajib baca",
]

DEFAULT_FILTERS = {
    "global_exclude": (["prediksi zodiak", "harga emas antam"]
                       + ROUNDUP_MARKERS),
    "categories": DEFAULT_CATEGORIES,
    "rules": DEFAULT_RULES,
}


# --------------------------------------------------------------- term syntax

_TERM_TOK = re.compile(r"[a-z0-9*]+")


def _token_pattern(pat):
    left, right = pat.startswith("*"), pat.endswith("*")
    core = pat.strip("*")
    if not core:
        return lambda w: False          # a bare "*" matches nothing, on purpose
    if left and right:
        return lambda w: core in w
    if right:
        return lambda w: w.startswith(core)
    if left:
        return lambda w: w.endswith(core)
    return lambda w: w == core


MAX_GAP = 3          # tokens a bare `*` may swallow between two words


def compile_term(term):
    """A term -> a predicate over a token list.

    Phrases match consecutive tokens, except a bare `*` between words, which
    matches up to MAX_GAP arbitrary tokens. See the module docstring.
    """
    raw = _TERM_TOK.findall((term or "").lower())
    if not raw:
        return lambda toks: False
    # A lone "*" stays inert; only a gap BETWEEN real words is meaningful.
    if len(raw) == 1:
        pat = _token_pattern(raw[0])
        return lambda toks: any(pat(w) for w in toks)

    parts = []                       # [pattern | None-for-gap, ...]
    for r in raw:
        parts.append(None if r == "*" else _token_pattern(r))
    if all(p is None for p in parts):
        return lambda toks: False

    def match_from(toks, i, k):
        while k < len(parts):
            p = parts[k]
            if p is None:            # gap: try every allowed width
                for width in range(0, MAX_GAP + 1):
                    if match_from(toks, i + width, k + 1):
                        return True
                return False
            if i >= len(toks) or not p(toks[i]):
                return False
            i += 1
            k += 1
        return True

    def hit(toks):
        return any(match_from(toks, i, 0) for i in range(len(toks)))
    return hit


def compile_terms(terms):
    return [compile_term(t) for t in (terms or []) if str(t).strip()]


def any_term(compiled, toks):
    return any(f(toks) for f in compiled)


def all_terms(compiled, toks):
    return all(f(toks) for f in compiled)


def categories_of(headline, categories=None):
    """Which named categories this headline matches. Order-independent."""
    cats = categories if categories is not None else DEFAULT_CATEGORIES
    toks = tokens(headline)
    out = set()
    for name, terms in cats.items():
        if any_term(compile_terms(terms), toks):
            out.add(name)
    return out


# --------------------------------------------------------------------- rules

class Decision:
    __slots__ = ("alert", "rule", "ticker", "categories", "reason")

    def __init__(self, alert, rule=None, ticker=None, categories=(), reason=""):
        self.alert, self.rule, self.ticker = alert, rule, ticker
        self.categories, self.reason = set(categories), reason

    def __repr__(self):
        return (f"Decision({'ALERT' if self.alert else 'drop'}"
                f"{', ' + self.rule if self.rule else ''}"
                f"{', ' + self.ticker if self.ticker else ''}"
                f"{', ' + self.reason if self.reason else ''})")


class FilterSet:
    """Compiled filters. Rebuild on config change; config hot-reloads per tick."""

    def __init__(self, cfg=None):
        cfg = dict(DEFAULT_FILTERS if cfg is None else cfg)
        self.categories = cfg.get("categories") or DEFAULT_CATEGORIES
        self.global_exclude = compile_terms(cfg.get("global_exclude"))
        self.rules = []
        for raw in cfg.get("rules") or []:
            if not raw.get("enabled", True):
                continue
            self.rules.append({
                "name": raw.get("name") or "(unnamed rule)",
                "tickers": {t.strip().upper() for t in raw.get("tickers") or [] if t.strip()},
                "categories": set(raw.get("categories") or []),
                # Category-level exclude. `exclude` drops on a KEYWORD; this
                # drops on a whole classification, which is what you want for
                # corporate PR: "csr" covers masker/UMKM/ESG-award/beasiswa
                # without listing every charitable noun in Indonesian.
                "categories_exclude": set(raw.get("categories_exclude") or []),
                "require": compile_terms(raw.get("require")),
                "any_of": compile_terms(raw.get("any_of")),
                "exclude": compile_terms(raw.get("exclude")),
                "sources": {s.strip().lower() for s in raw.get("sources") or [] if s.strip()},
                "min_confidence": float(raw.get("min_confidence", 0.0) or 0.0),
                "require_ticker": bool(raw.get("require_ticker", True)),
                "require_event": bool(raw.get("require_event", False)),
                "max_tickers": int(raw.get("max_tickers", 0) or 0),
            })
        self._cat_cache = {n: compile_terms(t) for n, t in self.categories.items()}

    def _categories(self, toks):
        return {n for n, c in self._cat_cache.items() if any_term(c, toks)}

    def evaluate(self, headline, hits=(), source=""):
        """-> Decision. `hits` is what jcimatch.find_tickers returned.

        The first enabled rule that accepts wins; rules are OR'd, so ordering
        affects only which rule name is reported, never whether it alerts.
        """
        toks = tokens(headline)
        src = (source or "").strip().lower()

        if any_term(self.global_exclude, toks):
            return Decision(False, reason="global exclude")

        cats = self._categories(toks)
        n_tickers = len({h.ticker for h in hits})
        if not self.rules:
            return Decision(False, reason="no enabled rules")

        # WHY it was refused, not just THAT it was. Nine different gates used
        # to collapse into one string, "no rule accepted" - which is the line
        # a reader now sees next to most headlines in the dashboard, and it
        # told them nothing. Each gate names itself, and across several rules
        # we report the one that got FURTHEST, since that is the rule the
        # headline came closest to satisfying and therefore the one worth
        # editing.
        best = (-1, "no rule accepted")

        def refuse(rank, why):
            nonlocal best
            if rank > best[0]:
                best = (rank, why)

        for r in self.rules:
            if r["sources"] and src not in r["sources"]:
                refuse(0, f"{r['name']}: not one of this rule's sources")
                continue
            if r["require"] and not all_terms(r["require"], toks):
                refuse(1, f"{r['name']}: missing a word the rule requires")
                continue
            if r["any_of"] and not any_term(r["any_of"], toks):
                refuse(2, f"{r['name']}: none of the rule's keywords are in it")
                continue
            if r["exclude"] and any_term(r["exclude"], toks):
                refuse(3, f"{r['name']}: contains a word the rule excludes")
                continue
            if r["categories"] and not (r["categories"] & cats):
                refuse(4, f"{r['name']}: not one of the categories the rule "
                          f"wants" + (f" (this is {'/'.join(sorted(cats))})"
                                      if cats else " (no category matched)"))
                continue
            if r["categories_exclude"] & cats:
                refuse(5, f"{r['name']}: excluded category "
                          f"{'/'.join(sorted(r['categories_exclude'] & cats))}")
                continue
            if r["max_tickers"] and n_tickers > r["max_tickers"]:
                refuse(6, f"{r['name']}: names {n_tickers} tickers, more than "
                          f"the {r['max_tickers']} this rule allows")
                continue
            if not r["require_ticker"]:
                return Decision(True, r["name"], None, cats, "keyword rule")

            # require_ticker: say which of the three ticker gates closed.
            if not hits:
                refuse(7, f"{r['name']}: no ticker in the headline")
                continue
            near = max(h.confidence for h in hits)
            wanted = [h for h in hits if h.confidence >= r["min_confidence"]]
            if not wanted:
                refuse(8, f"{r['name']}: ticker match too weak "
                          f"({near:.2f}, needs {r['min_confidence']:.2f})")
                continue
            for h in wanted:
                if r["tickers"] and h.ticker not in r["tickers"]:
                    # "not on the watchlist" was a lie whenever the rule had
                    # a hand-typed ticker list, and confusing whenever the
                    # watchlist was empty and the list came from somewhere
                    # else. Say what is actually being checked.
                    refuse(9, f"{r['name']}: {h.ticker} is not one of this "
                              f"rule's {len(r['tickers'])} tickers")
                    continue
                # A bare mention in a list is not an event. See the module
                # docstring - this is the roundup filter.
                if r["require_event"] and not cats and h.rule in WEAK_RULES:
                    refuse(10, f"{r['name']}: {h.ticker} is only mentioned, "
                               f"no event word in the headline")
                    continue
                return Decision(True, r["name"], h.ticker, cats,
                                f"{h.rule} {h.confidence:.2f}")

        return Decision(False, categories=cats, reason=best[1])


# ---------------------------------------------------------------- validation

def validate(cfg):
    """Errors the Options window must refuse to save. Returns a list of strings."""
    errs = []
    if not isinstance(cfg, dict):
        return ["filters must be an object"]

    cats = cfg.get("categories", DEFAULT_CATEGORIES)
    if not isinstance(cats, dict):
        errs.append("categories must be an object of name -> list of terms")
        cats = {}
    for name, terms in cats.items():
        if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
            errs.append(f"category {name!r} must be a list of strings")

    rules = cfg.get("rules")
    if not isinstance(rules, list):
        return errs + ["rules must be a list"]
    if not rules:
        errs.append("no rules defined - nothing can ever alert")
    elif not any(r.get("enabled", True) for r in rules if isinstance(r, dict)):
        errs.append("every rule is disabled - nothing can ever alert")

    seen = set()
    for i, r in enumerate(rules):
        where = f"rule {i + 1}"
        if not isinstance(r, dict):
            errs.append(f"{where} is not an object")
            continue
        nm = r.get("name") or ""
        where = f"rule {i + 1} ({nm})" if nm else where
        if not nm.strip():
            errs.append(f"{where} has no name")
        elif nm in seen:
            errs.append(f"{where}: duplicate rule name")
        seen.add(nm)

        for f, typ in RULE_FIELDS.items():
            if f not in r:
                continue
            if typ is float:
                try:
                    c = float(r[f])
                except (TypeError, ValueError):
                    errs.append(f"{where}: {f} must be a number")
                    continue
                if not 0.0 <= c <= 1.0:
                    errs.append(f"{where}: min_confidence must be between 0 and 1")
            elif typ is list and not isinstance(r[f], list):
                errs.append(f"{where}: {f} must be a list")
            elif typ is bool and not isinstance(r[f], bool):
                errs.append(f"{where}: {f} must be true or false")
            elif typ is str and not isinstance(r[f], str):
                errs.append(f"{where}: {f} must be text")

        for key in ("categories", "categories_exclude"):
            for c in r.get(key) or []:
                if c not in cats:
                    errs.append(f"{where}: unknown category {c!r} in {key}")
        for t in r.get("tickers") or []:
            # "@watchlist" is substituted from cfg["watchlist"] at load time -
            # without it, setting a watchlist would do nothing, since an empty
            # ticker list means ANY ticker.
            if str(t).strip().lower() == "@watchlist":
                continue
            if not re.fullmatch(r"[A-Za-z]{4}", str(t).strip()):
                errs.append(f"{where}: {t!r} is not a 4-letter ticker")
        # A rule with no ticker requirement and no keyword constraint accepts
        # the entire feed. Almost certainly a mistake, and an expensive one.
        if (not r.get("require_ticker", True) and not r.get("require")
                and not r.get("any_of") and not r.get("categories")):
            errs.append(f"{where}: require_ticker is off and nothing else is set, "
                        "so this rule alerts on every headline")
    return errs


def describe(rule):
    """One line for the Options list. What this rule actually does, in English."""
    bits = []
    t = [str(x) for x in (rule.get("tickers") or [])]
    named = [x for x in t if x.strip().lower() != "@watchlist"]
    if any(x.strip().lower() == "@watchlist" for x in t):
        # "1 tickers" for a rule holding only the token was misleading in
        # --show-config: the token is a placeholder, not a company.
        bits.append("your watchlist" + (f" + {len(named)} more" if named else ""))
    else:
        bits.append(f"{len(named)} tickers" if named else "any ticker")
    c = rule.get("categories") or []
    if c:
        bits.append("in " + "/".join(c))
    cx = rule.get("categories_exclude") or []
    if cx:
        bits.append("but never " + "/".join(cx))
    if rule.get("require"):
        bits.append("with all of: " + ", ".join(rule["require"]))
    if rule.get("any_of"):
        bits.append("with any of: " + ", ".join(rule["any_of"]))
    if rule.get("exclude"):
        bits.append("but not: " + ", ".join(rule["exclude"]))
    if rule.get("sources"):
        bits.append("from " + "/".join(rule["sources"]))
    if rule.get("max_tickers"):
        bits.append(f"at most {rule['max_tickers']} tickers")
    if rule.get("require_event"):
        bits.append("events only")
    if not rule.get("require_ticker", True):
        bits.append("(keyword-only, no ticker needed)")
    return " · ".join(bits)
