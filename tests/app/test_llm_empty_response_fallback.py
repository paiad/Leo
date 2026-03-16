from types import SimpleNamespace

import pytest

from app.llm import LLM


class _FakeCompletions:
    def __init__(self, response):
        self._response = response

    async def create(self, **kwargs):  # noqa: ANN003
        return self._response


class _FakeChat:
    def __init__(self, response):
        self.completions = _FakeCompletions(response)


class _FakeClient:
    def __init__(self, response):
        self.chat = _FakeChat(response)


def _build_llm_with_response(response):
    llm = object.__new__(LLM)
    llm.model = "gpt-4o-mini"
    llm.max_tokens = 128
    llm.temperature = 0
    llm.client = _FakeClient(response)
    llm.total_input_tokens = 0
    llm.total_completion_tokens = 0
    llm.max_input_tokens = None
    llm.count_message_tokens = lambda _messages: 1  # type: ignore[assignment]
    llm.check_token_limit = lambda _tokens: True  # type: ignore[assignment]
    llm.update_token_count = lambda _in, _out=0: None  # type: ignore[assignment]
    return llm


@pytest.mark.asyncio
async def test_ask_returns_empty_string_when_nonstream_content_missing():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0),
    )
    llm = _build_llm_with_response(response)

    result = await llm.ask(
        messages=[{"role": "user", "content": "hello"}],
        stream=False,
    )

    assert result == ""


@pytest.mark.asyncio
async def test_ask_returns_empty_string_when_nonstream_choices_missing():
    response = SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0),
    )
    llm = _build_llm_with_response(response)

    result = await llm.ask(
        messages=[{"role": "user", "content": "hello"}],
        stream=False,
    )

    assert result == ""
