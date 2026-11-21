"""Shared helpers: config, paths, JSON storage, logging and cost tracking."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Optional

import yaml

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

ROOT = Path(__file__).resolve().parent.parent


class Paths:
    """Where things live. Dry runs write to ./preview so real data is never touched."""

    def __init__(self, preview: bool = False):
        base = ROOT / "preview" if preview else ROOT
        self.preview = preview
        self.data = base / "data"
        self.daily = self.data / "daily"
        self.weekly = self.data / "weekly"
        self.seen = self.data / "seen.json"
        self.site = (base / "site") if preview else (ROOT / "docs")
        self.logs = base / "logs"
        self.run_log = self.logs / "runs.jsonl"
        self.templates = ROOT / "templates"
        self.static = ROOT / "static"
        for p in (self.daily, self.weekly, self.site, self.logs):
            p.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_sources() -> list:
    with open(ROOT / "sources.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return [s for s in data.get("sources", []) if s.get("enabled", True)]


def local_tz(cfg: dict):
    name = cfg.get("site", {}).get("timezone", "UTC")
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return dt.timezone.utc


def today_local(cfg: dict) -> dt.date:
    return dt.datetime.now(local_tz(cfg)).date()


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path: Path, obj: Any) -> None:
    """Atomic write: a crash mid-write never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def log(msg: str) -> None:
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def gh_annotation(level: str, msg: str) -> None:
    """Show a warning/error banner on the GitHub Actions run page."""
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::{level}::{msg}", flush=True)


def estimate_cost(cfg: dict, input_tokens: int, output_tokens: int) -> float:
    c = cfg["claude"]
    return (input_tokens * c["price_input_per_mtok"] + output_tokens * c["price_output_per_mtok"]) / 1_000_000


def record_run(paths: Paths, entry: dict) -> float:
    """Append one line to logs/runs.jsonl and return this month's total estimated cost."""
    entry = {"timestamp": utc_now().isoformat(timespec="seconds"), **entry}
    with open(paths.run_log, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    month = entry["timestamp"][:7]
    total = 0.0
    with open(paths.run_log, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("timestamp", "").startswith(month) and not row.get("estimated"):
                total += float(row.get("cost_usd") or 0)
    return total


def print_usage(cfg: dict, paths: Paths, kind: str, usage: Optional[dict], status: str,
                extra: Optional[dict] = None, estimated: bool = False) -> None:
    usage = usage or {}
    in_tok = int(usage.get("input_tokens", 0))
    out_tok = int(usage.get("output_tokens", 0))
    cost = estimate_cost(cfg, in_tok, out_tok)
    month_total = record_run(paths, {
        "kind": kind,
        "status": status,
        "model": usage.get("model", cfg["claude"]["model"]),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd": round(cost, 5),
        "estimated": estimated,
        **(extra or {}),
    })
    label = "ESTIMATED (dry run, no API call)" if estimated else "actual"
    log(f"Tokens ({label}): input={in_tok:,} output={out_tok:,}  cost≈${cost:.4f}")
    if not estimated:
        log(f"Estimated spend so far this month: ${month_total:.4f}")


def rough_tokens(text: str) -> int:
    """~4 characters per token; good enough for a dry-run estimate."""
    return max(1, len(text) // 4)
