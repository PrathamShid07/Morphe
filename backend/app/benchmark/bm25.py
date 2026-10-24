"""Local Okapi BM25 baseline (no Elasticsearch, no Docker).

Implementation details (documented so results are not mistaken for a published Elasticsearch/Lucene run):
* text = BEIR convention `title + " " + text`; tokens = lowercase `\\w+` (Unicode), **no stemming, no stop-word removal**;
* idf = ln(1 + (N - df + 0.5) / (df + 0.5))  (the non-negative Lucene form), k1 = 1.2, b = 0.75;
* repeated query tokens contribute repeatedly (each occurrence is a clause); scores are exact over the whole corpus.
"""
from __future__ import annotations

import time
from typing import Sequence

import numpy as np
import scipy.sparse as sp
from sklearn.feature_extraction.text import CountVectorizer


class BM25Index:
    def __init__(self, doc_ids: Sequence[str], texts: Sequence[str], k1: float = 1.2, b: float = 0.75):
        self.doc_ids = list(doc_ids)
        self.k1, self.b = k1, b
        self.vec = CountVectorizer(lowercase=True, token_pattern=r"(?u)\b\w+\b")
        tf = self.vec.fit_transform(texts).tocsr().astype(np.float64)  # docs x terms
        n = tf.shape[0]
        dl = np.asarray(tf.sum(axis=1)).ravel()
        avgdl = dl.mean() if n else 1.0
        df = np.asarray((tf > 0).sum(axis=0)).ravel()
        self.idf = np.log(1.0 + (n - df + 0.5) / (df + 0.5))
        coo = tf.tocoo()
        denom = coo.data + k1 * (1 - b + b * dl[coo.row] / avgdl)
        w = self.idf[coo.col] * coo.data * (k1 + 1) / denom
        self.weights = sp.csr_matrix((w, (coo.row, coo.col)), shape=tf.shape)

    def scores(self, query: str) -> np.ndarray:
        q = self.vec.transform([query])  # term counts, vocabulary terms only
        return np.asarray(self.weights @ q.T.toarray().ravel()).ravel() if q.nnz else np.zeros(len(self.doc_ids))

    def search(self, queries: dict[str, str], top_k: int = 100) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
        """Returns ({qid: {doc_id: score}}, {qid: latency_ms}). Latency covers tokenisation + scoring + top-k selection."""
        results: dict[str, dict[str, float]] = {}
        times: dict[str, float] = {}
        for qid, text in queries.items():
            t0 = time.perf_counter()
            s = self.scores(text)
            k = min(top_k, len(s))
            idx = np.argpartition(-s, k - 1)[:k] if k < len(s) else np.arange(len(s))
            idx = idx[np.lexsort((np.array([self.doc_ids[i] for i in idx]), -s[idx]))]  # score desc, id asc for determinism
            results[qid] = {self.doc_ids[i]: float(s[i]) for i in idx}
            times[qid] = (time.perf_counter() - t0) * 1000
        return results, times
