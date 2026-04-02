#!/usr/bin/env python3
from __future__ import annotations
import argparse
import asyncio
import json
import os
import random
import re
import sys
import time
import warnings
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import tldextract
try:
    import whois
except Exception:
    whois = None

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

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
    re.compile(r"(?:^|[-_.])(ad|ads|advert|sponsor)(?:[-_.]|$)", re.IGNORECASE),
    re.compile(r"(?:^|[-_.])gpt(?:[-_.]|$)", re.IGNORECASE),
    re.compile(r"google_ads_iframe", re.IGNORECASE),
    re.compile(r"\bprebid\b", re.IGNORECASE),
]

AFFILIATE_MARKERS = ["ref=", "aff=", "affiliate", "utm_aff", "partner=", "tracking", "clickid="]
MFA_KEYWORDS = re.compile(
    r"related searches|sponsored listings|top picks for you|best .* deals|you may also like|compare now",
    re.IGNORECASE,
)
AI_TEMPLATE_PATTERNS = [
    re.compile(r"\bin conclusion\b", re.IGNORECASE),
    re.compile(r"\bit is important to note\b", re.IGNORECASE),
    re.compile(r"\bin today's fast-paced digital landscape\b", re.IGNORECASE),
    re.compile(r"\bdelve into\b", re.IGNORECASE),
]
PAGINATION_PATH_PATTERNS = ["/page/", "?page=", "&page=", "/p/"]
KNOWN_SSP_ROOTS = {
    "google.com": "https://google.com/sellers.json",
    "doubleclick.net": "https://google.com/sellers.json",
    "rubiconproject.com": "https://rubiconproject.com/sellers.json",
    "pubmatic.com": "https://pubmatic.com/sellers.json",
    "openx.com": "https://openx.com/sellers.json",
    "indexexchange.com": "https://indexexchange.com/sellers.json",
    "appnexus.com": "https://appnexus.com/sellers.json",
    "xandr.com": "https://xandr.com/sellers.json",
    "triplelift.com": "https://triplelift.com/sellers.json",
    "criteo.com": "https://criteo.com/sellers.json",
    "sharethrough.com": "https://sharethrough.com/sellers.json",
    "sonobi.com": "https://sonobi.com/sellers.json",
}


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
    stats: NetworkStats,
) -> Tuple[int, str, str]:
    stats.requests_attempted += 1
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout_s), allow_redirects=True) as resp:
            status = resp.status
            final_url = str(resp.url)
            data = await resp.content.read(max_bytes + 1)
            if len(data) > max_bytes:
                data = data[:max_bytes]
            stats.requests_succeeded += 1
            stats.bytes_downloaded += len(data)
            try:
                html = data.decode(resp.charset or "utf-8", errors="replace")
            except Exception:
                html = data.decode("utf-8", errors="replace")
            return status, final_url, html
    except asyncio.TimeoutError:
        stats.requests_failed += 1
        stats.timeouts += 1
        return 0, url, ""
    except Exception:
        stats.requests_failed += 1
        return 0, url, ""


async def try_sitemap(
    session: aiohttp.ClientSession,
    base: str,
    timeout_s: float,
    max_bytes: int,
    stats: NetworkStats,
) -> Optional[List[str]]:
    for path in ["/sitemap.xml", "/sitemap_index.xml"]:
        status, _, html = await fetch_html(session, urljoin(base, path), timeout_s, max_bytes, stats)
        if status and status < 400 and html and ("<urlset" in html or "<sitemapindex" in html):
            locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", html, flags=re.IGNORECASE)
            urls = [loc.strip() for loc in locs if loc.strip().startswith("http")]
            return urls[:2000]
    return None


async def fetch_resource_text(
    session: aiohttp.ClientSession,
    url: str,
    timeout_s: float,
    max_bytes: int,
    stats: NetworkStats,
) -> Tuple[int, str]:
    status, _, text = await fetch_html(session, url, timeout_s, max_bytes, stats)
    return status, text


