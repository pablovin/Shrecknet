"""Build document-wide heading profiles without deleting or reparenting text."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .pipeline import NormalizedDocument, NormalizedBlock


_NUMBER = re.compile(r"^\s*(?P<number>(?:chapter\s+)?(?:\d+(?:\.\d+)*|[ivxlcdm]+)[.):]?)\s+(?P<title>\S.+)$", re.I)
_DIALOGUE = re.compile(r"^\s*[\"'“‘—–-].{0,180}[\"'”’?!.,]\s*$")


def _title_key(value: str) -> str:
    value = re.sub(r"\bchapter\s+", "", value, flags=re.I)
    value = re.sub(r"^\s*(?:\d+(?:\.\d+)*|[ivxlcdm]+)[.):]?\s+", "", value, flags=re.I)
    return re.sub(r"\W+", " ", value).strip().casefold()


@dataclass(slots=True)
class HeadingCandidate:
    title: str
    block_id: str
    physical_page: int | None
    reading_order: int
    level: int | None
    confidence: float
    disposition: str
    reasons: list[str] = field(default_factory=list)
    style_signature: str | None = None


@dataclass(slots=True)
class HeadingStyleProfile:
    signature: str
    candidate_count: int
    distinct_pages: int
    median_font_size: float | None
    parser_levels: list[int]


@dataclass(slots=True)
class HeadingInferenceResult:
    candidates: list[HeadingCandidate]
    profiles: list[HeadingStyleProfile]
    sufficient_evidence: bool
    diagnostics: list[str] = field(default_factory=list)


def _native(block: NormalizedBlock) -> dict:
    value = block.metadata.get("native")
    return value if isinstance(value, dict) else {}


def _style(block: NormalizedBlock) -> tuple[str | None, float | None, bool]:
    native = _native(block)
    formatting = native.get("formatting") if isinstance(native.get("formatting"), dict) else {}
    values = {**native, **formatting}
    size = None
    for name in ("font_size", "fontSize", "size", "point_size"):
        try:
            candidate = float(values[name])
            if 1 <= candidate <= 200:
                size = candidate
                break
        except (KeyError, TypeError, ValueError):
            continue
    font_name = next((str(values[name]).strip().casefold() for name in ("font_name", "fontName", "font", "style")
                      if values.get(name)), None)
    bold = any(values.get(name) is True for name in ("bold", "is_bold", "font_bold"))
    signature = "|".join(part for part in (font_name, f"{size:g}" if size else None, "bold" if bold else None) if part)
    return (signature or None), size, bold


def _numbered_level(text: str) -> tuple[str, int] | None:
    match = _NUMBER.match(text)
    if not match:
        return None
    token = re.sub(r"^(chapter\s+)", "", match.group("number"), flags=re.I).rstrip(".):")
    depth = token.count(".") + 1 if token[0].isdigit() else 1
    return match.group("title").strip(), depth


def infer_heading_candidates(document: NormalizedDocument, *, toc_titles: set[str] | None = None,
                             toc_source_block_ids: set[str] | None = None) -> HeadingInferenceResult:
    """Rank heading-like blocks with global style, numbering and repetition evidence."""
    toc_keys = {_title_key(value) for value in (toc_titles or set())}
    sections = {section.section_id: section for section in document.sections}
    by_text_pages: dict[str, set[int]] = defaultdict(set)
    for block in document.blocks:
        if block.block_id in (toc_source_block_ids or set()):
            continue
        normalized = re.sub(r"\W+", " ", block.display_text).strip().casefold()
        if normalized:
            by_text_pages[normalized].update(block.page_numbers)

    profiles_raw: dict[str, list[tuple[NormalizedBlock, float | None, int | None]]] = defaultdict(list)
    candidates: list[HeadingCandidate] = []
    for block in document.blocks:
        if block.block_id in (toc_source_block_ids or set()):
            continue
        text = " ".join(block.display_text.split())
        if not text:
            continue
        is_explicit = block.block_type == "heading"
        if block.block_type in {"table", "list", "list_item", "caption", "picture", "formula", "page_header", "page_footer"}:
            continue
        numbered = _numbered_level(text)
        native_type = (block.native_type or "").casefold()
        if not is_explicit and not numbered and native_type not in {"section_header", "title"}:
            signature, _, _ = _style(block)
            if not signature and not text.isupper() and not (text.istitle() and len(text.split()) <= 12):
                continue
        signature, size, bold = _style(block)
        source_section = sections.get(block.section_id)
        parser_level = source_section.heading_level if is_explicit and source_section else None
        inferred_level = numbered[1] if numbered else parser_level
        title = numbered[0] if numbered else text
        page = min(block.page_numbers) if block.page_numbers else None
        key = signature or ("numbered" if numbered else "unprofiled")
        profiles_raw[key].append((block, size, parser_level))
        reasons: list[str] = []
        score = 0.38 if is_explicit else 0.0
        if numbered:
            score += 0.27
            reasons.append("numbered_heading")
        if signature:
            score += 0.08
            reasons.append("native_style")
        if bold:
            score += 0.07
            reasons.append("bold_style")
        if size:
            reasons.append("font_size_available")
        if len(text) <= 120 and len(text.split()) <= 18:
            score += 0.12
            reasons.append("heading_length")
        if text.isupper() or text.istitle():
            score += 0.07
            reasons.append("title_case")
        norm = _title_key(title)
        if norm in toc_keys:
            score += 0.12
            reasons.append("toc_title_match")
        page_freq = len(by_text_pages[re.sub(r"\W+", " ", text).strip().casefold()])
        if page_freq >= 3:
            score -= min(0.55, 0.18 * (page_freq - 1))
            reasons.append("repeated_running_text")
        if _DIALOGUE.match(text):
            score -= 0.55
            reasons.append("dialogue_like")
        if text.endswith((".", ",", ";")) and not numbered:
            score -= 0.16
            reasons.append("sentence_punctuation")
        if page is None:
            score -= 0.1
            reasons.append("missing_page_provenance")
        score = max(0.0, min(1.0, score))
        disposition = "candidate" if score >= 0.48 else "rejected"
        if disposition == "rejected":
            reasons.append("insufficient_heading_evidence")
        candidates.append(HeadingCandidate(
            title=title, block_id=block.block_id, physical_page=page,
            reading_order=block.reading_order, level=inferred_level,
            confidence=round(score, 3), disposition=disposition, reasons=reasons,
            style_signature=signature,
        ))

    profiles: list[HeadingStyleProfile] = []
    for signature, records in sorted(profiles_raw.items()):
        sizes = sorted(size for _, size, _ in records if size is not None)
        median = sizes[len(sizes) // 2] if sizes else None
        profile_pages = {page for block, _, _ in records for page in block.page_numbers}
        levels = sorted({level for _, _, level in records if level is not None})
        profiles.append(HeadingStyleProfile(signature, len(records), len(profile_pages), median, levels))
    profile_by_signature = {profile.signature: profile for profile in profiles}
    distinct_sizes = sorted({profile.median_font_size for profile in profiles if profile.median_font_size is not None}, reverse=True)
    block_by_id = {block.block_id: block for block in document.blocks}
    for candidate in candidates:
        profile = profile_by_signature.get(candidate.style_signature or "unprofiled")
        if profile and profile.candidate_count >= 2 and profile.distinct_pages >= 2:
            candidate.confidence = min(1.0, candidate.confidence + 0.1)
            candidate.reasons.append("document_wide_style_profile")
        if profile and profile.median_font_size is not None and distinct_sizes:
            size_rank = distinct_sizes.index(profile.median_font_size)
            if len(distinct_sizes) > 1 and size_rank == 0:
                candidate.confidence = min(1.0, candidate.confidence + 0.08)
                candidate.reasons.append("globally_prominent_font_size")
        block = block_by_id.get(candidate.block_id)
        if block is not None and candidate.style_signature:
            _, font_size, _ = _style(block)
            if font_size is not None and len(distinct_sizes) > 1:
                candidate.level = distinct_sizes.index(font_size) + 1
        candidate.disposition = "candidate" if candidate.confidence >= 0.48 else "rejected"
    strong = [candidate for candidate in candidates if candidate.disposition == "candidate"]
    evidence_pages = {candidate.physical_page for candidate in strong if candidate.physical_page is not None}
    has_repeated_numbering = sum(1 for candidate in strong if "numbered_heading" in candidate.reasons) >= 2
    sufficient = len(evidence_pages) >= 2 or has_repeated_numbering
    diagnostics = [] if sufficient else ["heading_evidence_insufficient"]
    return HeadingInferenceResult(candidates, profiles, sufficient, diagnostics)
