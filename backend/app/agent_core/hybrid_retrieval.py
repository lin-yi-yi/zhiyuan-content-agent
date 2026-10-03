"""Small-corpus BM25 and reciprocal-rank fusion, independent of embeddings.

Chinese character bigrams/trigrams need no tokenizer download. Exact Latin terms,
versions and identifiers are kept intact. This is not a learned reranker.
"""
from collections import Counter
import math
import re


RRF_K = 60
BM25_K1 = 1.2
BM25_B = 0.75


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*", text.lower())
    for phrase in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(phrase) == 1:
            tokens.append(phrase)
        else:
            tokens.extend(phrase[start:start + size] for size in (2, 3)
                          for start in range(len(phrase) - size + 1))
    return tokens


def bm25_rank(query: str, documents: list[tuple[int, str]], limit: int) -> list[dict]:
    """Score only the caller's already authorized, current corpus.

    IDF and average length are computed inside that corpus: inaccessible records
    cannot affect ranking or corpus statistics. Query term frequency is ignored.
    """
    query_terms = set(tokenize(query))
    if not query_terms or not documents:
        return []
    token_counts = [(identifier, Counter(tokenize(text)), text) for identifier, text in documents]
    average_length = sum(sum(counts.values()) for _, counts, _ in token_counts) / len(token_counts)
    if not average_length:
        return []
    frequencies = Counter(term for _, counts, _ in token_counts for term in query_terms if term in counts)
    results = []
    for identifier, counts, text in token_counts:
        matched = query_terms.intersection(counts)
        if not matched:
            continue
        normalizer = BM25_K1 * (1 - BM25_B + BM25_B * sum(counts.values()) / average_length)
        score = sum(math.log(1 + (len(documents) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
                    * counts[term] * (BM25_K1 + 1) / (counts[term] + normalizer) for term in sorted(matched))
        overlap = min(1.0, len(matched) / len(query_terms) + (0.25 if query.lower() in text.lower() else 0))
        results.append({"id": identifier, "score": score, "term_overlap": overlap,
                        "matched_term_count": len(matched), "query_term_count": len(query_terms)})
    return sorted(results, key=lambda item: (-item["score"], item["id"]))[:limit]


def reciprocal_rank_fusion(semantic: list[dict], lexical: list[dict], limit: int) -> list[dict]:
    """One-based ranks with k=60; score magnitudes are never added together."""
    fused: dict[int, dict] = {}
    for route, results in (("semantic", semantic), ("bm25", lexical)):
        seen = set()
        rank = 0
        for item in results:
            identifier = item["id"]
            if identifier in seen:
                continue
            seen.add(identifier)
            rank += 1
            entry = fused.setdefault(identifier, {"id": identifier, "rrf_score": 0.0,
                "semantic_rank": None, "semantic_score": None, "bm25_rank": None,
                "bm25_score": None, "term_overlap": 0.0})
            entry["rrf_score"] += 1 / (RRF_K + rank)
            entry[f"{route}_rank"] = rank
            entry[f"{route}_score"] = item["score"]
            if route == "bm25":
                entry["term_overlap"] = item["term_overlap"]
                entry["matched_term_count"] = item["matched_term_count"]
                entry["query_term_count"] = item["query_term_count"]
    return sorted(fused.values(), key=lambda item: (-item["rrf_score"], item["id"]))[:limit]
