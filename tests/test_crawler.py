"""Deep crawler tests — pure logic, no network."""
import pytest


# ── URL handling ──────────────────────────────────────────────────────────
def test_normalize_strips_fragment_and_case():
    from trader.brain.crawler import normalize
    assert normalize("https://WWW.Site.com/A/?x=1") == \
        "https://www.site.com/A"
    assert normalize("https://a.com/p#sec") == "https://a.com/p"


def test_allowed_domain():
    from trader.brain.crawler import allowed_domain
    assert allowed_domain("https://www.investopedia.com/articles/a")
    assert allowed_domain("https://arxiv.org/abs/2608.21888")
    assert not allowed_domain("https://evil.com/investopedia")


def test_extract_links_filters_and_resolves():
    from trader.brain.crawler import extract_links
    html = """
    <a href="/articles/mean-reversion">mr</a>
    <a href="https://www.quantstart.com/articles/kalman">qs</a>
    <a href="https://someblog.io/post/ema-strategies">ext</a>
    <a href="mailto:a@b.c">m</a>
    <a href="#top">frag</a>
    <a href="/docs/paper.pdf">pdf</a>
    """
    links = extract_links(html, "https://www.investopedia.com/start/")
    joined = " ".join(links)
    # all absolute http(s) links kept — allowlisting happens at enqueue
    assert "investopedia.com/articles/mean-reversion" in joined
    assert "quantstart.com/articles/kalman" in joined
    assert "someblog.io" in joined
    assert "mailto" not in joined and "paper.pdf" not in joined
    assert len(links) == len(set(links))


def test_trusted_aggregator_gate():
    """_fetch refuses off-allowlist URLs unless vouched by an aggregator."""
    import yaml
    from trader.brain.crawler import DeepCrawler, TRUSTED_AGGREGATORS, \
        allowed_domain, host_of
    from trader.core.journal import Journal as J
    import tempfile, pathlib
    cfg = yaml.safe_load(open("config.yaml"))
    dc = DeepCrawler(J(pathlib.Path(tempfile.mkdtemp()) / "c2.db"), cfg,
                     feed=None)
    outside = "https://quantblog.example.com/great-essay"
    assert not allowed_domain(outside)
    assert host_of("https://quantocracy.com/x") in TRUSTED_AGGREGATORS
    # un-vouched off-allowlist → refused before any network call
    assert dc._fetch(outside, via_trusted=False) is None


# ── text extraction ───────────────────────────────────────────────────────
def test_html_to_text_strips_chrome():
    from trader.brain.crawler import html_to_text
    html = ("<html><head><title>EMA Strategy</title>"
            "<style>.x{color:red}</style></head><body>"
            "<script>var t=1;</script><nav>menu junk</nav>"
            "<p>An ema crossover strategy enters long when the fast ema "
            "crosses above the slow ema with adx above 25.</p>"
            "<footer>copyright</footer></body></html>")
    text = html_to_text(html)
    assert "EMA Strategy" in text
    assert "crossover strategy" in text
    assert "var t=1" not in text and "menu junk" not in text


# ── passage mining screen ────────────────────────────────────────────────
def test_passages_chunk_and_score():
    from trader.brain.crawler import passages_from, chunk_score
    filler = ("This article discusses market history at length. "
              "Nothing here is a rule. ")
    hot = ("A breakout setup triggers entry when price closes above "
           "resistance with rsi below 70; place the stop loss under the "
           "level and target a 2:1 risk reward. ")
    text = (hot + filler * 10 + hot.replace("breakout", "vwap"))
    chunks = passages_from(text, target_len=300)
    scores = [chunk_score(c) for c in chunks]
    assert max(scores) >= 4                       # dense chunk exists
    dense = [c for c in chunks if chunk_score(c) >= 3]
    assert any("rsi" in c.lower() for c in dense)


# ── dedupe ────────────────────────────────────────────────────────────────
def test_seen_doc_dedupe(tmp_path):
    import yaml
    from trader.brain.crawler import DeepCrawler, normalize
    from trader.core.journal import Journal
    cfg = yaml.safe_load(open("config.yaml"))
    j = Journal(tmp_path / "c.db")
    dc = DeepCrawler(j, cfg, feed=None)
    url = "https://www.investopedia.com/articles/trading/x"
    assert not dc._seen_doc(url)
    j.log_brain_event("crawl_doc",
                      __import__("hashlib").md5(
                          normalize(url).encode()).hexdigest()[:16],
                      {"url": url})
    assert dc._seen_doc(url)
