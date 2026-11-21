"""Render the static site (docs/) from the saved JSON in data/.

The whole site is rebuilt from data on every run, so it can always be
regenerated with:  python -m digest.build
"""
from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .common import Paths, read_json


def _date(value: str) -> dt.date:
    return dt.date.fromisoformat(value[:10])


def longdate(value: str) -> str:
    d = _date(value)
    return f"{d:%A}, {d:%B} {d.day}, {d.year}"


def shortdate(value: str) -> str:
    d = _date(value)
    return f"{d:%b} {d.day}"


def monthname(value: str) -> str:
    return f"{_date(value):%B %Y}"


def _load(folder: Path) -> list:
    items = [read_json(p) for p in sorted(folder.glob("*.json"), reverse=True)]
    return [i for i in items if i]


def build_site(paths: Paths, cfg: dict) -> None:
    env = Environment(loader=FileSystemLoader(str(paths.templates)),
                      autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
    env.filters.update(longdate=longdate, shortdate=shortdate, monthname=monthname)
    site = cfg.get("site", {})
    dailies = _load(paths.daily)
    weeklies = _load(paths.weekly)
    limit = int(site.get("archive_limit") or 0)
    base = {"site": site, "preview": paths.preview, "latest": dailies[0] if dailies else None}

    pages = {}  # path -> html. Render everything first, then write, so a template
    #            error never leaves a half-updated site.
    pages["index.html"] = env.get_template("day.html").render(
        **base, root="", active="today", digest=dailies[0] if dailies else None,
        prev=dailies[1] if len(dailies) > 1 else None, next=None)
    pages["week.html"] = env.get_template("week.html").render(
        **base, root="", active="week", weekly=weeklies[0] if weeklies else None,
        recent=dailies[:7])
    pages["archive/index.html"] = env.get_template("archive.html").render(
        **base, root="../", active="archive",
        dailies=dailies[:limit] if limit else dailies, weeklies=weeklies)
    for i, d in enumerate(dailies):
        pages[f"archive/{d['date']}.html"] = env.get_template("day.html").render(
            **base, root="../", active="archive", digest=d,
            prev=dailies[i + 1] if i + 1 < len(dailies) else None,
            next=dailies[i - 1] if i > 0 else None)
    for w in weeklies:
        pages[f"weekly/{w['date']}.html"] = env.get_template("week.html").render(
            **base, root="../", active="archive", weekly=w, recent=[])

    for rel, html in pages.items():
        out = paths.site / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
    shutil.copyfile(paths.static / "style.css", paths.site / "style.css")
    (paths.site / ".nojekyll").write_text("", encoding="utf-8")
