"""Talk to Claude: prompts, JSON schemas and the API call itself.

Claude never sees full articles - only title, source, link and a short excerpt.
It refers to articles by their [id]; links are attached afterwards from our own
data, so a link on the site can never be made up.
"""
from __future__ import annotations

import json
import os
from typing import Tuple

CATEGORIES = [
    ("launches", "Launches & Models"),
    ("tools", "Tools"),
    ("research", "Research"),
    ("business", "Business"),
    ("regulation", "Regulation"),
]


class DigestError(RuntimeError):
    pass


def _item(extra: dict = None) -> dict:
    props = {"id": {"type": "integer"}, "title": {"type": "string"}, "summary": {"type": "string"}}
    props.update(extra or {})
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


DAILY_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": _item(),
        "top_stories": {"type": "array", "items": _item()},
        "categories": {
            "type": "object",
            "properties": {k: {"type": "array", "items": _item()} for k, _ in CATEGORIES},
            "required": [k for k, _ in CATEGORIES],
            "additionalProperties": False,
        },
    },
    "required": ["headline", "top_stories", "categories"],
    "additionalProperties": False,
}

DAILY_SYSTEM = """You are the editor of a short, English-language morning newspaper about artificial intelligence.
You receive today's articles as numbered entries with a source, title, link and a short excerpt.

Return JSON only, following the schema:
- headline: the single most important story of the day. Rewrite the title as a crisp newspaper headline (max 12 words). Summary: 2-3 sentences, max 60 words.
- top_stories: the next 3-5 most important stories (fewer if there are fewer articles). Summary: 2 sentences, max 40 words.
- categories: every other relevant article, each in exactly one category. Summary: 1 sentence, max 25 words.
  launches = new models and product launches; tools = developer tools, libraries, features, how-tos;
  research = papers and scientific results; business = funding, deals, companies, people, markets;
  regulation = law, policy, safety, ethics, lawsuits.

Rules:
- Use each article id at most once across the whole answer. When several articles cover the same news, keep the best one and drop the rest.
- Skip articles that are not about AI, or are pure advertising.
- Use only facts present in the title and excerpt. Do not speculate or add outside information.
- Write everything in clear, neutral English, even if a source is in another language.
- Write headlines in sentence case. No emojis, no hype words like "groundbreaking" or "revolutionary"."""

WEEKLY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "intro": {"type": "string"},
        "top_stories": {"type": "array", "items": _item({"why_it_matters": {"type": "string"}})},
        "trends": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
                "required": ["name", "description"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "intro", "top_stories", "trends"],
    "additionalProperties": False,
}

WEEKLY_SYSTEM = """You are the editor of a weekly English-language review of artificial intelligence news.
You receive the week's already-summarized daily stories as numbered entries (date, section, source, title, summary).

Return JSON only, following the schema:
- title: a headline for the week (max 12 words).
- intro: 2-3 sentences, max 70 words, on what defined the week.
- top_stories: the 5-10 most important stories of the week, most important first. Use the entry's id.
  title: a crisp headline (max 12 words); summary: 2 sentences, max 45 words; why_it_matters: 1 sentence, max 25 words.
  If several entries cover the same news, pick one id and combine what they say.
- trends: 3-5 key trends that connect several stories. name: 2-5 words; description: 1-2 sentences, max 40 words.

Use only facts in the entries. Write in clear, neutral English. No emojis, no hype words."""


def build_daily_prompt(articles: list) -> str:
    lines = [f"Today's articles ({len(articles)}):", ""]
    for i, a in enumerate(articles):
        lines.append(f"[{i}] Source: {a['source']}\nTitle: {a['title']}\nLink: {a['link']}\nExcerpt: {a['excerpt']}\n")
    return "\n".join(lines)


def build_weekly_prompt(entries: list) -> str:
    lines = [f"This week's stories ({len(entries)}):", ""]
    for i, e in enumerate(entries):
        lines.append(f"[{i}] {e['date']} | {e['section']} | {e['source']} | {e['title']} - {e['summary']}")
    return "\n".join(lines)


def call_claude(cfg: dict, system: str, user: str, schema: dict, max_tokens: int) -> Tuple[dict, dict]:
    """One Messages API call with structured JSON output. Returns (data, usage)."""
    import anthropic  # imported here so dry runs work without an API key

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise DigestError("ANTHROPIC_API_KEY is not set (add it to .env locally or as a GitHub Secret).")

    c = cfg["claude"]
    client = anthropic.Anthropic(max_retries=3, timeout=300)
    kwargs = dict(
        model=c["model"],
        max_tokens=int(max_tokens),
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={
            "effort": c.get("effort", "low"),
            "format": {"type": "json_schema", "schema": schema},
        },
    )
    try:
        if c.get("refusal_fallback", False):
            # If a safety filter declines (rare false positive), the API retries
            # on a suitable fallback model inside the same call.
            resp = client.beta.messages.create(
                **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        else:
            resp = client.messages.create(**kwargs)
    except anthropic.AuthenticationError as e:
        raise DigestError(f"Invalid API key: {e.message}") from e
    except anthropic.PermissionDeniedError as e:
        raise DigestError(f"Permission denied (out of credits or spend limit reached?): {e.message}") from e
    except anthropic.NotFoundError as e:
        raise DigestError(f"Model not found - check claude.model in config.yaml: {e.message}") from e
    except anthropic.BadRequestError as e:
        raise DigestError(f"Bad request (credit balance too low?): {e.message}") from e
    except anthropic.RateLimitError as e:
        raise DigestError(f"Rate limited after retries: {e.message}") from e
    except anthropic.APIStatusError as e:
        raise DigestError(f"API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise DigestError(f"Could not reach the API: {e}") from e

    usage = {
        "model": resp.model,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }
    if resp.stop_reason == "refusal":
        raise DigestError("Claude declined the request (stop_reason=refusal).")
    if resp.stop_reason == "max_tokens":
        raise DigestError("Response was cut off: raise max_tokens in config.yaml.")
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if not text:
        raise DigestError("Response contained no text block.")
    try:
        return json.loads(text), usage
    except ValueError as e:
        raise DigestError(f"Response was not valid JSON: {e}") from e
