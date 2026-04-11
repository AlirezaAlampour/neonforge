from __future__ import annotations

import re

SENTENCE_PATTERN = re.compile(r".+?(?:[.!?](?=\s|$)|$)", re.DOTALL)


def _emit_text_chunk(text: str, *, soft_split: bool = False) -> dict:
    return {
        "text": text.strip(),
        "pause_ms": 0,
        "is_pause": False,
        "soft_split": soft_split,
    }


def _soft_split_sentence(sentence: str, max_chars: int) -> list[dict]:
    remaining = sentence.strip()
    parts: list[dict] = []

    while len(remaining) > max_chars:
        window = remaining[: max_chars + 1]
        comma_index = window.rfind(",")
        whitespace_index = window.rstrip().rfind(" ")

        if comma_index >= 0:
            split_index = comma_index + 1
        elif whitespace_index > 0:
            split_index = whitespace_index
        else:
            split_index = max_chars

        chunk_text = remaining[:split_index].rstrip()
        if not chunk_text:
            chunk_text = remaining[:max_chars].rstrip()
            split_index = max_chars

        parts.append(_emit_text_chunk(chunk_text, soft_split=True))
        remaining = remaining[split_index:].lstrip()

    if remaining:
        parts.append(_emit_text_chunk(remaining, soft_split=True))

    return parts


def _split_sentences(paragraph: str) -> list[str]:
    return [match.group(0).strip() for match in SENTENCE_PATTERN.finditer(paragraph.strip()) if match.group(0).strip()]


def _chunk_paragraph(paragraph: str, max_chars: int) -> list[dict]:
    sentences = _split_sentences(paragraph)
    if not sentences:
        return []

    chunks: list[dict] = []
    current = ""

    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                chunks.append(_emit_text_chunk(current))
                current = ""
            chunks.extend(_soft_split_sentence(sentence, max_chars))
            continue

        candidate = f"{current} {sentence}".strip() if current else sentence
        if current and len(candidate) > max_chars:
            chunks.append(_emit_text_chunk(current))
            current = sentence
        else:
            current = candidate

    if current:
        chunks.append(_emit_text_chunk(current))

    return chunks


def chunk_script(text: str, max_chars: int = 200) -> list[dict]:
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return []

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
    if not paragraphs:
        return []

    ordered: list[dict] = []

    for index, paragraph in enumerate(paragraphs):
        ordered.extend(_chunk_paragraph(paragraph, max_chars=max_chars))
        if index < len(paragraphs) - 1:
            ordered.append({"text": "", "pause_ms": 600, "is_pause": True})

    return ordered
