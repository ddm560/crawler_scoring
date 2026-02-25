#!/usr/bin/env python3
import argparse
import csv
import json
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def confidence_score(pages_fetched: int, success_rate: float, sitemap_found: bool, blocked: bool) -> float:
    c = 0.3
    if pages_fetched >= 5:
        c += 0.25
    elif pages_fetched >= 3:
        c += 0.15
    if success_rate >= 0.8:
        c += 0.2
    elif success_rate >= 0.5:
        c += 0.1
    if sitemap_found:
        c += 0.1
    if blocked:
        c -= 0.4
    return float(clamp(c, 0.0, 1.0))


def finalize_score(base_score: float, confidence: float) -> int:
    final = base_score * (0.6 + 0.4 * confidence)
    return int(round(clamp(final, 0, 100)))


def pick_bucket(score: int, confidence: float) -> str:
    if confidence < 0.6:
        return "Need more evidence"
    if score >= 70:
        return "Good domain"
    if score <= 40:
        return "Suspicious"
    return "Need more evidence"


def subscore_content(median_text_len: int, boilerplate_ratio: float, has_article_schema: bool) -> Tuple[int, List[str]]:
    s = 100
    reasons = []
    if median_text_len < 800:
        s -= 25
        reasons.append("Thin content on sampled pages (low median text length)")
    if boilerplate_ratio > 0.7:
        s -= 25
        reasons.append("High template/boilerplate similarity across pages")
    if not has_article_schema:
        s -= 10
        reasons.append("No obvious Article/News structured data detected")
    return int(clamp(s, 0, 100)), reasons


def subscore_ads(ad_count: int, third_party_scripts: int, external_ratio: float, affiliate_markers: bool) -> Tuple[int, List[str]]:
    s = 100
    reasons = []
    if ad_count > 80:
        s -= 35
        reasons.append("Very high ad-container signals in DOM")
    elif ad_count > 40:
        s -= 20
        reasons.append("High ad-container signals in DOM")

    if third_party_scripts > 35:
        s -= 20
        reasons.append("Very high third-party script load")
    elif third_party_scripts > 20:
        s -= 10
        reasons.append("High third-party script load")

    if external_ratio > 0.65:
        s -= 20
        reasons.append("High outbound/external link ratio")
    elif external_ratio > 0.45:
        s -= 10
        reasons.append("Elevated outbound/external link ratio")

    if affiliate_markers:
        s -= 15
        reasons.append("Affiliate/arbitrage markers detected")
    return int(clamp(s, 0, 100)), reasons


def subscore_legitimacy(has_about: bool, has_contact: bool, has_privacy_terms: bool, looks_parked: bool) -> Tuple[int, List[str]]:
    s = 70
    reasons = []
    if has_about:
        s += 10
    else:
        reasons.append("No clear About page found")
    if has_contact:
        s += 10
    else:
        reasons.append("No clear Contact page found")
    if has_privacy_terms:
        s += 10
    else:
        reasons.append("No clear Privacy/Terms links found")
    if looks_parked:
        s -= 30
        reasons.append("Site appears parked/for-sale or non-content landing")
    return int(clamp(s, 0, 100)), reasons


def subscore_ux(push: bool, interstitial: bool, autorefresh: bool) -> Tuple[int, List[str]]:
    s = 100
    reasons = []
    if push:
        s -= 30
        reasons.append("Push/notification prompting keywords detected")
    if interstitial:
        s -= 40
        reasons.append("Interstitial/overlay/dark-pattern keywords detected")
    if autorefresh:
        s -= 20
        reasons.append("Auto-refresh / ad refresh keywords detected")
    return int(clamp(s, 0, 100)), reasons


def compute_cluster_subscore(
    adsense_pub_ids: List[str],
    gtm_ids: List[str],
    homepage_simhash: int,
    counts_adsense: Dict[str, int],
    counts_gtm: Dict[str, int],
    template_sizes: Dict[int, int],
) -> Tuple[int, List[str]]:
    s = 100
    reasons = []

    for pid in adsense_pub_ids:
        n = counts_adsense.get(pid, 0)
        if n > 50:
            s -= 35
            reasons.append(f"Shares AdSense publisher ID with {n} other domains ({pid})")
            break
        elif n > 20:
            s -= 20
            reasons.append(f"Shares AdSense publisher ID with {n} other domains ({pid})")
            break

    for gid in gtm_ids:
        n = counts_gtm.get(gid, 0)
        if n > 200:
            s -= 25
            reasons.append(f"Shares GTM container with {n} other domains ({gid})")
            break
        elif n > 75:
            s -= 15
            reasons.append(f"Shares GTM container with {n} other domains ({gid})")
            break

    n = template_sizes.get(homepage_simhash, 0)
    if n > 100:
        s -= 30
        reasons.append(f"Homepage template signature appears on {n} domains (near-duplicate cluster)")
    elif n > 30:
        s -= 15
        reasons.append(f"Homepage template signature appears on {n} domains (near-duplicate cluster)")

    return int(clamp(s, 0, 100)), reasons


