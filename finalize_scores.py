#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Tuple


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def confidence_score(
    pages_attempted: int,
    pages_fetched: int,
    success_rate: float,
    sitemap_found: bool,
    robots_txt_accessible: bool,
    blocked: bool,
) -> float:
    c = 0.2
    coverage = (pages_fetched / pages_attempted) if pages_attempted else 0.0

    if coverage >= 0.85:
        c += 0.2
    elif coverage >= 0.6:
        c += 0.12
    elif coverage >= 0.35:
        c += 0.05

    if success_rate >= 0.85:
        c += 0.2
    elif success_rate >= 0.65:
        c += 0.12
    elif success_rate >= 0.4:
        c += 0.05

    if sitemap_found:
        c += 0.1
    if robots_txt_accessible:
        c += 0.1
    if pages_fetched >= 5:
        c += 0.1
    elif pages_fetched >= 3:
        c += 0.05

    if blocked:
        c -= 0.35

    return round(clamp(c, 0.0, 1.0), 3)


def finalize_score(base_score: float, confidence: float) -> int:
    final = base_score * (0.55 + 0.45 * confidence)
    return int(round(clamp(final, 0, 100)))


def pick_bucket(score: int, confidence: float) -> str:
    if confidence < 0.6:
        return "Needs Manual Review"
    if score >= 85:
        return "Pass (Fast Track)"
    if score >= 70:
        return "Manual Review"
    if score >= 40:
        return "High-Risk Review"
    return "Reject / Deprioritize"


def subscore_content(
    median_text_len: int,
    has_article_schema: bool,
    content_uniqueness_score: float,
    ai_template_score: float,
    keyword_repetition_score: float,
    pagination_thin_ratio: float,
) -> Tuple[int, List[str]]:
    s = 100
    reasons: List[str] = []

    if median_text_len < 500:
        s -= 12
        reasons.append("Thin content on sampled pages")
    elif median_text_len < 800:
        s -= 6

    if content_uniqueness_score < 0.2:
        s -= 28
        reasons.append("Very low content uniqueness across sampled pages")
    elif content_uniqueness_score < 0.35:
        s -= 16
        reasons.append("Low content uniqueness across sampled pages")

    if ai_template_score > 0.66:
        s -= 22
        reasons.append("AI-like templated phrasing detected across multiple pages")
    elif ai_template_score > 0.33:
        s -= 10
        reasons.append("Repeated AI-like templated phrasing detected")

    if keyword_repetition_score > 0.45:
        s -= 18
        reasons.append("Excessive keyword repetition detected")
    elif keyword_repetition_score > 0.3:
        s -= 8
        reasons.append("Elevated keyword repetition detected")

    if pagination_thin_ratio > 0.5:
        s -= 18
        reasons.append("Many sampled pages look like thin pagination pages")
    elif pagination_thin_ratio > 0.2:
        s -= 8
        reasons.append("Some sampled pages look like thin pagination pages")

    if not has_article_schema:
        s -= 6
        reasons.append("No obvious Article/News structured data detected")

    return int(clamp(s, 0, 100)), reasons


def subscore_ads(
    ad_count: int,
    third_party_scripts: int,
    external_ratio: float,
    affiliate_markers: bool,
    ads_txt_quality_score: int,
    reseller_ratio: float,
    direct_relationship_count: int,
    ads_txt_total_lines: int,
    ads_txt_duplicate_count: int,
    sellers_json_checked: int,
    sellers_json_mismatches: int,
    sellers_json_reasons: List[str],
) -> Tuple[int, List[str]]:
    s = 100
    reasons: List[str] = []

    if ad_count > 90:
        s -= 35
        reasons.append("Extremely high ad-container density")
    elif ad_count > 50:
        s -= 22
        reasons.append("High ad-container density")

    if third_party_scripts > 35:
        s -= 18
        reasons.append("Very high third-party script load")
    elif third_party_scripts > 20:
        s -= 8
        reasons.append("High third-party script load")

    if external_ratio > 0.65:
        s -= 16
        reasons.append("High outbound/external link ratio")
    elif external_ratio > 0.45:
        s -= 8
        reasons.append("Elevated outbound/external link ratio")

    if affiliate_markers:
        s -= 14
        reasons.append("Affiliate/arbitrage markers detected")

    if ads_txt_quality_score < 45:
        s -= 24
        reasons.append("Low ads.txt structural quality")
    elif ads_txt_quality_score < 70:
        s -= 12
        reasons.append("Mixed ads.txt structural quality")

    if reseller_ratio > 0.7:
        s -= 18
        reasons.append("Reseller-heavy ads.txt supply chain")
    elif reseller_ratio > 0.5:
        s -= 8
        reasons.append("Elevated reseller ratio in ads.txt")

    if direct_relationship_count == 0 and ads_txt_total_lines > 0:
        s -= 18
        reasons.append("ads.txt contains no DIRECT relationships")

    if ads_txt_total_lines > 150:
        s -= 12
        reasons.append("Very large ads.txt file")
    if ads_txt_duplicate_count >= 3:
        s -= 12
        reasons.append("Suspicious duplicate entries in ads.txt")

    if sellers_json_checked and sellers_json_mismatches:
        penalty = min(20, sellers_json_mismatches * 5)
        s -= penalty
        reasons.extend(sellers_json_reasons[:3])

    return int(clamp(s, 0, 100)), reasons


