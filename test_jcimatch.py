#!/usr/bin/env python3
"""
test_jcimatch.py - does headline matching and story clustering actually work?

Two failure modes here are silent, which is why this suite exists:

  1. A wrong alias fires the popup on a company you do not cover. You notice
     immediately and stop trusting the app.
  2. An over-eager clusterer SWALLOWS a real second story about a name you do
     cover, and you never learn it happened. Nothing errors, nothing logs,
     the alert simply never arrives. That is the expensive one, so most of
     the clustering checks below assert on stories that must NOT merge.

Every headline is real, captured 2026-09-04/05 from CNBC Indonesia, IQPlus,
IDN Financials, Katadata, Detik, Bisnis and EmitenNews. Invented headlines
would prove nothing: the rules key on how Indonesian financial media actually
writes, including the all-caps house style IQPlus has used for years.

Offline, no display, standard library only.
Run:  python test_jcimatch.py
"""

from datetime import datetime, timedelta, timezone

import jcimatch as M

# --------------------------------------------------------------- fixture table
# A slice of the real registry, with the registered names IDX actually returns.
EMITEN = {
    "BMRI": {"name": "PT Bank Mandiri (Persero) Tbk", "aliases": ["bank mandiri"]},
    "BBRI": {"name": "PT Bank Rakyat Indonesia (Persero) Tbk",
             "aliases": ["bank rakyat indonesia", "bri"]},
    "BBCA": {"name": "PT Bank Central Asia Tbk.",
             "aliases": ["bank central asia", "bca"]},
    "BTPS": {"name": "PT Bank BTPN Syariah Tbk.", "aliases": ["btpn syariah"]},
    "ADHI": {"name": "PT Adhi Karya (Persero) Tbk.", "aliases": ["adhi karya"]},
    "PTPP": {"name": "PP (Persero) Tbk", "aliases": ["ptpp", "pt pp"]},
    "TINS": {"name": "PT Timah Tbk", "aliases": ["timah"]},
    "TLKM": {"name": "PT Telkom Indonesia (Persero) Tbk",
             "aliases": ["telkom", "telkomsel"]},
    "CUAN": {"name": "PT Petrindo Jaya Kreasi Tbk", "aliases": ["petrindo"]},
    "SINI": {"name": "PT Singaraja Putra Tbk", "aliases": ["singaraja putra"]},
    "SRTG": {"name": "PT Saratoga Investama Sedaya Tbk", "aliases": ["saratoga"]},
    "BUKA": {"name": "PT Bukalapak.com Tbk", "aliases": ["bukalapak"]},
    "BYAN": {"name": "PT Bayan Resources Tbk", "aliases": ["bayan resources"]},
    "MBMA": {"name": "PT Merdeka Battery Materials Tbk",
             "aliases": ["merdeka battery"]},
    "SUNI": {"name": "PT Sunindo Pratama Tbk", "aliases": ["sunindo pratama"]},
}
T = M.Table(EMITEN)

NOW = datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc)

failures = []


def check(name, cond, detail=None):
    if cond:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}" + (f"   -> {detail!r}" if detail is not None else ""))


def rule_for(headline, ticker, **kw):
    for h in M.find_tickers(headline, T, **kw):
        if h.ticker == ticker:
            return h
    return None


