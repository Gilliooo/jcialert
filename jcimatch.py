#!/usr/bin/env python3
"""
jcimatch - decide which headlines are about which tickers, and which of those
headlines are the same story told by a different outlet.

Two independent jobs, deliberately kept separate so they can be tested apart:

  find_tickers(headline, table)   headline -> [Hit(ticker, rule, confidence)]
  Clusterer.add(item)             item     -> new story, or a duplicate of one

WHY THREE MATCHING RULES
------------------------
Indonesian tickers are exactly four letters. That single constraint is what
makes headline matching tractable, and every rule below leans on it.

  1. PARENTHETICAL   "Bank Mandiri (BMRI) Tebar Dividen"
     Indonesian financial media parenthesises the ticker on first mention.
     Near-zero false positives; this is the rule to trust.

  2. ALIAS           "Bos Bank Mandiri Jelaskan ..."   -> BMRI
     Needs emiten.json from build_aliases.py. Recall depends entirely on how
     well that table is maintained: IDX registers ADRO as "Alamtri Resources
     Indonesia" while every headline says "Adaro".

  3. BARE TOKEN      "... CUAN Nego Akuisisi SINI"
     Capitalisation is itself the signal, so a bare four-letter capital is a
     solid hit (0.60). Every registered source writes mixed case; the all-caps
     variant of this rule went with IQPlus on 2026-09-23.

WHY THE CLUSTERER STRIPS COMPANY NAME TOKENS
--------------------------------------------
Clusters are compared within a single ticker, so the company's own name is
constant across every candidate and inflates every similarity score. Two
unrelated BBCA stories would look near-identical on "bank central asia" alone.
Name tokens are removed before scoring; what is left is the event.

Similarity is the OVERLAP COEFFICIENT |A n B| / min(|A|,|B|), not Jaccard.
Headline length varies enormously between outlets - a wire one-liner and a
600-character CNBC headline about the same buyback share few tokens as a
fraction of their union, but the short one is almost wholly contained in the
long one. Jaccard punishes that; overlap does not.

KNOWN LIMIT: this clusters on shared tokens, so two headlines in different
languages about the same event would not merge. Every registered source is
Indonesian, so that case does not arise.

Standard library only.
"""

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone

# ------------------------------------------------------------------ tokenising

_WORD = re.compile(r"[a-z0-9]+")

# Function words plus the filler that appears in nearly every market headline.
# Kept deliberately short: over-pruning destroys the signal the clusterer needs.
STOPWORDS = {
    # Indonesian
    "ada", "adalah", "akan", "agar", "atau", "bagi", "bahwa", "bakal", "banyak",
    "bisa", "buat", "dan", "dari", "dalam", "dengan", "hingga", "ini", "itu",
    "jadi", "juga", "kepada", "karena", "lagi", "lalu", "lebih", "masih", "mau",
    "mulai", "oleh", "pada", "para", "per", "saat", "saja", "sampai", "sebagai",
    "sedang", "sejak", "selama", "serta", "setelah", "soal", "sudah", "tak",
    "tapi", "telah", "tentang", "terhadap", "tidak", "untuk", "usai", "yang",
    "kini", "bos", "begini", "ini", "simak", "cek",
    # English
    "the", "and", "for", "with", "from", "that", "this", "will", "has", "have",
    "its", "into", "amid", "over", "after", "says", "said", "was", "were",
    # market filler
    "saham", "emiten", "persen", "triliun", "miliar", "juta", "rupiah",
    "trillion", "billion", "million", "percent", "stock", "shares", "idr",
}


def tokens(text):
    """Casefold, strip accents, split to alphanumeric words."""
    if not text:
        return []
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return _WORD.findall(t.lower())


def content_tokens(text, drop=()):
    """Tokens that carry event meaning: no stopwords, no company name, len>=3."""
    drop = set(drop)
    return {w for w in tokens(text)
            if len(w) >= 3 and w not in STOPWORDS and w not in drop}


# --------------------------------------------------------------------- table

class Table:
    """The ticker/alias table produced by build_aliases.py."""

    def __init__(self, emiten):
        self.emiten = emiten
        self.tickers = set(emiten)
        # alias tokens indexed by first token, longest alias first, so
        # "bank central asia" is tried before "bank jago" at the same position.
        self.index = {}
        self.alias_tokens = {}
        for code, ent in emiten.items():
            for alias in ent.get("aliases", []):
                toks = tuple(tokens(alias))
                if not toks:
                    continue
                self.index.setdefault(toks[0], []).append((toks, code))
                self.alias_tokens.setdefault(code, set()).update(toks)
        for k in self.index:
            self.index[k].sort(key=lambda p: -len(p[0]))

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return cls(payload.get("emiten", payload))

    def name_tokens(self, code):
        """Tokens the clusterer must ignore for this ticker: its name and code."""
        out = set(self.alias_tokens.get(code, ()))
        out.update(tokens(self.emiten.get(code, {}).get("name", "")))
        out.add(code.lower())
        return out


class Hit:
    __slots__ = ("ticker", "rule", "confidence", "at")

    def __init__(self, ticker, rule, confidence, at=0):
        self.ticker, self.rule, self.confidence, self.at = ticker, rule, confidence, at

    def __repr__(self):
        return f"Hit({self.ticker}, {self.rule}, {self.confidence:.2f})"

    def __eq__(self, other):
        return (isinstance(other, Hit) and self.ticker == other.ticker
                and self.rule == other.rule)


# ------------------------------------------------------------------- matching