async def fetch_robots_txt(
    session: aiohttp.ClientSession,
    base: str,
    timeout_s: float,
    max_bytes: int,
    stats: NetworkStats,
) -> bool:
    status, text = await fetch_resource_text(session, urljoin(base, "/robots.txt"), timeout_s, max_bytes, stats)
    return bool(status and status < 500 and text is not None)


async def fetch_ads_txt_summary(
    session: aiohttp.ClientSession,
    base: str,
    timeout_s: float,
    max_bytes: int,
    stats: NetworkStats,
) -> Dict[str, object]:
    status, text = await fetch_resource_text(session, urljoin(base, "/ads.txt"), timeout_s, max_bytes, stats)
    if not status or status >= 500 or not text:
        return {
            "present": False,
            "total_lines": 0,
            "unique_ssp_domains": 0,
            "direct_relationship_count": 0,
            "reseller_relationship_count": 0,
            "reseller_ratio": 0.0,
            "duplicate_count": 0,
            "ssp_diversity_score": 0.0,
            "excessive_reseller_chain_risk": False,
            "quality_score": 40,
            "entries": [],
            "reasons": ["ads.txt not found or unreadable"],
        }
    return parse_ads_txt(text)


async def validate_sellers_json(
    session: aiohttp.ClientSession,
    reg_domain: str,
    ads_entries: List[Dict[str, str]],
    timeout_s: float,
    max_bytes: int,
    stats: NetworkStats,
) -> Tuple[int, int, List[str]]:
    checked = 0
    mismatches = 0
    reasons: List[str] = []

    by_ssp: Dict[str, List[Dict[str, str]]] = {}
    for entry in ads_entries:
        url = sellers_json_url_for_ssp(entry["ssp_domain"])
        if not url:
            continue
        by_ssp.setdefault(url, []).append(entry)

    for url, entries in list(by_ssp.items())[:6]:
        status, text = await fetch_resource_text(session, url, timeout_s, max_bytes, stats)
        if not status or status >= 500 or not text:
            continue
        try:
            payload = json.loads(text)
        except Exception:
            continue

        sellers = payload.get("sellers", [])
        seller_map = {str(item.get("seller_id", "")): item for item in sellers if item.get("seller_id") is not None}
        checked += len(entries)
        for entry in entries:
            seller = seller_map.get(entry["account_id"])
            if not seller:
                mismatches += 1
                reasons.append(f"sellers.json missing publisher ID {entry['account_id']} for {entry['ssp_domain']}")
                continue
            seller_type = str(seller.get("seller_type", "")).upper()
            relationship = entry["relationship"]
            if relationship == "DIRECT" and seller_type not in {"PUBLISHER", "BOTH"}:
                mismatches += 1
                reasons.append(f"sellers.json seller type mismatch for {entry['ssp_domain']} ({entry['account_id']})")
            if relationship == "RESELLER" and seller_type == "PUBLISHER":
                mismatches += 1
                reasons.append(f"sellers.json seller type mismatch for reseller {entry['ssp_domain']} ({entry['account_id']})")
            seller_domain = normalize_domain(str(seller.get("domain", "") or ""))
            if seller_domain and seller_domain != reg_domain and reg_domain not in seller_domain and seller_domain not in reg_domain:
                mismatches += 1
                reasons.append(f"sellers.json domain mismatch for {entry['ssp_domain']} ({entry['account_id']})")

    deduped = []
    for reason in reasons:
        if reason not in deduped:
            deduped.append(reason)
    return checked, mismatches, deduped[:6]


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


