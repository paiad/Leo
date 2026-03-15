from types import SimpleNamespace

import pytest

from bff.services.runtime.runtime_finalizer import RuntimeFinalizer


class _DummyLLM:
    def __init__(self, *, responses: list[str] | None = None, error: Exception | None = None):
        self._responses = list(responses or [])
        self._error = error
        self.calls: list[dict] = []

    async def ask(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        if self._responses:
            return self._responses.pop(0)
        return ""


def _message(role: str, content: str, *, tool_calls=None, name: str | None = None):
    return SimpleNamespace(
        role=SimpleNamespace(value=role),
        content=content,
        tool_calls=tool_calls,
        name=name,
    )


@pytest.mark.asyncio
async def test_finalize_prefers_complete_candidate_without_llm_call():
    finalizer = RuntimeFinalizer(lambda prompt: prompt)
    llm = _DummyLLM(responses=["should-not-be-used"])
    agent = SimpleNamespace(llm=llm, state=SimpleNamespace(value="FINISHED"), messages=[])

    result = await finalizer.finalize_response(
        agent=agent,
        user_prompt="今天开封天气咋样？",
        run_result="Step 1: done",
        candidate_final_text="开封今天局部多云，3°C~13°C。",
        messages=[],
    )

    assert result == "开封今天局部多云，3°C~13°C。"
    assert llm.calls == []


@pytest.mark.asyncio
async def test_finalize_repairs_incomplete_candidate():
    finalizer = RuntimeFinalizer(lambda prompt: prompt)
    llm = _DummyLLM(responses=["开封今天局部多云，当前7°C，预计3°C~13°C。"])
    agent = SimpleNamespace(llm=llm, state=SimpleNamespace(value="FINISHED"), messages=[])
    messages = [
        _message(
            "tool",
            "Observed output of cmd `python_execute` executed:\n开封今日温度 3~13，局部多云",
            name="python_execute",
        )
    ]

    result = await finalizer.finalize_response(
        agent=agent,
        user_prompt="今天开封天气咋样？",
        run_result="Step 1: weather queried",
        candidate_final_text="开封今日天气：",
        messages=messages,
    )

    assert result == "开封今天局部多云，当前7°C，预计3°C~13°C。"
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_finalize_falls_back_to_deterministic_when_repair_fails():
    finalizer = RuntimeFinalizer(lambda prompt: prompt)
    llm = _DummyLLM(error=RuntimeError("repair failed"))
    agent = SimpleNamespace(llm=llm, state=SimpleNamespace(value="FINISHED"), messages=[])
    messages = [
        _message(
            "tool",
            "Observed output of cmd `python_execute` executed:\n开封今日温度 3~13，局部多云",
            name="python_execute",
        )
    ]

    result = await finalizer.finalize_response(
        agent=agent,
        user_prompt="今天开封天气咋样？",
        run_result="Step 1: weather queried",
        candidate_final_text="开封今日天气：",
        messages=messages,
    )

    assert "任务已结束，但最终文本不完整" in result
    assert "python_execute" in result
    assert "候选回复（可能截断）" in result
