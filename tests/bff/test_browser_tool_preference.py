from app.prompt.manus import NEXT_STEP_PROMPT
from app.tool.python_execute import PythonExecute


def test_manus_prompt_prefers_playwright_for_browser_tasks():
    assert "always prefer Playwright MCP tools" in NEXT_STEP_PROMPT
    assert "Do NOT use `python_execute` to launch browsers" in NEXT_STEP_PROMPT


def test_python_execute_description_discourages_browser_automation():
    description = PythonExecute().description
    assert "Not for browser automation or opening websites" in description
    assert "use Playwright MCP browser tools" in description
