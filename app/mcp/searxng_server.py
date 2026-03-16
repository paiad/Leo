import argparse
import os
from typing import Any
from urllib.parse import urljoin

import httpx
from mcp.server.fastmcp import FastMCP


def _normalize_base_url(value: str | None) -> str:
    base = (value or "").strip() or "http://127.0.0.1:18080"
    return base.rstrip("/") + "/"


def _to_int(value: int, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return max(minimum, min(maximum, parsed))


class SearxngMCPServer:
    def __init__(self, base_url: str):
        self.base_url = _normalize_base_url(base_url)
        self.server = FastMCP("searxng")
        self._register_tools()

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        endpoint = urljoin(self.base_url, path.lstrip("/"))
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, trust_env=False) as client:
            # Retry once for transient upstream gateway errors.
            resp = await client.get(endpoint, params=params or {})
            if resp.status_code >= 500:
                resp = await client.get(endpoint, params=params or {})
            if resp.status_code >= 400:
                return {
                    "ok": False,
                    "status_code": resp.status_code,
                    "error": f"http_{resp.status_code}",
                    "body_preview": (resp.text or "")[:500],
                }
            data = resp.json()
            if not isinstance(data, dict):
                return {"raw": data}
            return data

    def _register_tools(self) -> None:
        @self.server.tool()
        async def search(
            query: str,
            limit: int = 8,
            page: int = 1,
            language: str = "zh-CN",
            categories: str = "general",
            time_range: str = "",
            safesearch: int = 1,
        ) -> dict[str, Any]:
            """Search by SearXNG and return structured results with source links."""
            q = (query or "").strip()
            if not q:
                return {"ok": False, "error": "query is required"}

            safe_level = _to_int(safesearch, default=1, minimum=0, maximum=2)
            page_no = _to_int(page, default=1, minimum=1, maximum=20)
            max_items = _to_int(limit, default=8, minimum=1, maximum=20)
            params: dict[str, Any] = {
                "q": q,
                "format": "json",
                "pageno": page_no,
                "language": (language or "").strip() or "zh-CN",
                "safesearch": safe_level,
            }
            if (categories or "").strip():
                params["categories"] = categories.strip()
            if (time_range or "").strip():
                params["time_range"] = time_range.strip()

            data = await self._get_json("/search", params=params)
            if data.get("ok") is False:
                return {
                    "ok": False,
                    "query": q,
                    "base_url": self.base_url,
                    "error": data.get("error"),
                    "status_code": data.get("status_code"),
                    "body_preview": data.get("body_preview"),
                }
            raw_results = data.get("results", [])
            results: list[dict[str, Any]] = []
            if isinstance(raw_results, list):
                for item in raw_results[:max_items]:
                    if not isinstance(item, dict):
                        continue
                    results.append(
                        {
                            "title": item.get("title"),
                            "url": item.get("url"),
                            "content": item.get("content"),
                            "engine": item.get("engine"),
                            "publishedDate": item.get("publishedDate"),
                            "score": item.get("score"),
                        }
                    )

            return {
                "ok": True,
                "query": q,
                "base_url": self.base_url,
                "number_of_results": data.get("number_of_results"),
                "results": results,
                "suggestions": data.get("suggestions", []),
                "infoboxes": data.get("infoboxes", []),
            }

        @self.server.tool()
        async def health() -> dict[str, Any]:
            """Check if SearXNG endpoint is reachable."""
            try:
                config_data = await self._get_json("/config", params={"format": "json"})
                return {
                    "ok": True,
                    "base_url": self.base_url,
                    "instance": config_data.get("instance_name"),
                    "version": config_data.get("version"),
                }
            except Exception as exc:
                return {"ok": False, "base_url": self.base_url, "error": str(exc)}

    def run(self, transport: str = "stdio") -> None:
        self.server.run(transport=transport)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SearXNG MCP bridge server")
    parser.add_argument("--transport", choices=["stdio"], default="stdio")
    parser.add_argument("--base-url", dest="base_url", default=os.getenv("SEARXNG_BASE_URL", "http://127.0.0.1:18080"))
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    server = SearxngMCPServer(base_url=args.base_url)
    server.run(transport=args.transport)
