import json
import pytest
from pathlib import Path

from finalize_scores import (
    clamp,
    confidence_score,
    finalize_score,
    pick_bucket,
    subscore_content,
    subscore_ads,
    subscore_legitimacy,
    subscore_ux,
    compute_network_risk_score,
    should_hard_fail,
    _validate_config,
    load_config,
)


# ---------- clamp ----------

def test_clamp_within_bounds():
    assert clamp(5, 0, 10) == 5

def test_clamp_below_lower():
    assert clamp(-1, 0, 10) == 0

def test_clamp_above_upper():
    assert clamp(15, 0, 10) == 10

def test_clamp_at_boundaries():
    assert clamp(0, 0, 10) == 0
    assert clamp(10, 0, 10) == 10


# ---------- confidence_score ----------

def test_confidence_score_perfect_crawl(default_config):
    cfg = default_config["confidence"]
    result = confidence_score(
        pages_attempted=6, pages_fetched=6, success_rate=1.0,
        sitemap_found=True, robots_txt_accessible=True, blocked=False, cfg=cfg,
    )
    # max is 0.2 + 0.2 + 0.2 + 0.05 + 0.05 + 0.1 = 0.8 with reduced sitemap/robots bonuses
    assert result >= 0.8

def test_confidence_score_blocked_site(default_config):
    cfg = default_config["confidence"]
    result = confidence_score(
        pages_attempted=6, pages_fetched=6, success_rate=1.0,
        sitemap_found=True, robots_txt_accessible=True, blocked=True, cfg=cfg,
    )
    blocked_penalty = cfg["blocked_penalty"]
    perfect = confidence_score(
        pages_attempted=6, pages_fetched=6, success_rate=1.0,
        sitemap_found=True, robots_txt_accessible=True, blocked=False, cfg=cfg,
    )
    assert result < perfect

def test_confidence_score_zero_pages(default_config):
    cfg = default_config["confidence"]
    result = confidence_score(
        pages_attempted=0, pages_fetched=0, success_rate=0.0,
        sitemap_found=False, robots_txt_accessible=False, blocked=False, cfg=cfg,
    )
    assert result >= 0.0
    assert result <= 1.0

def test_confidence_score_always_clamped(default_config):
    cfg = default_config["confidence"]
    result = confidence_score(
        pages_attempted=1, pages_fetched=0, success_rate=0.0,
        sitemap_found=False, robots_txt_accessible=False, blocked=True, cfg=cfg,
    )
    assert 0.0 <= result <= 1.0


# ---------- finalize_score ----------

def test_finalize_score_full_confidence(default_config):
    cfg = default_config["finalize"]
    # base=100, confidence=1.0 -> 100 * (0.55 + 0.45*1.0) = 100
    assert finalize_score(100, 1.0, cfg) == 100

def test_finalize_score_zero_confidence(default_config):
    cfg = default_config["finalize"]
    # base=100, confidence=0.0 -> 100 * 0.55 = 55
    assert finalize_score(100, 0.0, cfg) == 55

def test_finalize_score_zero_base(default_config):
    cfg = default_config["finalize"]
    assert finalize_score(0, 1.0, cfg) == 0

def test_finalize_score_clamped(default_config):
    cfg = default_config["finalize"]
    result = finalize_score(200, 1.0, cfg)
    assert result <= 100


# ---------- pick_bucket ----------

def test_pick_bucket_low_confidence(default_config):
    cfg = default_config["buckets"]
    assert pick_bucket(100, 0.3, cfg) == "Needs Manual Review"

def test_pick_bucket_pass(default_config):
    cfg = default_config["buckets"]
    assert pick_bucket(90, 0.9, cfg) == "Pass (Fast Track)"

def test_pick_bucket_manual_review(default_config):
    cfg = default_config["buckets"]
    assert pick_bucket(75, 0.9, cfg) == "Manual Review"

