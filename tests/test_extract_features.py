from extract_features import (
    normalize_domain,
    simhash,
    hamming_distance,
    tokenize,
    strip_visible_text,
    parse_ads_txt,
    detect_nav_pages,
    looks_parked_or_for_sale,
    has_article_schema,
    has_affiliate_markers,
    compute_content_uniqueness_score,
    compute_ai_template_score,
    compute_keyword_repetition_score,
    compute_pagination_thin_ratio,
)


# ---------- normalize_domain ----------

def test_normalize_domain_strips_protocol():
    assert normalize_domain("https://example.com/path") == "example.com"

def test_normalize_domain_strips_http():
    assert normalize_domain("http://www.foo.bar.com/") == "www.foo.bar.com"

def test_normalize_domain_strips_whitespace_and_dots():
    assert normalize_domain("  example.com.  ") == "example.com"

def test_normalize_domain_lowercases():
    assert normalize_domain("HTTPS://Example.COM") == "example.com"


# ---------- tokenize ----------

def test_tokenize_basic():
    tokens = tokenize("Hello World 123 ab")
    assert "hello" in tokens
    assert "world" in tokens
    assert "123" in tokens
    assert "ab" not in tokens  # too short (< 3 chars)

def test_tokenize_empty():
    assert tokenize("") == []


# ---------- simhash / hamming_distance ----------

def test_simhash_empty():
    assert simhash([]) == 0

def test_simhash_deterministic():
    tokens = ["hello", "world", "test"]
    assert simhash(tokens) == simhash(tokens)

def test_simhash_different_inputs():
    h1 = simhash(["alpha", "beta", "gamma"])
    h2 = simhash(["delta", "epsilon", "zeta"])
    assert h1 != h2

def test_hamming_distance_identical():
    assert hamming_distance(0, 0) == 0
    assert hamming_distance(12345, 12345) == 0

def test_hamming_distance_known():
    assert hamming_distance(0b1111, 0b0000) == 4

def test_hamming_distance_similar_simhashes():
    tokens = ["hello", "world", "test", "code"]
    h1 = simhash(tokens)
    h2 = simhash(tokens + ["extra"])
    assert hamming_distance(h1, h2) < 32  # should be somewhat similar


# ---------- strip_visible_text ----------

def test_strip_visible_text_removes_scripts():
    html = "<html><body><script>var x=1;</script><p>Hello</p></body></html>"
    text = strip_visible_text(html)
    assert "Hello" in text
    assert "var x" not in text

def test_strip_visible_text_removes_styles():
    html = "<html><body><style>.foo{color:red}</style><p>Content</p></body></html>"
    text = strip_visible_text(html)
    assert "Content" in text
    assert "color" not in text


# ---------- parse_ads_txt ----------

def test_parse_ads_txt_empty():
    result = parse_ads_txt("")
    assert result["present"] is False
    assert result["total_lines"] == 0

def test_parse_ads_txt_comments_only():
    result = parse_ads_txt("# comment\n# another comment\n")
    assert result["present"] is False
    assert result["total_lines"] == 0

def test_parse_ads_txt_valid_entries():
    txt = "google.com, pub-1234567890, DIRECT, f08c47fec0942fa0\nrubiconproject.com, 12345, RESELLER\n"
    result = parse_ads_txt(txt)
    assert result["present"] is True
    assert result["total_lines"] == 2
    assert result["direct_relationship_count"] == 1
    assert result["reseller_relationship_count"] == 1
    assert result["unique_ssp_domains"] == 2
    assert 0.0 < result["reseller_ratio"] < 1.0

def test_parse_ads_txt_duplicates():
    txt = "google.com, pub-123, DIRECT\ngoogle.com, pub-123, DIRECT\ngoogle.com, pub-123, DIRECT\n"
    result = parse_ads_txt(txt)
    assert result["duplicate_count"] == 2  # 2 duplicates of the first

def test_parse_ads_txt_reseller_heavy():
    lines = "\n".join(f"ssp{i}.com, acc-{i}, RESELLER" for i in range(10))
    result = parse_ads_txt(lines)
    assert result["reseller_ratio"] == 1.0
    assert result["direct_relationship_count"] == 0
    assert result["excessive_reseller_chain_risk"] is True

