# ai-daily-digest

A personal AI newspaper. Every morning a GitHub Action reads AI news feeds, asks
Claude to pick and summarize the stories, and publishes a newspaper-style page on
GitHub Pages. Every Monday it also writes a weekly review.

- **Today**: the headline of the day, 3–5 top stories, and the rest grouped into
  *Launches & Models, Tools, Research, Business, Regulation*. Every item links to the original.
- **This Week**: the 5–10 most important stories of the week and the key trends.
- **Archive**: every past daily issue and weekly review, each on its own page.

```
config.yaml            model, limits, prices, timezone
sources.yaml           RSS feeds (add/remove here)
digest/daily.py        daily job:  feeds → Claude → data/daily/<date>.json → docs/
digest/weekly.py       weekly job: data/daily/*.json → Claude → data/weekly/<date>.json → docs/
digest/feeds.py        RSS reading, 24h filter, de-duplication
digest/llm.py          prompts, JSON schemas, API call
digest/site.py         renders the HTML from templates/
templates/             Jinja2 HTML templates
static/style.css       the stylesheet (copied to docs/style.css)
data/seen.json         links already processed (so nothing appears twice)
docs/                  the generated website (GitHub Pages serves this folder)
logs/runs.jsonl        one line per run: tokens, estimated cost, errors
sample_data/           fake articles used by --dry-run
```

**What Claude receives:** only each article's title, source, link and first ~500
characters, never the full text. Claude refers to articles by number and the script
attaches the real links itself, so a link on the site can't be made up.

**If something fails** (the API is down, you run out of credits, the key is wrong), the
script logs the error, leaves the site exactly as it was, and shows a red error
annotation on the GitHub Actions run. If a feed is down, the other feeds still run.
After a failed day, the next run looks back up to 72 hours so stories aren't lost.

---

## 1. Run it locally (VS Code)

