"""Tiny synthetic dataset in the official BEIR on-disk format (corpus.jsonl, queries.jsonl, qrels/<split>.tsv)."""
from __future__ import annotations

import json
import random
from pathlib import Path

TOPICS = {
    "astronomy": "telescope galaxy planet orbit nebula comet star cosmos",
    "cooking": "recipe simmer saute oven flour butter spice garlic",
    "finance": "interest mortgage equity dividend bond inflation credit audit",
    "genetics": "genome allele protein mutation chromosome sequencing enzyme dna",
    "football": "striker goalkeeper penalty league referee midfield tackle stadium",
    "geology": "basalt tectonic magma erosion sediment quartz volcano fault",
    "music": "chord melody rhythm tempo guitar harmony octave symphony",
    "sailing": "keel rudder mainsail harbor tide anchor knot regatta",
}
FILLER = "the of and a in to is that for with as on by this are from have which also can".split()


def write_beir(root: Path, name: str = "synth", docs_per_topic: int = 36, splits: tuple[str, ...] = ("train", "test"),
               dup_query_text: bool = False, missing_title_every: int = 9) -> Path:
    rng = random.Random(7)
    d = root / name
    (d / "qrels").mkdir(parents=True, exist_ok=True)
    corpus, topic_docs = {}, {}
    for t, words in TOPICS.items():
        kws = words.split()
        topic_docs[t] = []
        for i in range(docs_per_topic):
            did = f"{t[:3]}-{i:03d}"
            body = " ".join(rng.choice(kws + FILLER) for _ in range(40)) + " " + " ".join(rng.sample(kws, 3)) + f" sig{t[:3]}{i:03d}"
            title = "" if i % missing_title_every == 0 else f"{t} study {i}"
            corpus[did] = {"title": title, "text": body}
            topic_docs[t].append(did)
    with open(d / "corpus.jsonl", "w") as f:
        for did, v in corpus.items():
            f.write(json.dumps({"_id": did, "title": v["title"], "text": v["text"], "metadata": {}}) + "\n")
    queries, qrels = {}, {sp: {} for sp in splits}
    n = 0
    for sp in splits:
        per_topic = {"train": 10, "dev": 6, "test": 6}[sp]
        for t, words in TOPICS.items():
            kws = words.split()
            for j in range(per_topic):
                qid = f"{sp[:2]}{n:04d}"
                n += 1
                rel = rng.sample(topic_docs[t], 3)
                sig = lambda d: "sig" + d[:3] + d[4:]  # noqa: E731  (the judged documents' unique signature tokens)
                queries[qid] = " ".join(rng.sample(kws, 2)) + f" {sig(rel[0])} {sig(rel[1])}" + (" shared duplicate probe" if (dup_query_text and sp == "train" and j == 0) else "")
                qrels[sp][qid] = {rel[0]: 2, rel[1]: 1, rel[2]: 1} if sp != "train" else {rel[0]: 1, rel[1]: 1}
    if dup_query_text and "test" in splits:  # a test query whose text equals a train query
        first_train = next(q for q in queries if q.startswith("tr"))
        first_test = next(q for q in queries if q.startswith("te"))
        queries[first_test] = queries[first_train]
    with open(d / "queries.jsonl", "w") as f:
        for qid, text in queries.items():
            f.write(json.dumps({"_id": qid, "text": text, "metadata": {}}) + "\n")
    for sp in splits:
        with open(d / "qrels" / f"{sp}.tsv", "w") as f:
            f.write("query-id\tcorpus-id\tscore\n")
            for qid, rels in qrels[sp].items():
                for did, g in rels.items():
                    f.write(f"{qid}\t{did}\t{g}\n")
    return d
