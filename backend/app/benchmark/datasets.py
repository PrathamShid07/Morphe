"""BEIR dataset access through the official loader, with explicit availability errors and validation.

Nothing is downloaded unless the caller passes `download=True`; a missing dataset is never silently replaced.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from ..common import MorpheError

BEIR_URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/{id}.zip"

# Licence notes are copied from BEIR's dataset table as I understand it; verify before redistributing any data.
REGISTRY: dict[str, dict[str, str]] = {
    "scifact": {"domain": "scientific claim verification", "license": "CC BY-NC 2.0 (per BEIR table; verify)"},
    "nfcorpus": {"domain": "biomedical / nutrition", "license": "non-commercial research use (per BEIR table; verify)"},
    "arguana": {"domain": "argument retrieval", "license": "CC BY 4.0 (per BEIR table; verify)"},
    "fiqa": {"domain": "financial QA", "license": "see BEIR table"},
    "trec-covid": {"domain": "bio-medical (COVID)", "license": "see BEIR table"},
    "scidocs": {"domain": "scientific citation prediction", "license": "see BEIR table"},
    "quora": {"domain": "duplicate question retrieval", "license": "see BEIR table"},
}
OFFICIAL_SPLITS = ("train", "dev", "test")


class DatasetNotAvailable(MorpheError):
    status_code, code = 404, "dataset_not_available"


class DatasetInvalid(MorpheError):
    status_code, code = 422, "dataset_invalid"


@dataclass
class BeirData:
    dataset_id: str
    path: str
    corpus: dict[str, dict[str, str]]
    queries: dict[str, dict[str, str]]  # split -> qid -> text
    qrels: dict[str, dict[str, dict[str, int]]]  # split -> qid -> docid -> grade
    source: dict[str, Any] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    def counts(self, split: str | None = None) -> dict[str, int]:
        c = {"corpus": len(self.corpus)}
        splits = [split] if split else list(self.qrels)
        for sp in splits:
            c[f"{sp}_queries"] = len(self.queries.get(sp, {}))
            c[f"{sp}_qrels"] = sum(len(v) for v in self.qrels.get(sp, {}).values())
        return c


def dataset_path(data_dir: str, dataset_id: str) -> Path:
    if not dataset_id or "/" in dataset_id or ".." in dataset_id or dataset_id.startswith("."):
        raise DatasetInvalid(f"invalid dataset id {dataset_id!r}")
    return Path(data_dir) / dataset_id


def split_files(path: Path) -> list[str]:
    q = path / "qrels"
    return sorted(f[:-4] for f in os.listdir(q) if f.endswith(".tsv")) if q.is_dir() else []


def is_cached(data_dir: str, dataset_id: str) -> bool:
    p = dataset_path(data_dir, dataset_id)
    return all((p / f).exists() for f in ("corpus.jsonl", "queries.jsonl")) and bool(split_files(p))


def list_datasets(data_dir: str, extra: Sequence[str] = ()) -> list[dict[str, Any]]:
    """Configured datasets with their local cache status (cheap: reads only qrels line counts for cached sets)."""
    out = []
    local = sorted(p.name for p in Path(data_dir).iterdir() if p.is_dir() and is_cached(data_dir, p.name)) if Path(data_dir).is_dir() else []
    for did in dict.fromkeys([*REGISTRY, *local, *extra]):
        p = dataset_path(data_dir, did)
        row: dict[str, Any] = {"dataset_id": did, "cached": is_cached(data_dir, did), "path": str(p),
                               "domain": REGISTRY.get(did, {}).get("domain"), "license": REGISTRY.get(did, {}).get("license"),
                               "source_url": BEIR_URL.format(id=did)}
        if row["cached"]:
            row["splits"] = {}
            for sp in split_files(p):
                with open(p / "qrels" / f"{sp}.tsv", encoding="utf-8") as f:
                    rows = [ln.split("\t")[0] for ln in f.read().splitlines()[1:] if ln.strip()]
                row["splits"][sp] = {"queries": len(set(rows)), "qrels": len(rows)}
            with open(p / "corpus.jsonl", "rb") as f:
                row["corpus_size"] = sum(1 for _ in f)
            row["size_mb"] = round(sum(x.stat().st_size for x in p.rglob("*") if x.is_file()) / 1_048_576, 1)
        out.append(row)
    return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_beir(corpus: dict[str, dict[str, str]], queries: dict[str, str], qrels: dict[str, dict[str, int]],
                  corpus_lines: int | None = None, strict_qrels: bool = False) -> list[str]:
    """Return a list of integrity problems; an empty list means the split is usable.

    Judgments that point at documents absent from the official corpus are reported by `qrels_missing_documents` and, unless
    `strict_qrels`, are NOT errors: BEIR's own evaluation keeps them (they are simply unretrievable for every method)."""
    issues: list[str] = []
    if not corpus:
        issues.append("corpus is empty")
    if corpus_lines is not None and corpus_lines != len(corpus):
        issues.append(f"corpus has {corpus_lines} lines but {len(corpus)} unique ids (duplicate document ids)")
    empty = [d for d, v in corpus.items() if not ((v.get("title") or "").strip() or (v.get("text") or "").strip())]
    if empty:
        issues.append(f"{len(empty)} corpus documents have neither title nor text (e.g. {empty[:3]})")
    if not queries:
        issues.append("no queries")
    missing_q = [q for q in qrels if q not in queries]
    if missing_q:
        issues.append(f"{len(missing_q)} qrels query ids are not in the query file (e.g. {missing_q[:3]})")
    if strict_qrels and qrels_missing_documents(corpus, qrels):
        missing_d = qrels_missing_documents(corpus, qrels)
        issues.append(f"{len(missing_d)} qrels document ids are not in the corpus (e.g. {sorted(missing_d)[:3]})")
    for qid, rels in qrels.items():
        if not rels:
            issues.append(f"query {qid} has no judgments")
        elif any(not isinstance(g, int) or g < 0 for g in rels.values()):
            issues.append(f"query {qid} has non-integer or negative relevance grades")
        elif not any(g > 0 for g in rels.values()):
            issues.append(f"query {qid} has no relevant (grade > 0) document")
        if len(issues) > 25:
            break
    return issues


