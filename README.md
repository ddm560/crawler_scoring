# Domain Crawler + Programmatic Advertising Scorer

This tool takes a list of domains, crawls each site, extracts signals related to content quality and monetization patterns, and then assigns a score for how strong the domain looks for programmatic advertising use.

It runs in 2 steps:

1. `extract_features.py` (crawl + feature extraction)
2. `finalize_scores.py` (scoring + labels + CSV export)

## What You Put In

- A text file with one domain per line (example: `domains.txt`)

Example:

```txt
example.com
news-site.net
mydomain.org
```

## What You Get Out

After both steps run, you get:

- `features.jsonl`: raw extracted features for each domain (technical signals)
- `scored.jsonl`: scored results in JSONL format
- `scored.csv`: scored results in spreadsheet-friendly format

## Step-by-Step: How It Works

### Step 1: Crawl domains and extract features (`extract_features.py`)

This script reads your input list and processes each domain.

For each domain, it:

1. Normalizes the domain (removes `http://`, `https://`, etc.)
2. Tries to fetch the homepage (`https://...` first, then `http://...`)
3. Detects the final URL after redirects
4. Collects internal links from the homepage
5. Optionally looks for a sitemap (`/sitemap.xml`, `/sitemap_index.xml`)
6. Fetches a small sample of pages from the site
7. Extracts signals (features) from the HTML and visible text
8. Writes one JSON record (one line) to `features.jsonl`

Important:

- This step does not assign a `score`.
- It only creates the raw signals used by the scoring step.

### Step 2: Score each domain (`finalize_scores.py`)

This script reads `features.jsonl` and computes:

- `score` (0-100)
- `confidence` (0.0-1.0)
- `bucket` (`Good domain`, `Suspicious`, or `Need more evidence`)
- human-readable reasons explaining what influenced the score

It also writes:

- `scored.jsonl`
- `scored.csv`

## The Scoring Logic (Plain English)

The scoring step combines several groups of signals.

### 1. Content quality signals

The tool looks for signs that pages contain real content vs. thin or templated content.

Examples:

- median text length across sampled pages
- boilerplate similarity across pages
- article/news schema hints in HTML

This helps detect:

- low-content sites
- heavily templated sites
- potential content farms

### 2. Ad/monetization signals

The tool looks for signs of aggressive monetization or arbitrage patterns.

Examples:

- ad-like container counts in the DOM
- number of third-party scripts
- external link ratio
- affiliate markers in HTML

This helps detect:

- ad-heavy pages
- affiliate-heavy setups
- suspicious outbound linking behavior

### 3. Legitimacy / trust signals

The tool checks for basic site trust indicators.

Examples:

- About page links
- Contact page links
- Privacy + Terms links
- parked / domain-for-sale indicators

This helps separate normal publishers from placeholder or low-trust sites.

### 4. UX risk signals

The tool flags patterns often associated with poor user experience.

Examples:

- push notification prompts
- interstitial/overlay keywords
- auto-refresh / ad-refresh keywords

These reduce score because they often correlate with poor inventory quality.

### 5. Cluster / shared-infrastructure signals

The tool compares domains against the full input batch to detect suspicious clustering.

Examples:

- shared AdSense publisher IDs across many domains
- shared GTM container IDs across many domains
- identical/near-identical homepage template signatures (simhash clusters)

This helps identify domain networks that may not be independently strong publishers.

## How the Final Score Is Built

`finalize_scores.py` computes sub-scores (0-100) and combines them using weights:

- 30% Content
- 30% Ads/Monetization
- 25% Cluster similarity / shared IDs
- 10% Legitimacy
- 5% UX

Then it applies a confidence adjustment based on crawl quality:

- how many pages were fetched
- success rate of requests
- whether a sitemap was found
- whether the site looked blocked/captcha-protected

So a site with decent raw signals but weak crawl coverage may end up with a lower final score.

## What the Output Fields Mean (High-Level)

In `scored.csv` / `scored.jsonl`, key fields are:

- `score`: final quality score (0-100)
- `bucket`: easy label for triage
- `confidence`: how reliable the score is based on crawl evidence
- `reasons`: short explanations for the score
- `pages_fetched`, `success_rate`: crawl quality diagnostics
- `adsense_pub_ids`, `gtm_ids`, `ga_ids`: IDs found during crawling