def test_pick_bucket_high_risk(default_config):
    cfg = default_config["buckets"]
    assert pick_bucket(50, 0.9, cfg) == "High-Risk Review"

def test_pick_bucket_reject(default_config):
    cfg = default_config["buckets"]
    assert pick_bucket(20, 0.9, cfg) == "Reject / Deprioritize"


# ---------- subscore_content ----------

def test_subscore_content_perfect(default_config):
    cfg = default_config["content"]
    score, reasons = subscore_content(
        median_text_len=2000, has_article_schema=True,
        content_uniqueness_score=0.8, ai_template_score=0.0,
        keyword_repetition_score=0.0, pagination_thin_ratio=0.0, cfg=cfg,
    )
    assert score == 100
    assert reasons == []

def test_subscore_content_thin_severe(default_config):
    cfg = default_config["content"]
    score, reasons = subscore_content(
        median_text_len=100, has_article_schema=True,
        content_uniqueness_score=0.8, ai_template_score=0.0,
        keyword_repetition_score=0.0, pagination_thin_ratio=0.0, cfg=cfg,
    )
    assert score == 100 - cfg["thin_severe_penalty"]
    assert any("Thin content" in r for r in reasons)

def test_subscore_content_low_uniqueness(default_config):
    cfg = default_config["content"]
    score, reasons = subscore_content(
        median_text_len=2000, has_article_schema=True,
        content_uniqueness_score=0.1, ai_template_score=0.0,
        keyword_repetition_score=0.0, pagination_thin_ratio=0.0, cfg=cfg,
    )
    assert score == 100 - cfg["uniqueness_severe_penalty"]
    assert any("uniqueness" in r.lower() for r in reasons)

def test_subscore_content_all_penalties_stack(default_config):
    cfg = default_config["content"]
    score, reasons = subscore_content(
        median_text_len=100, has_article_schema=False,
        content_uniqueness_score=0.1, ai_template_score=0.9,
        keyword_repetition_score=0.9, pagination_thin_ratio=0.9, cfg=cfg,
    )
    assert score <= 5  # near floor after stacking all penalties
    assert len(reasons) >= 4

def test_subscore_content_no_article_schema(default_config):
    cfg = default_config["content"]
    score, reasons = subscore_content(
        median_text_len=2000, has_article_schema=False,
        content_uniqueness_score=0.8, ai_template_score=0.0,
        keyword_repetition_score=0.0, pagination_thin_ratio=0.0, cfg=cfg,
    )
    # penalty is 0 by default — no article schema should not penalize non-editorial sites
    assert score == 100 - cfg["no_article_schema_penalty"]
    assert score == 100


# ---------- subscore_ads ----------

def test_subscore_ads_clean_with_bonuses(default_config):
    cfg = default_config["ads"]
    score, reasons = subscore_ads(
        ad_count=5, third_party_scripts=3, external_ratio=0.1,
        affiliate_markers=False, ads_txt_quality_score=90,
        reseller_ratio=0.1, direct_relationship_count=5,
        ads_txt_total_lines=20, ads_txt_duplicate_count=0,
        ads_txt_unique_ssp_domains=6,
        sellers_json_checked=0, sellers_json_mismatches=0,
        sellers_json_reasons=[], cfg=cfg,
    )
    # base 82 + direct bonus 8 + ssp diversity bonus 6 + clean ads.txt bonus 4 = 100
    assert score == 100
    assert reasons == []

def test_subscore_ads_no_bonuses(default_config):
    cfg = default_config["ads"]
    score, reasons = subscore_ads(
        ad_count=5, third_party_scripts=3, external_ratio=0.1,
        affiliate_markers=False, ads_txt_quality_score=90,
        reseller_ratio=0.1, direct_relationship_count=1,
        ads_txt_total_lines=0, ads_txt_duplicate_count=0,
        ads_txt_unique_ssp_domains=2,
        sellers_json_checked=0, sellers_json_mismatches=0,
        sellers_json_reasons=[], cfg=cfg,
    )
    # base 82, no bonuses triggered
    assert score == cfg["base"]