def hamming_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def compute_content_uniqueness_score(texts: List[str]) -> float:
    if len(texts) < 2:
        return 0.5
    hashes = [simhash(tokenize(t)) for t in texts if t]
    if len(hashes) < 2:
        return 0.5
    dists = []
    for i in range(len(hashes)):
        for j in range(i + 1, len(hashes)):
            dists.append(hamming_distance(hashes[i], hashes[j]) / 64.0)
    return round(clamp(sum(dists) / len(dists), 0.0, 1.0), 3)


def compute_ai_template_score(texts: List[str]) -> float:
    if not texts:
        return 0.0
    hits = 0
    for text in texts:
        lowered = text.lower()
        if any(p.search(lowered) for p in AI_TEMPLATE_PATTERNS):
            hits += 1
    return round(hits / len(texts), 3)


def compute_keyword_repetition_score(texts: List[str]) -> float:
    if not texts:
        return 0.0
    all_tokens: List[str] = []
    for text in texts:
        all_tokens.extend(tokenize(text))
    if not all_tokens:
        return 0.0
    freq: Dict[str, int] = {}
    for token in all_tokens:
        freq[token] = freq.get(token, 0) + 1
    top_share = max(freq.values()) / max(1, len(all_tokens))
    return round(clamp(top_share * 4.0, 0.0, 1.0), 3)


def compute_pagination_thin_ratio(urls: List[str], text_lens: List[int]) -> float:
    if not urls or not text_lens:
        return 0.0
    flagged = 0
    total = min(len(urls), len(text_lens))
    for idx in range(total):
        url = urls[idx].lower()
        if any(tok in url for tok in PAGINATION_PATH_PATTERNS) and text_lens[idx] < 700:
            flagged += 1
    return round(flagged / total, 3)


def parse_ads_txt(text: str) -> Dict[str, object]:
    entries = []
    duplicates = 0
    seen: Set[Tuple[str, str, str]] = set()
    unique_ssps: Set[str] = set()
    direct = 0
    reseller = 0
    total_lines = 0

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        total_lines += 1
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 3:
            continue
        ssp_domain = normalize_domain(parts[0])
        account_id = parts[1]
        relationship = parts[2].upper()
        key = (ssp_domain, account_id, relationship)
        if key in seen:
            duplicates += 1
        seen.add(key)
        unique_ssps.add(ssp_domain)
        if relationship == "DIRECT":
            direct += 1
        elif relationship == "RESELLER":
            reseller += 1
        entries.append(
            {
                "ssp_domain": ssp_domain,
                "account_id": account_id,
                "relationship": relationship,
            }
        )

    total_relationships = direct + reseller
    reseller_ratio = (reseller / total_relationships) if total_relationships else 0.0
    diversity_score = clamp(len(unique_ssps) / 12.0, 0.0, 1.0)
    excessive_reseller_chain_risk = reseller_ratio > 0.7 and reseller >= 8

    quality = 100
    reasons: List[str] = []
    if total_lines > 150:
        quality -= 30
        reasons.append("Very large ads.txt file")
    elif total_lines > 75:
        quality -= 15
        reasons.append("Bloated ads.txt file")
    if reseller_ratio > 0.7:
        quality -= 25
        reasons.append("Extremely high reseller ratio in ads.txt")
    elif reseller_ratio > 0.5:
        quality -= 10
        reasons.append("Elevated reseller ratio in ads.txt")
    if direct == 0 and total_lines > 0:
        quality -= 25
        reasons.append("No DIRECT relationships in ads.txt")
    if duplicates >= 3:
        quality -= 20
        reasons.append("Suspicious duplicate entries in ads.txt")
    elif duplicates > 0:
        quality -= 10
        reasons.append("Duplicate entries in ads.txt")
    if len(unique_ssps) < 2 and total_lines >= 5:
        quality -= 10
        reasons.append("Low SSP diversity in ads.txt")

    return {
        "present": bool(total_lines),
        "total_lines": total_lines,
        "unique_ssp_domains": len(unique_ssps),
        "direct_relationship_count": direct,
        "reseller_relationship_count": reseller,
        "reseller_ratio": round(reseller_ratio, 3),
        "duplicate_count": duplicates,
        "ssp_diversity_score": round(diversity_score, 3),
        "excessive_reseller_chain_risk": excessive_reseller_chain_risk,
        "quality_score": int(clamp(quality, 0, 100)),
        "entries": entries,
        "reasons": reasons,
    }