RE_PAREN = re.compile(r"\(\s*([A-Z]{4})\s*\)")
RE_UPPER4 = re.compile(r"(?<![A-Za-z0-9])([A-Z]{4})(?![A-Za-z0-9])")

def find_tickers(headline, table, watchlist=None, min_confidence=0.0):
    """Return the tickers this headline is about, best rule first.

    watchlist iterable of tickers to keep; None or empty keeps everything.
    min_confidence
              drop hits below this. 0.5 keeps every rule here; it exists so a
              future weaker rule can be excluded without a config change.
    """
    headline = headline or ""
    wl = {t.strip().upper() for t in (watchlist or []) if t.strip()}

    best = {}

    def offer(code, rule, conf, at=0):
        if code not in table.tickers:
            return
        cur = best.get(code)
        if cur is None or conf > cur.confidence:
            best[code] = Hit(code, rule, conf, at)

    # Rule 1 - parenthetical. Works on every source, all-caps included.
    for m in RE_PAREN.finditer(headline):
        offer(m.group(1), "paren", 1.0, m.start())

    # Rule 2 - alias, matched as whole token runs so "bca" never fires inside
    # another word and "bank jago" never matches "bank" alone.
    toks = tokens(headline)
    for i, w in enumerate(toks):
        for alias_toks, code in table.index.get(w, ()):
            n = len(alias_toks)
            if tuple(toks[i:i + n]) == alias_toks:
                offer(code, "alias", 0.90, i)
                break            # longest alias at this position wins

    # Rule 3 - bare four-letter capital. The capitals ARE the signal.
    for m in RE_UPPER4.finditer(headline):
        offer(m.group(1), "token", 0.60, m.start())

    hits = [h for h in best.values() if h.confidence >= min_confidence]
    if wl:
        hits = [h for h in hits if h.ticker in wl]
    hits.sort(key=lambda h: (-h.confidence, h.at))
    return hits


# ----------------------------------------------------------------- clustering

def overlap(a, b):
    """Overlap coefficient. 1.0 when the smaller set is wholly inside the larger."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


class Cluster:
    __slots__ = ("id", "ticker", "title", "toks", "first_seen", "sources")

    def __init__(self, cid, ticker, title, toks, ts, source, link):
        self.id, self.ticker, self.title = cid, ticker, title
        self.toks, self.first_seen = toks, ts
        self.sources = [(source, link, ts)]


class Result:
    __slots__ = ("is_new", "cluster", "reason", "score")

    def __init__(self, is_new, cluster, reason, score=0.0):
        self.is_new, self.cluster, self.reason, self.score = is_new, cluster, reason, score

    def __repr__(self):
        return f"Result(new={self.is_new}, {self.reason}, {self.score:.2f})"


class Clusterer:
    """Alert once per story. Later outlets attach to the cluster instead of firing.

    window            how long a story stays live for matching (default 6h)
    threshold         overlap coefficient above which two headlines are one story
    min_tokens        below this, similarity is meaningless - require an exact
                      normalised title instead
    """

    def __init__(self, table=None, window_minutes=360, threshold=0.6,
                 min_tokens=3):
        self.table = table
        self.window = timedelta(minutes=window_minutes)
        self.threshold = threshold
        self.min_tokens = min_tokens
        self.by_ticker = {}
        self._next = 1

    def _prune(self, now):
        cutoff = now - self.window
        for t in list(self.by_ticker):
            kept = [c for c in self.by_ticker[t] if c.first_seen >= cutoff]
            if kept:
                self.by_ticker[t] = kept
            else:
                del self.by_ticker[t]

    def export_state(self):
        """Serialisable live clusters, so a restart does not re-alert a story
        every outlet has already reported. Clusters are state, not a cache."""
        return [{"id": c.id, "ticker": c.ticker, "title": c.title,
                 "toks": sorted(c.toks),
                 "first_seen": c.first_seen.isoformat(),
                 "sources": [[s, l, t.isoformat()] for s, l, t in c.sources]}
                for cs in self.by_ticker.values() for c in cs]

    def import_state(self, rows):
        """Rebuild from export_state(). Unparseable rows are skipped, never
        fatal - a corrupt store must degrade to re-alerting, not to crashing."""
        self.by_ticker, top = {}, 0
        for r in rows or ():
            try:
                c = Cluster(int(r["id"]), r["ticker"], r.get("title", ""),
                            set(r.get("toks") or ()),
                            datetime.fromisoformat(r["first_seen"]),
                            "", "")
                c.sources = [(s, l, datetime.fromisoformat(t))
                             for s, l, t in (r.get("sources") or [])]
            except (KeyError, TypeError, ValueError):
                continue
            self.by_ticker.setdefault(c.ticker, []).append(c)
            top = max(top, c.id)
        self._next = top + 1
        return self

    def add(self, ticker, title, source="", link="", ts=None):
        ts = ts or datetime.now(timezone.utc)
        self._prune(ts)
        drop = self.table.name_tokens(ticker) if self.table else {ticker.lower()}
        toks = content_tokens(title, drop)

        for c in self.by_ticker.get(ticker, ()):
            if len(toks) < self.min_tokens or len(c.toks) < self.min_tokens:
                if toks and toks == c.toks:
                    c.sources.append((source, link, ts))
                    return Result(False, c, "identical short headline", 1.0)
                continue
            s = overlap(toks, c.toks)
            if s >= self.threshold:
                c.sources.append((source, link, ts))
                return Result(False, c, "title overlap", s)

        c = Cluster(self._next, ticker, title, toks, ts, source, link)
        self._next += 1
        self.by_ticker.setdefault(ticker, []).append(c)
        return Result(True, c, "new story", 0.0)
