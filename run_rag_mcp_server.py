# coding: utf-8
from app.rag_mcp.server import RagMCPServer, parse_args


if __name__ == "__main__":
    args = parse_args()
    RagMCPServer().run(transport=args.transport)
