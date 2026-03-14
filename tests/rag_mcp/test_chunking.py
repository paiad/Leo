from app.rag_mcp.chunking import chunk_text_by_tokens


def test_chunk_text_by_tokens_has_overlap():
    text = " ".join(f"token{i}" for i in range(120))
    chunks = chunk_text_by_tokens(text=text, chunk_size=40, overlap=10)

    assert len(chunks) >= 3
    assert chunks[0].token_count <= 40
    assert chunks[1].token_count <= 40
    assert chunks[0].index == 0
    assert chunks[1].index == 1


def test_chunk_text_by_tokens_empty_text():
    assert chunk_text_by_tokens(text="", chunk_size=100, overlap=10) == []
