import os

from bff.services.runtime.agent_runtime import ManusRuntime, _RuntimeStallDetector


def test_resolve_max_steps_defaults_to_10_when_dynamic_disabled(monkeypatch):
    monkeypatch.setenv("BFF_RUNTIME_DYNAMIC_STEPS_ENABLED", "0")
    monkeypatch.delenv("BFF_MANUS_MAX_STEPS", raising=False)

    runtime = ManusRuntime(store=None)
    steps = runtime._resolve_max_steps("hello", None)

    assert steps == 10


def test_resolve_max_steps_dynamic_increases_for_complex_prompt(monkeypatch):
    monkeypatch.setenv("BFF_RUNTIME_DYNAMIC_STEPS_ENABLED", "1")
    monkeypatch.setenv("BFF_MANUS_MAX_STEPS", "10")
    monkeypatch.setenv("BFF_RUNTIME_DYNAMIC_STEPS_MAX", "24")
    monkeypatch.setenv("BFF_RUNTIME_DYNAMIC_STEPS_MIN", "4")

    runtime = ManusRuntime(store=None)
    prompt = (
        "Please design and implement a multi-step migration and refactor plan.\n"
        "First analyze dependency graph, then update interfaces, next add tests,\n"
        "after that run validation and summarize risks.\n"
    ) * 8

    steps = runtime._resolve_max_steps(prompt, None)
    assert steps > 10


def test_resolve_max_steps_dynamic_can_reduce_simple_prompt(monkeypatch):
    monkeypatch.setenv("BFF_RUNTIME_DYNAMIC_STEPS_ENABLED", "1")
    monkeypatch.setenv("BFF_MANUS_MAX_STEPS", "10")
    monkeypatch.setenv("BFF_RUNTIME_DYNAMIC_STEPS_MAX", "20")
    monkeypatch.setenv("BFF_RUNTIME_DYNAMIC_STEPS_MIN", "4")

    runtime = ManusRuntime(store=None)
    steps = runtime._resolve_max_steps("hi", None)

    assert 4 <= steps < 10


def test_stall_detector_detects_same_tool_loop():
    detector = _RuntimeStallDetector(same_tool_limit=3, tool_error_limit=3)
    event = {"type": "tool_start", "toolName": "python_execute", "arguments": '{"x":1}'}

    assert detector.observe(event) is None
    assert detector.observe(event) is None
    reason = detector.observe(event)

    assert reason is not None
    assert "same_tool_loop" in reason


def test_stall_detector_detects_consecutive_tool_errors():
    detector = _RuntimeStallDetector(same_tool_limit=3, tool_error_limit=3)
    error_event = {"type": "tool_done", "ok": False}

    assert detector.observe(error_event) is None
    assert detector.observe(error_event) is None
    reason = detector.observe(error_event)

    assert reason is not None
    assert "consecutive_tool_errors" in reason
