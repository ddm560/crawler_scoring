#!/usr/bin/env python3
import argparse
import asyncio
import json
import random
import re
import sys
import time
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup
import tldextract

#
WORD_RE = re.compile(r"[a-zA-Z0-9]{3,}")
WS_RE = re.compile(r"\s+")
SCRIPT_SRC_RE = re.compile(r"<script[^>]+src=['\"]([^'\"]+)['\"]", re.IGNORECASE)
ADSENSE_PUB_RE = re.compile(r"(?:ca-pub-|pub-)(\d{10,})", re.IGNORECASE)
GTM_RE = re.compile(r"\bGTM-[A-Z0-9]+\b", re.IGNORECASE)
GA4_RE = re.compile(r"\bG-[A-Z0-9]{6,}\b")
UA_RE = re.compile(r"\bUA-\d{4,}-\d+\b")

PUSH_KEYWORDS = re.compile(r"push|notification|subscribe to notifications", re.IGNORECASE)
INTERSTITIAL_KEYWORDS = re.compile(r"overlay|modal|interstitial|subscribe to continue|disable adblock", re.IGNORECASE)
AUTOREFRESH_KEYWORDS = re.compile(r"setinterval|refresh.*ad|googletag.*refresh", re.IGNORECASE)

AD_CONTAINER_PATTERNS = [
    re.compile(r"\b(ad|ads|advert|sponsor)\b", re.IGNORECASE),
    re.compile(r"\bgpt\b", re.IGNORECASE),
    re.compile(r"google_ads_iframe", re.IGNORECASE),
    re.compile(r"\bprebid\b", re.IGNORECASE),
]

AFFILIATE_MARKERS = ["ref=", "aff=", "affiliate", "utm_aff", "partner=", "tracking", "clickid="]


def normalize_domain(d: str) -> str:
    d = d.strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = d.split("/")[0].strip(".")
    return d


def registrable_domain(host: str) -> str:
    ext = tldextract.extract(host)
    if not ext.domain or not ext.suffix:
        return host.lower()
    return f"{ext.domain}.{ext.suffix}".lower()


def strip_visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    text = WS_RE.sub(" ", text).strip()
    return text


def tokenize(text: str) -> List[str]:
    return WORD_RE.findall(text.lower())


def simhash(tokens: List[str], bits: int = 64) -> int:
    if not tokens:
        return 0
    freq: Dict[str, int] = {}
    for t in tokens:
        freq[t] = freq.get(t, 0) + 1
    v = [0] * bits
    for t, w in freq.items():
        h = int.from_bytes(__import__("hashlib").blake2b(t.encode("utf-8"), digest_size=8).digest(), "big")
        for i in range(bits):
            v[i] += w if ((h >> i) & 1) else -w
    out = 0
    for i in range(bits):
        if v[i] > 0:
            out |= (1 << i)
    return out


def extract_ids(html: str) -> Tuple[List[str], List[str], List[str]]:
    adsense = sorted(set(f"pub-{m}" for m in ADSENSE_PUB_RE.findall(html)))
    gtm = sorted(set(GTM_RE.findall(html)))
    ga = sorted(set(GA4_RE.findall(html) + UA_RE.findall(html)))
    return adsense, gtm, ga


def third_party_script_count(base_reg_domain: str, script_srcs: List[str]) -> int:
    c = 0
    for src in script_srcs:
        u = urlparse(src)
        if not u.netloc:
            continue
        if registrable_domain(u.netloc) != base_reg_domain:
            c += 1
    return c


def count_ad_containers(soup: BeautifulSoup) -> int:
    count = 0
    for tag in soup.find_all(True):
        attrs = " ".join([
            str(tag.get("id", "")),
            " ".join(tag.get("class", []) if isinstance(tag.get("class", []), list) else [str(tag.get("class", ""))]),
        ])
        if not attrs.strip():
            continue
        if any(p.search(attrs) for p in AD_CONTAINER_PATTERNS):
            count += 1
    return count


def compute_external_link_ratio(base_reg_domain: str, soup: BeautifulSoup) -> float:
    total = 0
    external = 0
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        total += 1
        if href.startswith("/"):
            continue
        u = urlparse(href)
        if u.scheme.startswith("http") and u.netloc:
            if registrable_domain(u.netloc) != base_reg_domain:
                external += 1
    return 0.0 if total == 0 else (external / total)