def sellers_json_url_for_ssp(ssp_domain: str) -> Optional[str]:
    reg = registrable_domain(ssp_domain)
    return KNOWN_SSP_ROOTS.get(reg)


def normalize_whois_date(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, list):
        values = [normalize_whois_date(v) for v in value]
        values = [v for v in values if v is not None]
        return min(values) if values else None
    try:
        timestamp = value.timestamp()
        age_seconds = max(0.0, time.time() - timestamp)
        return round(age_seconds / (365.25 * 24 * 3600), 2)
    except Exception:
        return None


def lookup_domain_age_years(domain: str) -> float:
    if whois is None:
        return -1.0
    try:
        result = whois.whois(domain)
        creation_date = getattr(result, "creation_date", None)
        age = normalize_whois_date(creation_date)
        return age if age is not None else -1.0
    except Exception:
        return -1.0


@dataclass
class DomainFeatures:
    input_domain: str
    final_url: str
    reg_domain: str

    pages_attempted: int
    pages_fetched: int
    success_rate: float

    sitemap_found: bool
    robots_txt_accessible: bool
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
    has_mfa_keywords: bool

    homepage_simhash: int
    boilerplate_ratio: float  # rough within-domain similarity proxy (v1)
    content_uniqueness_score: float
    ai_template_score: float
    keyword_repetition_score: float
    pagination_thin_ratio: float

    ads_txt_present: bool
    ads_txt_total_lines: int
    ads_txt_unique_ssp_domains: int
    direct_relationship_count: int
    reseller_relationship_count: int
    reseller_ratio: float
    ads_txt_duplicate_count: int
    ads_txt_quality_score: int
    sellers_json_checked: int
    sellers_json_mismatches: int
    sellers_json_reasons: List[str]

    domain_age_years: float