def subscore_legitimacy(
    has_about: bool,
    has_contact: bool,
    has_privacy_terms: bool,
    looks_parked: bool,
    domain_age_years: float,
) -> Tuple[int, List[str]]:
    s = 70
    reasons: List[str] = []

    if has_about:
        s += 8
    else:
        reasons.append("No clear About page found")

    if has_contact:
        s += 8
    else:
        reasons.append("No clear Contact page found")

    if has_privacy_terms:
        s += 8
    else:
        reasons.append("No clear Privacy/Terms links found")

    if looks_parked:
        s -= 35
        reasons.append("Site appears parked or listed for sale")

    if domain_age_years >= 0:
        if domain_age_years < 1:
            s -= 22
            reasons.append("Very young domain (<1 year)")
        elif domain_age_years < 2:
            s -= 12
            reasons.append("Young domain (1-2 years)")
        elif domain_age_years >= 5:
            s += 6
    else:
        reasons.append("Domain age unavailable")

    return int(clamp(s, 0, 100)), reasons


def subscore_ux(push: bool, interstitial: bool, autorefresh: bool, has_mfa_keywords: bool) -> Tuple[int, List[str]]:
    s = 100
    reasons: List[str] = []

    if push:
        s -= 25
        reasons.append("Push/notification prompting keywords detected")
    if interstitial:
        s -= 35
        reasons.append("Interstitial/overlay/dark-pattern keywords detected")
    if autorefresh:
        s -= 15
        reasons.append("Auto-refresh / ad refresh keywords detected")
    if has_mfa_keywords:
        s -= 20
        reasons.append("MFA-style monetization keywords detected")

    return int(clamp(s, 0, 100)), reasons


def compute_network_risk_score(
    adsense_pub_ids: List[str],
    gtm_ids: List[str],
    homepage_simhash: int,
    counts_adsense: Dict[str, int],
    counts_gtm: Dict[str, int],
    template_sizes: Dict[int, int],
) -> Tuple[int, int, List[str]]:
    risk = 0
    reasons: List[str] = []
    cluster_size = 1

    for pid in adsense_pub_ids:
        n = counts_adsense.get(pid, 0)
        cluster_size = max(cluster_size, n)
        if n > 75:
            risk += 45
            reasons.append(f"AdSense publisher ID shared across {n} domains ({pid})")
            break
        if n > 25:
            risk += 28
            reasons.append(f"AdSense publisher ID shared across {n} domains ({pid})")
            break

    for gid in gtm_ids:
        n = counts_gtm.get(gid, 0)
        cluster_size = max(cluster_size, n)
        if n > 250:
            risk += 35
            reasons.append(f"GTM container shared across {n} domains ({gid})")
            break
        if n > 80:
            risk += 20
            reasons.append(f"GTM container shared across {n} domains ({gid})")
            break

    template_cluster = template_sizes.get(homepage_simhash, 0)
    cluster_size = max(cluster_size, template_cluster)
    if template_cluster > 120:
        risk += 35
        reasons.append(f"Homepage template signature appears on {template_cluster} domains")
    elif template_cluster > 40:
        risk += 20
        reasons.append(f"Homepage template signature appears on {template_cluster} domains")

    if cluster_size > 100:
        risk += 10
    elif cluster_size > 40:
        risk += 5

    return int(clamp(risk, 0, 100)), int(cluster_size), reasons


