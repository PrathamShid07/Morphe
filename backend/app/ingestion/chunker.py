"""Text normalization and structure-aware chunking.

Chunks follow headings, paragraphs and fenced code blocks. A heading always starts a new chunk, so chunks never mix
sections. Overlap is applied only where a single block is too large and must be cut through (continuous prose); at
paragraph boundaries no facts cross the boundary, so repeating text would only create near-duplicates.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


@dataclass
class ChunkDraft:
    index: int
    text: str
    section: str | None
    token_count: int


@dataclass
class _Block:
    kind: str  # heading | para | code
    text: str
    section: str | None


def normalize_text(text: str) -> str:
    """Normalize line endings and whitespace without touching code-block content."""
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    in_code = False
    for line in text.split("\n"):
        if _FENCE.match(line):
            in_code = not in_code
            out.append(line.rstrip())
        elif in_code:
            out.append(line)
        else:
            out.append(re.sub(r"[ \t ]+", " ", line).strip())
    joined = "\n".join(out)
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    return joined.strip()


def _parse_blocks(text: str) -> list[_Block]:
    blocks: list[_Block] = []
    section: str | None = None
    para: list[str] = []
    code: list[str] = []
    in_code = False

    def flush_para() -> None:
        if para:
            blocks.append(_Block("para", "\n".join(para).strip(), section))
            para.clear()

    for line in text.split("\n"):
        if _FENCE.match(line):
            if in_code:
                code.append(line)
                blocks.append(_Block("code", "\n".join(code), section))
                code.clear()
                in_code = False
            else:
                flush_para()
                code.append(line)
                in_code = True
            continue
        if in_code:
            code.append(line)
            continue
        m = _HEADING.match(line)
        if m:
            flush_para()
            section = m.group(2).strip()
            blocks.append(_Block("heading", line.strip(), section))
        elif not line.strip():
            flush_para()
        else:
            para.append(line)
    flush_para()
    if code:  # unterminated fence: keep as code, do not lose text
        blocks.append(_Block("code", "\n".join(code), section))
    return [b for b in blocks if b.text.strip()]


def _split_words(text: str, count: Callable[[str], int], max_tokens: int, overlap: int) -> list[str]:
    words = text.split()
    tok = [max(1, count(w)) for w in words]
    pieces: list[str] = []
    start = 0
    while start < len(words):
        total, end = 0, start
        while end < len(words) and (total + tok[end] <= max_tokens or end == start):
            total += tok[end]
            end += 1
        pieces.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        back, acc = end, 0
        while back > start + 1 and acc + tok[back - 1] <= overlap:
            back -= 1
            acc += tok[back]
        start = back if back > start else end
    return pieces


def _split_paragraph(text: str, count: Callable[[str], int], max_tokens: int, overlap: int) -> list[str]:
    sentences = [s.strip() for s in _SENT.split(text) if s.strip()]
    pieces: list[str] = []
    cur: list[str] = []
    cur_tok = 0
    for s in sentences:
        st = count(s)
        if st > max_tokens:
            if cur:
                pieces.append(" ".join(cur))
                cur, cur_tok = [], 0
            pieces.extend(_split_words(s, count, max_tokens, overlap))
            continue
        if cur and cur_tok + st > max_tokens:
            pieces.append(" ".join(cur))
            carry: list[str] = []
            carry_tok = 0
            for prev in reversed(cur):  # overlap: trailing sentences that fit the overlap budget
                pt = count(prev)
                if carry_tok + pt > overlap:
                    break
                carry.insert(0, prev)
                carry_tok += pt
            cur, cur_tok = carry, carry_tok
        cur.append(s)
        cur_tok += st
    if cur:
        pieces.append(" ".join(cur))
    return pieces


def _split_code(text: str, count: Callable[[str], int], max_tokens: int) -> list[str]:
    pieces: list[str] = []
    cur: list[str] = []
    cur_tok = 0
    for line in text.split("\n"):
        lt = max(1, count(line))
        if cur and cur_tok + lt > max_tokens:
            pieces.append("\n".join(cur))
            cur, cur_tok = [], 0
        cur.append(line)
        cur_tok += lt
    if cur:
        pieces.append("\n".join(cur))
    return pieces


def chunk_text(text: str, count_tokens: Callable[[str], int], max_tokens: int = 200,
               overlap_tokens: int = 25) -> list[ChunkDraft]:
    text = normalize_text(text)
    if not text:
        return []
    drafts: list[tuple[str, str | None]] = []
    cur: list[str] = []
    cur_tok = 0
    cur_section: str | None = None

    def flush() -> None:
        nonlocal cur, cur_tok
        body = "\n\n".join(cur).strip()
        if body:
            drafts.append((body, cur_section))
        cur, cur_tok = [], 0

    for b in _parse_blocks(text):
        if b.kind == "heading":
            flush()
            cur_section = b.section
            cur.append(b.text)
            cur_tok += count_tokens(b.text)
            continue
        bt = count_tokens(b.text)
        if bt > max_tokens:
            pieces = (_split_code(b.text, count_tokens, max_tokens) if b.kind == "code"
                      else _split_paragraph(b.text, count_tokens, max_tokens, overlap_tokens))
            for p in pieces:
                pt = count_tokens(p)
                if cur and cur_tok + pt > max_tokens:
                    flush()
                cur.append(p)
                cur_tok += pt
            continue
        if cur and cur_tok + bt > max_tokens:
            flush()
        cur.append(b.text)
        cur_tok += bt
    flush()

    chunks: list[ChunkDraft] = []
    for body, section in drafts:
        if not body.strip() or all(ln.lstrip().startswith("#") for ln in body.split("\n") if ln.strip()):
            continue  # empty, or a heading with no body
        chunks.append(ChunkDraft(len(chunks), body, section, count_tokens(body)))
    return chunks