@dataclass
class NetworkStats:
    requests_attempted: int = 0
    requests_succeeded: int = 0
    requests_failed: int = 0
    timeouts: int = 0
    bytes_downloaded: int = 0


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
    stats: NetworkStats,
) -> DomainFeatures:
    d0 = normalize_domain(domain)
    candidates = [f"https://{d0}/", f"http://{d0}/"]

    status = 0
    final_url = candidates[0]
    home_html = ""

    for u in candidates:
        status, final_url, home_html = await fetch_html(session, u, timeout_s, max_bytes, stats)
        if status and status < 500 and home_html:
            break

    host = urlparse(final_url).netloc or d0
    reg = registrable_domain(host)
    base = f"{urlparse(final_url).scheme}://{urlparse(final_url).netloc}/"

    blocked = False
    robots_txt_accessible = False
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
        robots_txt_accessible = await fetch_robots_txt(session, base, timeout_s, max_bytes, stats)

        sitemap_urls = await try_sitemap(session, base, timeout_s, max_bytes, stats)
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

    ads_txt_summary = await fetch_ads_txt_summary(session, base, timeout_s, max_bytes, stats)
    sellers_checked, sellers_mismatches, sellers_reasons = await validate_sellers_json(
        session,
        reg,
        ads_txt_summary.get("entries", []),
        timeout_s,
        max_bytes,
        stats,
    )
    domain_age_years = await asyncio.to_thread(lookup_domain_age_years, reg)

    # Fetch pages
    text_lens: List[int] = []
    script_src_counts: List[int] = []
    third_party_scripts: List[int] = []
    ad_counts: List[int] = []
    external_ratios: List[float] = []
    fetched_urls: List[str] = []

    all_adsense: Set[str] = set()
    all_gtm: Set[str] = set()
    all_ga: Set[str] = set()

    any_push = False
    any_interstitial = False
    any_autorefresh = False
    any_mfa = False

    pages_attempted = len(pages_to_fetch)
    pages_fetched = 0

    # For boilerplate ratio: keep up to 3 texts (home + 2 internals)
    boiler_texts: List[str] = []

    for idx, u in enumerate(pages_to_fetch):
        st, fu, html = await fetch_html(session, u, timeout_s, max_bytes, stats)
        if st and st < 500 and html:
            pages_fetched += 1
        if not html:
            continue
        fetched_urls.append(fu)

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
        any_mfa = any_mfa or bool(MFA_KEYWORDS.search(html))

    success_rate = (pages_fetched / pages_attempted) if pages_attempted else 0.0

    home_text = strip_visible_text(home_html) if home_html else ""
    home_sim = simhash(tokenize(home_text)) if home_text else 0
    boil = compute_boilerplate_ratio(boiler_texts)
    content_uniqueness = compute_content_uniqueness_score(boiler_texts)
    ai_template_score = compute_ai_template_score(boiler_texts)
    keyword_repetition = compute_keyword_repetition_score(boiler_texts)
    pagination_thin_ratio = compute_pagination_thin_ratio(fetched_urls, text_lens)

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
        robots_txt_accessible=bool(robots_txt_accessible),
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
        has_mfa_keywords=any_mfa,

        homepage_simhash=int(home_sim),
        boilerplate_ratio=round(boil, 3),
        content_uniqueness_score=content_uniqueness,
        ai_template_score=ai_template_score,
        keyword_repetition_score=keyword_repetition,
        pagination_thin_ratio=pagination_thin_ratio,

        ads_txt_present=bool(ads_txt_summary["present"]),
        ads_txt_total_lines=int(ads_txt_summary["total_lines"]),
        ads_txt_unique_ssp_domains=int(ads_txt_summary["unique_ssp_domains"]),
        direct_relationship_count=int(ads_txt_summary["direct_relationship_count"]),
        reseller_relationship_count=int(ads_txt_summary["reseller_relationship_count"]),
        reseller_ratio=float(ads_txt_summary["reseller_ratio"]),
        ads_txt_duplicate_count=int(ads_txt_summary["duplicate_count"]),
        ads_txt_quality_score=int(ads_txt_summary["quality_score"]),
        sellers_json_checked=int(sellers_checked),
        sellers_json_mismatches=int(sellers_mismatches),
        sellers_json_reasons=list(sellers_reasons),

        domain_age_years=float(domain_age_years),
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


def print_resource_summary(args, total_domains: int, todo_domains: int, skipped_domains: int) -> None:
    max_inflight_bytes = args.concurrency * args.max_bytes
    cpu_count = os.cpu_count() or 0

    print("Run resource summary:")
    print(f"- Input domains: {total_domains}")
    print(f"- Domains to process this run: {todo_domains}")
    print(f"- Domains skipped by resume: {skipped_domains}")
    print(f"- Worker concurrency: {args.concurrency} async requests")
    print(f"- Pages per domain target: {args.pages}")
    print(f"- Request timeout: {args.timeout:.1f} seconds")
    print(f"- Max HTML bytes per response: {args.max_bytes:,}")
    print(f"- Estimated max in-flight HTML buffer: {max_inflight_bytes:,} bytes")
    print(f"- Detected CPU cores: {cpu_count}")
    print(f"- Output JSONL: {args.out_jsonl}")
    print(flush=True)