def should_hard_fail(obj: dict, confidence: float) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    if confidence < 0.7:
        return False, reasons

    if bool(obj.get("looks_parked", False)):
        reasons.append("Parked / for-sale domain signal")

    if int(obj.get("median_ad_container_count", 0)) > 90:
        reasons.append("Extremely high ad-container density")

    if bool(obj.get("has_mfa_keywords", False)):
        reasons.append("Strong MFA keyword pattern")

    if (
        float(obj.get("reseller_ratio", 0.0)) > 0.85
        and int(obj.get("direct_relationship_count", 0)) == 0
        and int(obj.get("ads_txt_total_lines", 0)) > 25
    ):
        reasons.append("Massive reseller-only ads.txt with no DIRECT relationships")

    return bool(reasons), reasons


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
    robots_txt_accessible: bool
    blocked_or_captcha: bool

    adsense_pub_ids: List[str]
    gtm_ids: List[str]
    ga_ids: List[str]

    ads_txt_quality_score: int
    network_risk_score: int
    domain_age_years: float
    reseller_ratio: float
    direct_relationship_count: int
    cluster_size: int
    hard_fail_triggered: bool


def parse_args(argv=None):
    ap = argparse.ArgumentParser("finalize_scores.py — cluster + score + buckets")
    ap.add_argument("--features-jsonl", required=True, help="JSONL produced by extract_features.py")
    ap.add_argument("--out-csv", default="scored.csv")
    ap.add_argument("--out-jsonl", default="scored.jsonl")
    return ap.parse_args(argv)


