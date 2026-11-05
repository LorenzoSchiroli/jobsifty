import json
import re
from backend.llm import get_pool
from backend.search.filters import SearchFilters

_SYSTEM_PROMPT = """Extract structured search filters and a semantic query from a CV and job search query.
Return a JSON object with exactly these fields (null for unknown):
{
  "semantic_query": "required — keyword-rich string combining CV skills and job intent",
  "is_consulting": boolean or null,
  "is_startup": boolean or null,
  "industry": "string or null",
  "country": "string or null",
  "employee_count": "string or null",
  "min_review_score": number or null,
  "min_financial_health_score": integer or null,
  "employment_type": "one of: full-time, part-time, contract, internship, stage, freelance — or null if not specified",
  "location_type": "one of: on-site, remote, hybrid — or null if not specified",
  "seniority": "one of: junior, mid, senior, lead, principal, staff, director — or null if not specified",
  "languages_required": ["list of spoken/natural languages e.g. English, German, French"] or null,
  "max_age_hours": integer — how many hours back to search. Default 72 (3 days). Lower when user says "last N hours/days" (e.g. "last 2 hours" → 2, "last 3 days" → 72). Null only if user explicitly asks for no time limit.
  "strict": false — set to true ONLY when the user explicitly requests strict/exact/no-miss matching (e.g. "strictly", "only", "exact", "no exceptions"). Default false.
}"""


async def extract_filters(cv_text: str, query: str) -> SearchFilters:
    def parse(content: str) -> dict:
        data = json.loads(re.sub(r"```(?:json)?\s*|\s*```", "", content.strip()))
        if not isinstance(data, dict):
            raise ValueError("filters must be a JSON object")
        return data

    data = await get_pool().acomplete(
        f"CV:\n{cv_text}\n\nSearch query:\n{query}", parse, "extract_filters", system=_SYSTEM_PROMPT, json=True,
    )
    if data is None:
        return SearchFilters(semantic_query=query)
    data.setdefault("semantic_query", query)
    valid_fields = SearchFilters.model_fields.keys()
    return SearchFilters(**{k: v for k, v in data.items() if k in valid_fields})