def test_subscore_ads_extreme_density(default_config):
    cfg = default_config["ads"]
    score, reasons = subscore_ads(
        ad_count=100, third_party_scripts=3, external_ratio=0.1,
        affiliate_markers=False, ads_txt_quality_score=90,
        reseller_ratio=0.1, direct_relationship_count=5,
        ads_txt_total_lines=20, ads_txt_duplicate_count=0,
        ads_txt_unique_ssp_domains=6,
        sellers_json_checked=0, sellers_json_mismatches=0,
        sellers_json_reasons=[], cfg=cfg,
    )
    # 100 (with bonuses) - severe penalty
    assert score == 100 - cfg["ad_count_severe_penalty"]
    assert any("ad-container" in r.lower() for r in reasons)

def test_subscore_ads_reseller_heavy(default_config):
    cfg = default_config["ads"]
    score, reasons = subscore_ads(
        ad_count=5, third_party_scripts=3, external_ratio=0.1,
        affiliate_markers=False, ads_txt_quality_score=90,
        reseller_ratio=0.8, direct_relationship_count=0,
        ads_txt_total_lines=30, ads_txt_duplicate_count=0,
        ads_txt_unique_ssp_domains=2,
        sellers_json_checked=0, sellers_json_mismatches=0,
        sellers_json_reasons=[], cfg=cfg,
    )
    assert any("Reseller" in r for r in reasons)
    assert any("DIRECT" in r for r in reasons)

def test_subscore_ads_sellers_json_mismatch_capped(default_config):
    cfg = default_config["ads"]
    score, _ = subscore_ads(
        ad_count=5, third_party_scripts=3, external_ratio=0.1,
        affiliate_markers=False, ads_txt_quality_score=90,
        reseller_ratio=0.1, direct_relationship_count=5,
        ads_txt_total_lines=20, ads_txt_duplicate_count=0,
        ads_txt_unique_ssp_domains=6,
        sellers_json_checked=10, sellers_json_mismatches=100,
        sellers_json_reasons=["a", "b", "c", "d"], cfg=cfg,
    )
    # 100 (with bonuses) - capped mismatch penalty
    assert score == 100 - cfg["sellers_json_mismatch_max_penalty"]


# ---------- subscore_legitimacy ----------

def test_subscore_legitimacy_all_good(default_config):
    cfg = default_config["legitimacy"]
    score, reasons = subscore_legitimacy(
        has_about=True, has_contact=True, has_privacy_terms=True,
        looks_parked=False, domain_age_years=10.0, cfg=cfg,
    )
    expected = cfg["base"] + cfg["has_about_bonus"] + cfg["has_contact_bonus"] + cfg["has_privacy_terms_bonus"] + cfg["mature_domain_bonus"]
    assert score == expected
    assert reasons == []

def test_subscore_legitimacy_parked(default_config):
    cfg = default_config["legitimacy"]
    score, reasons = subscore_legitimacy(
        has_about=True, has_contact=True, has_privacy_terms=True,
        looks_parked=True, domain_age_years=10.0, cfg=cfg,
    )
    assert any("parked" in r.lower() for r in reasons)
    assert score < 100

def test_subscore_legitimacy_very_young_domain(default_config):
    cfg = default_config["legitimacy"]
    score, reasons = subscore_legitimacy(
        has_about=True, has_contact=True, has_privacy_terms=True,
        looks_parked=False, domain_age_years=0.3, cfg=cfg,
    )
    assert any("<6 months" in r for r in reasons)

