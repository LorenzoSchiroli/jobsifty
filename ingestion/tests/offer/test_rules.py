import pytest

from ingestion.offer.parsing import rules


@pytest.mark.parametrize("title,text,expected", [
    ("Senior Data Engineer", None, "senior"),
    ("Sr. ML Engineer", None, "senior"),
    ("Junior Python Developer", None, "junior"),
    ("Lead Data Scientist", None, "lead"),
    ("Staff Software Engineer", None, "staff"),
    ("Principal Engineer", None, "principal"),
    ("Head of Data", None, "director"),
    ("Software Engineer II", None, "mid"),
    ("Werkstudent Data Science (m/w/d)", None, "junior"),
    ("Data Engineer confirmé (H/F)", None, "senior"),
    ("Data Engineer", "You have 5+ years of experience in Python.", "senior"),
    ("Data Engineer", "At least 3 years experience with Spark.", "mid"),
    ("Data Engineer", "Minimum of five (5) years in a similar role.", "senior"),
    ("Data Engineer", "1 year of experience is enough.", "junior"),
    ("Data Engineer", "Mindestens 3 Jahre Berufserfahrung im Bereich ML.", "mid"),
    ("Data Engineer", "Vous avez 5 ans d'expérience en data.", "senior"),
    ("Data Engineer", "Almeno 2 anni di esperienza nello sviluppo.", "junior"),
    ("Data Engineer", "2+ years of experience with Python.", "junior"),
    ("Data Engineer", "Experiencia mínima de 3 años en datos.", "mid"),
    ("Data Engineer", "Experiência profissional mínima de 6 anos.", "senior"),
    ("AI Engineer", "Minst 5 års arbetslivserfarenhet.", "senior"),
    ("Data Engineer", "4+ lata doświadczenia komercyjnego.", "mid"),
    ("Data Engineer", "5+ years of hands-on machine learning experience", "senior"),
    ("Stage - AI Engineer", "", "junior"),
    ("Alternance - Data scientist F/H", "", "junior"),
    ("Data Engineer", "We build data pipelines.", None),
])
def test_seniority(title, text, expected):
    assert rules.seniority(title, text) == expected


def test_seniority_title_wins_over_years():
    assert rules.seniority("Junior Analyst", "10+ years of experience") == "junior"


@pytest.mark.parametrize("title,text,expected", [
    ("Data Engineer", "Permanent full-time role.", "full-time"),
    ("Data Engineer", "We build pipelines.", "full-time"),
    ("Data Engineer (Teilzeit)", None, "part-time"),
    ("Werkstudent Data Engineering", None, "part-time"),
    ("Contract Data Engineer", None, "contract"),
    ("Data Engineer", "This is a 6-month contract role, £600 per day inside IR35.", "contract"),
    ("Data Engineer", "Befristet auf 12 Monate.", "contract"),
    ("Machine Learning Internship", None, "internship"),
    ("Stage Data Analyst", None, "stage"),
    ("Praktikum Data Science", None, "internship"),
    ("Data Engineer", "Join an early-stage startup.", "full-time"),
    ("Data Engineer", "Contract type: permanent", "full-time"),
    ("Data Engineer", "Vollzeit oder Teilzeit möglich", "full-time"),
    ("Data Engineer", "Poste en CDI, temps plein", "full-time"),
    ("Data Engineer", "Contratto a tempo determinato", "contract"),
    ("Alternant(e) Community Manager", "", "internship"),
    ("Computer Vision Engineer", "This is not a junior or internship position.", "full-time"),
    ("Ingeniero de IA", "Aplicamos buenas prácticas de ingeniería.", "full-time"),
    ("ML Prototyping Engineer", "Project duration: 6 months", "contract"),
])
def test_employment_type(title, text, expected):
    assert rules.employment_type(title, text) == expected


