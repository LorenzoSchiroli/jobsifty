import pytest

from ingestion.offer.scraping.scopes import AI, get_scope


@pytest.mark.parametrize("title", [
    "Senior Machine Learning Engineer", "Machine-Learning Engineer", "AI Engineer", "Senior AI/ML Engineer",
    "Applied AI Engineer, Operations Automation", "GenAI Developer", "Gen AI Consultant", "LLM Inference Engineer",
    "LLMOps Engineer", "MLOps Engineer", "Data Scientist", "Senior Data Science Engineer", "Data Engineer",
    "Data Engineering Manager", "Computer Vision Engineer - Tracker", "NLP Researcher", "Deep Learning Engineer",
    "Applied Scientist II", "KI-Entwickler (m/w/d)", "Expert IA Générative (F/H)", "Head of Artificial Intelligence",
    "ML Engineer, Video Understanding", "Product Engineer - AI Finance", "AI Solutions Architect",
    "AI Forward Deployed Engineer", "Natural Language Processing Engineer", "Large Language Model Researcher",
    "Neural Network Optimisation Engineer",
    "Data & AI Consultant", "Engineering Manager - AI Platform", "Forward Deployed Engineer GenAI",
    # a technical role wins over a topic word that is also a non-technical role
    "Machine Learning Engineer II - Growth & Personalisation", "Marketing Data Scientist",
    "Machine Learning Engineer (Governance ML Platform)", "Applied Scientist, Amazon Compliance and Safety Services",
    "Machine Learning & Data Engineer (m/w/d) – Sales Intelligence",
    "Associate in Reinforcement Learning", "Agentic Systems Engineer", "Recommender Systems Engineer",
    "AI Agent Engineer",
])
def test_ai_titles_match(title):
    assert AI.matches(title)


@pytest.mark.parametrize("title", [
    "Junior Graphic Designer", "Director of Quality", "DevSecOps Engineer", "Full Stack Developer",
    "Solution Sales Advisor - Microsoft Frontier Company", "Principal Data Architect", "Business Information Analyst",
    "Maintenance Technician", "Email Marketing Manager", "Mailroom Clerk", "", "Travel Agent",
    "Customer Service Agent",
    # not AI on their own
    "Sr. Robotics Software Engineer", "Perception Engineer", "Forward Deployed Software Engineer",
    "Founding Member of Technical Staff", "Senior Data Platform Engineer", "Senior Research Engineer",
    "Research Scientist, Computational Chemistry", "Mailing Specialist",
    # AI in the title, but not a technical role
    "AI Sales Specialist", "Marketing AI Creator", "AI Performance Video Editor (Worldwide)",
    "Group AI Risk Officer", "AI Trainers Network - Arabic", "Product Manager - AI Investing",
    "Technical Product Owner - AI Finance", "Account Executive, AI Platform", "Talent Acquisition Partner, AI",
    "AI Fulfillment & Customer Success Manager (f/m/d)", "Senior Performance Marketing Manager – Paid Social & AI",
    "Business Development Manager (AI Data Solutions)", "AI Governance Lead", "Freelance AI Tutor",
])
def test_other_titles_do_not_match(title):
    assert not AI.matches(title)


def test_or_query_quotes_phrases():
    q = AI.or_query()
    assert q.startswith('"machine learning" OR "artificial intelligence" OR AI OR ML OR ')
    assert q.count(" OR ") == len(AI.terms) - 1


def test_get_scope():
    assert get_scope("ai") is AI and get_scope(" AI ") is AI
    with pytest.raises(ValueError, match="available: ai"):
        get_scope("swe")