def extract_internal_links(base_url: str, html: str, reg_domain: str, limit: int = 30) -> List[str]:
    soup = BeautifulSoup(html, "lxml")
    links: List[str] = []
    seen: Set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        abs_url = urljoin(base_url, href)
        u = urlparse(abs_url)
        if not u.scheme.startswith("http"):
            continue
        if registrable_domain(u.netloc) != reg_domain:
            continue

        # trap avoidance
        if u.query and u.query.count("&") >= 2:
            continue
        path = (u.path or "/").lower()
        if any(tok in path for tok in ["calendar", "wp-json", "replytocom"]):
            continue

        clean = f"{u.scheme}://{u.netloc}{u.path}"
        if clean not in seen:
            seen.add(clean)
            links.append(clean)
        if len(links) >= limit:
            break
    return links


async def fetch_html(
    session: aiohttp.ClientSession,
    url: str,
    timeout_s: float,
    max_bytes: int,
) -> Tuple[int, str, str]:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout_s), allow_redirects=True) as resp:
            status = resp.status
            final_url = str(resp.url)
            data = await resp.content.read(max_bytes + 1)
            if len(data) > max_bytes:
                data = data[:max_bytes]
            try:
                html = data.decode(resp.charset or "utf-8", errors="replace")
            except Exception:
                html = data.decode("utf-8", errors="replace")
            return status, final_url, html
    except Exception:
        return 0, url, ""


async def try_sitemap(
    session: aiohttp.ClientSession,
    base: str,
    timeout_s: float,
    max_bytes: int,
) -> Optional[List[str]]:
    for path in ["/sitemap.xml", "/sitemap_index.xml"]:
        status, _, html = await fetch_html(session, urljoin(base, path), timeout_s, max_bytes)
        if status and status < 400 and html and ("<urlset" in html or "<sitemapindex" in html):
            locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", html, flags=re.IGNORECASE)
            urls = [loc.strip() for loc in locs if loc.strip().startswith("http")]
            return urls[:2000]
    return None


def detect_nav_pages(html: str) -> Tuple[bool, bool, bool]:
    lower = html.lower()
    has_about = ("/about" in lower) or ("about-us" in lower)
    has_contact = ("/contact" in lower) or ("contact-us" in lower)
    has_privacy = ("/privacy" in lower) or ("privacy policy" in lower)
    has_terms = ("/terms" in lower) or ("terms of service" in lower)
    return bool(has_about), bool(has_contact), bool(has_privacy and has_terms)


def looks_parked_or_for_sale(text: str) -> bool:
    t = text.lower()
    patterns = ["domain for sale", "buy this domain", "this domain is for sale", "sedo", "afternic", "hugedomains", "parkingcrew"]
    return any(p in t for p in patterns)


def has_article_schema(html: str) -> bool:
    h = html.lower()
    return ("newsarticle" in h) or ("\"@type\":\"article\"" in h) or ("\"@type\": \"article\"" in h)


def has_affiliate_markers(html: str) -> bool:
    h = html.lower()
    return any(m in h for m in AFFILIATE_MARKERS)


@dataclass
class DomainFeatures:
    input_domain: str
    final_url: str
    reg_domain: str

    pages_attempted: int
    pages_fetched: int
    success_rate: float

    sitemap_found: bool
    blocked_or_captcha: bool

    # IDs
    adsense_pub_ids: List[str]
    gtm_ids: List[str]
    ga_ids: List[str]

    # Aggregates
    homepage_text_len: int
    median_text_len: int
    median_script_src_count: int
    median_third_party_scripts: int
    median_ad_container_count: int
    median_external_link_ratio: float

    has_about: bool
    has_contact: bool
    has_privacy_terms: bool
    looks_parked: bool
    has_article_schema: bool
    affiliate_markers: bool

    has_push_keywords: bool
    has_interstitial_keywords: bool
    has_autorefresh_keywords: bool

    homepage_simhash: int
    boilerplate_ratio: float  # rough within-domain similarity proxy (v1)


