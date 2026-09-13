# ============================================================
# anime_scraper.py
# Scraper + formatter for Anime Hindi Dub Bot
#
# The single source of truth for the source website lives in
# config.py (SOURCE_BASE_URL). This module only owns the
# scraping + presentation logic.
# ============================================================

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus, urljoin

import aiohttp
from bs4 import BeautifulSoup, Tag

from config import REQUEST_TIMEOUT, SOURCE_BASE_URL as BASE_URL

logger = logging.getLogger("anime_hindi_dub_bot.scraper")

# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------
SEARCH_URL = BASE_URL + "/?s={query}"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)

CACHE_DIR = Path("anime_cache")
try:
    CACHE_DIR.mkdir(exist_ok=True)
except OSError as exc:
    logger.warning("Cannot create cache directory %s: %s", CACHE_DIR, exc)

ONGOING_CACHE_TTL = 5 * 60          # 5 minutes
COMPLETED_CACHE_TTL = 24 * 60 * 60  # 24 hours

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}

# ------------------------------------------------------------
# Small-caps helper (Telegram "card" aesthetic)
# ------------------------------------------------------------
_SMALL_CAPS_MAP = str.maketrans({
    "a": "ᴀ", "b": "ʙ", "c": "ᴄ", "d": "ᴅ", "e": "ᴇ", "f": "ғ", "g": "ɢ",
    "h": "ʜ", "i": "ɪ", "j": "ᴊ", "k": "ᴋ", "l": "ʟ", "m": "ᴍ", "n": "ɴ",
    "o": "ᴏ", "p": "ᴘ", "q": "ǫ", "r": "ʀ", "s": "s", "t": "ᴛ", "u": "ᴜ",
    "v": "ᴠ", "w": "ᴡ", "x": "x", "y": "ʏ", "z": "ᴢ",
})


def sc(text: str) -> str:
    """Convert a-z to Unicode small caps (e.g. Season -> Sᴇᴀsᴏɴ)."""
    return str(text or "").translate(_SMALL_CAPS_MAP)


# ------------------------------------------------------------
# Dataclasses
# ------------------------------------------------------------

@dataclass
class Episode:
    number: int
    title: str = ""
    languages: list[str] = field(default_factory=list)
    release_date: Optional[str] = None


@dataclass
class SearchCandidate:
    title: str
    url: str
    score: float = 0.0


@dataclass
class AnimeInfo:
    title: str = ""
    canonical_title: str = ""
    aliases: list[str] = field(default_factory=list)
    poster_url: Optional[str] = None
    source_url: Optional[str] = None
    url: Optional[str] = None  # kept for formatter compatibility
    source: str = "DC"
    hindi_available: bool = False
    platform: list[str] = field(default_factory=list)
    season: Optional[int] = None
    total_episodes: Optional[int] = None
    available_episodes: dict[str, int] = field(default_factory=dict)
    languages: list[str] = field(default_factory=list)
    status: str = "unknown"
    last_episode: Optional[int] = None
    last_release: Optional[str] = None
    next_episode: Optional[int] = None
    expected_release: Optional[str] = None
    schedule: Optional[str] = None
    studio: Optional[str] = None
    dub_by: Optional[str] = None
    release_year: Optional[int] = None
    runtime: Optional[str] = None
    genres: list[str] = field(default_factory=list)
    synopsis: Optional[str] = None
    episodes: list[Episode] = field(default_factory=list)
    # Franchise / multi-series support
    franchise: Optional[str] = None
    franchise_key: Optional[str] = None
    franchise_series: list[AnimeInfo] = field(default_factory=list)
    franchise_movies: list[str] = field(default_factory=list)
    series_type: str = "series"
    related_series: list[str] = field(default_factory=list)
    movies: list[str] = field(default_factory=list)
    seasons: list[str] = field(default_factory=list)
    scraped_at: float = field(default_factory=time.time)


# ------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------
class AnimeNotFound(Exception):
    pass


class ScraperError(Exception):
    pass


# ============================================================
# HTTP / CACHE / TEXT UTILITIES
# ============================================================

async def create_session() -> aiohttp.ClientSession:
    """Create a shared aiohttp session with sane defaults."""
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    connector = aiohttp.TCPConnector(limit=10, limit_per_host=5, ssl=False)
    return aiohttp.ClientSession(
        headers=HEADERS,
        timeout=timeout,
        connector=connector,
    )


async def fetch(session: aiohttp.ClientSession, url: str) -> str:
    """Fetch a URL; raises ScraperError on any failure."""
    logger.info("Fetching: %s", url)
    request_timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    try:
        async with session.get(
            url, allow_redirects=True, timeout=request_timeout
        ) as response:
            if response.status != 200:
                raise ScraperError(f"HTTP {response.status}: {url}")
            return await response.text(errors="ignore")
    except asyncio.TimeoutError:
        raise ScraperError(f"Timeout: {url}")
    except aiohttp.ClientError as exc:
        raise ScraperError(f"Request failed: {url} -> {exc}")


