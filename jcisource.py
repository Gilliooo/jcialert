#!/usr/bin/env python3
"""
jcisource - turn what each site serves into a common Item.

Fetching and parsing are deliberately separate: `parse()` takes bytes, so every
adapter is regression-tested offline against real captured bytes in fixtures/.
Nothing here needs the network, which matters because none of these sites are
reachable from where this code gets edited.

WHAT THE CAPTURED BYTES TAUGHT US (fixtures captured 2026-09-05)
----------------------------------------------------------------
1. **Kontan publishes no <guid> at all** - 0 of 25 items. Every other feed has
   one. So the id fallback chain is not theoretical; Kontan needs it on every
   single item.

2. **Liputan6 puts a SECOND <title> directly inside <item>** (flattened media
   metadata: title, copyright, text, description all repeat). The article title
   is the first one. Anything that does "find a title" without meaning "the
   first direct child" will silently pick an image caption.

3. Useful ids where they exist: IDX Channel `<idnews>` (394643), Liputan6
   `<guid isPermaLink="false">` (8285347), Kompas's 9-digit URL id.

Standard library only.
"""

import hashlib
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

WIB = timezone(timedelta(hours=7))


class Item:
    __slots__ = ("source", "id", "title", "url", "published", "feed_category")

    def __init__(self, source, id, title, url, published=None,
                 feed_category=""):
        self.source, self.id, self.title, self.url = source, id, title, url
        self.published = published
        self.feed_category = feed_category

    def __repr__(self):
        return f"Item({self.source}, {self.id}, {self.title[:44]!r})"


def _sha(*parts):
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


class Source:
    """One place to read from. Subclasses implement parse(body) -> [Item]."""

    kind = "rss"

    def __init__(self, name, url, enabled=True,
                 timeout=15, stale_minutes_day=120, stale_minutes_night=600,
                 every_n_ticks=1):
        self.name, self.url, self.enabled = name, url, enabled
        self.timeout = timeout
        # 24/7 operation means a FLAT staleness threshold turns the tray icon
        # blue every night. 90 minutes of silence at 11:00 WIB is a broken
        # source; at 03:00 it is Tuesday. See the project notes.
        self.stale_minutes_day = stale_minutes_day
        self.stale_minutes_night = stale_minutes_night
        # Poll only every Nth tick. Sources are fetched in SEQUENCE (one
        # in-flight request, as in IDXAlert), so a slow or heavy one is
        # everyone's problem unless it is polled less often - which is what
        # keeps Kompas's 198 KB index page off every tick.
        self.every_n_ticks = max(1, int(every_n_ticks or 1))

    def due(self, tick):
        return tick % self.every_n_ticks == 0

    def parse(self, body):
        raise NotImplementedError

    # Daytime, for the day/night staleness thresholds.
    PUBLISH_START, PUBLISH_END = 7, 19

    def in_publishing_window(self, now=None):
        now = (now or datetime.now(timezone.utc)).astimezone(WIB)
        return now.weekday() < 5 and self.PUBLISH_START <= now.hour < self.PUBLISH_END

    def stale_after(self, now=None):
        """Minutes of silence that mean something is wrong."""
        return (self.stale_minutes_day if self.in_publishing_window(now)
                else self.stale_minutes_night)

    def freshness(self, items, now=None):
        """(newest_datetime|None, minutes_old|None, is_stale)."""
        now = now or datetime.now(timezone.utc)
        stamped = [i.published for i in items if i.published]
        if not stamped:
            return None, None, False        # cannot judge; not an error
        newest = max(stamped)
        mins = (now - newest).total_seconds() / 60.0
        limit = self.stale_after(now)
        return newest, mins, (limit is not None and mins > limit)


# ---------------------------------------------------------------------- RSS

_DC = "{http://purl.org/dc/elements/1.1/}"


def _first_child_text(item, tag):
    """The FIRST direct child with this tag.

    Liputan6 repeats <title>/<description> inside <item> for its media
    metadata, so "the first direct child" is load-bearing, not pedantry.
    """
    for c in item:
        if c.tag.split("}")[-1] == tag:
            return (c.text or "").strip()
    return ""


def parse_rss_date(raw):
    if not raw:
        return None
    try:
        d = parsedate_to_datetime(raw)
    except Exception:
        try:
            d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return None
    return d if d.tzinfo else d.replace(tzinfo=WIB)


