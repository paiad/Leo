from importlib import import_module
from typing import Any

__all__ = ["feishu_long_connection"]


def __getattr__(name: str) -> Any:
    if name == "feishu_long_connection":
        return import_module("bff.services.integration.feishu_long_connection")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