@pytest.mark.parametrize("title,location,text,expected", [
    ("Data Engineer", "Remote", "", "remote"),
    ("Data Engineer (Remote)", "Berlin", "", "remote"),
    ("Data Engineer", "London", "Hybrid working: 3 days a week in the office.", "hybrid"),
    ("Data Engineer", "Paris", "Présence sur site : 3 jours par semaine", "hybrid"),
    ("Data Engineer", "München", "Mobiles Arbeiten und flexible Arbeitszeiten", "hybrid"),
    ("Data Engineer", "Milano", "Possibilità di smart working", "hybrid"),
    ("Data Engineer", "London", "This is a fully remote position.", "remote"),
    ("Data Engineer", "Madrid", "Trabajo 100% en remoto", "remote"),
    ("Data Engineer", "London", "5 days per week onsite", "on-site"),
    ("Data Engineer", "London", "25 days of paid annual leave at home or abroad", "on-site"),
    ("Data Engineer", "Berlin", "We build things.", "on-site"),
    ("Data Engineer", "", "We build things.", None),
    ("AI Security Engineer", "Oslo", "Work from home up to two days per week.", "hybrid"),
    ("Trainee Digitalisierung", "Stuttgart", "Wir bieten flexibles hybrides Arbeiten.", "hybrid"),
    ("ML Engineer", "Paris", "Poste basé à Paris avec possibilité de télétravail.", "hybrid"),
    ("Applied Scientist Intern", "Madrid", "Please note we do not offer remote internships.", "on-site"),
    ("MLOps Engineer", "Madrid", "Modalidad de trabajo 100% remota.", "remote"),
    ("Designer", "Puteaux", "Un profil créatif « hybride » et polyvalent.", "on-site"),
])
def test_location_type(title, location, text, expected):
    assert rules.location_type(title, location, text) == expected


def test_office():
    assert rules.office("Berlin, Germany", "on-site") == "berlin, germany"
    assert rules.office("Berlin", "hybrid") == "berlin"
    assert rules.office("Berlin", "remote") is None
    assert rules.office("Remote, EU", "on-site") is None
    assert rules.office(None, "on-site") is None


@pytest.mark.parametrize("text,expected", [
    ("Salary: £63,000.00-£65,000.00 per year", "£63,000.00-£65,000.00 per year"),
    ("Compensation €60k - €80k", "€60k - €80k"),
    ("Gehalt 55.000 - 70.000 EUR pro Jahr", "55.000 - 70.000 eur pro jahr"),
    ("Pay: $120,000/year", "$120,000/year"),
    ("Day rate £800 inside IR35", "£800 inside ir35"),
    ("We raised €50 million last year", None),
    ("5000–6400 EUR/month", "5000–6400 eur/month"),
    ("Retribuzione a partire da €1.500,00 al mese", "€1.500,00 al mese"),
    ("Taux journalier (TJM): 700", "taux journalier (tjm): 700"),
    ("No salary here", None),
])
def test_salary_range(text, expected):
    assert rules.salary_range(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("We are looking for an engineer to build data pipelines and machine learning systems.", "english"),
    ("Wir suchen eine erfahrene Entwicklerin für unser Data-Team in Berlin.", "german"),
    ("Nous recherchons un ingénieur pour construire nos pipelines de données.", "french"),
    ("Cerchiamo un ingegnere per sviluppare le nostre pipeline di dati.", "italian"),
])
def test_text_language(text, expected):
    assert rules.text_language(text) == expected


def test_text_language_none_for_short_text():
    assert rules.text_language("hi") is None


def test_languages_required_includes_text_language():
    assert rules.languages_required("Build pipelines.", "english") == ["english"]


@pytest.mark.parametrize("text,lang,expected", [
    ("Fluent German and English are required.", "english", {"english", "german"}),
    ("Sehr gute Deutsch- und Englischkenntnisse", "german", {"german", "english"}),
    ("Maîtrise de l'anglais indispensable", "french", {"french", "english"}),
    ("Ottima conoscenza della lingua inglese", "italian", {"italian", "english"}),
    ("Experience with Python and Java.", "english", {"english"}),
    ("Nivel alto de inglés y español.", "spanish", {"spanish", "english"}),
])
def test_languages_required_phrases(text, lang, expected):
    assert set(rules.languages_required(text, lang)) == expected
