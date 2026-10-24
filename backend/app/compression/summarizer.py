"""Deterministic extractive summarizer behind a SummaryProvider interface.

The embedding model is an encoder, not a text generator, so MORPHE does not use it for summaries. This provider
selects and orders existing sentences (nothing is paraphrased): repeated sentences are dropped, sentences that carry
numbers, dates, identifiers or other exact facts are always kept, and the rest are chosen by informativeness until the
token target is met.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Sequence

from ..adaptation.module_merger import fact_tokens

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[`])|\n+")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")
_IDENT = re.compile(r"`[^`]+`|\b[a-z]+_[a-z0-9_]+\b|\b[A-Z][a-z]+[A-Z]\w*\b|\b[A-Z]{3,}\b|https?://\S+|[\w./-]+\.\w{2,4}\b")
_STOP = frozenset("the a an and or of to in on for with is are was were be this that it as by at from can will "
                  "which when then than into more most also such these those their its not but have has".split())
FILLERS = [(r"\bit is important to note that\s*", ""), (r"\bit should be noted that\s*", ""), (r"\bbasically,?\s*", ""),
           (r"\bin order to\b", "to"), (r"\bas a matter of fact,?\s*", ""), (r"\bneedless to say,?\s*", ""),
           (r"\bfor the most part,?\s*", ""), (r"\bat the end of the day,?\s*", ""), (r"\bin essence,?\s*", "")]


def critical_facts(text: str) -> set[str]:
    """Exact facts a summary must not lose: numbers/versions/dates and code-like identifiers."""
    return fact_tokens(text) | {m.group(0).lower() for m in _IDENT.finditer(text)}


@dataclass
class SummaryResult:
    text: str
    source_memory_ids: list[str]
    input_token_count: int
    output_token_count: int
    provider_name: str
    provider_version: str = "1"
    warnings: list[str] = field(default_factory=list)
    preserved_facts: bool = True
    missing_facts: list[str] = field(default_factory=list)


class SummaryProvider(Protocol):
    name: str

    def summarize(self, records: Sequence[dict[str, Any]], max_output_tokens: int) -> SummaryResult: ...


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


class ExtractiveSummarizer:
    name = "extractive"
    version = "1"

    def __init__(self, count_tokens: Callable[[str], int]):
        self.count = count_tokens

    def _sentences(self, records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        out, seen = [], []
        for ri, r in enumerate(records):
            text = re.sub(r"^\s*#{1,6}\s+.*$", "", r["text"], flags=re.M)  # headings are repeated in source_name/section
            for si, s in enumerate(x.strip() for x in _SENT.split(text)):
                if len(s) < 3:
                    continue
                key = _norm(s)
                words = set(key.split())
                if any(key == k or (words and len(words & w) / len(words | w) >= 0.9) for k, w in seen):
                    continue  # repeated / near-identical sentence
                seen.append((key, words))
                out.append({"ri": ri, "si": si, "text": s, "facts": critical_facts(s), "tokens": self.count(s)})
        return out

    def summarize(self, records: Sequence[dict[str, Any]], max_output_tokens: int) -> SummaryResult:
        ids = [r["memory_id"] for r in records]
        if not records:
            return SummaryResult("", [], 0, 0, self.name, self.version, ["no input records"], False)
        input_tokens = sum(self.count(r["text"]) for r in records)
        sents = self._sentences(records)
        df = Counter(w for s in sents for w in set(_WORD.findall(s["text"].lower())) if w not in _STOP)
        n = max(1, len(sents))
        for s in sents:
            ws = [w for w in set(_WORD.findall(s["text"].lower())) if w not in _STOP]
            s["score"] = sum(math.log(1 + n / df[w]) for w in ws) / (1 + len(ws)) ** 0.5 + (0.3 if s["si"] == 0 else 0.0)
        warnings: list[str] = []
        must = [s for s in sents if s["facts"]]
        chosen = {id(s) for s in must}
        used = sum(s["tokens"] for s in must)
        if used > max_output_tokens:
            warnings.append(f"sentences carrying exact facts alone need {used} tokens (target {max_output_tokens})")
        for s in sorted((s for s in sents if not s["facts"]), key=lambda s: (-s["score"], s["ri"], s["si"])):
            if used + s["tokens"] <= max_output_tokens:
                chosen.add(id(s))
                used += s["tokens"]
        picked = sorted((s for s in sents if id(s) in chosen), key=lambda s: (s["ri"], s["si"]))
        text = " ".join(s["text"] for s in picked).strip()
        src_facts = set().union(*(critical_facts(r["text"]) for r in records))
        missing = sorted(src_facts - critical_facts(text))
        return SummaryResult(text, ids, input_tokens, self.count(text) if text else 0, self.name, self.version,
                             warnings, not missing, missing[:10])


def shorten_text(text: str) -> str:
    """Single-record shortening: drop repeated sentences and filler phrases line by line (layout is kept).
    Callers must not pass code or other structured text."""
    seen: set[str] = set()
    lines_out: list[str] = []
    for line in text.split("\n"):
        if line.lstrip().startswith("#") or not line.strip():
            lines_out.append(line)
            continue
        for pat, rep in FILLERS:
            line = re.sub(pat, rep, line, flags=re.I)
        kept = []
        for sent in re.split(r"(?<=[.!?])\s+", line.strip()):
            k = _norm(sent)
            if k and k in seen:
                continue
            seen.add(k)
            kept.append(sent)
        if kept:
            lines_out.append(re.sub(r"[ \t]{2,}", " ", " ".join(kept)).strip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines_out)).strip()
