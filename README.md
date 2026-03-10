# Domain Crawler + Programmatic Advertising Scorer

This tool takes a list of domains, crawls each site, extracts signals related to content quality and monetization patterns, and then assigns a score for how strong the domain looks for programmatic advertising use.

It is designed as a heuristic screening tool for open exchange whitelist / blacklist candidate generation. It is not a performance predictor.

It runs in 2 steps:

1. `extract_features.py` (crawl + feature extraction)
2. `finalize_scores.py` (scoring + labels + CSV export)

## What You Put In

- A text file with one domain per line
- Default location: `input/domains.txt`

Example:

```txt
example.com
news-site.net
mydomain.org
```

## What You Get Out

After both steps run, you get:

- `features.jsonl`: raw extracted features for each domain (technical signals)
- `output/scored.jsonl`: scored results in JSONL format
- `output/scored.csv`: scored results in spreadsheet-friendly format

## Step-by-Step: How It Works

### Step 1: Crawl domains and extract features (`extract_features.py`)

This script reads your input list and processes each domain.

For each domain, it:

1. Normalizes the domain (removes `http://`, `https://`, etc.)
2. Tries to fetch the homepage (`https://...` first, then `http://...`)
3. Detects the final URL after redirects
4. Collects internal links from the homepage
5. Optionally looks for a sitemap (`/sitemap.xml`, `/sitemap_index.xml`)
6. Checks whether `robots.txt` is accessible
7. Fetches a small sample of pages from the site
8. Fetches and analyzes `ads.txt` (if available)
9. Validates `sellers.json` against known major SSPs (if applicable)
10. Attempts a domain age lookup
11. Extracts signals (features) from the HTML and visible text
12. Writes one JSON record (one line) to `features.jsonl`

Important:

- This step does not assign a `score`.
- It only creates the raw signals used by the scoring step.
- After schema changes, old `features.jsonl` files will not contain the new fields. Regenerate features to fully use the upgraded model.

### Step 2: Score each domain (`finalize_scores.py`)

This script reads `features.jsonl` and computes:

- `score` (0-100)
- `confidence` (0.0-1.0)
- `bucket` (`Pass (Fast Track)`, `Manual Review`, `High-Risk Review`, `Reject / Deprioritize`, or `Needs Manual Review`)
- human-readable reasons explaining what influenced the score

It also writes:

- `output/scored.jsonl`
- `output/scored.csv`

At the end of the scoring step, the script prompts for an optional output base name.

Example:

- press Enter: keep `output/scored.csv` and `output/scored.jsonl`
- enter `batch_1`: rename to `output/batch_1.csv` and `output/batch_1.jsonl`

## The Scoring Logic (Plain English)

The scoring step combines several groups of signals.

### 1. Content quality signals

The tool looks for signs that pages contain real content vs. thin or templated content.

Examples:

- median text length across sampled pages
- content uniqueness across sampled pages
- AI-like templated phrasing
- keyword repetition patterns
- thin pagination-style pages
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
- `ads.txt` structural quality
- direct vs reseller relationships
- `sellers.json` mismatches

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
- domain age

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

The scorer converts these patterns into a `network_risk_score`, which is a major negative driver when strong network signals are present.

## How the Final Score Is Built

`finalize_scores.py` computes sub-scores (0-100) and combines them using weights:

- 35% Ads/Monetization
- 30% Cluster similarity / shared IDs
- 20% Content
- 10% Legitimacy
- 5% UX

Then it applies a confidence adjustment based on crawl quality:

- pages fetched vs requested
- success rate of requests
- whether a sitemap was found
- whether `robots.txt` was accessible
- whether the site looked blocked/captcha-protected

So a site with decent raw signals but weak crawl coverage may end up with a lower final score.

Hard-fail rules can override the normal score when confidence is high and severe risk signals are present.

## Tuning the Scoring Model (`scoring_config.json`)

All scoring thresholds, penalty values, and subscore weights live in `scoring_config.json`. You can edit this file to tune the model without changing any Python code.

### What the config controls