You need **Python 3.10 or newer** ([python.org/downloads](https://www.python.org/downloads/))
and **Git**. Open the project folder in VS Code, then open a terminal
(*Terminal → New Terminal*).

Create a virtual environment and install the dependencies:

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

(On Windows: `.venv\Scripts\activate`.) Then:

```bash
pip install -r requirements.txt
```

**Try it for free first (no API key needed).** This uses sample articles, makes no
API call, and writes to `preview/` (git-ignored), so your real data is untouched:

```bash
python -m digest.daily --dry-run
```

```bash
python -m digest.weekly --dry-run
```

Open `preview/site/index.html` in your browser to see the result. To preview other
days for the archive, add `--date 2026-11-18`.

**Check your feeds** (no API call): lists each source and how many recent articles it has:

```bash
python -m digest.daily --check-sources
```

**Real run.** Copy `.env.example` to `.env`, paste your API key into it (see step 2),
then:

```bash
python -m digest.daily
```

The page lands in `docs/index.html`. The terminal prints the tokens used and the
estimated cost. Other commands:

- `python -m digest.weekly`: build the weekly review from the saved daily issues.
- `python -m digest.build`: rebuild the HTML from saved data (after editing templates or CSS). No API call.

> `.env` is in `.gitignore`, so your key is never committed. Never paste the key into any other file.

## 2. Create an API key and set a spending cap

1. Go to [platform.claude.com](https://platform.claude.com) (console.anthropic.com
   redirects there) and sign up or log in.
2. **Load prepaid credits:** open **Settings → Billing**, add a card, and buy credits.
   $5–$10 lasts more than a month for this project.
3. **Turn off auto-reload:** on the same Billing page, make sure **auto-reload** is
   **off**. When the credits run out, requests simply stop. You are never charged
   automatically.
4. **Set a $10 monthly spend limit:** open **Settings → Limits** and set the monthly
   spend limit to **$10**. Above that limit the API refuses requests. This project then
   logs the error and keeps the last site.
5. **Create the key:** open **API Keys → Create Key**, name it `ai-daily-digest`, and
   copy it right away (it's shown only once). It starts with `sk-ant-`.

## 3. Add the key as a GitHub Secret

1. Create a new repository on GitHub (e.g. `ai-daily-digest`). It must be **public**
   for free GitHub Pages; your key stays private in Secrets.
2. Push this project to it. In VS Code use *Source Control → Publish to GitHub*, or:
   ```bash
   git init && git add . && git commit -m "Initial commit" && git branch -M main
   ```
   ```bash
   git remote add origin https://github.com/YOUR-USERNAME/ai-daily-digest.git && git push -u origin main
   ```
3. On GitHub, open the repository's **Settings → Secrets and variables → Actions →
   New repository secret**.
4. Name: `ANTHROPIC_API_KEY`. Value: your key. Click **Add secret**.
5. Check that the workflows may push: **Settings → Actions → General → Workflow
   permissions → Read and write permissions → Save**.

## 4. Turn on GitHub Pages from `/docs`

1. **Settings → Pages**.
2. *Source*: **Deploy from a branch**. *Branch*: **main**, folder **/docs** → **Save**.
3. After about a minute your site is at `https://YOUR-USERNAME.github.io/ai-daily-digest/`.
4. Make the first issue right away instead of waiting until 6 am: **Actions → Daily
   digest → Run workflow**. (Do the same with *Weekly digest* any time you like.)

The daily job runs at **6:00 am Costa Rica time (12:00 UTC)** and the weekly job on
**Mondays at 8:00 am (14:00 UTC)**. GitHub sometimes starts scheduled jobs a few
minutes late. If the repository gets no activity for 60 days GitHub pauses scheduled
jobs, but the daily commits count as activity.

Tip: add the site to your phone's home screen (Share → *Add to Home Screen*) so it
opens like an app.

## 5. Add or remove sources

Edit `sources.yaml`:

```yaml
  - name: My Favourite Blog          # shown on the site
    url: https://example.com/feed.xml
    max_items: 5                      # optional, default 10 per day
    enabled: false                    # optional, pause without deleting
```

Delete a block to remove a source. Run `python -m digest.daily --check-sources` to
test, then commit and push. Most blogs have a feed at `/feed`, `/rss` or `/rss.xml`.

Note: Anthropic and Meta AI don't publish official RSS feeds, so the default config
uses community-maintained mirrors (github.com/Olshansk/rss-feeds). If one stops
updating, swap the URL.

Other settings live in `config.yaml`: the model, `effort`, `max_tokens`, the daily
article limit (`max_articles_per_day`, default 40), the excerpt length, the timezone
and the site title.

## 6. Estimated monthly cost

The model is **Claude Opus 5.5** (`claude-opus-5-5`, $4 per million input tokens and
$20 per million output tokens), run at `effort: low` to keep its thinking short.

| Job | Input | Output (incl. thinking) | Cost per run | Runs/month | Monthly |
|---|---|---|---|---|---|
| Daily (40 articles) | ~9,000 tokens | ~3,500 tokens | ~$0.11 | 30 | ~$3.30 |
| Weekly | ~18,000 tokens | ~3,000 tokens | ~$0.13 | 4–5 | ~$0.60 |
| **Total** | | | | | **≈ $4 a month** |

These are estimates: busy news days cost more, quiet days less. `logs/runs.jsonl`
and the job output show the real numbers after each run, including the running
total for the month. The $10 spend limit from step 2 is a hard cap.

**To spend less**, set `max_articles_per_day: 25` in `config.yaml`, or switch to
`model: "claude-haiku-5-5"` with prices `0.10` / `0.50` and `refusal_fallback: false`.
That costs about 15 cents a month.

`refusal_fallback: true` means: if Claude's safety filter wrongly declines a request
(rare, but possible with news about security or biology), the API retries it on a
fallback model in the same call, so you still get an issue.

GitHub Actions and GitHub Pages are free for public repositories.
