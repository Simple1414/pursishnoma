"""Open-ended response analysis (Tajik / Russian / English).

All outputs are descriptive and traceable: every term, theme and tendency
lists the response IDs it came from. Tendencies are lexicon heuristics and
are labelled as such — they are not scientific facts.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Iterable

import numpy as np

WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)

STOPWORDS = set(
    """
    ва дар ба аз бо барои то ки ин он чӣ чи як ҳам низ мо шумо ман вай онҳо мебошад аст буд шуд мешавад
    карда кардан кунад кунанд мекунад мекунанд метавонад метавонанд бояд хеле бисёр баъзе ҳар ягон бештар
    танҳо агар зеро чунки лекин вале ё на не ҳа худ худро онро инро дорад доранд ҳастанд ҳаст нест шавад
    шаванд гардад гардида дода додан дар бораи оид тавр гуна чунин боз ҳамчун сабаби ҳангоми пас пеш
    мумкин лозим хуб зиёд кам тоҷикӣ ман маро ба ман мо моро
    и в во на с со по к ко у о об от до за из для что это как так же бы ли не нет да но а или его ее их
    мы вы они она он оно я ты мне меня нам вас вам при без над под про тоже очень уже еще ещё все всё
    был была были быть есть может могут нужно надо чтобы если когда где который которая которые этот эта
    the and for with that this are was were has have had not but you your our their they them from into
    about there what which when where who how can could should would will just very more most some any all
    """.split()
)

POSITIVE = (
    "хуб", "муфид", "самаранок", "осон", "фоида", "ёрӣ", "кӯмак", "кумак", "дуруст", "беҳтар", "зуд", "мусбат",
    "шавқ", "қулай", "равшан", "аъло", "полезн", "хорош", "удоб", "помога", "помощ", "эффектив",
    "быстр", "понятн", "интерес", "useful", "helpful", "good", "great", "easy", "effective", "clear", "fast",
    "benefit", "better",
)
NEGATIVE = (
    "бад", "нодуруст", "хато", "мушкил", "душвор", "суст", "нопурра", "хатар", "зарар", "гумроҳ", "маҳдуд",
    "набуд", "нест", "манфӣ", "плохо", "плох", "ошибк", "неправ", "проблем", "трудн", "сложн", "медлен",
    "вред", "опас", "огранич", "bad", "wrong", "error", "problem", "difficult", "slow", "harm", "risk",
    "limited", "incorrect", "misleading",
)
PROBLEM_MARKERS = (
    "мушкил", "хато", "нодуруст", "интернет", "суст", "душвор", "забон", "пулакӣ", "маҳдуд", "гумроҳ",
    "проблем", "ошибк", "интернет", "язык", "платн", "медлен", "problem", "error", "internet", "language",
    "paid", "slow", "wrong",
)


def tokenize(text: str) -> list[str]:
    return [w for w in WORD_RE.findall(text.lower()) if w not in STOPWORDS]


def top_terms(docs: list[tuple[int, str]], limit: int = 25) -> list[dict]:
    counts: Counter = Counter()
    where: dict[str, set[int]] = defaultdict(set)
    for rid, text in docs:
        tokens = tokenize(text)
        counts.update(tokens)
        for t in set(tokens):
            where[t].add(rid)
    return [
        {"term": term, "count": c, "responses": sorted(where[term]), "doc_count": len(where[term])}
        for term, c in counts.most_common(limit)
        if c > 1 or len(docs) < 5
    ]


def top_bigrams(docs: list[tuple[int, str]], limit: int = 15) -> list[dict]:
    counts: Counter = Counter()
    where: dict[str, set[int]] = defaultdict(set)
    for rid, text in docs:
        tokens = tokenize(text)
        grams = [f"{a} {b}" for a, b in zip(tokens, tokens[1:])]
        counts.update(grams)
        for g in set(grams):
            where[g].add(rid)
    return [
        {"term": g, "count": c, "responses": sorted(where[g])}
        for g, c in counts.most_common(limit)
        if c > 1
    ]


def _tfidf(docs_tokens: list[list[str]]) -> tuple[np.ndarray, list[str]]:
    vocab = sorted({t for toks in docs_tokens for t in toks})
    index = {t: i for i, t in enumerate(vocab)}
    n = len(docs_tokens)
    df = Counter(t for toks in docs_tokens for t in set(toks))
    mat = np.zeros((n, len(vocab)), dtype=float)
    for row, toks in enumerate(docs_tokens):
        tf = Counter(toks)
        for t, c in tf.items():
            mat[row, index[t]] = (1 + math.log(c)) * (math.log((1 + n) / (1 + df[t])) + 1)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return mat / norms, vocab


def similar_groups(docs: list[tuple[int, str]], threshold: float = 0.3, max_docs: int = 2000) -> list[dict]:
    """Greedy grouping of similar answers by TF-IDF cosine similarity."""
    docs = [(rid, t) for rid, t in docs if tokenize(t)][:max_docs]
    if len(docs) < 2:
        return []
    tokens = [tokenize(t) for _rid, t in docs]
    mat, vocab = _tfidf(tokens)
    sim = mat @ mat.T
    unassigned = set(range(len(docs)))
    groups = []
    # Seed with the documents that have the most similar neighbours
    order = sorted(range(len(docs)), key=lambda i: -(sim[i] >= threshold).sum())
    for seed in order:
        if seed not in unassigned:
            continue
        members = [j for j in unassigned if sim[seed, j] >= threshold]
        if len(members) < 2:
            continue
        unassigned -= set(members)
        centroid = mat[members].mean(axis=0)
        top_idx = np.argsort(centroid)[::-1][:4]
        groups.append(
            {
                "label": ", ".join(vocab[i] for i in top_idx if centroid[i] > 0),
                "size": len(members),
                "items": [{"response_id": docs[j][0], "text": docs[j][1]} for j in members],
            }
        )
    groups.sort(key=lambda g: -g["size"])
    return groups


def _matches(token: str, stems: Iterable[str]) -> bool:
    return any(token.startswith(s) for s in stems)


def tendencies(docs: list[tuple[int, str]]) -> dict:
    """Lexicon-based positive/negative tendency (heuristic, clearly labelled)."""
    labelled = {"positive": [], "negative": [], "mixed": [], "neutral": []}
    for rid, text in docs:
        toks = WORD_RE.findall(text.lower())
        pos = sum(_matches(t, POSITIVE) for t in toks)
        neg = sum(_matches(t, NEGATIVE) for t in toks)
        if pos and neg:
            key = "mixed"
        elif pos:
            key = "positive"
        elif neg:
            key = "negative"
        else:
            key = "neutral"
        labelled[key].append({"response_id": rid, "text": text, "pos": pos, "neg": neg})
    return {"counts": {k: len(v) for k, v in labelled.items()}, "items": labelled}


def problem_mentions(docs: list[tuple[int, str]]) -> list[dict]:
    out = []
    for rid, text in docs:
        toks = WORD_RE.findall(text.lower())
        hits = sorted({t for t in toks if _matches(t, PROBLEM_MARKERS)})
        if hits:
            out.append({"response_id": rid, "text": text, "markers": hits})
    return out


def analyse(docs: list[tuple[int, str]]) -> dict:
    lengths = [len(t.split()) for _rid, t in docs]
    return {
        "n": len(docs),
        "mean_words": round(float(np.mean(lengths)), 1) if lengths else None,
        "terms": top_terms(docs),
        "bigrams": top_bigrams(docs),
        "groups": similar_groups(docs),
        "tendencies": tendencies(docs),
        "problems": problem_mentions(docs),
    }
