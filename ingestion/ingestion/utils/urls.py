"""Company website URLs reduced to the domain the company registered.

Subdomains belong to whoever owns the registered domain: careers.criteo.com is
Criteo's, but zendesk.wd1.myworkdayjobs.com is Workday's. Matching a company name
against the registered domain only (and storing that site's root) therefore drops
hiring platforms and turns careers/newsroom pages into the company's homepage.
"""
from urllib.parse import urlparse

import tldextract

# the bundled public suffix list: no network call at runtime
_extract = tldextract.TLDExtract(suffix_list_urls=())


def registered_domain(url: str) -> str:
    """'https://careers.criteo.com/jobs' → 'criteo.com'; '' when there is none."""
    return _extract(url).top_domain_under_public_suffix.lower()


def site_root(url: str | None) -> str | None:
    """The company's homepage for any URL on its registered domain.

    'https://careers.criteo.com/jobs' → 'https://www.criteo.com';
    'https://huk.de/x' → 'https://huk.de'. None for anything that is not an http(s) URL.
    """
    if not url or not url.startswith(("http://", "https://")):
        return None
    parsed = urlparse(url)
    domain = registered_domain(url)
    if not domain:
        return None
    host = parsed.netloc.lower().split(":")[0]
    # keep the host when it already is the site (with or without www); other subdomains
    # are a section of it, whose public homepage is conventionally on www
    if host not in (domain, f"www.{domain}"):
        host = f"www.{domain}"
    return f"{parsed.scheme}://{host}"
