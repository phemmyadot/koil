"""Philosophy-document ingest for the daily review chatbot: parse -> chunk -> embed -> store.
See docs/superpowers/specs/2026-08-04-daily-trade-review-chatbot-design.md, Part 1.

Embeddings are local (sentence-transformers), not a Claude API call -- the model is a one-time
per-process load, reused for every embed_texts() call, same "load once" discipline as
data.py's fetch executor or app.py's compute executor.
"""
import re

import numpy as np
from sentence_transformers import SentenceTransformer

import backend.db as db

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def extract_text(file_path: str, file_type: str) -> str:
    """file_type: 'pdf' | 'docx' | 'md' | 'txt'."""
    if file_type == "pdf":
        import pypdf
        reader = pypdf.PdfReader(file_path)
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    if file_type == "docx":
        import docx
        d = docx.Document(file_path)
        return "\n\n".join(p.text for p in d.paragraphs if p.text.strip())
    if file_type in ("md", "txt"):
        with open(file_path, encoding="utf-8") as f:
            return f.read()
    raise ValueError(f"unsupported file_type: {file_type!r}")


def chunk_text(text: str, target_tokens: int = 650, overlap_tokens: int = 75) -> list[str]:
    """Paragraph-aware splitting with overlap -- ~4 chars/token as a rough estimate, adequate for
    a personal philosophy document (not a large corpus needing real tokenizer-aware chunking).
    Paragraphs longer than target_tokens on their own are kept whole rather than split mid-
    sentence -- a slightly-over-target chunk beats a sentence cut in half."""
    target_chars = target_tokens * 4
    overlap_chars = overlap_tokens * 4
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) > target_chars and current:
            chunks.append(current)
            tail = current[-overlap_chars:] if overlap_chars else ""
            current = f"{tail}\n\n{para}" if tail else para
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def embed_texts(texts: list[str]) -> list[np.ndarray]:
    if not texts:
        return []
    vectors = _get_model().encode(texts, batch_size=32, convert_to_numpy=True)
    return [v.astype(np.float32) for v in vectors]


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def top_k_chunks(user_id: int, query_text: str, k: int = 5) -> list[dict]:
    """Embeds query_text once, scores every stored chunk (original upload + enrichment, no
    distinction -- see design doc Part 1/3) by cosine similarity, returns the top K full row
    dicts. Brute-force is fine at this scale (a handful of document chunks, one review per day)
    -- no vector DB needed."""
    query_vec = embed_texts([query_text])[0]
    rows = db.get_document_chunks(user_id)
    if not rows:
        return []
    scored = [
        (cosine_similarity(query_vec, np.frombuffer(r["embedding"], dtype=np.float32)), r)
        for r in rows
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:k]]


def ingest_document(user_id: int, file_path: str, filename: str, file_type: str, now_iso: str) -> int:
    """Extract -> chunk -> embed -> store. Returns the new document_id. Marks the document row
    'failed' (not raised past the caller silently) if any step throws, so a bad upload shows up
    as a clear status rather than a 500 with no trace."""
    document_id = db.insert_user_document(user_id, filename, file_path, file_type, now_iso)
    try:
        text = extract_text(file_path, file_type)
        chunks = chunk_text(text)
        if not chunks:
            raise ValueError("no extractable text in document")
        embeddings = embed_texts(chunks)
        for i, (chunk, vec) in enumerate(zip(chunks, embeddings)):
            db.insert_document_chunk(
                user_id, document_id, "upload", None, i, chunk, vec.tobytes(), now_iso,
            )
        db.update_user_document_status(document_id, "ready")
    except Exception:
        db.update_user_document_status(document_id, "failed")
        raise
    return document_id