def print_usage_summary(stats: NetworkStats, started: float, out_jsonl: str) -> None:
    elapsed = max(0.001, time.time() - started)
    avg_bytes = (stats.bytes_downloaded / stats.requests_succeeded) if stats.requests_succeeded else 0.0
    req_rate = stats.requests_attempted / elapsed

    try:
        output_size = os.path.getsize(out_jsonl)
    except OSError:
        output_size = 0

    print("Run usage summary:")
    print(f"- Total runtime: {elapsed:.1f} seconds")
    print(f"- Requests attempted: {stats.requests_attempted}")
    print(f"- Successful responses: {stats.requests_succeeded}")
    print(f"- Failed responses: {stats.requests_failed}")
    print(f"- Timeouts: {stats.timeouts}")
    print(f"- Total bytes downloaded: {stats.bytes_downloaded:,}")
    print(f"- Average bytes per successful response: {avg_bytes:,.0f}")
    print(f"- Effective request rate: {req_rate:.2f} req/s")
    print(f"- Current output JSONL size: {output_size:,} bytes")
    print(flush=True)


def render_progress_bar(completed: int, total: int, started: float, width: int = 28) -> None:
    total = max(1, total)
    ratio = clamp(completed / total, 0.0, 1.0)
    filled = int(round(ratio * width))
    bar = "#" * filled + "-" * (width - filled)
    elapsed = max(0.001, time.time() - started)
    rate = completed / elapsed
    eta_s = (total - completed) / rate if rate > 0 else 0.0
    print(
        f"\r[{bar}] {completed}/{total} ({ratio * 100:5.1f}%) | {rate:.2f} domains/s | ETA {eta_s/60:.1f} min",
        end="",
        flush=True,
    )


def render_setup_progress(step: int, total_steps: int, label: str, width: int = 28) -> None:
    total_steps = max(1, total_steps)
    ratio = clamp(step / total_steps, 0.0, 1.0)
    filled = int(round(ratio * width))
    bar = "#" * filled + "-" * (width - filled)
    print(
        f"\r[{bar}] Setup {step}/{total_steps} ({ratio * 100:5.1f}%) | {label}",
        end="",
        flush=True,
    )


async def main_async(args) -> int:
    setup_steps = 4
    render_setup_progress(0, setup_steps, "Initializing")

    with open(args.input, "r", encoding="utf-8") as f:
        domains = [normalize_domain(line) for line in f if line.strip()]
    domains = [d for d in domains if d]
    render_setup_progress(1, setup_steps, "Loaded input domains")

    done = load_done_domains(args.out_jsonl) if args.resume else set()
    todo = [d for d in domains if d not in done]
    render_setup_progress(2, setup_steps, "Prepared resume state")

    if not todo:
        print()
        print("Nothing to do (all domains already present in output JSONL).")
        return 0

    print_resource_summary(
        args,
        total_domains=len(domains),
        todo_domains=len(todo),
        skipped_domains=max(0, len(domains) - len(todo)),
    )
    render_setup_progress(3, setup_steps, "Computed run plan")

    connector = aiohttp.TCPConnector(ssl=False, limit=args.concurrency)
    headers = {"User-Agent": args.user_agent}
    sem = asyncio.Semaphore(args.concurrency)
    stats = NetworkStats()
    render_setup_progress(4, setup_steps, "Starting crawl workers")

    started = time.time()
    completed = 0
    total = len(todo)

    render_progress_bar(completed, total, started)

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        with open(args.out_jsonl, "a", encoding="utf-8") as out:
            async def bound(d: str) -> DomainFeatures:
                async with sem:
                    return await process_domain(d, session, args.pages, args.timeout, args.max_bytes, stats)

            tasks = [asyncio.create_task(bound(d)) for d in todo]

            for coro in asyncio.as_completed(tasks):
                feat = await coro
                out.write(json.dumps(asdict(feat), ensure_ascii=False) + "\n")
                out.flush()

                completed += 1
                if completed % args.log_every == 0 or completed == total:
                    render_progress_bar(completed, total, started)

    if total > 0:
        print(flush=True)
    print_usage_summary(stats, started, args.out_jsonl)
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
    p.add_argument("--log-every", type=int, default=1, help="Progress log interval (domains)")
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