def median_int(vals: List[int]) -> int:
    if not vals:
        return 0
    vs = sorted(vals)
    return vs[len(vs) // 2]


def median_float(vals: List[float]) -> float:
    if not vals:
        return 0.0
    vs = sorted(vals)
    return float(vs[len(vs) // 2])


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def compute_boilerplate_ratio(texts: List[str]) -> float:
    # v1: use simhash distance among up to 3 texts (home + 2)
    if len(texts) < 2:
        return 0.5
    hashes = [simhash(tokenize(t)) for t in texts if t]
    if len(hashes) < 2:
        return 0.5
    dists = []
    for i in range(len(hashes)):
        for j in range(i + 1, len(hashes)):
            dists.append((hashes[i] ^ hashes[j]).bit_count())
    avg = sum(dists) / len(dists)  # 0..64
    ratio = 1.0 - clamp((avg - 8) / (28 - 8), 0.0, 1.0)
    return ratio


async def process_domain(
    domain: str,
    session: aiohttp.ClientSession,
    pages: int,
    timeout_s: float,
    max_bytes: int,
) -> DomainFeatures:
    d0 = normalize_domain(domain)
    candidates = [f"https://{d0}/", f"http://{d0}/"]

    status = 0
    final_url = candidates[0]
    home_html = ""

    for u in candidates:
        status, final_url, home_html = await fetch_html(session, u, timeout_s, max_bytes)
        if status and status < 500 and home_html:
            break

    host = urlparse(final_url).netloc or d0
    reg = registrable_domain(host)
    base = f"{urlparse(final_url).scheme}://{urlparse(final_url).netloc}/"

    blocked = False
    if home_html:
        lh = home_html.lower()
        if "captcha" in lh or ("cloudflare" in lh and "attention required" in lh):
            blocked = True

    pages_to_fetch: List[str] = []
    sitemap_found = False

    if home_html:
        pages_to_fetch.append(final_url)
        internal = extract_internal_links(final_url, home_html, reg_domain=reg, limit=30)
        random.shuffle(internal)
        pages_to_fetch.extend(internal[: max(0, pages - 1)])

        sitemap_urls = await try_sitemap(session, base, timeout_s, max_bytes)
        sitemap_found = sitemap_urls is not None
        if sitemap_urls:
            sample = []
            for u in random.sample(sitemap_urls, k=min(10, len(sitemap_urls))):
                if registrable_domain(urlparse(u).netloc) == reg:
                    sample.append(u)
                if len(sample) >= 3:
                    break
            for u in sample:
                if len(pages_to_fetch) < pages + 3:
                    pages_to_fetch.append(u)

    # Fetch pages
    text_lens: List[int] = []
    script_src_counts: List[int] = []
    third_party_scripts: List[int] = []
    ad_counts: List[int] = []
    external_ratios: List[float] = []

    all_adsense: Set[str] = set()
    all_gtm: Set[str] = set()
    all_ga: Set[str] = set()

    any_push = False
    any_interstitial = False
    any_autorefresh = False

    pages_attempted = len(pages_to_fetch)
    pages_fetched = 0

    # For boilerplate ratio: keep up to 3 texts (home + 2 internals)
    boiler_texts: List[str] = []

    for idx, u in enumerate(pages_to_fetch):
        st, fu, html = await fetch_html(session, u, timeout_s, max_bytes)
        if st and st < 500 and html:
            pages_fetched += 1
        if not html:
            continue

        # capture a few texts for boilerplate proxy
        if len(boiler_texts) < 3:
            boiler_texts.append(strip_visible_text(html))

        soup = BeautifulSoup(html, "lxml")
        text = boiler_texts[-1] if boiler_texts else strip_visible_text(html)
        scripts = SCRIPT_SRC_RE.findall(html)
        adsense, gtm, ga = extract_ids(html)

        all_adsense.update(adsense)
        all_gtm.update(gtm)
        all_ga.update(ga)

        text_lens.append(len(text))
        script_src_counts.append(len(scripts))
        third_party_scripts.append(third_party_script_count(reg, scripts))
        ad_counts.append(count_ad_containers(soup))
        external_ratios.append(compute_external_link_ratio(reg, soup))

        any_push = any_push or bool(PUSH_KEYWORDS.search(html))
        any_interstitial = any_interstitial or bool(INTERSTITIAL_KEYWORDS.search(html))
        any_autorefresh = any_autorefresh or bool(AUTOREFRESH_KEYWORDS.search(html))

    success_rate = (pages_fetched / pages_attempted) if pages_attempted else 0.0

    home_text = strip_visible_text(home_html) if home_html else ""
    home_sim = simhash(tokenize(home_text)) if home_text else 0
    boil = compute_boilerplate_ratio(boiler_texts)

    has_about, has_contact, has_privacy_terms = detect_nav_pages(home_html or "")
    parked = looks_parked_or_for_sale(home_text)
    article_schema = has_article_schema(home_html or "")
    affiliate = has_affiliate_markers(home_html or "")

    return DomainFeatures(
        input_domain=d0,
        final_url=final_url,
        reg_domain=reg,

        pages_attempted=pages_attempted,
        pages_fetched=pages_fetched,
        success_rate=round(success_rate, 3),

        sitemap_found=bool(sitemap_found),
        blocked_or_captcha=bool(blocked),

        adsense_pub_ids=sorted(all_adsense),
        gtm_ids=sorted(all_gtm),
        ga_ids=sorted(all_ga),

        homepage_text_len=len(home_text),
        median_text_len=median_int([x for x in text_lens if x > 0]),
        median_script_src_count=median_int(script_src_counts),
        median_third_party_scripts=median_int(third_party_scripts),
        median_ad_container_count=median_int(ad_counts),
        median_external_link_ratio=round(median_float(external_ratios), 3),

        has_about=has_about,
        has_contact=has_contact,
        has_privacy_terms=has_privacy_terms,
        looks_parked=parked,
        has_article_schema=article_schema,
        affiliate_markers=affiliate,

        has_push_keywords=any_push,
        has_interstitial_keywords=any_interstitial,
        has_autorefresh_keywords=any_autorefresh,

        homepage_simhash=int(home_sim),
        boilerplate_ratio=round(boil, 3),
    )


def load_done_domains(jsonl_path: str) -> Set[str]:
    done: Set[str] = set()
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    d = obj.get("input_domain")
                    if d:
                        done.add(str(d).lower())
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return done


async def main_async(args) -> int:
    with open(args.input, "r", encoding="utf-8") as f:
        domains = [normalize_domain(line) for line in f if line.strip()]
    domains = [d for d in domains if d]

    done = load_done_domains(args.out_jsonl) if args.resume else set()
    todo = [d for d in domains if d not in done]

    if not todo:
        print("Nothing to do (all domains already present in output JSONL).")
        return 0

    connector = aiohttp.TCPConnector(ssl=False, limit=args.concurrency)
    headers = {"User-Agent": args.user_agent}
    sem = asyncio.Semaphore(args.concurrency)

    started = time.time()
    completed = 0
    total = len(todo)

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        with open(args.out_jsonl, "a", encoding="utf-8") as out:
            async def bound(d: str) -> DomainFeatures:
                async with sem:
                    return await process_domain(d, session, args.pages, args.timeout, args.max_bytes)

            tasks = [asyncio.create_task(bound(d)) for d in todo]

            for coro in asyncio.as_completed(tasks):
                feat = await coro
                out.write(json.dumps(asdict(feat), ensure_ascii=False) + "\n")
                out.flush()

                completed += 1
                if completed % args.log_every == 0 or completed == total:
                    elapsed = max(0.001, time.time() - started)
                    rate = completed / elapsed
                    eta_s = (total - completed) / rate if rate > 0 else 0
                    print(f"[{completed}/{total}] {rate:.2f} domains/s | ETA ~ {eta_s/60:.1f} min", flush=True)

    print("Feature extraction complete.")
    print(f"Wrote: {args.out_jsonl}")
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser("extract_features.py — crawl + feature extraction (streaming JSONL + resume)")
    p.add_argument("--input", required=True, help="Text file with one root domain per line")
    p.add_argument("--out-jsonl", required=True, help="Output JSONL path (append)")
    p.add_argument("--pages", type=int, default=6, help="Target pages per domain (home + internal sample)")
    p.add_argument("--concurrency", type=int, default=60, help="Global concurrency (start 25-60 on laptop)")
    p.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout seconds")
    p.add_argument("--max-bytes", type=int, default=3_000_000, help="Max bytes per HTML response")
    p.add_argument("--resume", action="store_true", help="Skip domains already in out-jsonl")
    p.add_argument("--log-every", type=int, default=100, help="Progress log interval (domains)")
    p.add_argument("--user-agent", default="InventoryVettingBot/1.0 (+contact: ops@example.com)", help="User-Agent string")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return int(asyncio.run(main_async(args)))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
