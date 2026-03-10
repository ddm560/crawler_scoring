#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

def load_config(config_path: Optional[Path] = None) -> dict:
    if config_path is None:
        if getattr(sys, "frozen", False):
            # PyInstaller exe: check next to the .exe first (user override), then bundled default
            exe_dir = Path(sys.executable).parent
            user_override = exe_dir / "scoring_config.json"
            config_path = user_override if user_override.exists() else Path(sys._MEIPASS) / "scoring_config.json"  # type: ignore[attr-defined]
        else:
            config_path = Path(__file__).parent / "scoring_config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    _validate_config(config, config_path)
    return config


def _validate_config(config: dict, config_path: Path) -> None:
    weights = config.get("subscore_weights", {})
    if not weights:
        raise ValueError(f"[{config_path}] 'subscore_weights' section is missing or empty.")
    total = sum(weights.values())
    if abs(total - 1.0) > 0.001:
        lines = "\n".join(f"  {k}: {v}" for k, v in weights.items())
        raise ValueError(
            f"[{config_path}] subscore_weights must sum to 1.0, but got {total:.6f}:\n{lines}"
        )


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def confidence_score(
    pages_attempted: int,
    pages_fetched: int,
    success_rate: float,
    sitemap_found: bool,
    robots_txt_accessible: bool,
    blocked: bool,
    cfg: dict,
) -> float:
    c = cfg["base"]
    coverage = (pages_fetched / pages_attempted) if pages_attempted else 0.0

    if coverage >= cfg["coverage_high_threshold"]:
        c += cfg["coverage_high_bonus"]
    elif coverage >= cfg["coverage_mid_threshold"]:
        c += cfg["coverage_mid_bonus"]
    elif coverage >= cfg["coverage_low_threshold"]:
        c += cfg["coverage_low_bonus"]

    if success_rate >= cfg["success_rate_high_threshold"]:
        c += cfg["success_rate_high_bonus"]
    elif success_rate >= cfg["success_rate_mid_threshold"]:
        c += cfg["success_rate_mid_bonus"]
    elif success_rate >= cfg["success_rate_low_threshold"]:
        c += cfg["success_rate_low_bonus"]

    if sitemap_found:
        c += cfg["sitemap_bonus"]
    if robots_txt_accessible:
        c += cfg["robots_txt_bonus"]
    if pages_fetched >= cfg["pages_high_threshold"]:
        c += cfg["pages_high_bonus"]
    elif pages_fetched >= cfg["pages_mid_threshold"]:
        c += cfg["pages_mid_bonus"]

    if blocked:
        c -= cfg["blocked_penalty"]

    return round(clamp(c, 0.0, 1.0), 3)


def finalize_score(base_score: float, confidence: float, cfg: dict) -> int:
    final = base_score * (cfg["confidence_floor"] + cfg["confidence_weight"] * confidence)
    return int(round(clamp(final, 0, 100)))


def pick_bucket(score: int, confidence: float, cfg: dict) -> str:
    if confidence < cfg["low_confidence_threshold"]:
        return "Needs Manual Review"
    if score >= cfg["pass_threshold"]:
        return "Pass (Fast Track)"
    if score >= cfg["manual_review_threshold"]:
        return "Manual Review"
    if score >= cfg["high_risk_threshold"]:
        return "High-Risk Review"
    return "Reject / Deprioritize"