def test_subscore_legitimacy_young_domain(default_config):
    cfg = default_config["legitimacy"]
    score, reasons = subscore_legitimacy(
        has_about=True, has_contact=True, has_privacy_terms=True,
        looks_parked=False, domain_age_years=0.8, cfg=cfg,
    )
    assert any("6-12 months" in r for r in reasons)
    # softer penalty than very young
    very_young_score, _ = subscore_legitimacy(
        has_about=True, has_contact=True, has_privacy_terms=True,
        looks_parked=False, domain_age_years=0.3, cfg=cfg,
    )
    assert score > very_young_score

def test_subscore_legitimacy_unavailable_age(default_config):
    cfg = default_config["legitimacy"]
    _, reasons = subscore_legitimacy(
        has_about=True, has_contact=True, has_privacy_terms=True,
        looks_parked=False, domain_age_years=-1.0, cfg=cfg,
    )
    assert any("unavailable" in r.lower() for r in reasons)

def test_subscore_legitimacy_missing_pages(default_config):
    cfg = default_config["legitimacy"]
    _, reasons = subscore_legitimacy(
        has_about=False, has_contact=False, has_privacy_terms=False,
        looks_parked=False, domain_age_years=3.0, cfg=cfg,
    )
    assert any("About" in r for r in reasons)
    assert any("Contact" in r for r in reasons)
    assert any("Privacy" in r for r in reasons)


# ---------- subscore_ux ----------

def test_subscore_ux_clean(default_config):
    cfg = default_config["ux"]
    score, reasons = subscore_ux(
        push=False, interstitial=False, autorefresh=False,
        has_mfa_keywords=False, cfg=cfg,
    )
    assert score == 100
    assert reasons == []

def test_subscore_ux_all_flags(default_config):
    cfg = default_config["ux"]
    score, reasons = subscore_ux(
        push=True, interstitial=True, autorefresh=True,
        has_mfa_keywords=True, cfg=cfg,
    )
    total_penalty = cfg["push_penalty"] + cfg["interstitial_penalty"] + cfg["autorefresh_penalty"] + cfg["mfa_penalty"]
    expected = max(0, 100 - total_penalty)
    assert score == expected
    assert len(reasons) == 4

def test_subscore_ux_single_flag(default_config):
    cfg = default_config["ux"]
    score, reasons = subscore_ux(
        push=True, interstitial=False, autorefresh=False,
        has_mfa_keywords=False, cfg=cfg,
    )
    assert score == 100 - cfg["push_penalty"]
    assert len(reasons) == 1


# ---------- compute_network_risk_score ----------

def test_network_risk_no_sharing(default_config):
    cfg = default_config["network_risk"]
    risk, cluster, reasons = compute_network_risk_score(
        adsense_pub_ids=[], gtm_ids=[], homepage_simhash=12345,
        counts_adsense={}, counts_gtm={}, template_sizes={12345: 1}, cfg=cfg,
    )
    assert risk == 0
    assert cluster == 1
    assert reasons == []

def test_network_risk_adsense_severe(default_config):
    cfg = default_config["network_risk"]
    risk, cluster, reasons = compute_network_risk_score(
        adsense_pub_ids=["pub-123"], gtm_ids=[], homepage_simhash=0,
        counts_adsense={"pub-123": 100}, counts_gtm={}, template_sizes={}, cfg=cfg,
    )
    assert risk >= cfg["adsense_severe_penalty"]
    assert cluster == 100
    assert any("AdSense" in r for r in reasons)

def test_network_risk_template_cluster(default_config):
    cfg = default_config["network_risk"]
    risk, cluster, reasons = compute_network_risk_score(
        adsense_pub_ids=[], gtm_ids=[], homepage_simhash=999,
        counts_adsense={}, counts_gtm={}, template_sizes={999: 200}, cfg=cfg,
    )
    assert risk >= cfg["template_severe_penalty"]
    assert cluster == 200
    assert any("template" in r.lower() for r in reasons)

