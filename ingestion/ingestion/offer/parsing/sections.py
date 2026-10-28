"""Split a job description into sections and drop the boilerplate ones.

Headings are recognised by shape (short line, trailing colon, markdown, ALL CAPS) and classified with the
multilingual lexicon. Company / benefits / apply / legal sections and
equal-opportunity or privacy paragraphs are dropped; what remains is the text
that describes the role itself, and it is what gets chunked and embedded.

Before that, raw HTML is turned into lines and job-board clutter is cut. The opening
block, which has no heading, often starts with the company's pitch: its sentences up to
the first one about the role are dropped when they talk about the company. A company
section that clearly describes the role is kept from where the role starts.
"""
import html
import re
from dataclasses import dataclass

from ingestion.offer.parsing.lexicon import (
    BOARD_TAIL_PHRASES, BOILERPLATE_PHRASES, COMPANY_SIGNALS, DROPPED_SECTIONS, MARKER_LINES, READER_SIGNALS,
    ROLE_SIGNALS, SECTION_HEADINGS,
)

_HEADING_RES = {cls: re.compile("|".join(frags)) for cls, frags in SECTION_HEADINGS.items()}
_BOILERPLATE_RE = re.compile("|".join(BOILERPLATE_PHRASES), re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*(?:[-*•·▪◦‣–—>✓✔➢➤►●○■□]+|\(?\d{1,2}[.)]|[a-z][.)])\s+")
_MARKDOWN_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
_INLINE_HEADING_RE = re.compile(r"^([^:]{2,60}):\s+(.+)$")
_MARKDOWN_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!&|<>~=$])")
_HTML_RE = re.compile(r"</?(?:div|p|h\d|span|li|ul|br)\b", re.IGNORECASE)
_BOARD_TAIL_RE = re.compile(r"(?im)^(?=.{0,120}$).*(?:" + "|".join(BOARD_TAIL_PHRASES) + ").*$")
_MARKER_LINE_RE = re.compile(r"(?im)^\s*(?:" + "|".join(MARKER_LINES) + r")\s*$")
_ROLE_RE = re.compile(r"\b(?:" + "|".join(ROLE_SIGNALS) + ")", re.IGNORECASE)
_READER_RE = re.compile(r"\b(?:" + "|".join(READER_SIGNALS) + r")\b", re.IGNORECASE)
_COMPANY_RE = re.compile(r"\b(?:" + "|".join(COMPANY_SIGNALS) + ")", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý¿¡\"'(])")
_GENERIC_NAME_WORDS = {"gmbh", "group", "solutions", "consulting", "digital", "technologies", "limited", "services"}
_GENERIC_TITLE_WORDS = {"senior", "junior", "lead", "remote", "hybrid"}


def clean_markdown(text: str | None) -> str:
    """Undo markdown escapes left by scrapers ("5\\+ years", "£60,000\\.00\\-£65,000")."""
    return _MARKDOWN_ESCAPE_RE.sub(r"\1", text or "")


def clean_input(text: str | None) -> str:
    """Raw HTML as lines; job-board clutter (Adzuna's salary/similar-jobs tail, badges) cut."""
    text = text or ""
    if _HTML_RE.search(text):
        text = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|h\d|ul)>", "\n", text)
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
    if tail := _BOARD_TAIL_RE.search(text):
        text = text[:tail.start()]
    return _MARKER_LINE_RE.sub("", text)


@dataclass
class Sections:
    kept_text: str = ""
    headings_found: bool = False


def _strip_markers(line: str) -> str:
    line = _MARKDOWN_HEADING_RE.sub("", line)
    line = line.strip().strip("*_").strip()
    return line


def classify_heading(text: str) -> str | None:
    """Return the section class for a heading text, or None if unrecognised."""
    key = _strip_markers(text).rstrip(":：").strip().lower()
    if not key:
        return None
    for cls, regex in _HEADING_RES.items():
        if regex.search(key):
            return cls
    return None