def subscore_content(
    median_text_len: int,
    has_article_schema: bool,
    content_uniqueness_score: float,
    ai_template_score: float,
    keyword_repetition_score: float,
    pagination_thin_ratio: float,
    cfg: dict,
) -> Tuple[int, List[str]]:
    s = 100
    reasons: List[str] = []

    if median_text_len < cfg["thin_severe_threshold"]:
        s -= cfg["thin_severe_penalty"]
        reasons.append("Thin content on sampled pages")
    elif median_text_len < cfg["thin_mild_threshold"]:
        s -= cfg["thin_mild_penalty"]

    if content_uniqueness_score < cfg["uniqueness_severe_threshold"]:
        s -= cfg["uniqueness_severe_penalty"]
        reasons.append("Very low content uniqueness across sampled pages")
    elif content_uniqueness_score < cfg["uniqueness_mild_threshold"]:
        s -= cfg["uniqueness_mild_penalty"]
        reasons.append("Low content uniqueness across sampled pages")

    if ai_template_score > cfg["ai_template_severe_threshold"]:
        s -= cfg["ai_template_severe_penalty"]
        reasons.append("AI-like templated phrasing detected across multiple pages")
    elif ai_template_score > cfg["ai_template_mild_threshold"]:
        s -= cfg["ai_template_mild_penalty"]
        reasons.append("Repeated AI-like templated phrasing detected")

    if keyword_repetition_score > cfg["keyword_rep_severe_threshold"]:
        s -= cfg["keyword_rep_severe_penalty"]
        reasons.append("Excessive keyword repetition detected")
    elif keyword_repetition_score > cfg["keyword_rep_mild_threshold"]:
        s -= cfg["keyword_rep_mild_penalty"]
        reasons.append("Elevated keyword repetition detected")

    if pagination_thin_ratio > cfg["pagination_severe_threshold"]:
        s -= cfg["pagination_severe_penalty"]
        reasons.append("Many sampled pages look like thin pagination pages")
    elif pagination_thin_ratio > cfg["pagination_mild_threshold"]:
        s -= cfg["pagination_mild_penalty"]
        reasons.append("Some sampled pages look like thin pagination pages")

    if not has_article_schema:
        s -= cfg["no_article_schema_penalty"]
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
    cfg: dict,
) -> Tuple[int, List[str]]:
    s = 100
    reasons: List[str] = []

    if ad_count > cfg["ad_count_severe_threshold"]:
        s -= cfg["ad_count_severe_penalty"]
        reasons.append("Extremely high ad-container density")
    elif ad_count > cfg["ad_count_mild_threshold"]:
        s -= cfg["ad_count_mild_penalty"]
        reasons.append("High ad-container density")

    if third_party_scripts > cfg["third_party_scripts_severe_threshold"]:
        s -= cfg["third_party_scripts_severe_penalty"]
        reasons.append("Very high third-party script load")
    elif third_party_scripts > cfg["third_party_scripts_mild_threshold"]:
        s -= cfg["third_party_scripts_mild_penalty"]
        reasons.append("High third-party script load")

    if external_ratio > cfg["external_ratio_severe_threshold"]:
        s -= cfg["external_ratio_severe_penalty"]
        reasons.append("High outbound/external link ratio")
    elif external_ratio > cfg["external_ratio_mild_threshold"]:
        s -= cfg["external_ratio_mild_penalty"]
        reasons.append("Elevated outbound/external link ratio")

    if affiliate_markers:
        s -= cfg["affiliate_penalty"]
        reasons.append("Affiliate/arbitrage markers detected")

    if ads_txt_quality_score < cfg["ads_txt_quality_severe_threshold"]:
        s -= cfg["ads_txt_quality_severe_penalty"]
        reasons.append("Low ads.txt structural quality")
    elif ads_txt_quality_score < cfg["ads_txt_quality_mild_threshold"]:
        s -= cfg["ads_txt_quality_mild_penalty"]
        reasons.append("Mixed ads.txt structural quality")

    if reseller_ratio > cfg["reseller_ratio_severe_threshold"]:
        s -= cfg["reseller_ratio_severe_penalty"]
        reasons.append("Reseller-heavy ads.txt supply chain")
    elif reseller_ratio > cfg["reseller_ratio_mild_threshold"]:
        s -= cfg["reseller_ratio_mild_penalty"]
        reasons.append("Elevated reseller ratio in ads.txt")

    if direct_relationship_count == 0 and ads_txt_total_lines > 0:
        s -= cfg["no_direct_penalty"]
        reasons.append("ads.txt contains no DIRECT relationships")

    if ads_txt_total_lines > cfg["large_ads_txt_threshold"]:
        s -= cfg["large_ads_txt_penalty"]
        reasons.append("Very large ads.txt file")
    if ads_txt_duplicate_count >= cfg["duplicate_ads_txt_threshold"]:
        s -= cfg["duplicate_ads_txt_penalty"]
        reasons.append("Suspicious duplicate entries in ads.txt")

    if sellers_json_checked and sellers_json_mismatches:
        penalty = min(
            cfg["sellers_json_mismatch_max_penalty"],
            sellers_json_mismatches * cfg["sellers_json_mismatch_penalty_per"],
        )
        s -= penalty
        reasons.extend(sellers_json_reasons[:3])

    return int(clamp(s, 0, 100)), reasons


