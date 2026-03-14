from __future__ import annotations

from dataclasses import dataclass

import tiktoken


@dataclass(slots=True)
class TextChunk:
    index: int
    text: str
    token_count: int


def _encode_tokens(text: str) -> list[int]:
    encoding = tiktoken.get_encoding("cl100k_base")
    return encoding.encode(text)


def chunk_text_by_tokens(text: str, chunk_size: int, overlap: int) -> list[TextChunk]:
    normalized = (text or "").strip()
    if not normalized:
        return []
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_overlap must satisfy 0 <= overlap < chunk_size")

    encoding = tiktoken.get_encoding("cl100k_base")
    token_ids = _encode_tokens(normalized)
    if not token_ids:
        return []

    step = chunk_size - overlap
    chunks: list[TextChunk] = []
    idx = 0
    for start in range(0, len(token_ids), step):
        end = min(start + chunk_size, len(token_ids))
        sub_tokens = token_ids[start:end]
        if not sub_tokens:
            continue
        chunk_text = encoding.decode(sub_tokens).strip()
        if not chunk_text:
            continue
        chunks.append(TextChunk(index=idx, text=chunk_text, token_count=len(sub_tokens)))
        idx += 1
        if end >= len(token_ids):
            break
    return chunks
