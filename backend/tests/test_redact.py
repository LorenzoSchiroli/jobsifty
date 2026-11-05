from backend.cv.redact import redact

EN = """JOHN SMITH
Senior Data Scientist
john.smith@gmail.com | +44 20 7946 0958 | linkedin.com/in/johnsmith | https://johnsmith.dev
12 Baker Street, London NW1 6XE
Date of birth: 12/03/1990
Nationality: British

EXPERIENCE
2018 – 2021  Data Scientist, Acme Ltd: revenue +15%, 1.200.000 users (2019) 3 months
2016-2018  Analyst, Smithfield Foods
Tools: scikit-learn.org, Node.js, ASP.NET

References on request. John Smith
I hereby consent to the processing of my personal data for recruitment purposes.
"""

IT = """Curriculum Vitae
Nicolò D'Angelo – Data Engineer
Via Roma 12, 20100 Milano
Tel. 333 123 4567 · nicolo.dangelo@libero.it · github.com/ndangelo
Data di nascita: 01/02/1992
Stato civile: celibe
Codice fiscale: DNGNCL92B01F205X
IBAN: IT60 X054 2811 1010 0000 0123 456

ESPERIENZA
01/2020 – 03/2023  Data Engineer presso Banca Rossi S.p.A., Milano
Corso di Machine Learning 2 (Coursera)

Autorizzo il trattamento dei dati personali contenuti nel mio curriculum vitae ai sensi del D.Lgs. 196/2003 e del GDPR 679/16.
"""


def test_english_cv():
    out, found = redact(EN)
    assert found == {"consent": 1, "email": 1, "link": 2, "birth_date": 1, "personal": 1,
                     "address": 1, "phone": 1, "name": 2, "surname": 2}
    assert out.splitlines()[0] == "[NAME] [SURNAME]"
    for kept in ("2018 – 2021", "2016-2018", "+15%", "1.200.000", "(2019) 3 months",
                 "Acme Ltd", "Smithfield Foods", "scikit-learn.org", "Node.js", "ASP.NET",
                 "Senior Data Scientist", "Date of birth: [BIRTH DATE]", "Nationality: [REMOVED]"):
        assert kept in out
    assert "Smith" not in out.replace("Smithfield", "")


def test_italian_cv():
    out, found = redact(IT)
    assert found == {"consent": 1, "email": 1, "link": 1, "id": 2, "birth_date": 1, "personal": 1,
                     "address": 1, "phone": 1, "name": 1, "surname": 1}
    assert "[NAME] [SURNAME] – Data Engineer" in out
    for kept in ("01/2020 – 03/2023", "Banca Rossi S.p.A.", "Corso di Machine Learning 2",
                 "Curriculum Vitae", "Stato civile: [REMOVED]"):
        assert kept in out


def test_second_run_is_a_no_op():
    for cv in (EN, IT):
        once, _ = redact(cv)
        assert redact(once) == (once, {})


def test_clean_cv_has_no_findings():
    cv = "Senior ML Engineer\n\nSkills: Python, PyTorch, SQL\n2019-2024 ML Engineer, Foo GmbH"
    assert redact(cv) == (cv, {})


def test_no_name_line_removes_no_name():
    _, found = redact("Profile\nExperienced engineer\nPython developer since 2015")
    assert "name" not in found and "surname" not in found


def test_name_also_an_ordinary_word_is_left_in_lowercase_prose():
    out, _ = redact("Rosa Bianco\nI will grant access; rosa and bianco wines.")
    assert out.startswith("[NAME] [SURNAME]") and "rosa and bianco" in out