## How to Use the Results

Recommended workflow:

1. Sort `scored.csv` by `score` (highest to lowest)
2. Review `bucket` and `confidence`
3. Read `reasons` for domains with very low or very high scores
4. Manually review a sample of domains before making business decisions

Best practice:

- Treat this as a screening/ranking tool, not a final approval system.
- Use it to prioritize review and reduce manual work.

## Business Interpretation (Pass / Review / Reject)

Use the score as an operational triage signal, not a standalone approval decision.

Suggested thresholds (starting point):

- `80-100` -> `Pass (Fast Track)`
- `60-79` -> `Manual Review`
- `40-59` -> `High-Risk Review`
- `0-39` -> `Reject / Deprioritize`

How to apply these in practice:

### `80-100` (Pass / Fast Track)

Typical meaning:

- Good content signals
- Lower signs of aggressive arbitrage behavior
- Better crawl evidence / confidence

Recommended action:

- Move to business/commercial checks (pricing, geography, inventory type, policy fit)
- Do a light manual QA sample before activation

### `60-79` (Manual Review)

Typical meaning:

- Mixed signals
- Possibly decent domains with some risk markers (thin pages, heavy scripts, weak crawl evidence)

Recommended action:

- Review `reasons` and `confidence`
- Manually inspect homepage + 2-3 article/content pages
- Verify ad density and user experience

### `40-59` (High-Risk Review)

Typical meaning:

- Multiple negative signals
- Weak legitimacy indicators or ad-heavy patterns
- Possible low-value or unstable inventory

Recommended action:

- Only continue if there is a strong business reason
- Require manual approval and stricter QA checks
- Consider lower bid/test budgets if piloting

### `0-39` (Reject / Deprioritize)

Typical meaning:

- Strong suspicious signals
- Poor content/UX/legitimacy pattern
- Cluster/network similarity or parked-like behavior

Recommended action:

- Do not onboard by default
- Keep for exception review only if externally validated

## Confidence-First Rule (Important)

Always read `confidence` together with `score`.

Examples:

- High score + low confidence = not enough crawl evidence yet (re-crawl or manually review)
- Low score + high confidence = stronger negative signal
- Mid score + low confidence = inconclusive, needs more evidence

Practical rule:

- If `confidence < 0.6`, treat the result as `Needs manual review` regardless of score

## Suggested Ops Workflow

1. Filter out domains with `confidence < 0.6` into a review queue
2. Auto-prioritize domains with `score >= 80`
3. Manually review domains in the `60-79` range
4. Reject or deprioritize domains `< 40` unless there is a business exception
5. Spot-check a sample from every bucket to validate the model over time

## Calibrating Thresholds for Your Business

The thresholds above are a starting point. Adjust based on your goals:

- Brand safety focus:
  - Raise pass threshold (for example, `>= 85`)
  - Be stricter on low-confidence domains
- Scale / growth focus:
  - Lower pass threshold (for example, `>= 70`) but require sampling QA
- Arbitrage / monetization risk sensitivity:
  - Pay closer attention to ad-density, affiliate, and cluster reasons

Best practice:

- Review actual performance outcomes (RPM, viewability, policy issues, fraud flags) and tune thresholds based on your own results.

## Example Run Commands

Create and activate a virtual environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Run feature extraction:

```powershell
python extract_features.py --input domains.txt --out-jsonl features.jsonl --concurrency 60 --pages 6 --timeout 10 --resume
```

Run scoring/export:

```powershell
python finalize_scores.py --features-jsonl features.jsonl --out-csv scored.csv --out-jsonl scored.jsonl
```

## Notes and Limitations

- Scores depend on what pages were reachable during crawling.
- Some sites block crawlers, which can lower confidence.
- JavaScript-heavy sites may hide content/signals from a simple HTML fetch.
- This tool is a heuristic system, not a definitive fraud/quality detector.

## Quick Troubleshooting

- `features.jsonl` is empty:
  - Check network access and domain list
  - Confirm `extract_features.py` ran successfully
- `KeyError: ['score', 'bucket', 'confidence'] not in index`:
  - You loaded `features.jsonl` instead of `scored.jsonl`
- `.gitignore` not working for outputs:
  - The file was already tracked; run `git rm --cached <file>`