def qrels_missing_documents(corpus: dict[str, dict[str, str]], qrels: dict[str, dict[str, int]]) -> set[str]:
    return {d for q in qrels.values() for d in q if d not in corpus}


def load_beir(dataset_id: str, data_dir: str, splits: Sequence[str] = ("test",), *, download: bool = False) -> BeirData:
    """Load corpus + the requested qrels splits with `beir.datasets.data_loader.GenericDataLoader`."""
    from beir import util
    from beir.datasets.data_loader import GenericDataLoader

    path = dataset_path(data_dir, dataset_id)
    if not is_cached(data_dir, dataset_id):
        if not download:
            raise DatasetNotAvailable(
                f"BEIR dataset '{dataset_id}' is not cached in {data_dir}. Download it explicitly "
                f"(`--download` on the CLI) or place the official folder there. No substitute dataset is used.")
        try:
            util.download_and_unzip(BEIR_URL.format(id=dataset_id), str(data_dir))
        except Exception as exc:
            raise DatasetNotAvailable(f"could not download BEIR dataset '{dataset_id}': {exc}") from exc
        if not is_cached(data_dir, dataset_id):
            raise DatasetNotAvailable(f"downloaded archive for '{dataset_id}' has an unexpected layout")
    available = split_files(path)
    bad = [s for s in splits if s not in available]
    if bad:
        raise DatasetNotAvailable(f"dataset '{dataset_id}' has no official '{bad[0]}' qrels (available: {available})")
    loader = GenericDataLoader(str(path))
    queries: dict[str, dict[str, str]] = {}
    qrels: dict[str, dict[str, dict[str, int]]] = {}
    corpus: dict[str, dict[str, str]] = {}
    for sp in splits:
        loader.queries, loader.qrels = {}, {}  # the loader caches queries after the first call; reset per split
        corpus, queries[sp], qrels[sp] = loader.load(sp)
    with open(path / "corpus.jsonl", "rb") as f:
        lines = sum(1 for _ in f)
    issues: list[str] = []
    for sp in splits:
        issues += [f"[{sp}] {i}" for i in validate_beir(corpus, queries[sp], qrels[sp], lines)]
    if issues:
        raise DatasetInvalid(f"BEIR dataset '{dataset_id}' failed validation: " + "; ".join(issues[:6]), details=issues)
    warnings: list[str] = []
    for sp in splits:
        miss = qrels_missing_documents(corpus, qrels[sp])
        if miss:
            rows = sum(1 for q in qrels[sp].values() for d in q if d in miss)
            warnings.append(f"[{sp}] {len(miss)} judged document ids ({rows} qrels rows) are not in the official corpus "
                            f"(e.g. {sorted(miss)[:2]}); kept as in BEIR — they cannot be retrieved by any method")
    source = {"dataset_id": dataset_id, "url": BEIR_URL.format(id=dataset_id), "path": str(path),
              "corpus_sha256": _sha256(path / "corpus.jsonl"), "loader": "beir.datasets.data_loader.GenericDataLoader",
              "license_note": REGISTRY.get(dataset_id, {}).get("license", "see BEIR dataset table"),
              "official_splits_available": available}
    return BeirData(dataset_id, str(path), corpus, queries, qrels, source, warnings)


def doc_text(doc: dict[str, str]) -> str:
    """BEIR/SBERT convention: title and body joined by a space. Missing title/text are handled safely."""
    title = (doc.get("title") or "").strip()
    text = (doc.get("text") or "").strip()
    return f"{title} {text}".strip()
