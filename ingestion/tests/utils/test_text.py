from unittest.mock import patch
from ingestion.utils.text import clean_html, has_text, _dedup_paragraphs, truncate_description, MAX_OFFER_DESCRIPTION_CHARS as MAX_DESCRIPTION_CHARS


# --- clean_html ---

def test_clean_html_removes_nav_and_footer():
    html = """<html><body>
        <nav>Home | About | Jobs</nav>
        <main><p>Senior Python Engineer needed.</p></main>
        <footer>Privacy Policy | Cookie Policy</footer>
    </body></html>"""
    result = clean_html(html)
    assert "Python Engineer" in result
    assert "Privacy Policy" not in result


def test_clean_html_removes_script_and_style():
    html = "<script>alert('x')</script><style>.a{color:red}</style><p>Job description here.</p>"
    result = clean_html(html)
    assert "Job description here" in result
    assert "alert" not in result
    assert "color:red" not in result


def test_clean_html_strips_html_tags():
    html = "<div><h1>Title</h1><p>We need a <strong>developer</strong>.</p></div>"
    result = clean_html(html)
    assert "Title" in result
    assert "developer" in result
    assert "<" not in result


def test_clean_html_empty_returns_empty():
    assert clean_html("") == ""
    assert clean_html("   ") == ""


def test_clean_html_removes_divider_noise():
    html = "<p>First section.</p><p>---</p><p>Second section.</p>"
    result = clean_html(html)
    assert "First section" in result
    assert "Second section" in result
    assert "---" not in result


def test_clean_html_fixes_encoding_artifacts():
    # ftfy should fix common mojibake
    html = "<p>We’re hiring a café manager.</p>"
    result = clean_html(html)
    assert "hiring" in result


def test_clean_html_collapses_excessive_whitespace():
    html = "<p>First.</p><p>   </p><p>   </p><p>Second.</p>"
    result = clean_html(html)
    assert "\n\n\n" not in result


# --- deduplication ---

def test_clean_html_removes_duplicate_paragraphs():
    repeated = "We are a leading technology company committed to innovation and excellence."
    html = f"""<html><body><article>
        <p>{repeated}</p>
        <p>The role involves building scalable backend systems using Python and PostgreSQL.</p>
        <p>{repeated}</p>
    </article></body></html>"""
    result = clean_html(html)
    assert result.count(repeated) == 1


def test_clean_html_dedup_is_case_insensitive():
    block = "About Barnet Council: a borough with much to be proud of."
    html = f"""<html><body><article>
        <p>{block}</p>
        <p>This is an exciting time to join our digital transformation team.</p>
        <p>{block}</p>
    </article></body></html>"""
    result = clean_html(html)
    assert result.lower().count(block.lower()) == 1


def test_clean_html_keeps_distinct_paragraphs():
    html = "<p>First paragraph.</p><p>Second paragraph.</p><p>Third paragraph.</p>"
    result = clean_html(html)
    assert "First paragraph" in result
    assert "Second paragraph" in result
    assert "Third paragraph" in result


# --- has_text ---

def test_has_text_true_for_content():
    assert has_text("<html><body><p>Hello world.</p></body></html>") is True


def test_has_text_false_for_empty_body():
    assert has_text("<html><body></body></html>") is False


def test_has_text_false_for_empty_string():
    assert has_text("") is False


# --- _dedup_paragraphs ---

def test_dedup_paragraphs_removes_exact_repeats():
    text = "Intro paragraph.\n\nRole details.\n\nIntro paragraph."
    result = _dedup_paragraphs(text)
    assert result.count("Intro paragraph.") == 1
    assert "Role details." in result


def test_dedup_paragraphs_preserves_order():
    text = "A.\n\nB.\n\nC.\n\nA."
    result = _dedup_paragraphs(text)
    assert result == "A.\n\nB.\n\nC."


def test_dedup_paragraphs_handles_whitespace_variants():
    text = "Hello world.\n\nHELLO   WORLD."
    result = _dedup_paragraphs(text)
    assert result.count("world") == 1


# --- truncate_description ---

def test_truncate_description_short_text_unchanged():
    text = "Short description."
    assert truncate_description(text) == text


def test_truncate_description_caps_at_limit():
    text = "a" * (MAX_DESCRIPTION_CHARS + 1000)
    result = truncate_description(text)
    assert len(result) <= MAX_DESCRIPTION_CHARS


def test_truncate_description_breaks_at_sentence():
    # Build a text that exceeds limit, with a sentence boundary just before the cap
    body = "This is a sentence. " * 900  # ~18000 chars
    result = truncate_description(body)
    assert len(result) <= MAX_DESCRIPTION_CHARS
    assert result.endswith(".")


def test_truncate_description_breaks_at_newline():
    line = "x" * 100 + "\n"
    body = line * 200  # ~20200 chars
    result = truncate_description(body)
    assert len(result) <= MAX_DESCRIPTION_CHARS
    # result length + 1 (stripped \n) should align with line boundary (101 chars per line)
    assert (len(result) + 1) % 101 == 0


def test_clean_html_plain_text_skips_trafilatura():
    # jobspy's Indeed/Glassdoor descriptions are markdown; "<5" is not a tag
    with patch("ingestion.utils.text.trafilatura.extract") as extract:
        result = clean_html("**About the role**\n\nPython, <5 people team.")
    extract.assert_not_called()
    assert "Python, <5 people team." in result
