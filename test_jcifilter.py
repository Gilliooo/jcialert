#!/usr/bin/env python3
"""
test_jcifilter.py - do the filter rules do what the Options window will claim?

The failure that matters here is silent in exactly the same way IDXAlert's was:
a filter that quietly accepts nothing looks identical to a quiet news day.
Nothing errors, nothing logs, the popup simply never appears. So most of these
assert on what MUST still get through, not only on what is blocked.

The other trap is the empty list. "tickers": [] means ANY ticker, not NO
ticker. Every hand-edit of the config risks reading it the other way, so each
empty-list field gets its own check.

Headlines are real, from CNBC, IQPlus, Katadata, Kontan, IDX Channel and
EmitenNews, 2026-09-04/05.

Offline. Run:  python test_jcifilter.py
"""

import copy

import jcifilter as F
import jcimatch as M

EMITEN = {
    "BMRI": {"name": "PT Bank Mandiri (Persero) Tbk", "aliases": ["bank mandiri"]},
    "BBRI": {"name": "PT Bank Rakyat Indonesia (Persero) Tbk", "aliases": ["bri"]},
    "BTPS": {"name": "PT Bank BTPN Syariah Tbk.", "aliases": ["btpn syariah"]},
    "ADHI": {"name": "PT Adhi Karya (Persero) Tbk.", "aliases": ["adhi karya"]},
    "PTPP": {"name": "PP (Persero) Tbk", "aliases": ["ptpp"]},
    "CUAN": {"name": "PT Petrindo Jaya Kreasi Tbk", "aliases": ["petrindo"]},
    "SINI": {"name": "PT Singaraja Putra Tbk", "aliases": ["singaraja putra"]},
    "MDLN": {"name": "PT Modernland Realty Tbk", "aliases": ["modernland"]},
    "RGAS": {"name": "PT Rukun Raharja Gas Tbk", "aliases": ["rukun raharja gas"]},
}
T = M.Table(EMITEN)

H_DIVIDEN = "Bos Bank Mandiri Jelaskan Alasan Tebar Dividen Interim Rp6,16 T"
H_BUYBACK = "BTPS BERENCANA GELAR BUYBACK SAHAM"
H_MERGER = "MERGER ADHI KARYA DAN PTPP DITARGETKAN RAMPUNG AKHIR 2026"
H_AKUISISI = "Emiten Prajogo (CUAN) Nego Akuisisi SINI, Siap Jadi Pengendali Baru"
H_RUPS = "MDLN Siapkan RUPSLB Kedua Bahas Pengalihan Aset"
H_LOSERS = "10 Saham yang Rugi Sepekan, RGAS Pimpin Top Losers"
H_INDEX = "IHSG Naik Saat Asing Net Buy Rp 2,3 T Sepekan, BBRI hingga BMRI Jadi Penggerak"

failures = []


def check(name, cond, detail=None):
    if cond:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}" + (f"   -> {detail!r}" if detail is not None else ""))


def rule(**kw):
    r = {"name": kw.pop("name", "r"), "enabled": True, "tickers": [],
         "categories": [], "require": [], "any_of": [], "exclude": [],
         "sources": [], "min_confidence": 0.0, "require_ticker": True}
    r.update(kw)
    return r


def fs(rules, **cfg):
    base = {"global_exclude": [], "categories": F.DEFAULT_CATEGORIES,
            "rules": rules if isinstance(rules, list) else [rules]}
    base.update(cfg)
    return F.FilterSet(base)


def hits(headline):
    return M.find_tickers(headline, T)


