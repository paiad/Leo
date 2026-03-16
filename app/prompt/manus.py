from app.prompt.llm_prompts import LEO_SYSTEM_PROMPT_TEMPLATE as SYSTEM_PROMPT

NEXT_STEP_PROMPT = """
Based on user needs, proactively select the most appropriate tool or combination of tools. For complex tasks, you can break down the problem and use different tools step by step to solve it. After using each tool, clearly explain the execution results and suggest the next steps.

Browser task policy:
- For website/app opening, searching, clicking, form filling, login, screenshots, playback control, and other browser interactions, always prefer Playwright MCP tools (browser_navigate/browser_click/browser_type/browser_snapshot/...).
- Do NOT use `python_execute` to launch browsers or automate websites (for example playwright/selenium/pyautogui/webbrowser scripts), unless the user explicitly asks for a Python script and accepts the tradeoff.

If you want to stop the interaction at any point, use the `terminate` tool/function call.
"""