def subscore_legitimacy(
    has_about: bool,
    has_contact: bool,
    has_privacy_terms: bool,
    looks_parked: bool,
    domain_age_years: float,
    cfg: dict,
) -> Tuple[int, List[str]]:
    s = cfg["base"]
    reasons: List[str] = []

    if has_about:
        s += cfg["has_about_bonus"]
    else:
        reasons.append("No clear About page found")

    if has_contact:
        s += cfg["has_contact_bonus"]
    else:
        reasons.append("No clear Contact page found")

    if has_privacy_terms:
        s += cfg["has_privacy_terms_bonus"]
    else:
        reasons.append("No clear Privacy/Terms links found")

    if looks_parked:
        s -= cfg["looks_parked_penalty"]
        reasons.append("Site appears parked or listed for sale")

    if domain_age_years >= 0:
        if domain_age_years < cfg["young_domain_threshold"]:
            s -= cfg["young_domain_penalty"]
            reasons.append("Very young domain (<1 year)")
        elif domain_age_years < cfg["mid_domain_threshold"]:
            s -= cfg["mid_domain_penalty"]
            reasons.append("Young domain (1-2 years)")
        elif domain_age_years >= cfg["mature_domain_threshold"]:
            s += cfg["mature_domain_bonus"]
    else:
        reasons.append("Domain age unavailable")

    return int(clamp(s, 0, 100)), reasons


def subscore_ux(
    push: bool,
    interstitial: bool,
    autorefresh: bool,
    has_mfa_keywords: bool,
    cfg: dict,
) -> Tuple[int, List[str]]:
    s = 100
    reasons: List[str] = []

    if push:
        s -= cfg["push_penalty"]
        reasons.append("Push/notification prompting keywords detected")
    if interstitial:
        s -= cfg["interstitial_penalty"]
        reasons.append("Interstitial/overlay/dark-pattern keywords detected")
    if autorefresh:
        s -= cfg["autorefresh_penalty"]
        reasons.append("Auto-refresh / ad refresh keywords detected")
    if has_mfa_keywords:
        s -= cfg["mfa_penalty"]
        reasons.append("MFA-style monetization keywords detected")

    return int(clamp(s, 0, 100)), reasons


def compute_network_risk_score(
    adsense_pub_ids: List[str],
    gtm_ids: List[str],
    homepage_simhash: int,
    counts_adsense: Dict[str, int],
    counts_gtm: Dict[str, int],
    template_sizes: Dict[int, int],
    cfg: dict,
) -> Tuple[int, int, List[str]]:
    risk = 0
    reasons: List[str] = []
    cluster_size = 1

    for pid in adsense_pub_ids:
        n = counts_adsense.get(pid, 0)
        cluster_size = max(cluster_size, n)
        if n > cfg["adsense_severe_threshold"]:
            risk += cfg["adsense_severe_penalty"]
            reasons.append(f"AdSense publisher ID shared across {n} domains ({pid})")
            break
        if n > cfg["adsense_mild_threshold"]:
            risk += cfg["adsense_mild_penalty"]
            reasons.append(f"AdSense publisher ID shared across {n} domains ({pid})")
            break

    for gid in gtm_ids:
        n = counts_gtm.get(gid, 0)
        cluster_size = max(cluster_size, n)
        if n > cfg["gtm_severe_threshold"]:
            risk += cfg["gtm_severe_penalty"]
            reasons.append(f"GTM container shared across {n} domains ({gid})")
            break
        if n > cfg["gtm_mild_threshold"]:
            risk += cfg["gtm_mild_penalty"]
            reasons.append(f"GTM container shared across {n} domains ({gid})")
            break

    template_cluster = template_sizes.get(homepage_simhash, 0)
    cluster_size = max(cluster_size, template_cluster)
    if template_cluster > cfg["template_severe_threshold"]:
        risk += cfg["template_severe_penalty"]
        reasons.append(f"Homepage template signature appears on {template_cluster} domains")
    elif template_cluster > cfg["template_mild_threshold"]:
        risk += cfg["template_mild_penalty"]
        reasons.append(f"Homepage template signature appears on {template_cluster} domains")

    if cluster_size > cfg["cluster_large_threshold"]:
        risk += cfg["cluster_large_penalty"]
    elif cluster_size > cfg["cluster_mid_threshold"]:
        risk += cfg["cluster_mid_penalty"]

    return int(clamp(risk, 0, 100)), int(cluster_size), reasons