def main():
    print("\n== term syntax ==")
    t = M.tokens
    check("an exact term matches a whole token",
          F.compile_term("dividen")(t(H_DIVIDEN)))
    check("an exact term never fires as a substring",
          not F.compile_term("laba")(t("Pasar Kelabakan Usai Rilis Data")))
    check("prefix wildcard: 'akuisisi*'",
          F.compile_term("akuisisi*")(t("Rencana Akuisisinya Ditunda")))
    check("suffix wildcard: '*akuisisi'",
          F.compile_term("*akuisisi")(t("Perseroan Mengakuisisi Dua Tambang")))
    check("both ends: '*akuisisi*'",
          F.compile_term("*akuisisi*")(t("Saham Diakuisisinya Naik")))
    check("a phrase matches consecutive tokens",
          F.compile_term("tebar dividen")(t(H_DIVIDEN)))
    check("a phrase does not match its words scattered apart",
          not F.compile_term("tebar dividen")(t("Dividen Final Akan Ditebar Juni")))
    check("a bare '*' matches nothing", not F.compile_term("*")(t(H_DIVIDEN)))

    print("\n== the gap operator, from real Indonesian headlines ==")
    # Indonesian headlines put the quantity INSIDE the phrase that names the
    # event. Strictly consecutive matching missed every ownership story on
    # 2026-09-09.
    check("'borong * saham' spans an inserted quantity",
          F.compile_term("borong * saham")(
              t("Putra Bos Emtek Borong 635 Juta Saham BUKA, Ada Apa?")))
    check("and still matches with no gap at all",
          F.compile_term("borong * saham")(t("Borong Saham GOTO")))
    check("'tawar * saham' likewise",
          F.compile_term("tawar * saham")(
              t("Haji Isam Dikabarkan Tawar 62% Saham Bayan")))
    check("the gap is bounded, so it cannot span a whole headline",
          not F.compile_term("borong * saham")(
              t("Borong satu dua tiga empat lima Saham")))
    check("a lone '*' is still inert", not F.compile_term("*")(t("apapun juga")))
    check("word order still matters",
          not F.compile_term("saham * borong")(t("Borong 635 Juta Saham")))

    print("\n== categories learned from a real trading day ==")
    LEARNED = [
        ("JEMMY KURNIAWAN KURANGI KEPEMILIKAN SAHAM MEDS", "ownership"),
        ("ADI WARDHANA TAMBAH KEPEMILIKAN SAHAM BUKALAPAK", "ownership"),
        ("Putra Bos Emtek Borong 635 Juta Saham BUKA", "ownership"),
        ("INDY DIRIKAN ANAK USAHA BARU", "structure"),
        ("SMRA KURANGI MODAL KE ANAK USAHANYA", "structure"),
        ("FITCH TETAPKAN PERINGKAT AAA UNTUK INDOSAT", "rating_credit"),
        ("ADI SARANA ARMADA RAIH KREDIT DARI BANK OCBC NISP", "rating_credit"),
        ("DPK Bank Jatim Anjlok 10,6%", "earnings"),
        ("BLOG Terus Perkuat Operasional, Didukung 16 Gudang dan 3.500 Armada Truk",
         "operations"),
        ("PT TIMAH DUKUNG PEMBERDAYAAN UMKM DI BANGKA SELATAN", "csr"),
        ("SIDO MUNCUL DORONG JAMU MASUK MENU ANGKRINGAN", "csr"),
    ]
    for headline, want in LEARNED:
        got = F.categories_of(headline)
        check(f"{want:<13} {headline[:44]}...", want in got, sorted(got))
    check("insider dealing was the single biggest gap - now its own category",
          "ownership" in F.DEFAULT_CATEGORIES)
    check("corporate PR is a category, not a global exclude, so it stays "
          "Gill's choice", "csr" in F.DEFAULT_CATEGORIES)

    print("\n== Indonesian suffixes: why event terms end in * ==")
    # Invisible misses, found over 162 real alerts: the term is right, the
    # headline inflects it, nothing matches and nothing complains.
    for term, headline in (("prospek*", "Simak Prospeknya Tahun Ini"),
                           ("kepemilikan*", "Kepemilikannya Berkurang Jadi 4%"),
                           ("kontrak*", "Raih Kontraknya dari Pertamina"),
                           ("produksi*", "Produksinya Naik 12%"),
                           ("target*", "Targetkan Kredit Tumbuh 10%")):
        check(f"{term:<14} matches {headline[:34]!r}",
              F.compile_term(term)(t(headline)), term)
    check("the un-suffixed form would have missed it",
          not F.compile_term("prospek")(t("Simak Prospeknya Tahun Ini")))

    print("\n== what the remaining gap actually is ==")
    # After two rounds of expansion the misses are source TYPOS and genuine
    # non-events. Both are the right place to stop - chasing further means
    # terms broad enough to match everything.
    check("a source typo cannot be matched, and that is accepted",
          F.categories_of("BNI Catat Transkasi Digital Rp 958 T") == set(),
          F.categories_of("BNI Catat Transkasi Digital Rp 958 T"))
    check("colloquial commentary with no event stays uncategorised",
          F.categories_of("Investasinya Nyangkut di Saham GOTO, "
                          "Ini Kata Telkomsel") == set())

    print("\n== categories ==")
    check("a dividend headline is a corporate action",
          "corporate_action" in F.categories_of(H_DIVIDEN))
    check("a buyback headline is a corporate action",
          "corporate_action" in F.categories_of(H_BUYBACK))
    check("a merger headline is M&A", "ma" in F.categories_of(H_MERGER))
    check("wildcards inside a category work ('Akuisisi')",
          "ma" in F.categories_of(H_AKUISISI), F.categories_of(H_AKUISISI))
    check("an RUPSLB headline is governance",
          "governance" in F.categories_of(H_RUPS))
    check("a headline can belong to several categories",
          len(F.categories_of("BTPS GELAR RUPSLB BAHAS BUYBACK SAHAM")) >= 2,
          F.categories_of("BTPS GELAR RUPSLB BAHAS BUYBACK SAHAM"))
    check("an index headline matches no category",
          F.categories_of("IHSG Ditutup Menguat 31 Poin") == set(),
          F.categories_of("IHSG Ditutup Menguat 31 Poin"))

    print("\n== empty list means ANY, never NONE ==")
    d = fs(rule()).evaluate(H_DIVIDEN, hits(H_DIVIDEN))
    check("an all-empty rule accepts any ticker hit", d.alert is True, d)
    d = fs(rule(tickers=[])).evaluate(H_BUYBACK, hits(H_BUYBACK))
    check("empty tickers = any ticker", d.alert is True, d)
    d = fs(rule(categories=[])).evaluate(H_RUPS, hits(H_RUPS))
    check("empty categories = any category", d.alert is True, d)
    d = fs(rule(sources=[])).evaluate(H_RUPS, hits(H_RUPS), source="kontan")
    check("empty sources = any source", d.alert is True, d)

    print("\n== one rule, every populated field must pass ==")
    d = fs(rule(tickers=["BBRI"])).evaluate(H_DIVIDEN, hits(H_DIVIDEN))
    check("a ticker outside the rule's list does not alert", d.alert is False, d)
    d = fs(rule(tickers=["BMRI"])).evaluate(H_DIVIDEN, hits(H_DIVIDEN))
    check("a ticker inside the rule's list does",
          d.alert is True and d.ticker == "BMRI", d)
    d = fs(rule(categories=["ma"])).evaluate(H_DIVIDEN, hits(H_DIVIDEN))
    check("the wrong category does not alert", d.alert is False, d)
    d = fs(rule(categories=["corporate_action"])).evaluate(H_DIVIDEN, hits(H_DIVIDEN))
    check("the right category does", d.alert is True, d)
    d = fs(rule(require=["dividen", "interim"])).evaluate(H_DIVIDEN, hits(H_DIVIDEN))
    check("require: all terms present -> alert", d.alert is True, d)
    d = fs(rule(require=["dividen", "buyback"])).evaluate(H_DIVIDEN, hits(H_DIVIDEN))
    check("require: one term missing -> no alert", d.alert is False, d)
    d = fs(rule(any_of=["buyback", "dividen"])).evaluate(H_DIVIDEN, hits(H_DIVIDEN))
    check("any_of: one term present -> alert", d.alert is True, d)
    d = fs(rule(any_of=["buyback", "merger"])).evaluate(H_DIVIDEN, hits(H_DIVIDEN))
    check("any_of: none present -> no alert", d.alert is False, d)
    d = fs(rule(exclude=["interim"])).evaluate(H_DIVIDEN, hits(H_DIVIDEN))
    check("exclude blocks a rule that would otherwise pass", d.alert is False, d)
    d = fs(rule(sources=["iqplus"])).evaluate(H_RUPS, hits(H_RUPS), source="kontan")
    check("a source outside the rule's list does not alert", d.alert is False, d)
    d = fs(rule(min_confidence=0.9)).evaluate(H_INDEX, hits(H_INDEX))
    check("min_confidence drops a bare-token hit (0.60)", d.alert is False, d)
    d = fs(rule(min_confidence=0.5)).evaluate(H_INDEX, hits(H_INDEX))
    check("and keeps it when the floor is lower", d.alert is True, d)

    print("\n== rules are OR'd ==")
    two = [rule(name="coverage", tickers=["BBRI"]),
           rule(name="all buybacks", any_of=["buyback"])]
    d = fs(two).evaluate(H_BUYBACK, hits(H_BUYBACK))
    check("a second rule can accept what the first rejected",
          d.alert is True and d.rule == "all buybacks", d)
    d = fs(two).evaluate(H_RUPS, hits(H_RUPS))
    check("a headline no rule accepts does not alert", d.alert is False, d)
    disabled = [dict(rule(name="off", any_of=["buyback"]), enabled=False)]
    d = fs(disabled).evaluate(H_BUYBACK, hits(H_BUYBACK))
    check("a disabled rule is ignored", d.alert is False, d)
    check("no enabled rules alerts on nothing",
          fs([]).evaluate(H_BUYBACK, hits(H_BUYBACK)).alert is False)

    print("\n== global exclude and keyword-only rules ==")
    d = fs(rule(), global_exclude=["tebar dividen"]).evaluate(H_DIVIDEN, hits(H_DIVIDEN))
    check("global_exclude beats every rule", d.alert is False, d)
    check("and says so", "global" in d.reason, d.reason)
    d = fs(rule(require_ticker=False, any_of=["ihsg"])).evaluate(
        "IHSG Ditutup Menguat 31 Poin", [])
    check("a keyword-only rule can alert with no ticker at all",
          d.alert is True and d.ticker is None, d)

    print("\n== the false-positive that motivates exclude ==")
    # "Rugi" here is a price move, not an accounting loss. The earnings
    # category cannot tell the difference, so the rule must.
    d = fs(rule(categories=["earnings"])).evaluate(H_LOSERS, hits(H_LOSERS))
    check("an earnings rule DOES fire on a top-losers table (the problem)",
          d.alert is True, d)
    d = fs(rule(categories=["earnings"],
                exclude=["top losers", "top gainers", "sepekan"])
           ).evaluate(H_LOSERS, hits(H_LOSERS))
    check("and an exclude term is how you stop it", d.alert is False, d)
    d = fs(rule(categories=["earnings"],
                exclude=["top losers", "top gainers", "sepekan"])
           ).evaluate("MDLN Bukukan Laba Bersih Rp1,2 Triliun", hits("MDLN Bukukan Laba Bersih Rp1,2 Triliun"))
    check("without blocking a real earnings headline", d.alert is True, d)

    print("\n== the roundup problem, from the first live weekday run ==")
    # All three of these alerted on 2026-09-07 and all three are LISTS: the
    # tickers are enumerated, not reported on. Same fingerprint every time -
    # bare token attribution, no category matched.
    ROUNDUPS = [
        "IHSG Berpotensi Turun pada Awal Pekan, Saham JPFA hingga PTRO Jadi Rekomendasi",
        "Asing Ramai Borong Saham Big Banks BBRI, BBCA dan BMRI Sepekan Terakhir",
        "Daftar Saham PER Terendah & Tertinggi LQ45 (4 September 2026), JPFA dan CUAN Disorot",
    ]
    T2 = M.Table({t: {"name": t, "aliases": []} for t in
                  ("JPFA", "PTRO", "BBRI", "BBCA", "BMRI", "CUAN", "BTPS",
                   "MDLN", "BBTN", "ADRO")})
    wide = fs(rule(), global_exclude=[])
    check("without the markers all three alert - this was the bug",
          all(wide.evaluate(h, M.find_tickers(h, T2, min_confidence=0.5)).alert
              for h in ROUNDUPS))
    guarded = F.FilterSet({"categories": F.DEFAULT_CATEGORIES,
                           "global_exclude": F.DEFAULT_FILTERS["global_exclude"],
                           "rules": [rule()]})
    for h in ROUNDUPS:
        d = guarded.evaluate(h, M.find_tickers(h, T2, min_confidence=0.5))
        check(f"blocked: {h[:44]}...", d.alert is False and "exclude" in d.reason, d)
    KEEP = ["Bank Mandiri (BMRI) Tebar Dividen Interim Rp6,16 Triliun",
            "Rombak Susunan Direksi, Ini Harapan Dirut BTN (BBTN)",
            "MDLN Siapkan RUPSLB Kedua Bahas Pengalihan Aset",
            "ADRO Bangun Pabrik Aluminium di Kalimantan"]
    for h in KEEP:
        d = guarded.evaluate(h, M.find_tickers(h, T2, min_confidence=0.5))
        check(f"still alerts: {h[:44]}...", d.alert is True, d)

    print("\n== max_tickers: the count is the signal, not the vocabulary ==")
    # From 69 real alerts on 2026-09-09. The giveaway word in a movers list is
    # "hingga" ("up to"), which is ordinary Indonesian - blocking it would kill
    # real headlines. How MANY companies are named is the reliable signal.
    T3 = M.Table({t: {"name": t, "aliases": []} for t in
                  ("MEDS", "KAEF", "PYFA", "ADHI", "PTPP", "BBCA", "UNTR")})
    movers = "IHSG Terkoreksi ke Level 6.647, Saham Kesehatan MEDS, KAEF hingga PYFA Rontok"
    merger = "MERGER ADHI KARYA DAN PTPP DITARGETKAN RAMPUNG AKHIR 2026"
    capped = fs(rule(max_tickers=2), global_exclude=[])
    check("three tickers in one headline is a list, and is dropped",
          capped.evaluate(movers, M.find_tickers(movers, T3,
                                                 min_confidence=0.5)).alert is False)
    check("two tickers is legitimate and still alerts",
          capped.evaluate(merger, M.find_tickers(merger, T3,
                                                 min_confidence=0.5)).alert is True)
    check("one ticker is obviously fine",
          capped.evaluate("UNTR Bidik Investasi Coking Coal",
                          M.find_tickers("UNTR Bidik Investasi Coking Coal", T3,
                                         min_confidence=0.5)).alert is True)
    off = fs(rule(max_tickers=0), global_exclude=[])
    check("0 means no limit, so it is off unless asked",
          off.evaluate(movers, M.find_tickers(movers, T3,
                                              min_confidence=0.5)).alert is True)
    check("describe() tells the user the cap is on",
          "at most 2 tickers" in F.describe(rule(max_tickers=2)),
          F.describe(rule(max_tickers=2)))

    print("\n== foreign-flow roundups ==")
    flow = F.FilterSet({"categories": F.DEFAULT_CATEGORIES,
                        "global_exclude": F.DEFAULT_FILTERS["global_exclude"],
                        "rules": [rule()]})
    for h in ("Investor Asing Catat Net Foreign Sell Rp577,21 Miliar, BBCA Jadi Saham Paling Dilego",
              "Saham BBCA Tiba-tiba Anjlok 2% Lebih, Asing Net Sell Jumbo",
              "Investor Wajib Baca, SdanP Global Kasih Kabar Baik Soal BBRI"):
        d = flow.evaluate(h, M.find_tickers(h, T3, min_confidence=0.5))
        check(f"blocked: {h[:46]}...", d.alert is False, d)

    print("\n== require_event: the structural backstop ==")
    ev = fs(rule(require_event=True), global_exclude=[])
    # NOTE: ROUNDUPS[1] no longer works as the example here, and that is
    # correct. Expanding `ownership` with "borong * saham" means foreign-flow
    # roundups DO now match a category, so require_event alone stops catching
    # them. They are still blocked - by the flow markers and by max_tickers.
    # Defence in depth is the point: no single guard carries all of them.
    bare = "BBRI Jadi Sorotan Pelaku Pasar Hari Ini"
    d = ev.evaluate(bare, M.find_tickers(bare, T2, min_confidence=0.5))
    check("a bare token with no category is a mention, not an event",
          d.alert is False, d)
    check("and the flow roundup is still caught, just by a different guard",
          F.FilterSet({"categories": F.DEFAULT_CATEGORIES,
                       "global_exclude": F.DEFAULT_FILTERS["global_exclude"],
                       "rules": [rule(max_tickers=2)]}).evaluate(
              ROUNDUPS[1], M.find_tickers(ROUNDUPS[1], T2,
                                          min_confidence=0.5)).alert is False)
    d = ev.evaluate("MDLN Siapkan RUPSLB Kedua Bahas Pengalihan Aset",
                    M.find_tickers("MDLN Siapkan RUPSLB Kedua Bahas Pengalihan Aset",
                                   T2, min_confidence=0.5))
    check("a bare token WITH a category still alerts", d.alert is True, d)
    h = "Bank Mandiri (BMRI) Ganti Logo"
    d = ev.evaluate(h, M.find_tickers(h, T2, min_confidence=0.5))
    check("a parenthetical ticker alerts even with no category",
          d.alert is True, d)
    off = fs(rule(require_event=False), global_exclude=[])
    check("and it is OFF by default, so nothing changes unless asked",
          off.evaluate(ROUNDUPS[1],
                       M.find_tickers(ROUNDUPS[1], T2, min_confidence=0.5)).alert
          is True)

    print("\n== categories_exclude: dropping a CLASS, not a keyword ==")
    # Corporate PR is the biggest remaining noise source for a bank watchlist.
    # `exclude` would mean listing every charitable noun in Indonesian;
    # excluding the csr CATEGORY covers masker/UMKM/ESG-award/beasiswa at once.
    T4 = M.Table({t: {"name": t, "aliases": []} for t in ("BBNI", "BBCA", "JSMR")})
    nopr = fs(rule(categories_exclude=["csr"]), global_exclude=[])
    for h in ("BBNI Salurkan Masker dan Obat-obatan bagi Warga Terdampak Erupsi",
              "BBCA Raih KEHATI ESG Award 2026 untuk Investasi Berkelanjutan"):
        check(f"dropped: {h[:44]}...",
              nopr.evaluate(h, M.find_tickers(h, T4, min_confidence=0.5)).alert
              is False)
    for h in ("KREDIT BBCA TUMBUH 8% JADI RP1.036 TRILIUN",
              "JSMR TOL YOGYA-BAWEN SEKSI 6 AKAN BEROPERASI NOVEMBER"):
        check(f"kept:    {h[:44]}...",
              nopr.evaluate(h, M.find_tickers(h, T4,
                                              min_confidence=0.5)).alert is True)
    check("describe() says so",
          "but never csr" in F.describe(rule(categories_exclude=["csr"])),
          F.describe(rule(categories_exclude=["csr"])))
    bad = {"categories": F.DEFAULT_CATEGORIES,
           "rules": [rule(categories_exclude=["tidak_ada"])]}
    check("an unknown name in categories_exclude is rejected too",
          any("categories_exclude" in e for e in F.validate(bad)), F.validate(bad))

    print("\n== validation ==")
    def errs(cfg):
        return F.validate(cfg)

    check("the shipped defaults validate clean", errs(F.DEFAULT_FILTERS) == [],
          errs(F.DEFAULT_FILTERS))
    bad = {"categories": F.DEFAULT_CATEGORIES, "rules": [rule(categories=["nope"])]}
    check("an unknown category is rejected",
          any("unknown category" in e for e in errs(bad)), errs(bad))
    bad = {"categories": F.DEFAULT_CATEGORIES, "rules": [rule(tickers=["BBCAX"])]}
    check("a malformed ticker is rejected",
          any("4-letter" in e for e in errs(bad)), errs(bad))
    bad = {"categories": F.DEFAULT_CATEGORIES, "rules": [rule(min_confidence=1.7)]}
    check("min_confidence out of range is rejected",
          any("between 0 and 1" in e for e in errs(bad)), errs(bad))
    bad = {"categories": F.DEFAULT_CATEGORIES,
           "rules": [rule(name="dup"), rule(name="dup")]}
    check("duplicate rule names are rejected",
          any("duplicate" in e for e in errs(bad)), errs(bad))
    bad = {"categories": F.DEFAULT_CATEGORIES,
           "rules": [dict(rule(), enabled=False)]}
    check("all-rules-disabled is rejected as unalertable",
          any("nothing can ever alert" in e for e in errs(bad)), errs(bad))
    bad = {"categories": F.DEFAULT_CATEGORIES,
           "rules": [rule(require_ticker=False)]}
    check("a rule that would alert on every headline is rejected",
          any("every headline" in e for e in errs(bad)), errs(bad))
    check("an empty rule list is rejected",
          any("nothing can ever alert" in e
              for e in errs({"categories": {}, "rules": []})))

    print("\n== a refusal says WHICH gate closed ==")
    # "no rule accepted" covered nine different conditions. It is the line the
    # dashboard shows next to most headlines, so it has to name the gate - and
    # with several rules, the one the headline came CLOSEST to passing, since
    # that is the rule worth editing.
    def why(rules, headline, **kw):
        return fs(rules, **kw).evaluate(headline, hits(headline)).reason

    plain = "Pemerintah Rilis Aturan Baru Soal Tarif"
    check("a headline with no ticker says exactly that",
          "no ticker in the headline" in why(rule(name="cov"), plain),
          why(rule(name="cov"), plain))
    check("a match below the floor reports both numbers",
          "too weak" in why(rule(min_confidence=0.99), H_DIVIDEN)
          and "0.99" in why(rule(min_confidence=0.99), H_DIVIDEN),
          why(rule(min_confidence=0.99), H_DIVIDEN))
    check("a ticker outside the rule's list is named, and the LIST is named",
          "not one of this rule's 1 tickers" in why(rule(tickers=["ZZZZ"]),
                                                    H_DIVIDEN),
          why(rule(tickers=["ZZZZ"]), H_DIVIDEN))
    check("and an empty ticker list never produces that reason at all",
          "not one of" not in why(rule(tickers=[]), H_DIVIDEN),
          why(rule(tickers=[]), H_DIVIDEN))
    check("an excluded category names the category",
          "excluded category" in why(
              rule(categories_exclude=["csr"]),
              "BBCA Salurkan Bantuan CSR ke Sekolah"),
          why(rule(categories_exclude=["csr"]),
              "BBCA Salurkan Bantuan CSR ke Sekolah"))
    check("a required word that is missing says so",
          "requires" in why(rule(require=["akuisisi"]), H_DIVIDEN),
          why(rule(require=["akuisisi"]), H_DIVIDEN))
    check("the wrong source is named as such",
          "sources" in why(rule(sources=["kontan"]), H_DIVIDEN),
          why(rule(sources=["kontan"]), H_DIVIDEN))
    check("and every reason carries the rule's name, so you know what to edit",
          why(rule(name="My coverage"), plain).startswith("My coverage:"),
          why(rule(name="My coverage"), plain))

    far = rule(name="near", min_confidence=0.99)
    close = rule(name="far", sources=["nowhere"])
    check("with two rules, the one that got furthest is reported",
          "near" in why([close, far], H_DIVIDEN), why([close, far], H_DIVIDEN))

    print("\n== describe() for the Options list ==")
    s = F.describe(rule(tickers=["BBCA", "BBRI"], categories=["ma"],
                        any_of=["akuisisi*"], exclude=["rumor"]))
    check("describe names every populated field",
          all(x in s for x in ("2 tickers", "ma", "akuisisi*", "rumor")), s)
    check("describe says 'any ticker' when the list is empty",
          "any ticker" in F.describe(rule()), F.describe(rule()))

    print("\n%d checks failed" % len(failures) if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
