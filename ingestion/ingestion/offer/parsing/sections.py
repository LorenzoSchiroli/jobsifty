"""Split a job description into sections and drop the boilerplate ones.

Headings are recognised by shape (short line, trailing colon, markdown, ALL CAPS) and classified with the
multilingual lexicon. Company / benefits / apply / legal sections and
equal-opportunity or privacy paragraphs are dropped; what remains is the text
that describes the role itself, and it is what gets chunked and embedded.
"""
import re
from dataclasses import dataclass

from ingestion.offer.parsing.lexicon import BOILERPLATE_PHRASES, DROPPED_SECTIONS, SECTION_HEADINGS

_HEADING_RES = {cls: re.compile("|".join(frags)) for cls, frags in SECTION_HEADINGS.items()}
_BOILERPLATE_RE = re.compile("|".join(BOILERPLATE_PHRASES), re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*(?:[-*•·▪◦‣–—>✓✔➢➤►●○■□]+|\(?\d{1,2}[.)]|[a-z][.)])\s+")
_MARKDOWN_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
_INLINE_HEADING_RE = re.compile(r"^([^:]{2,60}):\s+(.+)$")
_MARKDOWN_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!&|<>~=$])")


def clean_markdown(text: str | None) -> str:
    """Undo markdown escapes left by scrapers ("5\\+ years", "£60,000\\.00\\-£65,000")."""
    return _MARKDOWN_ESCAPE_RE.sub(r"\1", text or "")


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


def split_sections(description: str | None) -> Sections:
    if not description or not description.strip():
        return Sections()

    description = clean_markdown(description)
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
            blocks.append((cls, []))
            continue

        blocks[-1][1].append(line)

    kept = [
        line
        for cls, lines in blocks
        if not (headings_found and cls in DROPPED_SECTIONS)
        for line in lines
        if not _BOILERPLATE_RE.search(line)
    ]
    return Sections(kept_text="\n".join(kept), headings_found=headings_found)