- `subscore_weights`: the five weights (ads, cluster, content, legitimacy, ux) — must sum to exactly 1.0
- `confidence`: thresholds and bonuses used to compute crawl confidence
- `finalize`: the confidence multiplier formula (`confidence_floor` + `confidence_weight * confidence`)
- `buckets`: score cutoffs for Pass / Manual Review / High-Risk Review / Reject
- `content`, `ads`, `legitimacy`, `ux`, `network_risk`: individual penalty and bonus values per signal
- `hard_fail`: thresholds for the hard-fail override (score cap and trigger conditions)

### Validation

On every run, the tool checks that `subscore_weights` sums to 1.0. If not, it exits immediately with a clear error listing each weight and the actual total.

### Using a custom config

Pass a different config file at runtime:

```powershell
python finalize_scores.py --features-jsonl features.jsonl --config my_brand_safe_config.json
```

### Using a custom config with the `.exe`

There are two ways:

1. Drop a `scoring_config.json` file next to `domains_scorer.exe`. The tool will use it automatically — no rebuild required.
2. The bundled default config (baked in at build time) is used as a fallback if no override file is present.

This means you can ship one `.exe` and let users adjust scoring behavior by editing a plain JSON file.

## What the Output Fields Mean (High-Level)

In `output/scored.csv` / `output/scored.jsonl`, key fields are:

- `score`: final quality score (0-100)
- `bucket`: easy label for triage
- `confidence`: how reliable the score is based on crawl evidence
- `reasons`: short explanations for the score
- `pages_fetched`, `success_rate`: crawl quality diagnostics
- `adsense_pub_ids`, `gtm_ids`, `ga_ids`: IDs found during crawling
- `ads_txt_quality_score`: structural quality score for `ads.txt`
- `network_risk_score`: strength of batch-level network / clustering risk
- `domain_age_years`: best-effort domain age
- `reseller_ratio`: reseller share inside `ads.txt`
- `direct_relationship_count`: number of DIRECT `ads.txt` relationships
- `cluster_size`: largest detected infrastructure/template cluster size
- `hard_fail_triggered`: whether hard-fail override forced rejection

## How to Use the Results

Recommended workflow:

1. Open `output/scored.csv` — rows are already sorted highest score first
2. Review `bucket` and `confidence`
3. Read `reasons` for domains with very low or very high scores
4. Manually review a sample of domains before making business decisions

Best practice:

- Treat this as a screening/ranking tool, not a final approval system.
- Use it to prioritize review and reduce manual work.

## Business Interpretation (Pass / Review / Reject)

Use the score as an operational triage signal, not a standalone approval decision.

Suggested thresholds (starting point):

- `85-100` -> `Pass (Fast Track)`
- `70-84` -> `Manual Review`
- `40-69` -> `High-Risk Review`
- `0-39` -> `Reject / Deprioritize`

Important:

- If `confidence < 0.6`, bucket is always `Needs Manual Review`
- If a hard-fail rule triggers at high confidence, bucket is forced to `Reject / Deprioritize` and score is capped at `35`

How to apply these in practice:

### `85-100` (Pass / Fast Track)

Typical meaning:

- Good content signals
- Lower signs of aggressive arbitrage behavior
- Better crawl evidence / confidence

Recommended action:

- Move to business/commercial checks (pricing, geography, inventory type, policy fit)
- Do a light manual QA sample before activation

### `70-84` (Manual Review)

Typical meaning:

- Mixed signals
- Possibly decent domains with some risk markers (thin pages, heavy scripts, weak crawl evidence)

Recommended action:

- Review `reasons` and `confidence`
- Manually inspect homepage + 2-3 article/content pages
- Verify ad density and user experience

### `40-69` (High-Risk Review)

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

- If `confidence < 0.6`, treat the result as `Needs Manual Review` regardless of score

## Suggested Ops Workflow

1. Filter out domains with `confidence < 0.6` into a review queue
2. Auto-prioritize domains with `score >= 85`
3. Manually review domains in the `70-84` range
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
  - Pay closer attention to ad-density, `ads.txt`, reseller ratio, and cluster reasons

Best practice:

- Review actual performance outcomes (RPM, viewability, policy issues, fraud flags) and tune thresholds based on your own results.

## Run Options

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

After upgrading from an older version of the tool, regenerate `features.jsonl` to populate new fields such as `ads_txt_quality_score`, `domain_age_years`, and stronger content / cluster signals.

Run feature extraction:

```powershell
python extract_features.py --input input/domains.txt --out-jsonl features.jsonl --concurrency 60 --pages 6 --timeout 10 --resume
```

