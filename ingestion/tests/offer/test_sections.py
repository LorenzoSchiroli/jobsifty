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


def test_split_sections_keeps_role_sections():
    s = split_sections(DESCRIPTION)
    assert "Train machine learning models" in s.kept_text
    assert "3+ years of Python experience" in s.kept_text


def test_no_headings_keeps_everything_but_eeo():
    text = "We build data tools in Berlin.\nYou will write Python every day.\nWe are an equal opportunity employer."
    s = split_sections(text)
    assert not s.headings_found
    assert "We build data tools in Berlin." in s.kept_text
    assert "equal opportunity" not in s.kept_text
    assert s.kept_text == "We build data tools in Berlin.\nYou will write Python every day."


def test_inline_heading():
    s = split_sections("Requirements: Python and SQL experience\nBenefits: free lunch for everyone")
    assert s.kept_text == "Python and SQL experience"
    assert "free lunch" not in s.kept_text


def test_empty_description():
    s = split_sections(None)
    assert s.kept_text == "" and not s.headings_found


def test_clean_markdown_unescapes():
    assert clean_markdown(r"5\+ years, £60,000\.00\-£65,000 \& more") == "5+ years, £60,000.00-£65,000 & more"


# --- input cleaning ---

def test_html_description_becomes_lines():
    kept = split_sections("<div><h3>Your role</h3><p>Build ML models.</p><h3>Benefits</h3><p>Free lunch.</p></div>").kept_text
    assert kept == "Your role\nBuild ML models."


def test_adzuna_salary_tail_and_badges_are_cut():
    description = "AI Engineer\nNUEVO\nBuild agents in Python.\nSalary comparison:\nSimilar jobs ML Engineer 50k"
    assert split_sections(description).kept_text == "AI Engineer\nBuild agents in Python."


def test_a_long_line_mentioning_similar_jobs_is_not_a_tail():
    line = ("You will compare our ranking with similar jobs boards and offres similaires across Europe every week, "
            "then report what you find to the product team.")
    assert split_sections(line).kept_text == line


# --- headings ---

def test_more_headings_recognised():
    assert classify_heading("CE QUE NOUS PROPOSONS :") == "benefits"
    assert classify_heading("Dit krijg jij:") == "benefits"
    assert classify_heading("About the assignment") == "responsibilities"
    assert classify_heading("A propos de KPMG :") == "company"
    assert classify_heading("DE&I Statement:") == "legal"


def test_a_short_skill_line_is_kept_although_it_reads_like_a_heading():
    description = "Requirements:\n5 years of Python.\nStrong SQL skills\nExperience with Playwright\nBenefits\nFree lunch."
    assert split_sections(description).kept_text == (
        "5 years of Python.\nStrong SQL skills\nExperience with Playwright"
    )


def test_contact_only_counts_at_the_start_of_a_heading():
    assert classify_heading("Erfahrung mit cloudbasierten Contact-Center-Lösungen") is None
    assert classify_heading("Contact:") == "apply"


# --- opening blurb ---

def test_opening_company_pitch_is_dropped_up_to_the_role():
    description = ("Founded in 1968, Acme is a global engineering partner with 2,000 employees. "
                   "Our teams work across Europe. We are looking for an ML Engineer to build ranking models.\n"
                   "Requirements\n- Python")
    kept = split_sections(description, company="Acme", title="ML Engineer").kept_text
    assert kept.startswith("We are looking for an ML Engineer")
    assert "Founded" not in kept


def test_pitch_addressing_the_reader_is_still_a_pitch():
    description = ("Discover your future at Acme. Acme is a global leader in payments. "
                   "As a Data Scientist you will build fraud models.")
    kept = split_sections(description, company="Acme", title="Data Scientist").kept_text
    assert kept == "As a Data Scientist you will build fraud models."


def test_opening_that_starts_with_the_role_is_kept_whole():
    description = "As a Data Engineer you will own our pipelines. We are a global leader in retail."
    assert split_sections(description, company="Acme", title="Data Engineer").kept_text == description


def test_opening_without_a_sentence_about_the_role_is_kept_whole():
    description = "Acme is a global leader in retail. We have offices in 12 countries."
    assert split_sections(description, company="Acme").kept_text == description


def test_role_signals_are_whole_words():
    """"has an" is not "as an", "ricercata" is not "cerca"."""
    description = ("Acme has an international growth agenda and is a global bank. Per la posizione ricercata "
                   "offriamo un contratto. We are seeking a Data Engineer for our platform team.")
    kept = split_sections(description, company="Acme", title="Data Engineer").kept_text
    assert kept == "We are seeking a Data Engineer for our platform team."


def test_company_section_describing_the_role_keeps_the_role():
    description = ("Your tasks\n- Build pipelines\nAbout us\n"
                   "Acme is a consultancy. We are looking for a consultant to join the team. "
                   "In this role you will run AI diagnostics for clients.")
    kept = split_sections(description, company="Acme").kept_text
    assert "We are looking for a consultant" in kept and "In this role" in kept
    assert "Acme is a consultancy" not in kept