def run(args) -> int:
    output_dir = Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_csv_path = output_dir / Path(args.out_csv).name
    out_jsonl_path = output_dir / Path(args.out_jsonl).name

    rows: List[dict] = []
    counts_adsense: Dict[str, int] = {}
    counts_gtm: Dict[str, int] = {}
    template_sizes: Dict[int, int] = {}

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
            pages_attempted=int(obj.get("pages_attempted", 0)),
            pages_fetched=int(obj.get("pages_fetched", 0)),
            success_rate=float(obj.get("success_rate", 0.0)),
            sitemap_found=bool(obj.get("sitemap_found", False)),
            robots_txt_accessible=bool(obj.get("robots_txt_accessible", False)),
            blocked=bool(obj.get("blocked_or_captcha", False)),
        )

        content_s, content_r = subscore_content(
            median_text_len=int(obj.get("median_text_len", 0)),
            has_article_schema=bool(obj.get("has_article_schema", False)),
            content_uniqueness_score=float(obj.get("content_uniqueness_score", 0.5)),
            ai_template_score=float(obj.get("ai_template_score", 0.0)),
            keyword_repetition_score=float(obj.get("keyword_repetition_score", 0.0)),
            pagination_thin_ratio=float(obj.get("pagination_thin_ratio", 0.0)),
        )

        ads_s, ads_r = subscore_ads(
            ad_count=int(obj.get("median_ad_container_count", 0)),
            third_party_scripts=int(obj.get("median_third_party_scripts", 0)),
            external_ratio=float(obj.get("median_external_link_ratio", 0.0)),
            affiliate_markers=bool(obj.get("affiliate_markers", False)),
            ads_txt_quality_score=int(obj.get("ads_txt_quality_score", 40)),
            reseller_ratio=float(obj.get("reseller_ratio", 0.0)),
            direct_relationship_count=int(obj.get("direct_relationship_count", 0)),
            ads_txt_total_lines=int(obj.get("ads_txt_total_lines", 0)),
            ads_txt_duplicate_count=int(obj.get("ads_txt_duplicate_count", 0)),
            sellers_json_checked=int(obj.get("sellers_json_checked", 0)),
            sellers_json_mismatches=int(obj.get("sellers_json_mismatches", 0)),
            sellers_json_reasons=list(obj.get("sellers_json_reasons", [])),
        )

        legit_s, legit_r = subscore_legitimacy(
            has_about=bool(obj.get("has_about", False)),
            has_contact=bool(obj.get("has_contact", False)),
            has_privacy_terms=bool(obj.get("has_privacy_terms", False)),
            looks_parked=bool(obj.get("looks_parked", False)),
            domain_age_years=float(obj.get("domain_age_years", -1.0)),
        )

        ux_s, ux_r = subscore_ux(
            push=bool(obj.get("has_push_keywords", False)),
            interstitial=bool(obj.get("has_interstitial_keywords", False)),
            autorefresh=bool(obj.get("has_autorefresh_keywords", False)),
            has_mfa_keywords=bool(obj.get("has_mfa_keywords", False)),
        )

        network_risk_score, cluster_size, cluster_r = compute_network_risk_score(
            adsense_pub_ids=list(obj.get("adsense_pub_ids", [])),
            gtm_ids=list(obj.get("gtm_ids", [])),
            homepage_simhash=int(obj.get("homepage_simhash", 0)),
            counts_adsense=counts_adsense,
            counts_gtm=counts_gtm,
            template_sizes=template_sizes,
        )

        cluster_score = 100 - network_risk_score
        base = (
            0.35 * ads_s
            + 0.30 * cluster_score
            + 0.20 * content_s
            + 0.10 * legit_s
            + 0.05 * ux_s
        )

        final = finalize_score(base, conf)
        bucket = pick_bucket(final, conf)

        hard_fail_triggered, hard_fail_reasons = should_hard_fail(obj, conf)
        if hard_fail_triggered:
            final = min(final, 35)
            bucket = "Reject / Deprioritize"

        reasons: List[str] = []
        reasons.extend(hard_fail_reasons)
        reasons.extend(cluster_r)
        for reason in ads_r + content_r + legit_r + ux_r:
            if reason not in reasons:
                reasons.append(reason)
        reasons = reasons[:10]

        scored.append(
            ScoredDomain(
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
                robots_txt_accessible=bool(obj.get("robots_txt_accessible", False)),
                blocked_or_captcha=bool(obj.get("blocked_or_captcha", False)),
                adsense_pub_ids=list(obj.get("adsense_pub_ids", [])),
                gtm_ids=list(obj.get("gtm_ids", [])),
                ga_ids=list(obj.get("ga_ids", [])),
                ads_txt_quality_score=int(obj.get("ads_txt_quality_score", 40)),
                network_risk_score=int(network_risk_score),
                domain_age_years=float(obj.get("domain_age_years", -1.0)),
                reseller_ratio=float(obj.get("reseller_ratio", 0.0)),
                direct_relationship_count=int(obj.get("direct_relationship_count", 0)),
                cluster_size=int(cluster_size),
                hard_fail_triggered=bool(hard_fail_triggered),
            )
        )

    with open(out_jsonl_path, "w", encoding="utf-8") as f:
        for row in scored:
            f.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")

    with open(out_csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "input_domain",
                "final_url",
                "reg_domain",
                "score",
                "bucket",
                "confidence",
                "pages_attempted",
                "pages_fetched",
                "success_rate",
                "sitemap_found",
                "robots_txt_accessible",
                "blocked_or_captcha",
                "ads_txt_quality_score",
                "network_risk_score",
                "domain_age_years",
                "reseller_ratio",
                "direct_relationship_count",
                "cluster_size",
                "hard_fail_triggered",
                "adsense_pub_ids",
                "gtm_ids",
                "ga_ids",
                "reasons",
            ]
        )
        for row in sorted(scored, key=lambda x: x.score):
            w.writerow(
                [
                    row.input_domain,
                    row.final_url,
                    row.reg_domain,
                    row.score,
                    row.bucket,
                    row.confidence,
                    row.pages_attempted,
                    row.pages_fetched,
                    row.success_rate,
                    row.sitemap_found,
                    row.robots_txt_accessible,
                    row.blocked_or_captcha,
                    row.ads_txt_quality_score,
                    row.network_risk_score,
                    row.domain_age_years,
                    row.reseller_ratio,
                    row.direct_relationship_count,
                    row.cluster_size,
                    row.hard_fail_triggered,
                    "|".join(row.adsense_pub_ids),
                    "|".join(row.gtm_ids),
                    "|".join(row.ga_ids),
                    " ; ".join(row.reasons),
                ]
            )

    print(f"Done. Wrote {out_csv_path} and {out_jsonl_path}.")

    if sys.stdin.isatty():
        try:
            base_name = input(
                "Enter output base name (without extension) to rename files, or press Enter to keep current names: "
            ).strip()
        except EOFError:
            base_name = ""

        if base_name:
            csv_path = out_csv_path
            jsonl_path = out_jsonl_path
            new_csv = csv_path.with_name(f"{base_name}.csv")
            new_jsonl = jsonl_path.with_name(f"{base_name}.jsonl")

            if new_csv.exists() or new_jsonl.exists():
                print("Rename skipped: target file already exists.")
            else:
                csv_path.rename(new_csv)
                jsonl_path.rename(new_jsonl)
                print(f"Renamed outputs to {new_csv} and {new_jsonl}.")

    return 0


def main(argv=None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