def clean_text(value: Any) -> str:
    """Normalize whitespace / HTML entities to a single clean string."""
    if value is None:
        return ""
    text = str(value)
    text = html.unescape(text)
    text = text.replace("\xa0", " ").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_title(title: str) -> str:
    title = clean_text(title).lower()
    title = re.sub(r"[^\w\s]", " ", title, flags=re.UNICODE)
    title = re.sub(r"\s+", " ", title)
    noise = {
        "season", "hindi", "dubbed", "dub", "episodes", "episode",
        "download", "hd", "watch", "online", "full", "complete",
    }
    words = [w for w in title.split() if w not in noise]
    return " ".join(words).strip()


def title_match_score(query: str, title: str) -> float:
    q = normalize_title(query)
    t = normalize_title(title)
    if not q or not t:
        return 0.0
    if q == t:
        return 100.0
    if q in t:
        return 90.0
    if t in q:
        return 80.0
    q_words = set(q.split())
    t_words = set(t.split())
    if not q_words or not t_words:
        return 0.0
    overlap = len(q_words & t_words)
    return (overlap / len(q_words)) * 70.0


def normalize_slug(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


# ------------------------------------------------------------
# Main-content isolation
# ------------------------------------------------------------

def get_main_content(soup: BeautifulSoup) -> Tag:
    """Return the main article/post container only.

    Sidebar widgets, related posts, pagination and other anime
    cards live OUTSIDE this container and are the root cause of
    garbage seasons/episodes/movies.
    """
    for selector in (
        ".entry-content", ".post-content", ".single-content",
        "article .content", "article", "main", ".content-area",
    ):
        node = soup.select_one(selector)
        if node is not None:
            return node
    return soup


# ------------------------------------------------------------
# Cache helpers (file-based, saved per URL)
# ------------------------------------------------------------

def cache_file(url: str) -> Path:
    key = normalize_slug(url) or "home"
    return CACHE_DIR / f"{key[:180]}.json"


def read_cache(url: str) -> Optional[dict]:
    path = cache_file(url)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        timestamp = data.get("timestamp", 0)
        ttl = data.get("ttl", ONGOING_CACHE_TTL)
        if time.time() - timestamp > ttl:
            return None
        return data
    except Exception:
        return None


def write_cache(url: str, html_text: str, ttl: int) -> None:
    path = cache_file(url)
    payload = {
        "timestamp": time.time(),
        "ttl": ttl,
        "html": html_text,
    }
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Cache write failed: %s", exc)


async def fetch_cached(
    session: aiohttp.ClientSession,
    url: str,
    ttl: int = ONGOING_CACHE_TTL,
) -> str:
    cached = read_cache(url)
    if cached:
        logger.info("CACHE HIT: %s", url)
        return cached["html"]
    html_text = await fetch(session, url)
    write_cache(url, html_text, ttl)
    return html_text


# ============================================================
# SEARCH + TITLE RESOLVER
# ============================================================

def parse_search_results(html_text: str, query: str) -> list[SearchCandidate]:
    soup = BeautifulSoup(html_text, "html.parser")
    candidates: list[SearchCandidate] = []
    selectors = [
        "article a", ".post a", ".item a", ".anime a",
        "h2 a", "h3 a",
    ]
    seen: set[str] = set()
    for selector in selectors:
        for tag in soup.select(selector):
            href = tag.get("href")
            title = clean_text(tag.get_text(" ", strip=True))
            if not href or not title:
                continue
            href = urljoin(BASE_URL, href)
            if href in seen:
                continue
            seen.add(href)
            score = title_match_score(query, title)
            candidates.append(
                SearchCandidate(title=title, url=href, score=score)
            )
    return candidates


async def search_anime(
    session: aiohttp.ClientSession,
    query: str,
) -> list[SearchCandidate]:
    query = clean_text(query)
    if not query:
        return []
    encoded = quote_plus(query)
    url = SEARCH_URL.format(query=encoded)
    html_text = await fetch_cached(session, url, ttl=ONGOING_CACHE_TTL)
    candidates = parse_search_results(html_text, query)
    unique_c: dict[str, SearchCandidate] = {}
    for candidate in candidates:
        unique_c[candidate.url] = candidate
    candidates = list(unique_c.values())
    candidates.sort(key=lambda x: x.score, reverse=True)
    logger.info("Search results for %r: %d", query, len(candidates))
    return candidates


async def find_anime_page(
    session: aiohttp.ClientSession,
    query: str,
) -> Optional[SearchCandidate]:
    candidates = await search_anime(session, query)
    if not candidates:
        logger.warning("No search results found for %r", query)
        return None
    for candidate in candidates:
        if candidate.score >= 90:
            logger.info("Strong match: %s (%s)", candidate.title, candidate.url)
            return candidate
    best = candidates[0]
    logger.info("Best match: %s (score %.1f)", best.title, best.score)
    return best


# ============================================================
# FRANCHISE DEFINITIONS
# ============================================================
FRANCHISE_SERIES = {
    "dragon ball": [
        "Dragon Ball", "Dragon Ball Z", "Dragon Ball GT",
        "Dragon Ball Super", "Dragon Ball DAIMA",
    ],
    "naruto": ["Naruto", "Naruto Shippuden"],
    "one piece": ["One Piece"],
    "bleach": ["Bleach", "Bleach Thousand-Year Blood War"],
    "pokemon": [
        "Pokémon", "Pokémon Indigo League", "Pokémon Advanced",
        "Pokémon Diamond and Pearl", "Pokémon Black and White",
        "Pokémon XY", "Pokémon Sun and Moon", "Pokémon Journeys",
        "Pokémon Horizons",
    ],
    "digimon": [
        "Digimon Adventure", "Digimon Adventure 02", "Digimon Tamers",
        "Digimon Frontier", "Digimon Data Squad", "Digimon Fusion",
        "Digimon Adventure tri.", "Digimon Ghost Game",
    ],
    "yu gi oh": [
        "Yu-Gi-Oh!", "Yu-Gi-Oh! GX", "Yu-Gi-Oh! 5D's",
        "Yu-Gi-Oh! ZEXAL", "Yu-Gi-Oh! ARC-V", "Yu-Gi-Oh! VRAINS",
    ],
}

FRANCHISE_MOVIES = {
    "dragon ball": [
        "Dragon Ball Z: Dead Zone",
        "Dragon Ball Z: The World's Strongest",
        "Dragon Ball Z: The Tree of Might",
        "Dragon Ball Z: Lord Slug",
        "Dragon Ball Z: Cooler's Revenge",
        "Dragon Ball Z: Return of Cooler",
        "Dragon Ball Z: Super Android 13",
        "Dragon Ball Z: Broly",
        "Dragon Ball Z: Bojack Unbound",
        "Dragon Ball Z: Broly Second Coming",
        "Dragon Ball Z: Bio-Broly",
        "Dragon Ball Z: Fusion Reborn",
        "Dragon Ball Z: Wrath of the Dragon",
        "Dragon Ball Super: Broly",
        "Dragon Ball Super: Super Hero",
    ],
    "naruto": [
        "Naruto the Movie: Ninja Clash in the Land of Snow",
        "Naruto the Movie: Legend of the Stone of Gelel",
        "Naruto the Movie: Guardians of the Crescent Moon Kingdom",
        "Naruto Shippuden the Movie",
        "Naruto Shippuden the Movie: Bonds",
        "Naruto Shippuden the Movie: The Will of Fire",
        "Naruto Shippuden the Movie: The Lost Tower",
        "Naruto Shippuden the Movie: Blood Prison",
        "Road to Ninja: Naruto the Movie",
        "The Last: Naruto the Movie",
        "Boruto: Naruto the Movie",
    ],
    "bleach": [
        "Bleach the Movie: Memories of Nobody",
        "Bleach the Movie: The DiamondDust Rebellion",
        "Bleach the Movie: Fade to Black",
        "Bleach the Movie: Hell Verse",
    ],
}


# ============================================================
# ANIME SCRAPER CLASS
# ============================================================
class AnimeScraper:
    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self.session = session
        self._own_session = session is None

    async def __aenter__(self):
        if self.session is None:
            self.session = await create_session()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._own_session and self.session:
            await self.session.close()

    async def ensure_session(self) -> None:
        if self.session is None:
            self.session = await create_session()

    # --------------------------------------------------------
    # Scrape one anime
    # --------------------------------------------------------
    async def scrape_single(self, query: str) -> Optional[AnimeInfo]:
        await self.ensure_session()
        logger.info("Scraping anime: %s", query)
        candidate = await find_anime_page(self.session, query)
        if not candidate:
            return None

        # Status-aware caching: completed anime can stay cached for a
        # long time, ongoing anime must be re-fetched quickly.
        try:
            cached = read_cache(candidate.url)
            if cached:
                html_text: str = cached["html"]
            else:
                html_text = await fetch(self.session, candidate.url)
                write_cache(candidate.url, html_text, ONGOING_CACHE_TTL)
        except Exception as exc:
            logger.error("Failed to fetch anime page: %s", exc)
            return None

        try:
            anime = parse_anime_page(html_text, candidate.url, candidate.title)
            if anime is not None and (anime.status or "").lower() == "completed":
                write_cache(candidate.url, html_text, COMPLETED_CACHE_TTL)
        except Exception as exc:
            logger.exception("Anime page parsing failed: %s", exc)
            return None
        return anime

    # --------------------------------------------------------
    # Scrape franchise
    # --------------------------------------------------------
    async def scrape_franchise(
        self,
        query: str,
        franchise_key: str,
    ) -> Optional[AnimeInfo]:
        await self.ensure_session()
        logger.info("Scraping franchise: %s", franchise_key)

        series = await scrape_franchise_series(self, franchise_key)
        movies = await scrape_franchise_movies(self, franchise_key)

        if not series and not movies:
            logger.warning("No Hindi franchise data found: %s", franchise_key)
            return None

        return build_franchise_info(
            query=query,
            franchise_key=franchise_key,
            series=series,
            movies=movies,
        )

    # --------------------------------------------------------
    # Main search method
    # --------------------------------------------------------
    async def search(self, query: str) -> Optional[AnimeInfo]:
        query = clean_text(query)
        if not query:
            return None
        normalized = normalize_title(query)

        franchise_key = None
        for key in FRANCHISE_SERIES:
            nk = normalize_title(key)
            if normalized == nk or nk in normalized or normalized in nk:
                franchise_key = key
                break

        if franchise_key:
            logger.info("Known franchise detected: %s", franchise_key)
            try:
                franchise = await self.scrape_franchise(query, franchise_key)
                if franchise:
                    return franchise
            except Exception as exc:
                logger.exception("Franchise scrape failed: %s", exc)

        return await self.scrape_single(query)


# ============================================================
# FRANCHISE HELPERS
# ============================================================
async def scrape_franchise_series(
    scraper: AnimeScraper,
    franchise_key: str,
) -> list[AnimeInfo]:
    titles = FRANCHISE_SERIES.get(franchise_key, [])
    results: list[AnimeInfo] = []
    for title in titles:
        try:
            info = await scraper.scrape_single(title)
            if not info:
                continue
            if not info.hindi_available:
                logger.info("Skipping non-Hindi franchise series: %s", title)
                continue
            results.append(info)
        except Exception as exc:
            logger.warning("Franchise series failed: %s -> %s", title, exc)
    return results


async def scrape_franchise_movies(
    scraper: AnimeScraper,
    franchise_key: str,
) -> list[str]:
    titles = FRANCHISE_MOVIES.get(franchise_key, [])
    movies: list[str] = []
    for title in titles:
        try:
            info = await scraper.scrape_single(title)
            if not info or not info.hindi_available:
                continue
            movie_title = info.title.strip() if info.title else title
            if movie_title not in movies:
                movies.append(movie_title)
        except Exception as exc:
            logger.warning("Franchise movie failed: %s -> %s", title, exc)
    return movies


def build_franchise_info(
    query: str,
    franchise_key: str,
    series: list[AnimeInfo],
    movies: list[str],
) -> AnimeInfo:
    franchise_name = query.strip()
    total_episodes = sum((a.total_episodes or 0) for a in series)
    hindi_total = sum(
        (a.available_episodes.get("Hindi", 0) if a.available_episodes else 0)
        for a in series
    )

    if series:
        base = series[0]
        base.title = franchise_name
        base.franchise_key = franchise_key
        base.franchise_series = series
        base.franchise_movies = movies
        base.total_episodes = total_episodes
        base.available_episodes = {"Hindi": hindi_total}
        return base

    return AnimeInfo(
        title=franchise_name,
        franchise_key=franchise_key,
        franchise_series=[],
        franchise_movies=movies,
        hindi_available=bool(movies),
        total_episodes=total_episodes,
        available_episodes={"Hindi": hindi_total},
    )


# ============================================================
# PAGE PARSING
# ============================================================
def parse_anime_page(
    html_text: str,
    url: str,
    fallback_title: str = "",
) -> Optional[AnimeInfo]:
    """Parse an anime detail page.

    Everything is read from the MAIN CONTENT container first.
    Sidebar widgets, related posts and pagination are ignored so
    they cannot leak garbage seasons / episodes / movies in.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    main = get_main_content(soup)
    text = clean_text(main.get_text("\n", strip=True))

    title = extract_title(soup, fallback_title)
    poster_url = extract_poster(soup, url)

    total_episodes = extract_total_episodes(text)
    hindi_available = detect_hindi(text)
    status = detect_status(text)
    seasons = extract_seasons(text)
    episodes = extract_episode_data(main)
    hindi_count = extract_hindi_episode_count(text, episodes)
    available_episodes = {"Hindi": hindi_count}
    movies = extract_movies(main, text)
    genres = extract_genres(main)
    languages = extract_languages(main, episodes)

    return AnimeInfo(
        title=title,
        url=url,
        source_url=url,
        poster_url=poster_url,
        total_episodes=total_episodes,
        available_episodes=available_episodes,
        hindi_available=hindi_available,
        status=status,
        episodes=episodes,
        seasons=seasons,
        movies=movies,
        genres=genres,
        languages=languages,
        source="DC",
    )


def clean_anime_title(value: str) -> str:
    """Strip site-noise suffixes like "... Hindi Dubbed Episodes Download HD".

    Keeps display titles clean without touching legitimate anime names.
    """
    title = clean_text(value)
    for _ in range(3):
        cleaned = re.sub(
            r"(?i)\s+(?:hindi\s+dubbed?\s*)?"
            r"(?:episodes?|series)?\s*"
            r"(?:download|watch|stream)\s*(?:online\s+)?(?:in\s+)?"
            r"(?:hd|480p|720p|1080p|full\s*hd)?\s*$",
            "",
            title,
        )
        cleaned = re.sub(r"(?i)\s+hindi\s+dubbed?\s*$", "", cleaned)
        cleaned = re.sub(r"[\s\-|:]+$", "", cleaned)
        if cleaned == title:
            break
        title = cleaned
    return title


def extract_title(soup: BeautifulSoup, fallback_title: str = "") -> str:
    main = get_main_content(soup)
    for selector in ("h1", ".entry-title", ".anime-title", ".post-title"):
        node = main.select_one(selector)
        if not node:
            continue
        value = clean_text(node.get_text(" ", strip=True))
        if value:
            return clean_anime_title(value)
    og = soup.select_one("meta[property='og:title']")
    if og:
        value = clean_text(og.get("content", ""))
        if value:
            return clean_anime_title(value)
    return fallback_title


def extract_poster(soup: BeautifulSoup, url: str) -> Optional[str]:
    main = get_main_content(soup)
    for selector in (
        ".poster img", ".anime-poster img",
        ".post-thumbnail img", ".thumbnail img",
    ):
        node = main.select_one(selector)
        if not node:
            continue
        image = (
            node.get("src")
            or node.get("data-src")
            or node.get("data-lazy-src")
        )
        if image:
            return urljoin(url, image)
    og = soup.select_one("meta[property='og:image']")
    if og and og.get("content"):
        return urljoin(url, og["content"])
    img = main.select_one("img")
    if img:
        image = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        if image:
            return urljoin(url, image)
    return None


# ------------------------------------------------------------
# Total episode extraction
# ------------------------------------------------------------
def extract_total_episodes(text: str) -> int:
    """Return a sane total-episode count or 0.

    Only explicit labelled patterns are accepted; the loose
    `(\\d+)\\s*episodes?` fallback is GONE because it matched page
    counters and other cards (the "1981 episodes" bug).
    """
    patterns = [
        r"total\s*episodes?\s*[:\-]?\s*(\d{1,4})",
        r"no\.?\s*of\s*episodes?\s*[:\-]?\s*(\d{1,4})",
        r"episode\s*count\s*[:\-]?\s*(\d{1,4})",
        r"episodes?\s*[:\-]\s*(\d{1,4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                value = int(match.group(1))
            except (TypeError, ValueError):
                continue
            if 1 <= value <= 3000:
                return value
    return 0


# ------------------------------------------------------------
# Hindi detection / status
# ------------------------------------------------------------
def detect_hindi(text: str) -> bool:
    if not text:
        return False
    patterns = [
        r"\bhindi\b",
        r"\bhindi dubbed\b",
        r"\bhindi dub\b",
        r"\bdubbed in hindi\b",
        r"\blanguage\s*[:\-]?\s*hindi\b",
    ]
    return any(
        re.search(pattern, text, re.IGNORECASE)
        for pattern in patterns
    )


def detect_status(text: str) -> str:
    """Best-effort status detection from the main page content."""
    low = " " + text.lower() + " "
    if re.search(
        r"\b(currently\s+airing|airing|ongoing)\b", low, re.IGNORECASE
    ):
        return "ongoing"
    if re.search(r"\b(completed|finished|released)\b", low, re.IGNORECASE):
        return "completed"
    if re.search(r"\b(upcoming|coming\s+soon)\b", low, re.IGNORECASE):
        return "upcoming"
    return "unknown"


# ------------------------------------------------------------
# Season extraction — ONLY "Season N", formatted cleanly
# ------------------------------------------------------------
def extract_seasons(text: str, max_seasons: int = 20) -> list[str]:
    """Return only `Season N` labels in the 1..max_seasons range.

    `S1` / `S04` noise is dropped; page text is expected to come
    from the main content container only.
    """
    seasons: set[int] = set()
    for match in re.findall(r"\bseason\s+(\d{1,2})\b", text, re.IGNORECASE):
        number = int(match)
        if 1 <= number <= max_seasons:
            seasons.add(number)
    return [f"Season {number}" for number in sorted(seasons)]


# ------------------------------------------------------------
# Episode data extraction
# ------------------------------------------------------------
def extract_episode_data(container: Tag) -> list[Episode]:
    """Extract episodes from the main content ONLY.

    Nav / menu links like `Episodes` (no number) are skipped, so we
    never count the site navigation as episodes.
    """
    episodes: list[Episode] = []
    selectors = (
        ".episode", ".episodes a", ".episode-list a",
        ".ep-list a", ".episodelist a", "a[href*='episode']",
    )
    seen: set[tuple] = set()
    for selector in selectors:
        for node in container.select(selector):
            href = node.get("href") or ""
            title = clean_text(node.get_text(" ", strip=True))
            if len(title) < 4 and not href:
                continue
            key = (href, title)
            if key in seen:
                continue
            seen.add(key)
            number = extract_episode_number(title)
            if number <= 0:
                continue  # "Episode N" pattern required
            languages = detect_episode_languages(title)
            episodes.append(
                Episode(number=number, title=title, languages=languages)
            )

    # dedupe, then sort by episode number
    by_number: dict[int, Episode] = {}
    for episode in episodes:
        by_number.setdefault(episode.number, episode)
    return sorted(by_number.values(), key=lambda e: e.number)


def extract_episode_number(text: str) -> int:
    patterns = [
        r"\bepisode\s*(\d+)\b",
        r"\bep\.?\s*(\d+)\b",
        r"\bep\s*[-:]?\s*(\d+)\b",
        r"\bE(\d+)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                pass
    return 0


def detect_episode_languages(text: str) -> list[str]:
    languages: list[str] = []
    if re.search(r"\bhindi\b", text, re.IGNORECASE):
        languages.append("Hindi")
    if re.search(r"\benglish\b", text, re.IGNORECASE):
        languages.append("English")
    if re.search(r"\bjapanese\b", text, re.IGNORECASE):
        languages.append("Japanese")
    return languages


# ------------------------------------------------------------
# Hindi episode count (no more fake counts)
# ------------------------------------------------------------
def extract_hindi_episode_count(text: str, episodes: list[Episode]) -> int:
    """Prefer an explicit 'Hindi Episodes' field; otherwise count the
    Hindi episodes found in the real episode list on the page.
    """
    field_patterns = [
        # "Hindi Episodes: 7" / "Hindi Episodes - 8"
        r"hindi\s*(?:episodes?|eps?)\s*[:\-]\s*(\d{1,4})",
        # "Hindi Dub Episodes: 12"
        r"hindi\s*dub(?:bed)?\s*(?:episodes?|eps?)\s*[:\-]?\s*(\d{1,4})",
        # "6 Hindi Episodes"
        r"(\d{1,4})\s*hindi\s*(?:episodes?|eps?)\b",
    ]
    for pattern in field_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                value = int(match.group(1))
            except (TypeError, ValueError):
                continue
            if 1 <= value <= 3000:
                return value

    hindi_numbers = {
        episode.number
        for episode in episodes
        if "Hindi" in episode.languages and episode.number > 0
    }
    if hindi_numbers:
        return len(hindi_numbers)
    return 0


# ------------------------------------------------------------
# Movie extraction — main content only
# ------------------------------------------------------------
def extract_movies(soup: BeautifulSoup, text: str) -> list[str]:
    main = get_main_content(soup)
    movies: list[str] = []
    seen: set[str] = set()
    noise = {"movies", "movie", "all movies", "watch movie"}
    for node in main.select("a"):
        href = (node.get("href") or "").lower()
        title = clean_text(node.get_text(" ", strip=True))
        if not title or len(title) < 4 or title.lower() in noise:
            continue
        if "movie" not in href and not re.search(
            r"\bm\d+\b", title, re.IGNORECASE
        ):
            continue
        if title.lower() in seen:
            continue
        seen.add(title.lower())
        movies.append(title)
        if len(movies) >= 25:
            break
    return movies


# ------------------------------------------------------------
# Genres / languages
# ------------------------------------------------------------
def extract_genres(container: Tag) -> list[str]:
    genres: list[str] = []
    seen: set[str] = set()
    for node in container.select("a[href*='genre']"):
        genre = clean_text(node.get_text(" ", strip=True))
        if not genre or genre.lower() in seen:
            continue
        seen.add(genre.lower())
        genres.append(genre)
    return genres[:15]


def extract_languages(container: Tag, episodes: list[Episode]) -> list[str]:
    languages: list[str] = []
    seen: set[str] = set()

    def add(language: str) -> None:
        key = language.lower()
        if language and key not in seen:
            seen.add(key)
            languages.append(language)

    for episode in episodes:
        for language in episode.languages:
            add(language)

    text = clean_text(container.get_text(" ", strip=True)).lower()
    candidates = {
        "Hindi": [r"\bhindi\b"],
        "English": [r"\benglish\b"],
        "Japanese": [r"\bjapanese\b", r"\bjap\b"],
    }
    for language, patterns in candidates.items():
        if any(re.search(p, text) for p in patterns):
            add(language)
    return languages


# ============================================================
# FORMATTERS
# ============================================================
CARD_DIVIDER = "✦━━━━━━━━━━━━━━━━━━━━━━━━✦"


def format_status(status: str) -> str:
    """Format a status string into an emoji-prefixed label."""
    return {
        "completed": "✅ Completed",
        "ongoing": "🔄 Ongoing",
        "upcoming": "⏳ Upcoming",
        "unknown": "❔ Unknown",
    }.get((status or "").lower(), "❔ Unknown")


def _season_numbers(seasons: list[str]) -> list[str]:
    """Extract just the numbers from `Season N` labels.

    Strict: labels like `S1` / `S04` (old noise formats) are ignored
    so only real `Season N` entries count.
    """
    numbers: list[int] = []
    for season in seasons or []:
        match = re.match(
            r"season\s+(\d{1,2})\b",
            str(season).strip(),
            re.IGNORECASE,
        )
        if match:
            try:
                numbers.append(int(match.group(1)))
            except (TypeError, ValueError):
                continue
    return [str(number) for number in sorted(set(numbers))]


def format_episode(episode: Episode) -> str:
    number = episode.number
    title = episode.title.strip() if episode.title else ""
    if title:
        return f"Episode {number} — {title}"
    return f"Episode {number}"


def format_single_anime_info(anime: AnimeInfo) -> str:
    """Card-style result for a single anime. HTML-safe for captions."""
    title = html.escape((anime.title or "Unknown Anime").strip())

    season_line = ",".join(_season_numbers(getattr(anime, "seasons", None)))
    if not season_line and getattr(anime, "season", None):
        season_line = str(anime.season)
    season_line = season_line or "N/A"

    total = getattr(anime, "total_episodes", None)
    hindi_count = get_hindi_episode_count(anime)
    if total:
        if hindi_count > 0:
            episode_line = f"1-{total} (Hindi: {hindi_count})"
        else:
            episode_line = f"1-{total}"
    else:
        episode_line = "N/A"

    status = format_status(getattr(anime, "status", "unknown"))

    genres = ", ".join(
        html.escape(str(genre)) for genre in (getattr(anime, "genres", None) or [])
    ) or "N/A"

    languages = ", ".join(
        html.escape(str(lang)) for lang in (getattr(anime, "languages", None) or [])
    )

    lines = [
        f"◆ <b>{title}</b> ◆",
        CARD_DIVIDER,
        f"➥ {sc('Season')}:- {html.escape(season_line)}",
        f"➥ {sc('Episode')}:- {html.escape(episode_line)}",
        f"➥ {sc('Status')}:- {status}",
        f"➥ {sc('Genres')}:- {genres}",
    ]
    if languages:
        lines.append(f"➥ {sc('Audio')}:- {languages}")
    else:
        lines.append(f"➥ {sc('Audio')}:- N/A")

    movies = getattr(anime, "movies", None) or []
    movie_titles: list[str] = []
    for movie in movies:
        movie_title = (
            getattr(movie, "title", None) if not isinstance(movie, str) else movie
        )
        if movie_title:
            movie_titles.append(html.escape(str(movie_title).strip()))
    if movie_titles:
        capped = movie_titles[:20]
        if len(movie_titles) > 20:
            capped.append(f"…and {len(movie_titles) - 20} more")
        lines.append(f"➥ {sc('Movies')}:- {', '.join(capped)}")

    lines.append(CARD_DIVIDER)
    lines.append(f"⌬ {sc('Powered by')}:- Anime Hindi Dub Bot")
    return "\n".join(lines)


def format_franchise_info(anime: AnimeInfo) -> str:
    """Card-style result for a multi-series franchise."""
    franchise_title = html.escape((anime.title or "Anime Franchise").strip())

    season_line = ",".join(_season_numbers(getattr(anime, "seasons", None)))
    if not season_line:
        all_seasons: list[str] = []
        for item in getattr(anime, "franchise_series", []):
            all_seasons.extend(getattr(item, "seasons", None) or [])
        season_line = ",".join(_season_numbers(all_seasons))
    season_line = season_line or "N/A"

    total = sum(
        (item.total_episodes or 0)
        for item in getattr(anime, "franchise_series", [])
        if getattr(item, "total_episodes", None)
    ) or getattr(anime, "total_episodes", None)
    hindi_count = get_hindi_episode_count(anime)
    if total:
        if hindi_count > 0:
            episode_line = f"1-{total} (Hindi: {hindi_count})"
        else:
            episode_line = f"1-{total}"
    else:
        episode_line = "N/A"

    lines = [
        f"◆ <b>{franchise_title}</b> ◆",
        CARD_DIVIDER,
        f"➥ {sc('Season')}:- {html.escape(season_line)}",
        f"➥ {sc('Episode')}:- {html.escape(episode_line)}",
    ]

    statuses = {
        getattr(item, "status", "unknown")
        for item in getattr(anime, "franchise_series", [])
        if getattr(item, "status", None)
    }
    if statuses:
        status = Counter(statuses).most_common(1)[0][0]
        lines.append(f"➥ {sc('Status')}:- {format_status(status)}")

    series = getattr(anime, "franchise_series", [])
    if series:
        lines.append(f"➥ {sc('Series')}:-")
        for item in series:
            item_title = html.escape((item.title or "Unknown").strip())
            lines.append(f"   🔹 {item_title}")
            item_seasons = _season_numbers(getattr(item, "seasons", None))
            if item_seasons:
                lines.append(
                    f"      {sc('Season')}: {html.escape(', '.join(item_seasons))}"
                )
            item_total = getattr(item, "total_episodes", None)
            if item_total:
                item_hindi = (
                    item.available_episodes.get("Hindi", 0)
                    if item.available_episodes else 0
                )
                if item_hindi:
                    lines.append(
                        f"      {sc('Episode')}: 1-{item_total} "
                        f"({sc('Hindi')}: {item_hindi})"
                    )
                else:
                    lines.append(f"      {sc('Episode')}: 1-{item_total}")

    movies = getattr(anime, "franchise_movies", [])
    if movies:
        lines.append(f"➥ {sc('Movies')}:-")
        for movie in movies[:20]:
            movie_title = (
                getattr(movie, "title", None) if not isinstance(movie, str) else movie
            )
            if movie_title:
                lines.append(f"   • {html.escape(str(movie_title).strip())}")
        if len(movies) > 20:
            lines.append(f"   …and {len(movies) - 20} more")

    lines.append(CARD_DIVIDER)
    lines.append(f"⌬ {sc('Powered by')}:- Anime Hindi Dub Bot")
    return "\n".join(lines)


def format_anime_info(anime: AnimeInfo) -> str:
    franchise_series = getattr(anime, "franchise_series", None)
    franchise_movies = getattr(anime, "franchise_movies", None)
    if franchise_series or franchise_movies:
        result = format_franchise_info(anime)
    else:
        result = format_single_anime_info(anime)

    if not result.strip():
        title = (
            getattr(anime, "canonical_title", None)
            or getattr(anime, "title", None)
            or "Unknown Anime"
        )
        return f"🎌 {html.escape(title)}\n\n❌ No anime information found."
    return result


# ============================================================
# HELPERS
# ============================================================
def get_hindi_episode_count(anime: AnimeInfo) -> int:
    available = getattr(anime, "available_episodes", {})
    if not available:
        return 0
    if isinstance(available, dict):
        try:
            return int(available.get("Hindi", 0))
        except (TypeError, ValueError):
            return 0
    try:
        return int(available)
    except (TypeError, ValueError):
        return 0


def get_poster_url(anime: AnimeInfo) -> Optional[str]:
    poster = getattr(anime, "poster_url", None)
    if not poster:
        poster = getattr(anime, "poster", None)
    if not poster:
        return None
    poster = str(poster).strip()
    if not re.match(r"^https?://", poster, re.IGNORECASE):
        return None
    return poster


def safe_format_text(anime: Optional[AnimeInfo]) -> str:
    if anime is None:
        return "❌ No anime information found."
    try:
        return format_anime_info(anime)
    except Exception as exc:
        logger.exception("Anime formatter failed: %s", exc)
        title = getattr(anime, "title", None) or "Unknown Anime"
        return f"🎌 {html.escape(title)}\n\n❌ Unable to format anime information."


# ============================================================
# PUBLIC API
# ============================================================
async def get_anime_info(query: str) -> Optional[AnimeInfo]:
    query = clean_text(query)
    if not query:
        return None
    try:
        async with AnimeScraper() as scraper:
            return await scraper.search(query)
    except Exception as exc:
        logger.exception("Anime search failed for %r: %s", query, exc)
        return None


async def search_and_format(
    query: str,
) -> tuple[Optional[AnimeInfo], str]:
    query = clean_text(query)
    if not query:
        return None, "❌ Please enter an anime name."
    try:
        anime = await get_anime_info(query)
        if anime is None:
            return (
                None,
                "❌ Unable to fetch anime information for: "
                f"{query}\n\nYe temporary problem ho sakti hai.\n"
                "Thodi der baad dobara try karo.",
            )
        log_anime_summary(anime)
        result = safe_format_text(anime)
        if not result.strip():
            return (
                anime,
                "❌ Unable to fetch anime information for: "
                f"{query}\n\nYe temporary problem ho sakti hai.\n"
                "Thodi der baad dobara try karo.",
            )
        return anime, result
    except Exception as exc:
        logger.exception("search_and_format failed: %s", exc)
        return (
            None,
            "❌ Unable to fetch anime information for: "
            f"{query}\n\nYe temporary problem ho sakti hai.\n"
            "Thodi der baad dobara try karo.",
        )


def log_anime_summary(anime: Optional[AnimeInfo]) -> None:
    if anime is None:
        logger.info("Anime result: None")
        return
    title = getattr(anime, "title", "Unknown")
    total = getattr(anime, "total_episodes", 0)
    hindi = get_hindi_episode_count(anime)
    logger.info(
        "Anime result: title=%s total=%d hindi=%d",
        title,
        total,
        hindi,
    )


async def get_formatted_anime_info(query: str) -> str:
    try:
        anime = await get_anime_info(query)
        if not anime:
            return "❌ Anime information not found."
        return format_anime_info(anime)
    except AnimeNotFound:
        return "❌ Anime not found.\n\nAnime ka exact naam try karo."
    except asyncio.TimeoutError:
        return "⏳ Request timeout.\n\nThodi der baad dobara try karo."
    except Exception:
        logger.exception("Anime lookup failed: %s", query)
        return (
            "❌ Error: Unable to fetch anime information.\n\n"
            "Ye temporary problem ho sakti hai.\n"
            "Thodi der baad dobara try karo."
        )


# ============================================================
# OPTIONAL CLI TEST
# ============================================================
async def _cli_test(query: str) -> None:
    try:
        result = await get_formatted_anime_info(query)
        print(result)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        asyncio.run(_cli_test(query))
    else:
        print("Usage: python -m services.anime_scraper <anime name>")