def test_network_risk_gtm_mild(default_config):
    cfg = default_config["network_risk"]
    risk, _, reasons = compute_network_risk_score(
        adsense_pub_ids=[], gtm_ids=["GTM-ABC"], homepage_simhash=0,
        counts_adsense={}, counts_gtm={"GTM-ABC": 100}, template_sizes={}, cfg=cfg,
    )
    assert risk >= cfg["gtm_mild_penalty"]
    assert any("GTM" in r for r in reasons)


# ---------- should_hard_fail ----------

def test_hard_fail_low_confidence(default_config):
    cfg = default_config["hard_fail"]
    triggered, reasons = should_hard_fail({"looks_parked": True}, confidence=0.5, cfg=cfg)
    assert triggered is False
    assert reasons == []

def test_hard_fail_parked(default_config):
    cfg = default_config["hard_fail"]
    triggered, reasons = should_hard_fail({"looks_parked": True}, confidence=0.9, cfg=cfg)
    assert triggered is True
    assert any("Parked" in r for r in reasons)

def test_hard_fail_extreme_ads(default_config):
    cfg = default_config["hard_fail"]
    triggered, reasons = should_hard_fail(
        {"median_ad_container_count": 100}, confidence=0.9, cfg=cfg,
    )
    assert triggered is True
    assert any("ad-container" in r.lower() for r in reasons)

def test_hard_fail_mfa(default_config):
    cfg = default_config["hard_fail"]
    triggered, reasons = should_hard_fail(
        {"has_mfa_keywords": True}, confidence=0.9, cfg=cfg,
    )
    assert triggered is True
    assert any("MFA" in r for r in reasons)

def test_hard_fail_reseller_only(default_config):
    cfg = default_config["hard_fail"]
    triggered, reasons = should_hard_fail(
        {"reseller_ratio": 0.95, "direct_relationship_count": 0, "ads_txt_total_lines": 50},
        confidence=0.9, cfg=cfg,
    )
    assert triggered is True
    assert any("reseller" in r.lower() for r in reasons)

def test_hard_fail_clean_domain(default_config):
    cfg = default_config["hard_fail"]
    triggered, reasons = should_hard_fail({}, confidence=0.9, cfg=cfg)
    assert triggered is False
    assert reasons == []


# ---------- _validate_config ----------

def test_validate_config_valid(default_config, tmp_path):
    config_path = tmp_path / "test.json"
    config_path.write_text(json.dumps(default_config))
    _validate_config(default_config, config_path)  # should not raise

def test_validate_config_bad_weights(tmp_path):
    bad_config = {"subscore_weights": {"ads": 0.5, "cluster": 0.5, "content": 0.5, "legitimacy": 0.0, "ux": 0.0}}
    config_path = tmp_path / "bad.json"
    config_path.write_text(json.dumps(bad_config))
    with pytest.raises(ValueError, match="must sum to 1.0"):
        _validate_config(bad_config, config_path)

def test_validate_config_empty_weights(tmp_path):
    bad_config = {"subscore_weights": {}}
    config_path = tmp_path / "bad.json"
    config_path.write_text(json.dumps(bad_config))
    with pytest.raises(ValueError, match="missing or empty"):
        _validate_config(bad_config, config_path)

def test_validate_config_missing_section(tmp_path):
    bad_config = {}
    config_path = tmp_path / "bad.json"
    config_path.write_text(json.dumps(bad_config))
    with pytest.raises(ValueError, match="missing or empty"):
        _validate_config(bad_config, config_path)


# ---------- load_config ----------

def test_load_config_from_project_root():
    config = load_config()
    assert "subscore_weights" in config
    assert abs(sum(config["subscore_weights"].values()) - 1.0) < 0.001

def test_load_config_custom_path(tmp_path):
    valid_config = {
        "subscore_weights": {"ads": 0.2, "cluster": 0.2, "content": 0.2, "legitimacy": 0.2, "ux": 0.2},
    }
    config_path = tmp_path / "custom.json"
    config_path.write_text(json.dumps(valid_config))
    config = load_config(config_path)
    assert config["subscore_weights"]["ads"] == 0.2
