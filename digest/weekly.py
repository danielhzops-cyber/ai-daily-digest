"""Weekly digest: read the week's data/daily/*.json -> ask Claude -> data/weekly/<date>.json.

It never re-reads the original articles, only the daily summaries already saved.

Usage:
    python -m digest.weekly            # real run (needs ANTHROPIC_API_KEY)
    python -m digest.weekly --dry-run  # uses ./preview data, no API call
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from . import llm
from .common import (ROOT, Paths, gh_annotation, load_config, log, print_usage, read_json,
                     rough_tokens, today_local, utc_now, write_json)
from .site import build_site


def week_entries(paths: Paths, start: dt.date, end: dt.date) -> tuple:
    entries, days = [], []
    d = start
    while d <= end:
        daily = read_json(paths.daily / f"{d.isoformat()}.json")
        if daily:
            days.append(d.isoformat())
            groups = [("Headline", [daily["headline"]]), ("Top story", daily.get("top_stories", []))]
            groups += [(c["name"], c["items"]) for c in daily.get("categories", [])]
            for section, items in groups:
                for it in items:
                    entries.append({"date": d.isoformat(), "section": section, **it})
        d += dt.timedelta(days=1)
    return entries, days


def sample_answer(entries: list) -> dict:
    heads = [i for i, e in enumerate(entries) if e["section"] in ("Headline", "Top story")][:8]
    return {
        "title": "Sample week: smaller models, bigger deals",
        "intro": "This is a dry-run weekly digest built from sample daily issues. "
                 "With a real API key, Claude picks the week's most important stories and names the trends.",
        "top_stories": [{"id": i, "title": entries[i]["title"], "summary": entries[i]["summary"],
                         "why_it_matters": "Placeholder text: shows where the analysis goes."} for i in heads],
        "trends": [
            {"name": "Efficient models", "description": "Sample trend: labs compete on cost and speed, not only size."},
            {"name": "Rules take shape", "description": "Sample trend: regulators move from principles to enforcement."},
            {"name": "Agents at work", "description": "Sample trend: tools that act on a user's behalf reach everyday software."},
        ],
    }


def run(args) -> int:
    cfg = load_config()
    paths = Paths(preview=args.dry_run)
    end = dt.date.fromisoformat(args.date) if args.date else today_local(cfg)
    start = end - dt.timedelta(days=int(cfg["limits"].get("weekly_days", 7)) - 1)
    log(f"Weekly digest {start} → {end} {'(DRY RUN - no API call)' if args.dry_run else ''}")

    entries, days = week_entries(paths, start, end)
    if not entries:
        log("No daily digests found for this week. Nothing to do.")
        return 0
    log(f"{len(entries)} stories from {len(days)} daily issues")

    prompt = llm.build_weekly_prompt(entries)
    if args.dry_run:
        data = sample_answer(entries)
        usage = {"model": cfg["claude"]["model"],
                 "input_tokens": rough_tokens(llm.WEEKLY_SYSTEM + prompt),
                 "output_tokens": rough_tokens(str(data)) + 1500}
    else:
        try:
            data, usage = llm.call_claude(cfg, llm.WEEKLY_SYSTEM, prompt, llm.WEEKLY_SCHEMA,
                                          cfg["claude"]["max_tokens_weekly"])
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            log(f"ERROR - weekly digest not generated, previous site kept. {msg}")
            gh_annotation("error", f"Weekly digest failed, previous site kept: {msg}")
            print_usage(cfg, paths, "weekly", None, "error", {"date": end.isoformat(), "error": msg})
            return 0

    used, stories = set(), []
    for s in data.get("top_stories", []):
        i = s.get("id")
        if isinstance(i, int) and 0 <= i < len(entries) and i not in used:
            used.add(i)
            e = entries[i]
            stories.append({"title": s.get("title") or e["title"], "summary": s.get("summary", ""),
                            "why_it_matters": s.get("why_it_matters", ""), "source": e["source"],
                            "link": e["link"], "date": e["date"]})
    weekly = {
        "date": end.isoformat(),
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "generated_at": utc_now().isoformat(timespec="seconds"),
        "model": usage.get("model"),
        "days": days,
        "title": data.get("title", "The week in AI"),
        "intro": data.get("intro", ""),
        "top_stories": stories,
        "trends": data.get("trends", []),
        "usage": usage,
        "sample": args.dry_run,
    }
    write_json(paths.weekly / f"{end.isoformat()}.json", weekly)
    build_site(paths, cfg)
    log(f"Site written to {paths.site.relative_to(ROOT)}/")
    print_usage(cfg, paths, "weekly", usage, "ok", {"date": end.isoformat()}, estimated=args.dry_run)
    return 0


def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="use ./preview data, no API call")
    p.add_argument("--date", help="last day of the week YYYY-MM-DD (default: today)")
    sys.exit(run(p.parse_args()))


if __name__ == "__main__":
    main()