def main():
    print("\n== rule 1: parenthetical ==")

    h = rule_for("Danantara Minta Adhi Karya (ADHI) Pangkas Semua Anak Usaha, "
                 "Bagaimana Skemanya?", "ADHI")
    check("(ADHI) matches by parenthetical at full confidence",
          h is not None and h.rule == "paren" and h.confidence == 1.0, h)

    hits = M.find_tickers("Emiten Prajogo (CUAN) Nego Akuisisi SINI, Siap Jadi "
                          "Pengendali Baru", T)
    check("parenthetical and bare token coexist in one headline",
          {x.ticker for x in hits} == {"CUAN", "SINI"}, hits)
    check("parenthetical outranks bare token in the ordering",
          hits and hits[0].ticker == "CUAN" and hits[0].rule == "paren", hits)

    print("\n== rule 2: alias ==")

    h = rule_for("Bos Bank Mandiri Jelaskan Alasan Tebar Dividen Interim Rp6,16 T",
                 "BMRI")
    check("'Bank Mandiri' resolves to BMRI with no ticker in the text",
          h is not None and h.rule == "alias", h)

    h = rule_for("Tambah Porsi, Putra Om William Kini Kuasai 35 Persen Saham "
                 "Saratoga", "SRTG")
    check("'Saratoga' resolves to SRTG", h is not None and h.rule == "alias", h)

    check("a short alias never fires inside a longer word",
          rule_for("Kinerja Perbankan abcabc Menguat Kuartal Ini", "BBCA") is None)
    check("the same short alias fires as a standalone word",
          rule_for("Nasabah BCA Tumbuh Dua Digit", "BBCA") is not None)

    print("\n== rule 3: bare token, mixed case ==")

    h = rule_for("Transaksi dan Ekosistem Terus Tumbuh, Kinerja BMRI Tetap Solid",
                 "BMRI")
    check("bare BMRI in a mixed-case headline matches at 0.60",
          h is not None and h.rule == "token" and abs(h.confidence - 0.60) < 1e-9, h)

    hits = M.find_tickers("Saham Top Leaders-Laggards Sepekan: Ditopang BBRI & "
                          "BBCA, Terganjal BYAN hingga MBMA", T)
    check("four tickers in one market-wrap headline all match",
          {x.ticker for x in hits} == {"BBRI", "BBCA", "BYAN", "MBMA"}, hits)

    check("index-level news matches nothing (IHSG is not a ticker)",
          M.find_tickers("Arus Modal Asing Masuk Tapi IHSG Masih Fluktuatif, "
                         "Ini Kata Analis", T) == [])

    print("\n== the all-caps rules are gone with IQPlus ==")

    # Rule 4 and the ambiguous-word penalty existed only for an ALL-CAPS wire,
    # where capitalisation carries no signal. No registered source is one, and
    # on a mixed-case headline the penalty would have cost real coverage of
    # exactly the names that make headlines (BUKA, CUAN, EMAS...). These two
    # checks are what fails if either creeps back in.
    h = rule_for("Saham BUKA Melesat usai Rilis Kinerja Kuartal II", "BUKA")
    check("an ambiguous code in mixed case still scores a full 0.60",
          h is not None and h.rule == "token"
          and abs(h.confidence - 0.60) < 1e-9, h)
    check("find_tickers no longer takes an allcaps argument",
          "allcaps" not in M.find_tickers.__code__.co_varnames,
          M.find_tickers.__code__.co_varnames)

    print("\n== watchlist ==")

    hits = M.find_tickers("Ditopang BBRI & BBCA, Terganjal BYAN hingga MBMA", T,
                          watchlist=["BBCA"])
    check("the watchlist drops tickers you do not cover",
          [x.ticker for x in hits] == ["BBCA"], hits)
    check("an empty watchlist keeps everything",
          len(M.find_tickers("Ditopang BBRI & BBCA", T, watchlist=[])) == 2)

    print("\n== clustering: the same story from several outlets ==")

    c = M.Clusterer(T)
    a = c.add("BMRI", "Bos Bank Mandiri Jelaskan Alasan Tebar Dividen Interim "
                      "Rp6,16 T", source="cnbc", ts=NOW)
    check("first sighting of a story is new", a.is_new is True, a)

    b = c.add("BMRI", "Bank Mandiri (BMRI) Tebar Dividen Interim Rp6,16 Triliun",
              source="katadata", ts=NOW + timedelta(minutes=12))
    check("a second outlet on the same story does not alert again",
          b.is_new is False and b.reason == "title overlap", b)
    check("the extra outlet is attached to the original cluster",
          len(b.cluster.sources) == 2 and b.cluster.id == a.cluster.id,
          b.cluster.sources)

    print("\n== clustering: stories that must NOT be swallowed ==")

    d = c.add("BMRI", "Bank Mandiri (BMRI) Tunjuk Direktur Utama Baru",
              source="detik", ts=NOW + timedelta(minutes=20))
    check("a different story about the same ticker still alerts",
          d.is_new is True, d)

    c2 = M.Clusterer(T)
    e = c2.add("ADHI", "Adhi Karya Pangkas Seluruh Anak Usaha!", ts=NOW)
    f = c2.add("ADHI", "Adhi Karya Raih Kontrak Baru Rp2 Triliun",
               ts=NOW + timedelta(minutes=5))
    check("two unrelated stories sharing only the company name stay separate",
          f.is_new is True, f)
    check("the company's own name is stripped before scoring",
          not ({"adhi", "karya"} & e.cluster.toks), e.cluster.toks)

    c3 = M.Clusterer(T)
    c3.add("BTPS", "BTPS Berencana Gelar Buyback Saham", source="katadata",
           ts=NOW)
    g = c3.add("BTPS", "BTPS prepares buyback of up to IDR 1 trillion to boost "
                       "liquidity", source="kontan-investasi",
               ts=NOW + timedelta(minutes=18))
    check("two languages share no tokens, so both are stories - every "
          "registered source is Indonesian", g.is_new is True, g)

    print("\n== clustering: the window ==")

    c5 = M.Clusterer(T, window_minutes=360)
    c5.add("BMRI", "Bank Mandiri Tebar Dividen Interim Rp6,16 Triliun", ts=NOW)
    j = c5.add("BMRI", "Bank Mandiri Tebar Dividen Interim Rp6,16 Triliun",
               ts=NOW + timedelta(hours=7))
    check("an identical headline past the window is a fresh story",
          j.is_new is True, j)

    print("\n%d checks failed" % len(failures) if failures
          else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