Run scoring/export:

```powershell
python finalize_scores.py --features-jsonl features.jsonl --out-csv scored.csv --out-jsonl scored.jsonl
```

One-command PowerShell runner:

```powershell
.\run.ps1
```

What `run.ps1` does:

- prompts for input values
- validates each value immediately
- runs extraction first
- runs scoring only if extraction succeeds

One-command macOS runner:

```bash
chmod +x run.command
./run.command
```

What `run.command` does:

- uses `.venv/bin/python3`
- runs the same interactive launcher as the Windows exe
- opens a file picker for the domains file when available
- runs extraction first, then scoring

## Windows `.exe` Build and Distribution

Build dependency:

```powershell
python -m pip install pyinstaller
```

Build the executable:

```powershell
.\build_exe.ps1
```

The build output is:

- `dist/domains_scorer.exe`

To run on another Windows machine, ship this layout:

```txt
dist/
  domains_scorer.exe
  input/
    domains.txt
  scoring_config.json   ← optional: drop here to override bundled scoring defaults
```

When the exe runs, it:

- defaults to `input/domains.txt` next to the exe
- validates each prompt as it is entered
- writes `features.jsonl` next to the exe
- writes scored files into `output/`
- prints the real error instead of silently closing if something fails

For best troubleshooting, run the exe from PowerShell:

```powershell
.\domains_scorer.exe
```

## macOS Build and Distribution

If the Mac is a fresh machine with nothing installed, use this full setup flow.

### Build on macOS From Scratch

1. Install Python 3.12

- Download and install Python 3.12 from `python.org`, or install it with Homebrew:

```bash
brew install python@3.12
```

2. Open Terminal and go to the project folder

```bash
cd /path/to/crawler_scoring
```

3. Create a virtual environment

```bash
python3.12 -m venv .venv
```

4. Activate the virtual environment

```bash
source .venv/bin/activate
```

5. Upgrade pip

```bash
python -m pip install --upgrade pip
```

6. Install project dependencies

```bash
python -m pip install -r requirements.txt
```

7. Install PyInstaller

```bash
python -m pip install pyinstaller
```

8. Make the build script executable

```bash
chmod +x build_macos.sh
```

9. Build the macOS binary

```bash
./build_macos.sh
```

10. Run the built binary

```bash
./dist/domains_scorer
```

Expected output:

- built binary: `dist/domains_scorer`
- runtime outputs:
  - `features.jsonl`
  - `output/*.csv`
  - `output/*.jsonl`

Set up a virtual environment:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller
```

Build the macOS binary:

```bash
chmod +x build_macos.sh
./build_macos.sh
```

The build output is:

- `dist/domains_scorer`

To run on another Mac, ship this layout:

```txt
dist/
  domains_scorer
  input/
    domains.txt
  scoring_config.json   ← optional: drop here to override bundled scoring defaults
```

Notes:

- macOS builds must be created on macOS. You cannot reliably build a native macOS binary from Windows.
- The first run may trigger Gatekeeper warnings on another Mac if the binary is unsigned.
- If you do not need a packaged binary, `run.command` is the simpler path.

## Notes and Limitations

- Scores depend on what pages were reachable during crawling.
- Some sites block crawlers, which can lower confidence.
- JavaScript-heavy sites may hide content/signals from a simple HTML fetch.
- `ads.txt`, `sellers.json`, and WHOIS checks are best-effort and may fail or be unavailable for some domains.
- This tool is a heuristic system, not a definitive fraud/quality detector.

## Quick Troubleshooting

- `features.jsonl` is empty:
  - Check network access and domain list
  - Confirm `extract_features.py` ran successfully
- The exe closes too quickly:
  - Rebuild it after code changes using `.\build_exe.ps1`
  - Run it from PowerShell to see the printed error
  - Check the timestamped file in `logs/`
- `KeyError: ['score', 'bucket', 'confidence'] not in index`:
  - You loaded `features.jsonl` instead of `output/scored.jsonl`
- `ValueError: subscore_weights must sum to 1.0`:
  - Open `scoring_config.json` and check the `subscore_weights` section
  - The five values (ads, cluster, content, legitimacy, ux) must add up to exactly 1.0
- `.gitignore` not working for outputs:
  - The file was already tracked; run `git rm --cached <file>`
