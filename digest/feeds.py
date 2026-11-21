"""Read RSS feeds, keep recent unseen articles, and trim them to short excerpts."""
from __future__ import annotations

import calendar
import datetime as dt
import html
import re
import socket
from typing import Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser

USER_AGENT = "Mozilla/5.0 (compatible; ai-daily-digest/1.0; personal RSS reader)"
socket.setdefaulttimeout(20)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TRACKING = {"ref", "fbclid", "gclid", "mc_cid", "mc_eid", "cmpid"}


def normalize_link(url: str) -> str:
    """Canonical form of a URL, used as the dedup key."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    query = [(k, v) for k, v in parse_qsl(parts.query)
             if not (k.lower().startswith("utm_") or k.lower() in _TRACKING)]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower() or "https", parts.netloc.lower().removeprefix("www."),
                       path, urlencode(query), ""))


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def clean_text(raw: str, limit: int) -> str:
    text = html.unescape(_TAG_RE.sub(" ", raw or ""))
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def entry_time(entry) -> Optional[dt.datetime]:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        t = entry.get(key)
        if t:
            return dt.datetime.fromtimestamp(calendar.timegm(t), tz=dt.timezone.utc)
    return None


def fetch_source(src: dict) -> Tuple[list, Optional[str]]:
    """Return (entries, error_message)."""
    try:
        feed = feedparser.parse(src["url"], agent=USER_AGENT)
    except Exception as e:  # network errors, timeouts
        return [], f"{type(e).__name__}: {e}"
    status = getattr(feed, "status", 200)
    if status >= 400:
        return [], f"HTTP {status}"
    if feed.bozo and not feed.entries:
        return [], f"Could not parse feed ({feed.get('bozo_exception')})"
    return list(feed.entries), None


def collect(sources: list, cfg: dict, seen: dict, since: dt.datetime) -> Tuple[list, dict]:
    """Fetch every source and return (articles, per-source stats)."""
    limits = cfg["limits"]
    excerpt_chars = int(limits.get("excerpt_chars", 500))
    per_source: dict = {}
    stats: dict = {}
    seen_titles: set = set()
    seen_links: set = set()

    for src in sources:
        name = src["name"]
        entries, err = fetch_source(src)
        if err:
            stats[name] = {"error": err}
            continue
        picked = []
        for e in entries:
            link = (e.get("link") or "").strip()
            title = clean_text(e.get("title", ""), 300)
            if not link or not title:
                continue
            published = entry_time(e)
            if published and published < since:
                continue
            key = normalize_link(link)
            tkey = normalize_title(title)
            if key in seen or key in seen_links or tkey in seen_titles:
                continue
            seen_links.add(key)
            seen_titles.add(tkey)
            body = e.get("summary") or e.get("description") or ""
            if not body and e.get("content"):
                body = e["content"][0].get("value", "")
            picked.append({
                "source": name,
                "title": title,
                "link": link,
                "key": key,
                "published": published.isoformat() if published else None,
                "excerpt": clean_text(body, excerpt_chars),
            })
            if len(picked) >= int(src.get("max_items", 10)):
                break
        per_source[name] = picked
        stats[name] = {"new": len(picked), "in_feed": len(entries)}

    # Round-robin across sources so one busy feed can't crowd out the rest.
    cap = int(limits.get("max_articles_per_day", 40))
    articles: list = []
    queues = [list(v) for v in per_source.values()]
    while len(articles) < cap and any(queues):
        for q in queues:
            if q and len(articles) < cap:
                articles.append(q.pop(0))
    return articles, stats