class RssSource(Source):
    """Generic RSS 2.0. Handles every feed captured on 2026-09-05."""

    kind = "rss"

    # Feeds that carry a clean numeric id somewhere other than <guid>.
    ID_TAGS = ("idnews", "postid", "articleid")

    def parse(self, body):
        root = ET.fromstring(body)
        out = []
        for it in root.findall(".//item"):
            title = _first_child_text(it, "title")
            if not title:
                continue
            link = _first_child_text(it, "link")
            published = parse_rss_date(_first_child_text(it, "pubDate")
                                       or it.findtext(_DC + "date"))
            out.append(Item(
                source=self.name,
                id=self._identify(it, title, link),
                title=title,
                url=link,
                published=published,
                feed_category=_first_child_text(it, "category"),
            ))
        return out

    def _identify(self, it, title, link):
        """guid, else a publisher id tag, else link, else a title hash.

        The last branch is not defensive padding: Kontan ships ZERO guids.
        """
        for tag in self.ID_TAGS:
            v = _first_child_text(it, tag)
            if v:
                return f"{self.name}:{v}"
        guid = _first_child_text(it, "guid")
        if guid:
            return f"{self.name}:{guid}"
        if link:
            return f"{self.name}:{link}"
        return f"{self.name}:{_sha(self.name, _norm_title(title))}"


# ---------------------------------------------------------------------- HTML

# Kompas's index page. The anchor WRAPS the headline, so the title is the
# first articleTitle heading after each /read/ link and before that anchor
# closes. Measured against probe/kompas-money-html.html, 2026-09-14: 27 unique
# articles, every title in an h1 or h2, no JS needed.
KO_LINK = re.compile(
    r'<a\s[^>]*href="(https?://[^"]*?/read/(\d{4})/(\d{2})/(\d{2})/(\d{6,})/'
    r'[^"#?]*)"[^>]*>(.*?)</a>', re.S | re.I)
# TWO class names, not one. The "Terpopuler" sidebar uses mostTitle, and
# skipping it dropped `Usai Borong, Morgan Stanley Jual Saham GOTO Rp 187
# Miliar` - a real emiten headline sitting in the most-read list. A link with
# NO heading inside it is still skipped, which is what keeps the 2019
# self-promo tile in the footer out.
KO_TITLE = re.compile(r'class="(?:articleTitle|mostTitle)"[^>]*>(.*?)</h\d',
                      re.S | re.I)
TAG = re.compile(r"<[^>]+>")


class KompasSource(Source):
    """money.kompas.com, scraped from its index page.

    NO RSS EXISTS ANY MORE. The three classic paths 404 and the feeds moved to
    an `rss.kompas.com` service whose root announces itself as "API Feed
    Social" and whose every endpoint 403s - a partner API, not a public feed.
    The HTML index is the only way in, and it is plain server-rendered markup.

    THE PUBLICATION TIME IS IN THE URL, which is the reason this source is
    worth having at all. `/read/2026/09/14/113756426/slug` is 11:37:56 WIB on
    the 14th, to the second. The page itself only says "20 jam lalu", and a
    relative stamp cannot drive an age cutoff - it would have made every
    article look as old as the moment we happened to read the page. The same
    9-digit id is also a natural dedup key.
    """

    kind = "html"

    def parse(self, body):
        html = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
        out, seen = [], set()
        for url, y, mo, d, num, inner in KO_LINK.findall(html):
            if url in seen:
                continue
            m = KO_TITLE.search(inner)
            if not m:
                continue
            title = TAG.sub("", m.group(1))
            title = re.sub(r"\s+", " ", title).replace("&amp;", "&").strip()
            if not title:
                continue
            seen.add(url)

            # HHMMSS + a counter. Anything that does not parse as a clock is
            # left undated rather than guessed at - an invented timestamp is
            # worse than none, because the age cutoff would act on it.
            try:
                published = datetime(int(y), int(mo), int(d), int(num[0:2]),
                                     int(num[2:4]), int(num[4:6]), tzinfo=WIB)
            except ValueError:
                published = None

            out.append(Item(self.name, f"{self.name}:{num}", title, url,
                            published))
        return out


# ------------------------------------------------------------------ registry