def _looks_like_heading(line: str) -> bool:
    """Shape-only heading test (no lexicon)."""
    raw = line.strip()
    if not raw or len(raw) > 80:
        return False
    words = _strip_markers(raw).split()
    if not words or len(words) > 8:
        return False
    if _MARKDOWN_HEADING_RE.match(raw) or (raw.startswith("**") and raw.rstrip(":").endswith("**")):
        return True
    if raw.endswith(":") or raw.endswith("："):
        return True
    letters = [c for c in raw if c.isalpha()]
    return len(letters) >= 4 and all(c.isupper() for c in letters)


def _lexicon_heading(line: str) -> str | None:
    """A short, unpunctuated, non-bullet line naming a known section ("Requirements")."""
    if _BULLET_RE.match(line) or re.search(r"\d|[.!?;,]$", line.strip()):
        return None
    if len(_strip_markers(line).split()) > 5:
        return None
    return classify_heading(line)


def _words(text: str, generic: set[str]) -> list[str]:
    return [w for w in re.findall(r"\w{4,}", (text or "").lower()) if w not in generic]


def _trim_opening_blurb(lines: list[str], company: str, title: str) -> list[str]:
    """Drop the sentences before the first one about the role, when they are about the
    company. The role is signalled by hiring words or the job title; addressing the reader
    counts only when nothing stronger is found, since pitches address the reader too."""
    sentences = _SENTENCE_RE.split(" ".join(lines))
    name_words = _words(company, _GENERIC_NAME_WORDS)
    title_words = _words(title, _GENERIC_TITLE_WORDS)[:3]

    def about_role(k: int, sentence: str) -> bool:
        low = sentence.lower()
        names_title = k > 0 and len(title_words) >= 2 and sum(w in low for w in title_words) >= 2
        return bool(_ROLE_RE.search(sentence)) or names_title

    first = next((k for k, s in enumerate(sentences) if about_role(k, s)), None)
    if first is None:
        first = next((k for k, s in enumerate(sentences) if k > 0 and _READER_RE.search(s)), None)
    if not first:  # the role comes first, or no sentence is about the role: keep everything
        return lines
    prefix = " ".join(sentences[:first])
    if not (_COMPANY_RE.search(prefix) or any(w in prefix.lower() for w in name_words)):
        return lines
    return [" ".join(sentences[first:])]


def _role_in_company_section(lines: list[str]) -> list[str] | None:
    """A "company" section that describes the role (two sentences about it): from there on."""
    sentences = _SENTENCE_RE.split(" ".join(lines))
    hits = [k for k, s in enumerate(sentences) if _ROLE_RE.search(s)]
    return [" ".join(sentences[hits[0]:])] if len(hits) >= 2 else None


def split_sections(description: str | None, company: str = "", title: str = "") -> Sections:
    if not description or not description.strip():
        return Sections()

    description = clean_markdown(clean_input(description))
    blocks: list[tuple[str, list[str]]] = [("other", [])]
    headings_found = False

    for raw in description.splitlines():
        line = raw.strip()
        if not line:
            continue

        if _looks_like_heading(line):
            cls = classify_heading(line)
            headings_found = headings_found or cls is not None
            blocks.append((cls or "other", []))
            continue

        inline = _INLINE_HEADING_RE.match(line)
        if inline and len(inline.group(1).split()) <= 6 and not _BULLET_RE.match(line):
            cls = classify_heading(inline.group(1))
            if cls is not None:
                headings_found = True
                blocks.append((cls, [inline.group(2)]))
                continue

        cls = _lexicon_heading(line)
        if cls is not None:
            headings_found = True
            # a short skill line looks the same ("Strong Python skills"): kept with its section
            blocks.append((cls, [] if cls in DROPPED_SECTIONS else [line]))
            continue

        blocks[-1][1].append(line)

    kept: list[str] = []
    for n, (cls, lines) in enumerate(blocks):
        lines = [line for line in lines if not _BOILERPLATE_RE.search(line)]
        if n == 0 and lines:
            lines = _trim_opening_blurb(lines, company, title)
        if headings_found and cls in DROPPED_SECTIONS:
            lines = (_role_in_company_section(lines) if cls == "company" else None) or []
        kept.extend(lines)
    return Sections(kept_text="\n".join(kept), headings_found=headings_found)