def test_parse_ads_txt_malformed_lines():
    txt = "this is not valid\ngoogle.com, pub-123, DIRECT\n"
    result = parse_ads_txt(txt)
    assert result["total_lines"] == 2  # both counted
    assert result["direct_relationship_count"] == 1  # only valid one parsed


# ---------- detect_nav_pages ----------

def test_detect_nav_pages_all_present():
    html = '<a href="/about">About</a><a href="/contact">Contact</a><a href="/privacy">Privacy</a><a href="/terms">Terms of Service</a>'
    about, contact, privacy_terms = detect_nav_pages(html)
    assert about is True
    assert contact is True
    assert privacy_terms is True

def test_detect_nav_pages_none():
    html = "<p>Just some content</p>"
    about, contact, privacy_terms = detect_nav_pages(html)
    assert about is False
    assert contact is False
    assert privacy_terms is False

def test_detect_nav_pages_partial():
    html = '<a href="/about-us">About Us</a>'
    about, contact, privacy_terms = detect_nav_pages(html)
    assert about is True
    assert contact is False
    assert privacy_terms is False


# ---------- looks_parked_or_for_sale ----------

def test_parked_domain_for_sale():
    assert looks_parked_or_for_sale("This domain is for sale") is True

def test_parked_sedo():
    assert looks_parked_or_for_sale("Powered by Sedo") is True

def test_parked_hugedomains():
    assert looks_parked_or_for_sale("HugeDomains.com") is True

def test_not_parked():
    assert looks_parked_or_for_sale("Welcome to our news website") is False


# ---------- has_article_schema ----------

def test_article_schema_newsarticle():
    assert has_article_schema('<script type="application/ld+json">{"@type":"NewsArticle"}</script>') is True

def test_article_schema_article():
    assert has_article_schema('<script>{"@type":"article"}</script>') is True

def test_article_schema_none():
    assert has_article_schema("<html><body>No schema here</body></html>") is False


# ---------- has_affiliate_markers ----------

def test_affiliate_detected():
    assert has_affiliate_markers('<a href="https://example.com?ref=abc123">Buy</a>') is True

def test_affiliate_not_detected():
    assert has_affiliate_markers("<p>Welcome to our site</p>") is False


# ---------- compute_content_uniqueness_score ----------

def test_uniqueness_single_text():
    assert compute_content_uniqueness_score(["Hello world"]) == 0.5

def test_uniqueness_identical_texts():
    score = compute_content_uniqueness_score(["Hello world test", "Hello world test"])
    assert score < 0.1  # near-zero distance

def test_uniqueness_different_texts():
    score = compute_content_uniqueness_score([
        "The quick brown fox jumps over the lazy dog in the garden today",
        "Financial markets reported heavy losses across all major indices yesterday",
    ])
    assert score > 0.2


# ---------- compute_ai_template_score ----------

def test_ai_template_none():
    assert compute_ai_template_score(["Normal text here", "Another normal passage"]) == 0.0

def test_ai_template_all():
    score = compute_ai_template_score([
        "In conclusion, this is important",
        "In today's fast-paced digital landscape we must delve into this topic",
    ])
    assert score == 1.0

def test_ai_template_empty():
    assert compute_ai_template_score([]) == 0.0


# ---------- compute_keyword_repetition_score ----------

def test_keyword_repetition_diverse():
    score = compute_keyword_repetition_score([
        "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron"
    ])
    assert score < 0.5

def test_keyword_repetition_spammy():
    score = compute_keyword_repetition_score(["buy buy buy buy buy buy buy buy buy buy"])
    assert score > 0.5

def test_keyword_repetition_empty():
    assert compute_keyword_repetition_score([]) == 0.0


# ---------- compute_pagination_thin_ratio ----------

def test_pagination_thin_none():
    urls = ["https://example.com/article1", "https://example.com/article2"]
    lens = [2000, 3000]
    assert compute_pagination_thin_ratio(urls, lens) == 0.0

def test_pagination_thin_all():
    urls = ["https://example.com/page/1", "https://example.com/page/2"]
    lens = [100, 200]
    assert compute_pagination_thin_ratio(urls, lens) == 1.0

def test_pagination_thin_empty():
    assert compute_pagination_thin_ratio([], []) == 0.0

def test_pagination_thick_pages_not_flagged():
    urls = ["https://example.com/page/1", "https://example.com/page/2"]
    lens = [2000, 3000]  # above 700 threshold
    assert compute_pagination_thin_ratio(urls, lens) == 0.0