def build_sources(tickers=(), cfg=None):
    """The launch set, with everything the fixtures proved about each.

    Deliberately small. More sources means more duplicates for the clusterer
    to get right and more chances a stale feed goes unnoticed - and two of the
    feeds probed on 2026-09-05 returned a clean 200 over dead content.
    """
    s = [
        RssSource("katadata", "https://katadata.co.id/rss"),
        RssSource("kontan-investasi", "https://investasi.kontan.co.id/rss"),
        RssSource("idxchannel", "https://www.idxchannel.com/rss",
                  stale_minutes_day=240, stale_minutes_night=900),

        # Available and working, off by default until a weekday run shows
        # whether their emiten density justifies the extra duplicates.
        RssSource("cnbc-market", "https://www.cnbcindonesia.com/market/rss",
                  enabled=False),
        RssSource("kontan-keuangan", "https://keuangan.kontan.co.id/rss",
                  enabled=False),
        RssSource("liputan6-bisnis", "https://feed.liputan6.com/rss/bisnis",
                  enabled=False),
        RssSource("detik-finance", "https://finance.detik.com/rss", enabled=False),
        RssSource("wartaekonomi", "https://wartaekonomi.co.id/rss", enabled=False),

        # ---- ASKED FOR, NOT YET MEASURED. Both are OFF, and the reason is
        # not caution for its own sake: a source wired at a guessed URL either
        # fails every tick (a red icon that means nothing) or, worse, parses
        # something that is not the feed and looks like it works.
        #
        # bisnis.com   403'd from Gill's own residential IP on 2026-09-05, on
        #              BOTH of these paths, with the full Chrome header set -
        #              so a trimmed header list does not explain it.
        # kompas       the three paths probed on 09-05 all 404'd, but they
        #              were the old scheme; the feeds moved to rss.kompas.com.
        #
        # `python probe_sources.py --only bisnis,kompas` settles both. The
        # winning URL goes in config under sources -> <name> -> url, so
        # neither of these needs a rebuild to come alive.
        # Kompas has no RSS any more - see KompasSource. Scraped from the
        # index page, which is 198 KB, so every third tick rather than every
        # one: 60s polling would be a quarter of a gigabyte a day for a feed
        # that publishes a few times an hour.
        KompasSource("kompas-money", "https://money.kompas.com/",
                     enabled=False, every_n_ticks=3,
                     stale_minutes_day=240, stale_minutes_night=900),
    ]
    over = (cfg or {}).get("sources") or {}
    for src in s:
        row = over.get(src.name)
        if not row:
            continue
        src.enabled = bool(row.get("enabled", src.enabled))
        # A FEED URL CAN BE OVERRIDDEN FROM CONFIG. Publishers move their RSS
        # paths (Kompas already has, Liputan6 serves only one of three), and
        # before this the only fix was a rebuild. It is also how a source
        # measured after release gets wired in without shipping a new exe.
        if str(row.get("url") or "").strip():
            src.url = str(row["url"]).strip()
    return s


# Sources deliberately NOT here, and why:
#   iqplus-*         REMOVED at Gill's request, 2026-09-14, after being
#                    unreachable from his PC since 09-08 (WinError 10060) and
#                    costing a 45s timeout per attempt while dead. Its parser
#                    went with it on 09-23: it was the only source that was
#                    ALL-CAPS and the only one carrying tickers in its URL
#                    slug, and keeping a dead source alive kept four other
#                    mechanisms alive with it.
#   okezone          clean 200 over content from July 2016
#   tempo            clean 200, 12 days stale
#   bisnis.com       BLOCKED, confirmed twice. 2026-09-05: 403 on the two RSS
#                    paths. 2026-09-14: 403 on EIGHT paths - rss, root, index,
#                    market html - with the full Chrome header set AND a
#                    same-site Referer retry. Whatever they run refuses this
#                    client outright, and there is no header left to try.
#                    Not registered: an entry that can never be ticked on is
#                    clutter in the Options list, not an option.
#   idnfinancials    403; was the only English source
#   kompas RSS       gone. money.kompas.com/rss and the two /rss/ variants
#                    404; rss.kompas.com announces itself as "API Feed Social"
#                    and 403s every endpoint - a partner API. The HTML index
#                    works, so KompasSource scrapes that instead.