@dataclass
class ScoredDomain:
    input_domain: str
    final_url: str
    reg_domain: str
    score: int
    bucket: str
    confidence: float
    reasons: List[str]

    pages_attempted: int
    pages_fetched: int
    success_rate: float
    sitemap_found: bool
    blocked_or_captcha: bool

    adsense_pub_ids: List[str]
    gtm_ids: List[str]
    ga_ids: List[str]


def main():
    ap = argparse.ArgumentParser("finalize_scores.py — cluster + score + buckets")
    ap.add_argument("--features-jsonl", required=True, help="JSONL produced by extract_features.py")
    ap.add_argument("--out-csv", default="scored.csv")
    ap.add_argument("--out-jsonl", default="scored.jsonl")
    args = ap.parse_args()

    rows: List[dict] = []
    counts_adsense: Dict[str, int] = {}
    counts_gtm: Dict[str, int] = {}
    template_sizes: Dict[int, int] = {}

    # First pass: load + build counts
    with open(args.features_jsonl, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)
            rows.append(obj)

            for pid in obj.get("adsense_pub_ids", []):
                counts_adsense[pid] = counts_adsense.get(pid, 0) + 1
            for gid in obj.get("gtm_ids", []):
                counts_gtm[gid] = counts_gtm.get(gid, 0) + 1

            h = int(obj.get("homepage_simhash", 0))
            template_sizes[h] = template_sizes.get(h, 0) + 1

    scored: List[ScoredDomain] = []

    for obj in rows:
        conf = confidence_score(
            pages_fetched=int(obj.get("pages_fetched", 0)),
            success_rate=float(obj.get("success_rate", 0.0)),
            sitemap_found=bool(obj.get("sitemap_found", False)),
            blocked=bool(obj.get("blocked_or_captcha", False)),
        )

        content_s, content_r = subscore_content(
            int(obj.get("median_text_len", 0)),
            float(obj.get("boilerplate_ratio", 0.5)),
            bool(obj.get("has_article_schema", False)),
        )
        ads_s, ads_r = subscore_ads(
            int(obj.get("median_ad_container_count", 0)),
            int(obj.get("median_third_party_scripts", 0)),
            float(obj.get("median_external_link_ratio", 0.0)),
            bool(obj.get("affiliate_markers", False)),
        )
        legit_s, legit_r = subscore_legitimacy(
            bool(obj.get("has_about", False)),
            bool(obj.get("has_contact", False)),
            bool(obj.get("has_privacy_terms", False)),
            bool(obj.get("looks_parked", False)),
        )
        ux_s, ux_r = subscore_ux(
            bool(obj.get("has_push_keywords", False)),
            bool(obj.get("has_interstitial_keywords", False)),
            bool(obj.get("has_autorefresh_keywords", False)),
        )

        cluster_s, cluster_r = compute_cluster_subscore(
            obj.get("adsense_pub_ids", []),
            obj.get("gtm_ids", []),
            int(obj.get("homepage_simhash", 0)),
            counts_adsense,
            counts_gtm,
            template_sizes,
        )

        base = (
            0.30 * content_s +
            0.30 * ads_s +
            0.25 * cluster_s +
            0.10 * legit_s +
            0.05 * ux_s
        )

        final = finalize_score(base, conf)
        bucket = pick_bucket(final, conf)

        reasons: List[str] = []
        reasons.extend(cluster_r)
        for r in (content_r + ads_r + legit_r + ux_r):
            if r not in reasons:
                reasons.append(r)
        reasons = reasons[:8]

        scored.append(ScoredDomain(
            input_domain=obj.get("input_domain", ""),
            final_url=obj.get("final_url", ""),
            reg_domain=obj.get("reg_domain", ""),
            score=int(final),
            bucket=bucket,
            confidence=round(conf, 3),
            reasons=reasons,

            pages_attempted=int(obj.get("pages_attempted", 0)),
            pages_fetched=int(obj.get("pages_fetched", 0)),
            success_rate=float(obj.get("success_rate", 0.0)),
            sitemap_found=bool(obj.get("sitemap_found", False)),
            blocked_or_captcha=bool(obj.get("blocked_or_captcha", False)),

            adsense_pub_ids=obj.get("adsense_pub_ids", []),
            gtm_ids=obj.get("gtm_ids", []),
            ga_ids=obj.get("ga_ids", []),
        ))

    # Write JSONL
    with open(args.out_jsonl, "w", encoding="utf-8") as f:
        for s in scored:
            f.write(json.dumps(asdict(s), ensure_ascii=False) + "\n")

    # Write CSV
    with open(args.out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "input_domain", "final_url", "reg_domain",
            "score", "bucket", "confidence",
            "pages_fetched", "success_rate", "sitemap_found", "blocked_or_captcha",
            "adsense_pub_ids", "gtm_ids", "ga_ids",
            "reasons"
        ])
        for s in sorted(scored, key=lambda x: x.score):
            w.writerow([
                s.input_domain, s.final_url, s.reg_domain,
                s.score, s.bucket, s.confidence,
                s.pages_fetched, s.success_rate, s.sitemap_found, s.blocked_or_captcha,
                "|".join(s.adsense_pub_ids),
                "|".join(s.gtm_ids),
                "|".join(s.ga_ids),
                " ; ".join(s.reasons),
            ])

    print(f"Done. Wrote {args.out_csv} and {args.out_jsonl}.")


if __name__ == "__main__":
    main()