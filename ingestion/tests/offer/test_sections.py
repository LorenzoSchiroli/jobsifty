from ingestion.offer.parsing.sections import classify_heading, clean_markdown, split_sections

DESCRIPTION = """About Us
Acme is a leading platform used by millions of customers worldwide.

Your Responsibilities:
- Build and maintain data pipelines
- Train machine learning models

Requirements
* 3+ years of Python experience
* Experience with Spark and Airflow

What we offer
- 30 days of vacation
- Free lunch every day

Acme is an equal opportunity employer and values diversity of all kinds."""


def test_classify_heading_multilingual():
    assert classify_heading("Your Responsibilities:") == "responsibilities"
    assert classify_heading("Dein Profil") == "requirements"
    assert classify_heading("Ce que nous offrons") == "benefits"
    assert classify_heading("Chi siamo") == "company"
    assert classify_heading("How to apply") == "apply"
    assert classify_heading("Random words") is None


def test_split_sections_drops_boilerplate_sections():
    s = split_sections(DESCRIPTION)
    assert s.headings_found
    assert "millions of customers" not in s.kept_text
    assert "vacation" not in s.kept_text
    assert "equal opportunity" not in s.kept_text
    assert "Build and maintain data pipelines" in s.kept_text


def test_split_sections_fills_lists_without_bullets():
    s = split_sections(DESCRIPTION)
    assert s.responsibilities == ["Build and maintain data pipelines", "Train machine learning models"]
    assert s.requirements == ["3+ years of Python experience", "Experience with Spark and Airflow"]


def test_no_headings_keeps_everything_but_eeo():
    text = "We build data tools in Berlin.\nYou will write Python every day.\nWe are an equal opportunity employer."
    s = split_sections(text)
    assert not s.headings_found
    assert "We build data tools in Berlin." in s.kept_text
    assert "equal opportunity" not in s.kept_text
    assert s.other == ["We build data tools in Berlin.", "You will write Python every day."]


def test_inline_heading():
    s = split_sections("Requirements: Python and SQL experience\nBenefits: free lunch for everyone")
    assert s.requirements == ["Python and SQL experience"]
    assert "free lunch" not in s.kept_text


def test_empty_description():
    s = split_sections(None)
    assert s.kept_text == "" and s.requirements == []


def test_clean_markdown_unescapes():
    assert clean_markdown(r"5\+ years, £60,000\.00\-£65,000 \& more") == "5+ years, £60,000.00-£65,000 & more"
