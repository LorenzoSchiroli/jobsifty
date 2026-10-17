"""What a run searches for.

A scope is a set of search terms plus the rule a job's title must pass (match `title`;
then, unless it names a `role`, not match `exclude`). Each source turns the terms into the query it understands (one OR
query where the board supports it), and every scraped job, whatever its source, is kept
only when its title passes:
boards that also match descriptions (Indeed, Adzuna) bring plenty of jobs that merely
mention the terms.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Scope:
    name: str
    terms: tuple[str, ...]
    title: re.Pattern
    exclude: re.Pattern | None = None
    role: re.Pattern | None = None

    def or_query(self) -> str:
        """`"machine learning" OR AI OR ...`: LinkedIn, Indeed and Glassdoor syntax."""
        return " OR ".join(f'"{t}"' if " " in t else t for t in self.terms)

    def matches(self, title: str) -> bool:
        title = title or ""
        if not self.title.search(title):
            return False
        if self.role and self.role.search(title):
            return True
        return not (self.exclude and self.exclude.search(title))


AI = Scope(
    name="ai",
    terms=(
        "machine learning", "artificial intelligence", "AI", "ML", "LLM", "GenAI", "deep learning",
        "NLP", "computer vision", "data scientist", "MLOps", "applied scientist", "data engineer",
        "generative AI", "reinforcement learning", "agentic", "recommender systems",
    ),
    # The terms plus title variants: IA (fr/es/it), KI (de), AI/ML, LLMOps. Only words that
    # mean AI on their own: robotics, perception or "Forward Deployed" alone do not ("AI
    # Forward Deployed Engineer" matches on AI).
    title=re.compile(
        r"\b(?:ai|ia|ki|ml|llms?|llmops|gen\s?ai|machine[\s-]learning|artificial intelligence|deep learning"
        r"|nlp|natural language processing|large language models?|computer vision|data scien\w*|mlops"
        r"|applied scien\w*|data engineer\w*|reinforcement learning|neural|agentic|recommender systems?)\b",
        re.IGNORECASE,
    ),
    # Technical roles only: "AI" in a title also brings sales, marketing, recruiting, legal
    # and data-labelling gigs ("AI Trainer"). Role words, kept specific so ML roles survive
    # ("video editor", not "video": "ML Engineer, Video Understanding"); a title that
    # names a technical role (below) is kept whatever topic it adds ("Marketing Data
    # Scientist", "ML Engineer - Growth & Personalisation").
    exclude=re.compile(
        r"\b(?:sales|pre-?sales|account (?:executive|manager|director)|business development|bdr|sdr"
        r"|marketing|marketers?|growth|brand|seo|copywriters?|writers?|editors?|influencers?|creators?"
        r"|content (?:manager|strategist|specialist)s?|designers?"
        r"|recruit\w*|talent|sourcers?|hr|people partners?"
        r"|customer (?:success|support)|support specialists?|community managers?"
        r"|legal|counsel|lawyers?|compliance|governance|risk officers?|policy"
        r"|trainers?|annotators?|tutors?|teachers?|instructors?"
        r"|product (?:manager|owner|lead)s?)\b",
        re.IGNORECASE,
    ),
    role=re.compile(r"\b(?:engineer\w*|scientists?|developers?|architects?|researchers?|programmers?|mlops)\b",
                    re.IGNORECASE),
)

SCOPES = {scope.name: scope for scope in (AI,)}


def get_scope(name: str) -> Scope:
    try:
        return SCOPES[name.strip().lower()]
    except KeyError:
        raise ValueError(f"Unknown scope {name!r}; available: {', '.join(SCOPES)}") from None
