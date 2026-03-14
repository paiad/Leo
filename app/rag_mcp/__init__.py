__all__ = ["RagMCPServer", "parse_args"]


def __getattr__(name: str):
    if name in {"RagMCPServer", "parse_args"}:
        from .server import RagMCPServer, parse_args

        return {"RagMCPServer": RagMCPServer, "parse_args": parse_args}[name]
    raise AttributeError(name)
