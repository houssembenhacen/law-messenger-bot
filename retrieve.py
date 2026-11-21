#!/usr/bin/env python3
"""Lecture-only retrieval: BM25-style ranking over indexed chunks.

No external API, no web search. If nothing in the lectures matches,
the caller must say so instead of inventing an answer.
"""
import json
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
INDEX = BASE / "data" / "index.json"

STOP_FR = {
    "le", "la", "les", "de", "des", "du", "un", "une", "et", "ou", "est",
    "dans", "sur", "pour", "par", "au", "aux", "ce", "cet", "cette", "ces",
    "il", "elle", "ils", "elles", "qui", "que", "quoi", "comment", "est-ce",
    "avec", "sans", "plus", "moins", "être", "avoir", "faire", "peut",
}
STOP_EN = {
    "the", "a", "an", "and", "or", "is", "are", "in", "on", "for", "of",
    "to", "what", "how", "why", "when", "with", "does", "do", "is",
}


STOP_AR = {
    "ما", "هو", "هي", "من", "في", "على", "إلى", "أن", "إن", "كان", "كانت",
    "هذا", "هذه", "ذلك", "التي", "الذي", "الذين", "عن", "مع", "بعد", "قبل",
    "بين", "عند", "لدى", "كما", "لكن", "أو", "و", "ثم", "قد", "لقد", "لا",
    "لم", "لن", "هل", "كيف", "ماذا", "أين", "متى", "يكون", "تكون", "يتم",
}


_AR_NORM = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ؤ": "و", "ئ": "ي", "ة": "ه", "ى": "ي",
    "ـ": "",  # tatweel
})


def _normalize(word: str) -> str:
    # Unify Arabic spelling variants: hamza forms (أ/إ/آ), ؤ/ئ, ة/ه, ى/ي,
    # and strip diacritics. Some PDFs encode words with an extra alef
    # ("الأحداث" appears as "األحداث", "الإداري" as "اإلداري"), so the
    # alef itself is dropped entirely: "الأحداث" and "األحداث" both
    # become "لحدث" and match. This loses alef-only distinctions, which
    # is a fair trade for a retrieval index over messy PDF text.
    # NFKC first: some PDFs store Arabic in presentation forms
    # (e.g. ﻗﺎﻧﻭﻥ instead of قانون), which are different codepoints
    # and would otherwise never match a normally typed question.
    word = unicodedata.normalize("NFKC", word)
    word = word.translate(_AR_NORM)
    word = re.sub(r"[\u064b-\u0652]", "", word)
    word = word.replace("ا", "").replace("ء", "")
    return word


def tokens(text: str):
    # Letter sequences only (Unicode-aware): this splits off Arabic
    # punctuation such as ؟ and ،, which otherwise glues onto words
    # ("التجريم؟" would never match "التجريم").
    words = re.findall(r"[^\W\d_]+", text.lower(), flags=re.UNICODE)
    return [
        _normalize(w) for w in words
        if w not in STOP_FR and w not in STOP_EN and w not in STOP_AR and len(w) > 2
    ]


class Index:
    def __init__(self, path=INDEX):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.chunks = data["chunks"]
        self.docs = [tokens(c["text"]) for c in self.chunks]
        self.df = Counter()
        for d in self.docs:
            self.df.update(set(d))
        self.n = max(len(self.docs), 1)
        self.avg_len = sum(len(d) for d in self.docs) / self.n if self.docs else 0

    def search(self, query: str, top_k: int = 3):
        q = tokens(query)
        if not q or not self.docs:
            return []
        scores = []
        for idx, doc in enumerate(self.docs):
            tf = Counter(doc)
            matched = set(q) & set(doc)
            # A multi-word question must match at least two of its terms:
            # otherwise one rare word in a citation (e.g. "France" in a
            # bibliography) would pass as an answer to an off-topic question.
            if len(set(q)) >= 2 and len(matched) < 2:
                continue
            score = 0.0
            for term in matched:
                idf = math.log(1 + (self.n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                denom = tf[term] + 1.2 * (1 - 0.75 + 0.75 * len(doc) / max(self.avg_len, 1))
                score += idf * (tf[term] * 2.2) / denom
            # Small bonus when several query terms appear in one chunk.
            score *= 1 + 0.15 * len(matched)
            scores.append((score, idx))
        scores.sort(reverse=True)
        # A chunk is only usable if it shares at least one real term.
        return [
            (score, self.chunks[idx])
            for score, idx in scores[:top_k]
            if score > 0.5
        ]


def load_index():
    if not INDEX.exists():
        return None
    return Index(INDEX)
