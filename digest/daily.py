"""Daily digest: fetch feeds -> ask Claude -> save data/daily/<date>.json -> rebuild site.

Usage:
    python -m digest.daily                  # real run (needs ANTHROPIC_API_KEY)
    python -m digest.daily --dry-run        # sample data, no API call, writes to ./preview
    python -m digest.daily --check-sources  # fetch feeds and report, no API call
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import traceback

from . import feeds, llm
from .common import (ROOT, Paths, gh_annotation, load_config, load_sources, log, print_usage,
                     read_json, rough_tokens, today_local, utc_now, write_json)
from .site import build_site

MAX_CATCH_UP_HOURS = 72  # after a failed day, look back further (up to 3 days)


def assemble(data: dict, articles: list, date: dt.date, cfg: dict, usage: dict, sample: bool) -> dict:
    """Turn Claude's id-based answer into a self-contained record with real links."""
    used: set = set()

    def resolve(item: dict):
        i = item.get("id")
        if not isinstance(i, int) or not 0 <= i < len(articles) or i in used:
            return None
        used.add(i)
        a = articles[i]
        return {"title": item.get("title") or a["title"], "summary": item.get("summary", ""),
                "source": a["source"], "link": a["link"], "original_title": a["title"]}

    headline = resolve(data.get("headline") or {})
    top = [x for x in (resolve(i) for i in data.get("top_stories", [])) if x]
    if headline is None and top:
        headline = top.pop(0)
    cats = []
    for key, name in llm.CATEGORIES:
        items = [x for x in (resolve(i) for i in data.get("categories", {}).get(key, [])) if x]
        if items:
            cats.append({"key": key, "name": name, "items": items})
    if headline is None:
        raise llm.DigestError("Claude's answer had no usable stories.")
    return {
        "date": date.isoformat(),
        "generated_at": utc_now().isoformat(timespec="seconds"),
        "model": usage.get("model", cfg["claude"]["model"]),
        "article_count": len(articles),
        "headline": headline,
        "top_stories": top,
        "categories": cats,
        "usage": usage,
        "sample": sample,
    }


def sample_answer(articles: list) -> dict:
    """What Claude might return, built locally from the sample file's hints."""
    def item(i):
        a = articles[i]
        text = a["excerpt"].replace("SAMPLE ARTICLE. ", "")
        first = text.split(". ")[0].rstrip(".") + "."
        return {"id": i, "title": a["title"], "summary": first}
    cats = {k: [] for k, _ in llm.CATEGORIES}
    for i in range(5, len(articles)):
        cats[articles[i].get("category", "business")].append(item(i))
    return {"headline": item(0), "top_stories": [item(i) for i in range(1, min(5, len(articles)))],
            "categories": cats}


def check_sources(cfg: dict) -> int:
    since = utc_now() - dt.timedelta(hours=cfg["limits"]["lookback_hours"])
    articles, stats = feeds.collect(load_sources(), cfg, {}, since)
    bad = 0
    for name, s in stats.items():
        if "error" in s:
            bad += 1
            print(f"  ✗ {name:<24} ERROR: {s['error']}")
        else:
            print(f"  ✓ {name:<24} {s['new']:>3} recent / {s['in_feed']} in feed")
    print(f"\n{len(articles)} articles would be sent to Claude (cap {cfg['limits']['max_articles_per_day']}).")
    return 1 if bad == len(stats) else 0


def lookback_start(cfg: dict, paths: Paths) -> dt.datetime:
    now = utc_now()
    since = now - dt.timedelta(hours=cfg["limits"]["lookback_hours"])
    files = sorted(paths.daily.glob("*.json"))
    if files:
        last = read_json(files[-1], {}) or {}
        try:
            last_ok = dt.datetime.fromisoformat(last["generated_at"])
            since = min(since, max(last_ok, now - dt.timedelta(hours=MAX_CATCH_UP_HOURS)))
        except (KeyError, ValueError):
            pass
    return since


def run(args) -> int:
    cfg = load_config()
    if args.check_sources:
        return check_sources(cfg)

    paths = Paths(preview=args.dry_run)
    date = dt.date.fromisoformat(args.date) if args.date else today_local(cfg)
    log(f"Daily digest for {date} {'(DRY RUN - sample data, no API call)' if args.dry_run else ''}")

    seen = {} if args.dry_run else (read_json(paths.seen, {}) or {})
    if args.dry_run:
        articles = read_json(ROOT / "sample_data" / "sample_articles.json")[: cfg["limits"]["max_articles_per_day"]]
    else:
        since = lookback_start(cfg, paths)
        log(f"Looking for articles published since {since.isoformat(timespec='minutes')}")
        articles, stats = feeds.collect(load_sources(), cfg, seen, since)
        for name, s in stats.items():
            if "error" in s:
                log(f"  ! {name}: {s['error']}")
                gh_annotation("warning", f"Feed '{name}' failed: {s['error']}")
            else:
                log(f"  {name}: {s['new']} new")

    if not articles:
        log("No new articles today. Site left unchanged.")
        print_usage(cfg, paths, "daily", None, "no_articles", {"date": date.isoformat()})
        return 0
    log(f"{len(articles)} articles selected")

    prompt = llm.build_daily_prompt(articles)
    if args.dry_run:
        data = sample_answer(articles)
        usage = {"model": cfg["claude"]["model"],
                 "input_tokens": rough_tokens(llm.DAILY_SYSTEM + prompt),
                 "output_tokens": rough_tokens(str(data)) + 1500}  # + typical low-effort thinking
    else:
        try:
            data, usage = llm.call_claude(cfg, llm.DAILY_SYSTEM, prompt, llm.DAILY_SCHEMA,
                                          cfg["claude"]["max_tokens_daily"])
        except Exception as e:
            return fail(cfg, paths, date, e)

    try:
        digest = assemble(data, articles, date, cfg, usage, sample=args.dry_run)
    except Exception as e:
        return fail(cfg, paths, date, e)

    write_json(paths.daily / f"{date.isoformat()}.json", digest)
    if not args.dry_run:
        today = date.isoformat()
        for a in articles:
            seen.setdefault(a["key"], today)
        cutoff = (date - dt.timedelta(days=cfg["limits"]["seen_retention_days"])).isoformat()
        write_json(paths.seen, {k: v for k, v in sorted(seen.items()) if v >= cutoff})

    try:
        build_site(paths, cfg)
    except Exception as e:
        log(f"Site build failed: {e}")
        traceback.print_exc()
        gh_annotation("error", f"Site build failed: {e}")
    log(f"Site written to {paths.site.relative_to(ROOT)}/")
    print_usage(cfg, paths, "daily", usage, "ok",
                {"date": date.isoformat(), "articles": len(articles)}, estimated=args.dry_run)
    return 0


def fail(cfg: dict, paths: Paths, date: dt.date, e: Exception) -> int:
    """Log the error and keep yesterday's site exactly as it was."""
    msg = f"{type(e).__name__}: {e}"
    log(f"ERROR - digest not generated, previous site kept. {msg}")
    gh_annotation("error", f"Daily digest failed, previous site kept: {msg}")
    print_usage(cfg, paths, "daily", None, "error", {"date": date.isoformat(), "error": msg})
    return 0  # exit 0 so the workflow still commits the log; the error shows as an annotation


def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="use sample data, no API call, write to ./preview")
    p.add_argument("--check-sources", action="store_true", help="fetch feeds and print what was found")
    p.add_argument("--date", help="issue date YYYY-MM-DD (default: today in the configured timezone)")
    sys.exit(run(p.parse_args()))


if __name__ == "__main__":
    main()
