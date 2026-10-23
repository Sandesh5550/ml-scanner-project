from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

KNOWLEDGE_BASE_DIR = Path(__file__).resolve().parents[1] / "knowledge_base"


class KnowledgeRetriever:
    """Lightweight, deterministic local RAG retriever using TF-IDF and token similarity."""

    def __init__(self, kb_dir: Optional[Path] = None):
        self.kb_dir = kb_dir or KNOWLEDGE_BASE_DIR
        self.documents: List[Dict[str, Any]] = []
        self.vectorizer: Optional[TfidfVectorizer] = None
        self.tfidf_matrix: Optional[np.ndarray] = None
        self._load_knowledge_base()

    def _load_knowledge_base(self) -> None:
        """Load and index all JSON knowledge chunks in the knowledge_base directory."""
        self.documents = []
        if not self.kb_dir.exists():
            logger.warning("Knowledge base directory not found at: %s", self.kb_dir)
            return

        json_files = sorted(self.kb_dir.glob("*.json"))
        for fpath in json_files:
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    items = json.load(f)
                    if isinstance(items, list):
                        for item in items:
                            if isinstance(item, dict) and "text" in item:
                                # Ensure normalized fields
                                doc = {
                                    "id": str(item.get("id", f"{fpath.stem}_{len(self.documents)}")),
                                    "topic": str(item.get("topic", "")),
                                    "text": str(item.get("text", "")),
                                    "source": str(item.get("source", "Unknown Source")),
                                    "source_type": str(item.get("source_type", "guideline")),
                                    "version_or_date": str(item.get("version_or_date", "")),
                                }
                                self.documents.append(doc)
            except Exception as exc:
                logger.error("Failed to load knowledge file %s: %s", fpath, exc)

        if self.documents:
            corpus = [f"{d['topic']} {d['text']}" for d in self.documents]
            self.vectorizer = TfidfVectorizer(
                stop_words="english",
                lowercase=True,
                ngram_range=(1, 2),
            )
            self.tfidf_matrix = self.vectorizer.fit_transform(corpus)
            logger.info("Indexed %d knowledge base chunks from %s", len(self.documents), self.kb_dir)

    def retrieve(self, query: str, top_k: int = 3, min_score: float = 0.20) -> List[Dict[str, Any]]:
        """Retrieve the top_k most relevant knowledge items for a user query."""
        q = (query or "").strip()
        if not q or not self.documents or self.vectorizer is None or self.tfidf_matrix is None:
            return []

        # 1. Cosine similarity via TF-IDF
        query_vec = self.vectorizer.transform([q])
        cos_scores = cosine_similarity(query_vec, self.tfidf_matrix).flatten()

        # 2. Token / keyword similarity via RapidFuzz for query-topic matching
        q_lower = q.lower()
        q_words = set(q_lower.split())
        results: List[Dict[str, Any]] = []

        for idx, doc in enumerate(self.documents):
            tfidf_score = float(cos_scores[idx])
            topic_str = doc["topic"].lower().replace("_", " ")
            doc_words = set((topic_str + " " + doc["text"]).lower().split())

            # Skip documents with zero vocabulary and zero token overlap
            if tfidf_score == 0.0 and not bool(q_words & doc_words):
                continue

            topic_sim = fuzz.partial_ratio(q_lower, topic_str) / 100.0
            topic_token_sim = fuzz.token_sort_ratio(q_lower, topic_str) / 100.0
            text_sim = fuzz.token_set_ratio(q_lower, doc["text"].lower()) / 100.0

            # Guard against spurious matches for out-of-domain queries with weak vocabulary overlap
            if tfidf_score < 0.15 and topic_token_sim < 0.40 and text_sim < 0.35:
                continue

            # Combined hybrid score (70% TF-IDF, 20% topic match, 10% text token set match)
            hybrid_score = (0.70 * tfidf_score) + (0.20 * topic_sim) + (0.10 * text_sim)

            # Extra boost if key terms match directly
            for term in ["sugar", "protein", "fibre", "fiber", "fat", "salt", "sodium", "calorie", "fssai", "claim", "score"]:
                if term in q_lower and term in doc["topic"].lower():
                    hybrid_score += 0.10

            if hybrid_score >= min_score:
                doc_copy = dict(doc)
                doc_copy["similarity"] = round(hybrid_score, 3)
                results.append(doc_copy)

        # Sort descending by hybrid similarity score with deterministic tie-breaking by id
        results.sort(key=lambda x: (x["similarity"], x["id"]), reverse=True)

        # Deduplicate results by document id or topic while preserving ranking order
        deduped: List[Dict[str, Any]] = []
        seen_ids = set()
        seen_topics = set()
        for doc_item in results:
            doc_id = doc_item["id"]
            topic = doc_item.get("topic", "")
            if doc_id in seen_ids or (topic and topic in seen_topics):
                continue
            seen_ids.add(doc_id)
            if topic:
                seen_topics.add(topic)
            deduped.append(doc_item)

        return deduped[: int(top_k)]


# Global retriever singleton
_default_retriever: Optional[KnowledgeRetriever] = None


def get_knowledge_retriever() -> KnowledgeRetriever:
    global _default_retriever
    if _default_retriever is None:
        _default_retriever = KnowledgeRetriever()
    return _default_retriever


def retrieve_relevant_knowledge(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """Convenience helper to retrieve relevant chunks."""
    retriever = get_knowledge_retriever()
    return retriever.retrieve(query, top_k=top_k)
