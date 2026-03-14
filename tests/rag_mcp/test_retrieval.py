from app.rag_mcp.service import _normalize_query_for_retrieval, _rrf, _tokenize_for_bm25


def test_rrf_merges_rank_sources():
    merged = _rrf(
        {
            "vector": {"a": 1, "b": 2},
            "bm25": {"b": 1, "c": 2},
        }
    )
    assert merged["b"] > merged["a"]
    assert merged["b"] > merged["c"]


def test_bm25_tokenizer_supports_cjk_bigrams():
    tokens = _tokenize_for_bm25("津液的意思")
    assert "津液" in tokens


def test_query_normalization_for_definition_style_query():
    assert _normalize_query_for_retrieval("津液的意思") == "津液"
    assert _normalize_query_for_retrieval("什么是津液？") == "津液"
