import logging
import re
import unicodedata

import requests

from ingestion.utils.urls import site_root

logger = logging.getLogger(__name__)

_API_URL = "https://www.wikidata.org/w/api.php"
_HEADERS = {"User-Agent": "jobsifty-ingestion/0.1 (https://jobsifty.com)"}
_TIMEOUT = 10

_LEGAL_SUFFIXES = {
    "inc", "incorporated", "ltd", "limited", "llc", "llp", "plc", "corp", "corporation", "co", "company",
    "gmbh", "ag", "se", "kg", "kgaa", "mbh", "sa", "sas", "sarl", "spa", "srl", "bv", "nv", "ab", "as", "asa",
    "oy", "oyj", "group", "holding", "holdings",
}

# instance-of (P31) values that mark an entity as a business/organisation
_ORG_TYPES = {
    "Q4830453",   # business
    "Q783794",    # company
    "Q891723",    # public company
    "Q6881511",   # enterprise
    "Q43229",     # organization
    "Q1058914",   # software company
    "Q18388277",  # technology company
    "Q2401749",   # consulting company (management consulting firm)
    "Q1589009",   # privately held company
    "Q161726",    # multinational corporation
    "Q4182287",   # conglomerate
    "Q22687",     # bank
    "Q1137109",   # video game developer
    "Q210167",    # video game publisher
    "Q1331793",   # media company
    "Q15911314",  # association
    "Q167037",    # corporation
    "Q658255",    # subsidiary
    "Q1664720",   # institute
    "Q3918",      # university
}


def normalize(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = text.replace("&", " and ")
    tokens = re.sub(r"[^a-z0-9 ]+", " ", text).split()
    while len(tokens) > 1 and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _get(params: dict) -> dict:
    resp = requests.get(_API_URL, params={**params, "format": "json"}, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _search(query: str) -> list[dict]:
    data = _get({"action": "wbsearchentities", "search": query, "language": "en", "type": "item", "limit": 7})
    return data.get("search", [])


def _entities(ids: list[str], props: str) -> dict:
    if not ids:
        return {}
    data = _get({"action": "wbgetentities", "ids": "|".join(ids), "props": props, "languages": "en"})
    return data.get("entities", {})


def _statements(entity: dict, prop: str) -> list[dict]:
    claims = [c for c in entity.get("claims", {}).get(prop, []) if c.get("rank") != "deprecated"]
    preferred = [c for c in claims if c.get("rank") == "preferred"]
    return preferred or claims


def _value(claim: dict):
    return claim.get("mainsnak", {}).get("datavalue", {}).get("value")


def _item_id(claim: dict) -> str | None:
    value = _value(claim)
    return value.get("id") if isinstance(value, dict) else None


def _is_org(entity: dict) -> bool:
    claims = entity.get("claims", {})
    if "P1128" in claims or "P452" in claims:
        return True
    return any(_item_id(c) in _ORG_TYPES for c in claims.get("P31", []))


def _founded_year(entity: dict) -> int | None:
    for claim in _statements(entity, "P571"):
        value = _value(claim)
        if isinstance(value, dict) and (m := re.match(r"^\+?(\d{4})", value.get("time", ""))):
            return int(m.group(1))
    return None


def _employee_count(entity: dict) -> str | None:
    best: tuple[str, str] | None = None  # (point-in-time, amount)
    for claim in entity.get("claims", {}).get("P1128", []):
        if claim.get("rank") == "deprecated":
            continue
        value = _value(claim)
        if not isinstance(value, dict) or "amount" not in value:
            continue
        times = claim.get("qualifiers", {}).get("P585", [])
        when = (times[0].get("datavalue", {}).get("value", {}).get("time", "") if times else "")
        amount = str(int(float(value["amount"])))
        if best is None or when >= best[0]:
            best = (when, amount)
    return best[1] if best else None


def _website(entity: dict) -> str | None:
    """The official website (P856), as the site root."""
    for claim in _statements(entity, "P856"):
        if isinstance(value := _value(claim), str) and (root := site_root(value)):
            return root
    return None


def _matches(candidate: dict, target: str) -> bool:
    texts = [candidate.get("label", ""), candidate.get("match", {}).get("text", "")]
    texts += candidate.get("aliases", [])
    return any(normalize(t) == target for t in texts if t)


def lookup(name: str) -> dict:
    """Best-effort Wikidata facts for a company; {} when no confident match or on any error."""
    target = normalize(name)
    if not target:
        return {}
    try:
        queries = [name] + ([target] if target != name.lower() else [])
        candidates: list[dict] = []
        for query in queries:
            candidates = [c for c in _search(query) if _matches(c, target)]
            if candidates:
                break
        if not candidates:
            return {}

        ids = [c["id"] for c in candidates]
        entities = _entities(ids, "claims|descriptions")
        entity = next((entities[i] for i in ids if i in entities and _is_org(entities[i])), None)
        if entity is None:
            return {}

        country_id = next((i for c in _statements(entity, "P17") if (i := _item_id(c))), None)
        industry_id = next((i for c in _statements(entity, "P452") if (i := _item_id(c))), None)
        labels = _entities([i for i in (country_id, industry_id) if i], "labels")

        def label(item_id: str | None) -> str | None:
            if not item_id or item_id not in labels:
                return None
            return labels[item_id].get("labels", {}).get("en", {}).get("value")

        result = {
            "founded_year": _founded_year(entity),
            "employee_count": _employee_count(entity),
            "country": label(country_id),
            "industry": label(industry_id),
            "description": entity.get("descriptions", {}).get("en", {}).get("value"),
            "website": _website(entity),
        }
        return {k: v for k, v in result.items() if v is not None}
    except Exception as e:
        logger.warning("Wikidata lookup failed for %r: %s", name, e)
        return {}
