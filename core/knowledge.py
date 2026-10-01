"""Search over the curated, versioned knowledge base (public product information only - no customer data, OWASP LLM08).

Okapi BM25 over paragraphs, with a few Shona/Ndebele synonyms mapped to English terms. Small, dependency-free and
deterministic, which keeps evals reproducible.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ARTICLES_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "articles"

STOPWORDS = set("a an and are as at be by can do does for from how i if in is it my of on or our the to we what when "
                "will with you your me am".split())
SYNONYMS = {
    "kubhadhara": "pay", "bhadhara": "pay", "ukubhadala": "pay", "premium": "premium", "mari": "money",
    "chikwereti": "loan", "isikweleti": "loan", "rufu": "funeral", "kufa": "death", "ukufa": "death",
    "motokari": "motor", "imota": "motor", "tsaona": "accident", "ingozi": "accident", "lapse": "lapsed",
    "reinstate": "reinstate", "reinstatement": "reinstate", "settle": "settlement", "complain": "complaint",
}


@dataclass(frozen=True)
class Passage:
    title: str
    text: str
    slug: str


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    out = []
    for w in words:
        w = SYNONYMS.get(w, w)
        if w in STOPWORDS:
            continue
        if len(w) > 4 and w.endswith("s"):
            w = w[:-1]
        out.append(w)
    return out


class KnowledgeBase:
    def __init__(self, directory: Path = ARTICLES_DIR, k1: float = 1.4, b: float = 0.75):
        self.passages: list[Passage] = []
        for path in sorted(directory.glob("*.md")):
            lines = path.read_text(encoding="utf-8").splitlines()
            title = lines[0].lstrip("# ").strip()
            body = "\n".join(lines[1:])
            for para in [p.strip() for p in body.split("\n\n") if p.strip()]:
                self.passages.append(Passage(title, para, path.stem))
        self._docs = [tokenize(p.title + " " + p.text) for p in self.passages]
        self._titles = [set(tokenize(p.title)) for p in self.passages]
        self._tf = [Counter(d) for d in self._docs]
        n = len(self._docs)
        df = Counter(t for d in self._docs for t in set(d))
        self._idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self._avgdl = sum(len(d) for d in self._docs) / max(n, 1)
        self.k1, self.b = k1, b

    def search(self, query: str, top_k: int = 3, min_score: float = 1.5) -> list[tuple[Passage, float]]:
        terms = tokenize(query)
        scored = []
        for i, tf in enumerate(self._tf):
            dl = len(self._docs[i])
            s = 0.0
            for t in terms:
                if t not in tf:
                    continue
                f = tf[t]
                s += self._idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self._avgdl))
                if t in self._titles[i]:
                    s += self._idf[t]  # a query word in the article title is a strong topical signal
            if s >= min_score:
                scored.append((self.passages[i], round(s, 3)))
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]