def should_hard_fail(obj: dict, confidence: float, cfg: dict) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    if confidence < cfg["min_confidence"]:
        return False, reasons

    if bool(obj.get("looks_parked", False)):
        reasons.append("Parked / for-sale domain signal")

    if int(obj.get("median_ad_container_count", 0)) > cfg["max_ad_containers"]:
        reasons.append("Extremely high ad-container density")

    if bool(obj.get("has_mfa_keywords", False)):
        reasons.append("Strong MFA keyword pattern")

    if (
        float(obj.get("reseller_ratio", 0.0)) > cfg["reseller_ratio_threshold"]
        and int(obj.get("direct_relationship_count", 0)) == 0
        and int(obj.get("ads_txt_total_lines", 0)) > cfg["min_ads_txt_lines_for_reseller_check"]
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
    ap.add_argument(
        "--config",
        default=None,
        help="Path to scoring config JSON (default: scoring_config.json next to this script/exe)",
    )
    return ap.parse_args(argv)


def run(args) -> int:
    config_path = Path(args.config) if args.config else None
    config = load_config(config_path)

    weights = config["subscore_weights"]
    conf_cfg = config["confidence"]
    finalize_cfg = config["finalize"]
    bucket_cfg = config["buckets"]
    content_cfg = config["content"]
    ads_cfg = config["ads"]
    legit_cfg = config["legitimacy"]
    ux_cfg = config["ux"]
    network_cfg = config["network_risk"]
    hard_fail_cfg = config["hard_fail"]

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
            cfg=conf_cfg,
        )

        content_s, content_r = subscore_content(
            median_text_len=int(obj.get("median_text_len", 0)),
            has_article_schema=bool(obj.get("has_article_schema", False)),
            content_uniqueness_score=float(obj.get("content_uniqueness_score", 0.5)),
            ai_template_score=float(obj.get("ai_template_score", 0.0)),
            keyword_repetition_score=float(obj.get("keyword_repetition_score", 0.0)),
            pagination_thin_ratio=float(obj.get("pagination_thin_ratio", 0.0)),
            cfg=content_cfg,
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
            cfg=ads_cfg,
        )

        legit_s, legit_r = subscore_legitimacy(
            has_about=bool(obj.get("has_about", False)),
            has_contact=bool(obj.get("has_contact", False)),
            has_privacy_terms=bool(obj.get("has_privacy_terms", False)),
            looks_parked=bool(obj.get("looks_parked", False)),
            domain_age_years=float(obj.get("domain_age_years", -1.0)),
            cfg=legit_cfg,
        )

        ux_s, ux_r = subscore_ux(
            push=bool(obj.get("has_push_keywords", False)),
            interstitial=bool(obj.get("has_interstitial_keywords", False)),
            autorefresh=bool(obj.get("has_autorefresh_keywords", False)),
            has_mfa_keywords=bool(obj.get("has_mfa_keywords", False)),
            cfg=ux_cfg,
        )

        network_risk_score, cluster_size, cluster_r = compute_network_risk_score(
            adsense_pub_ids=list(obj.get("adsense_pub_ids", [])),
            gtm_ids=list(obj.get("gtm_ids", [])),
            homepage_simhash=int(obj.get("homepage_simhash", 0)),
            counts_adsense=counts_adsense,
            counts_gtm=counts_gtm,
            template_sizes=template_sizes,
            cfg=network_cfg,
        )

        cluster_score = 100 - network_risk_score
        base = (
            weights["ads"] * ads_s
            + weights["cluster"] * cluster_score
            + weights["content"] * content_s
            + weights["legitimacy"] * legit_s
            + weights["ux"] * ux_s
        )

        final = finalize_score(base, conf, finalize_cfg)
        bucket = pick_bucket(final, conf, bucket_cfg)

        hard_fail_triggered, hard_fail_reasons = should_hard_fail(obj, conf, hard_fail_cfg)
        if hard_fail_triggered:
            final = min(final, hard_fail_cfg["score_cap"])
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
        for row in sorted(scored, key=lambda x: x.score, reverse=True):
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
