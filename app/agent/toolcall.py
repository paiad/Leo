import asyncio
import json
import os
import re
import time
from typing import Any, List, Optional, Union

from pydantic import Field

from app.agent.react import ReActAgent
from app.exceptions import TokenLimitExceeded
from app.logger import logger
from app.prompt.toolcall import NEXT_STEP_PROMPT, SYSTEM_PROMPT
from app.schema import TOOL_CHOICE_TYPE, AgentState, Message, ToolCall, ToolChoice
from app.tool import CreateChatCompletion, Terminate, ToolCollection


TOOL_CALL_REQUIRED = "Tool calls required but none provided"


class ToolCallAgent(ReActAgent):
    """Base agent class for handling tool/function calls with enhanced abstraction"""

    name: str = "toolcall"
    description: str = "an agent that can execute tool calls."

    system_prompt: str = SYSTEM_PROMPT
    next_step_prompt: str = NEXT_STEP_PROMPT

    available_tools: ToolCollection = ToolCollection(
        CreateChatCompletion(), Terminate()
    )
    tool_choices: TOOL_CHOICE_TYPE = ToolChoice.AUTO  # type: ignore
    special_tool_names: List[str] = Field(default_factory=lambda: [Terminate().name])

    tool_calls: List[ToolCall] = Field(default_factory=list)
    _current_base64_image: Optional[str] = None

    max_steps: int = 30
    max_observe: Optional[Union[int, bool]] = None
    cleanup_on_run_finish: bool = True

    async def think(self) -> bool:
        """Process current state and decide next actions using tools"""
        if self.next_step_prompt:
            user_msg = Message.user_message(self.next_step_prompt)
            self.messages += [user_msg]

        try:
            # Get response with tool options
            response = await self.llm.ask_tool(
                messages=self.messages,
                system_msgs=(
                    [Message.system_message(self.system_prompt)]
                    if self.system_prompt
                    else None
                ),
                tools=self.available_tools.to_params(),
                tool_choice=self.tool_choices,
            )
        except ValueError:
            raise
        except Exception as e:
            # Check if this is a RetryError containing TokenLimitExceeded
            if hasattr(e, "__cause__") and isinstance(e.__cause__, TokenLimitExceeded):
                token_limit_error = e.__cause__
                logger.error(
                    f"🚨 Token limit error (from RetryError): {token_limit_error}"
                )
                self.memory.add_message(
                    Message.assistant_message(
                        f"Maximum token limit reached, cannot continue execution: {str(token_limit_error)}"
                    )
                )
                self.state = AgentState.FINISHED
                return False
            raise

        self.tool_calls = tool_calls = (
            response.tool_calls if response and response.tool_calls else []
        )
        content = response.content if response and response.content else ""

        # Log response info
        logger.info(f"✨ {self.name}'s thoughts: {content}")
        logger.info(
            f"🛠️ {self.name} selected {len(tool_calls) if tool_calls else 0} tools to use"
        )
        if tool_calls:
            logger.info(
                f"🧰 Tools being prepared: {[call.function.name for call in tool_calls]}"
            )
            logger.info(f"🔧 Tool arguments: {tool_calls[0].function.arguments}")

        try:
            if response is None:
                raise RuntimeError("No response received from the LLM")

            # Handle different tool_choices modes
            if self.tool_choices == ToolChoice.NONE:
                if tool_calls:
                    logger.warning(
                        f"🤔 Hmm, {self.name} tried to use tools when they weren't available!"
                    )
                if content:
                    self.memory.add_message(Message.assistant_message(content))
                    return True
                return False

            # Create and add assistant message
            assistant_msg = (
                Message.from_tool_calls(content=content, tool_calls=self.tool_calls)
                if self.tool_calls
                else Message.assistant_message(content)
            )
            self.memory.add_message(assistant_msg)

            if self.tool_choices == ToolChoice.REQUIRED and not self.tool_calls:
                return True  # Will be handled in act()

            # For 'auto' mode, continue with content if no commands but content exists
            if self.tool_choices == ToolChoice.AUTO and not self.tool_calls:
                if content:
                    self.state = AgentState.FINISHED
                    return False
                return False

            return bool(self.tool_calls)
        except Exception as e:
            logger.error(f"🚨 Oops! The {self.name}'s thinking process hit a snag: {e}")
            self.memory.add_message(
                Message.assistant_message(
                    f"Error encountered while processing: {str(e)}"
                )
            )
            return False

    async def act(self) -> str:
        """Execute tool calls and handle their results"""
        if not self.tool_calls:
            if self.tool_choices == ToolChoice.REQUIRED:
                raise ValueError(TOOL_CALL_REQUIRED)

            # Return last message content if no tool calls
            return self.messages[-1].content or "No content or commands to execute"

        results = []
        for command in self.tool_calls:
            # Reset base64_image for each tool call
            self._current_base64_image = None
            tool_name = command.function.name
            raw_arguments = command.function.arguments or "{}"

            await self._emit_event(
                {
                    "type": "tool_start",
                    "step": self.current_step,
                    "toolName": tool_name,
                    "arguments": raw_arguments[:500],
                }
            )
            started = time.perf_counter()

            result = await self.execute_tool(command)
            duration_ms = int((time.perf_counter() - started) * 1000)
            success = not result.lstrip().startswith("Error:")

            if self.max_observe:
                result = result[: self.max_observe]

            logger.info(
                f"🎯 Tool '{command.function.name}' completed its mission! Result: {result}"
            )
            await self._emit_event(
                {
                    "type": "tool_done",
                    "step": self.current_step,
                    "toolName": tool_name,
                    "ok": success,
                    "durationMs": duration_ms,
                    "resultPreview": result[:500],
                    "error": None if success else result[:500],
                }
            )

            # Add tool response to memory
            tool_msg = Message.tool_message(
                content=result,
                tool_call_id=command.id,
                name=command.function.name,
                base64_image=self._current_base64_image,
            )
            self.memory.add_message(tool_msg)
            results.append(result)

        return "\n\n".join(results)

    async def execute_tool(self, command: ToolCall) -> str:
        """Execute a single tool call with robust error handling"""
        if not command or not command.function or not command.function.name:
            return "Error: Invalid command format"

        name = command.function.name
        if name not in self.available_tools.tool_map:
            return f"Error: Unknown tool '{name}'"

        try:
            # Parse arguments
            args = json.loads(command.function.arguments or "{}")

            if self._should_block_python_browser_automation(name=name, args=args):
                return (
                    "Error: Policy blocked `python_execute` browser automation. "
                    "For opening websites, search/click/playback, use Playwright MCP tools "
                    "such as `mcp_playwright_browser_navigate`, "
                    "`mcp_playwright_browser_type`, `mcp_playwright_browser_click`."
                )
            if self._should_block_python_news_scraping(name=name, args=args):
                return (
                    "Error: Policy blocked `python_execute` news scraping while TrendRadar MCP is available. "
                    "For hot/news requests, use TrendRadar MCP tools such as "
                    "`mcp_trendradar_get_latest_news` or `mcp_trendradar_search_news`."
                )
            if self._should_block_editor_for_browser_task(name=name, args=args):
                return (
                    "Error: Policy blocked `str_replace_editor` for browser automation tasks. "
                    "For website operations, use Playwright MCP tools such as "
                    "`mcp_playwright_browser_navigate`, "
                    "`mcp_playwright_browser_click`, `mcp_playwright_browser_type`."
                )

            # Execute the tool
            logger.info(f"🔧 Activating tool: '{name}'...")
            result = await self.available_tools.execute(name=name, tool_input=args)

            # Handle special tools
            await self._handle_special_tool(name=name, result=result)

            # Check if result is a ToolResult with base64_image
            if hasattr(result, "base64_image") and result.base64_image:
                # Store the base64_image for later use in tool_message
                self._current_base64_image = result.base64_image

            # Format result for display (standard case)
            observation = (
                f"Observed output of cmd `{name}` executed:\n{str(result)}"
                if result
                else f"Cmd `{name}` completed with no output"
            )

            return observation
        except json.JSONDecodeError:
            error_msg = f"Error parsing arguments for {name}: Invalid JSON format"
            logger.error(
                f"📝 Oops! The arguments for '{name}' don't make sense - invalid JSON, arguments:{command.function.arguments}"
            )
            return f"Error: {error_msg}"
        except Exception as e:
            error_msg = f"⚠️ Tool '{name}' encountered a problem: {str(e)}"
            logger.exception(error_msg)
            return f"Error: {error_msg}"

    @staticmethod
    def _contains_browser_automation_code(code: str) -> bool:
        if not code:
            return False

        patterns = (
            r"\bimport\s+webbrowser\b",
            r"\bwebbrowser\.open\s*\(",
            r"\bfrom\s+playwright\b",
            r"\bimport\s+playwright\b",
            r"\bplaywright\.sync_api\b",
            r"\bplaywright\.async_api\b",
            r"\bimport\s+selenium\b",
            r"\bfrom\s+selenium\b",
            r"\bwebdriver\b",
            r"\bpyautogui\b",
            r"\bpage\.goto\s*\(",
            r"\bbrowser\.new_page\s*\(",
        )
        return any(
            re.search(pattern, code, flags=re.IGNORECASE | re.MULTILINE)
            for pattern in patterns
        )

    def _user_explicitly_requests_python_browser_script(self) -> bool:
        last_user_text = self._last_real_user_text()

        if not last_user_text:
            return False

        python_hints = (
            "python script",
            "python 脚本",
            "用python",
            "用 python",
            "写脚本",
            "playwright 脚本",
            "selenium 脚本",
        )
        browser_hints = (
            "browser",
            "playwright",
            "selenium",
            "web",
            "website",
            "浏览器",
            "网页",
            "网站",
            "自动化",
            "打开",
        )
        return any(h in last_user_text for h in python_hints) and any(
            h in last_user_text for h in browser_hints
        )

    def _last_real_user_text(self) -> str:
        for msg in reversed(self.memory.messages):
            if msg.role == "user" and msg.content:
                # The agent appends `next_step_prompt` as a synthetic user message
                # each step; it is not an actual user intent signal.
                if self.next_step_prompt and (
                    msg.content.strip() == self.next_step_prompt.strip()
                ):
                    continue
                return msg.content.lower()
        return ""

    def _should_block_python_browser_automation(
        self, name: str, args: dict[str, Any]
    ) -> bool:
        if name != "python_execute":
            return False

        code = args.get("code")
        if not isinstance(code, str):
            return False

        if not self._contains_browser_automation_code(code):
            return False

        if (os.getenv("BFF_ALLOW_PY_BROWSER_AUTOMATION", "").strip().lower()
            in {"1", "true", "yes", "on"}):
            return False

        if self._user_explicitly_requests_python_browser_script():
            return False

        return True

    @staticmethod
    def _contains_news_scraping_code(code: str) -> bool:
        if not code:
            return False

        patterns = (
            r"douyin\.com/hot",
            r"weibo\.com/(?:hot|top|rank)",
            r"zhihu\.com/(?:hot|billboard)",
            r"toutiao",
            r"(?:requests|httpx)\.(?:get|post)\s*\(",
            r"\bBeautifulSoup\b",
            r"\bbs4\b",
        )
        if not any(
            re.search(pattern, code, flags=re.IGNORECASE | re.MULTILINE)
            for pattern in patterns
        ):
            return False

        news_hints = (
            "hot",
            "hotspot",
            "trend",
            "top",
            "news",
            "rss",
            "热点",
            "热搜",
            "趋势",
            "新闻",
            "抖音",
            "微博",
            "知乎",
            "头条",
        )
        lowered = code.lower()
        return any(hint in lowered for hint in news_hints)

    def _has_trendradar_tools(self) -> bool:
        return any(
            str(tool_name).startswith("mcp_trendradar_")
            for tool_name in self.available_tools.tool_map.keys()
        )

    def _user_explicitly_requests_python_news_script(self) -> bool:
        last_user_text = self._last_real_user_text()

        if not last_user_text:
            return False

        python_hints = (
            "python script",
            "python 脚本",
            "用python",
            "用 python",
            "写脚本",
            "爬虫",
            "crawler",
            "抓取",
        )
        news_hints = (
            "news",
            "hot",
            "trend",
            "热点",
            "热搜",
            "新闻",
            "抖音",
            "微博",
            "知乎",
            "头条",
        )
        return any(h in last_user_text for h in python_hints) and any(
            h in last_user_text for h in news_hints
        )

    def _should_block_python_news_scraping(
        self, name: str, args: dict[str, Any]
    ) -> bool:
        if name != "python_execute":
            return False

        if (
            os.getenv("BFF_ALLOW_PY_NEWS_SCRAPING", "").strip().lower()
            in {"1", "true", "yes", "on"}
        ):
            return False

        code = args.get("code")
        if not isinstance(code, str):
            return False

        if not self._has_trendradar_tools():
            return False

        if not self._contains_news_scraping_code(code):
            return False

        if self._user_explicitly_requests_python_news_script():
            return False

        return True

    def _looks_like_browser_task_request(self) -> bool:
        last_user_text = self._last_real_user_text()
        if not last_user_text:
            return False
        hints = (
            "b站",
            "bilibili",
            "youtube",
            "浏览器",
            "网页",
            "网站",
            "打开",
            "播放",
            "点击",
            "video",
            "music",
            "song",
        )
        return any(hint in last_user_text for hint in hints)

    def _user_explicitly_requests_file_editing(self) -> bool:
        last_user_text = self._last_real_user_text()
        if not last_user_text:
            return False
        hints = (
            "编辑文件",
            "修改文件",
            "查看文件",
            "代码",
            "repo",
            "repository",
            "patch",
            "diff",
            "str_replace_editor",
            "workspace",
            "目录",
            "文件夹",
            "file",
            "folder",
        )
        return any(hint in last_user_text for hint in hints)

    def _should_block_editor_for_browser_task(
        self, name: str, args: dict[str, Any]
    ) -> bool:
        if name != "str_replace_editor":
            return False
        if (
            os.getenv("BFF_ALLOW_EDITOR_FOR_BROWSER_TASK", "").strip().lower()
            in {"1", "true", "yes", "on"}
        ):
            return False
        if not self._looks_like_browser_task_request():
            return False
        if self._user_explicitly_requests_file_editing():
            return False
        command = str(args.get("command") or "").strip().lower()
        # In browser tasks, `view/create/str_replace/insert` on workspace files is
        # almost always a planning detour rather than user intent.
        return command in {"view", "create", "str_replace", "insert", "undo_edit"}

    async def _handle_special_tool(self, name: str, result: Any, **kwargs):
        """Handle special tool execution and state changes"""
        if not self._is_special_tool(name):
            return

        if self._should_finish_execution(name=name, result=result, **kwargs):
            # Set agent state to finished
            logger.info(f"🏁 Special tool '{name}' has completed the task!")
            self.state = AgentState.FINISHED

    @staticmethod
    def _should_finish_execution(**kwargs) -> bool:
        """Determine if tool execution should finish the agent"""
        return True

    def _is_special_tool(self, name: str) -> bool:
        """Check if tool name is in special tools list"""
        return name.lower() in [n.lower() for n in self.special_tool_names]

    async def cleanup(self):
        """Clean up resources used by the agent's tools."""
        logger.info(f"🧹 Cleaning up resources for agent '{self.name}'...")
        for tool_name, tool_instance in self.available_tools.tool_map.items():
            if hasattr(tool_instance, "cleanup") and asyncio.iscoroutinefunction(
                tool_instance.cleanup
            ):
                try:
                    logger.debug(f"🧼 Cleaning up tool: {tool_name}")
                    await tool_instance.cleanup()
                except Exception as e:
                    logger.error(
                        f"🚨 Error cleaning up tool '{tool_name}': {e}", exc_info=True
                    )
        logger.info(f"✨ Cleanup complete for agent '{self.name}'.")

    async def run(self, request: Optional[str] = None) -> str:
        """Run the agent with cleanup when done."""
        try:
            return await super().run(request)
        finally:
            if not self.cleanup_on_run_finish:
                return
            try:
                await self.cleanup()
            except asyncio.CancelledError as exc:
                logger.warning(
                    f"Cleanup cancelled in ToolCallAgent.run and ignored: {exc}"
                )
                task = asyncio.current_task()
                if task is not None and hasattr(task, "uncancel"):
                    while task.cancelling():
                        task.uncancel()
            except Exception as exc:
                logger.warning(
                    f"Cleanup failed in ToolCallAgent.run and ignored: {exc}"
                )
