import pytest

from app.agent.toolcall import ToolCallAgent
from app.schema import Function, Message, ToolCall
from app.tool.base import BaseTool
from app.tool import ToolCollection
from app.tool.python_execute import PythonExecute


class _GuardedAgent(ToolCallAgent):
    available_tools: ToolCollection = ToolCollection(PythonExecute())


class _DummyTrendRadarTool(BaseTool):
    name: str = "mcp_trendradar_get_latest_news"
    description: str = "dummy"
    parameters: dict = {}

    async def execute(self, **kwargs):
        return {"ok": True, "items": []}


class _DummyEditorTool(BaseTool):
    name: str = "str_replace_editor"
    description: str = "dummy editor"
    parameters: dict = {}

    async def execute(self, **kwargs):
        return {"ok": True}


class _GuardedAgentWithTrendRadar(ToolCallAgent):
    available_tools: ToolCollection = ToolCollection(
        PythonExecute(), _DummyTrendRadarTool()
    )


class _GuardedAgentWithEditor(ToolCallAgent):
    available_tools: ToolCollection = ToolCollection(_DummyEditorTool())


@pytest.mark.asyncio
async def test_block_python_execute_for_browser_automation_code():
    agent = _GuardedAgent()
    call = ToolCall(
        id="1",
        function=Function(
            name="python_execute",
            arguments='{"code":"import webbrowser\\nwebbrowser.open(\\"https://www.bilibili.com\\")"}',
        ),
    )

    result = await agent.execute_tool(call)
    assert result.startswith("Error:")
    assert "Policy blocked `python_execute` browser automation" in result
    assert "mcp_playwright_browser_navigate" in result


@pytest.mark.asyncio
async def test_allow_python_execute_for_regular_python_code():
    agent = _GuardedAgent()
    call = ToolCall(
        id="2",
        function=Function(
            name="python_execute",
            arguments='{"code":"print(1 + 1)"}',
        ),
    )

    result = await agent.execute_tool(call)
    assert result.startswith("Observed output of cmd `python_execute` executed:")
    assert '"success": True' in result or "'success': True" in result


@pytest.mark.asyncio
async def test_do_not_treat_next_step_prompt_as_user_python_request():
    agent = _GuardedAgent()
    agent.next_step_prompt = (
        "For website/app opening, use Playwright MCP. "
        "Do NOT use python_execute unless user explicitly asks for a Python script."
    )
    # Simulate framework-injected synthetic user prompt.
    agent.memory.add_message(Message.user_message(agent.next_step_prompt))

    call = ToolCall(
        id="3",
        function=Function(
            name="python_execute",
            arguments='{"code":"import webbrowser\\nwebbrowser.open(\\"https://www.bilibili.com\\")"}',
        ),
    )

    result = await agent.execute_tool(call)
    assert result.startswith("Error:")
    assert "Policy blocked `python_execute` browser automation" in result


@pytest.mark.asyncio
async def test_block_python_execute_news_scraping_when_trendradar_available():
    agent = _GuardedAgentWithTrendRadar()
    call = ToolCall(
        id="4",
        function=Function(
            name="python_execute",
            arguments='{"code":"import requests\\nfrom bs4 import BeautifulSoup\\nrequests.get(\\"https://www.douyin.com/hot\\")"}',
        ),
    )

    result = await agent.execute_tool(call)
    assert result.startswith("Error:")
    assert "Policy blocked `python_execute` news scraping" in result
    assert "mcp_trendradar_get_latest_news" in result


@pytest.mark.asyncio
async def test_allow_python_execute_news_scraping_when_user_explicitly_requests_python():
    agent = _GuardedAgentWithTrendRadar()
    agent.memory.add_message(Message.user_message("请用Python脚本抓取抖音热搜新闻"))
    call = ToolCall(
        id="5",
        function=Function(
            name="python_execute",
            arguments='{"code":"import requests\\nfrom bs4 import BeautifulSoup\\nrequests.get(\\"https://www.douyin.com/hot\\")"}',
        ),
    )

    result = await agent.execute_tool(call)
    assert result.startswith("Observed output of cmd `python_execute` executed:")


@pytest.mark.asyncio
async def test_block_str_replace_editor_for_browser_task():
    agent = _GuardedAgentWithEditor()
    agent.memory.add_message(Message.user_message("打开B站并播放周杰伦稻香"))
    call = ToolCall(
        id="6",
        function=Function(
            name="str_replace_editor",
            arguments='{"command":"view","path":"E:\\\\Github\\\\OpenManus\\\\workspace"}',
        ),
    )

    result = await agent.execute_tool(call)
    assert result.startswith("Error:")
    assert "Policy blocked `str_replace_editor` for browser automation tasks" in result
    assert "mcp_playwright_browser_navigate" in result


@pytest.mark.asyncio
async def test_allow_str_replace_editor_when_user_requests_file_operation():
    agent = _GuardedAgentWithEditor()
    agent.memory.add_message(Message.user_message("请查看 workspace 目录文件结构"))
    call = ToolCall(
        id="7",
        function=Function(
            name="str_replace_editor",
            arguments='{"command":"view","path":"E:\\\\Github\\\\OpenManus\\\\workspace"}',
        ),
    )

    result = await agent.execute_tool(call)
    assert result.startswith("Observed output of cmd `str_replace_editor` executed:")
