"""列出或调用远端 MCP Tool，用于部署后的最小冒烟验证。"""
import argparse
import json

from mcp_runtime.client import MCPGateway
from models.settings import Settings


def main() -> None:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="Smoke test the tech KG MCP server")
    parser.add_argument("--server-url", default=settings.mcp_server_url)
    parser.add_argument("--tool")
    parser.add_argument("--arguments", default="{}", help="JSON object")
    args = parser.parse_args()
    gateway = MCPGateway(args.server_url, settings.mcp_request_timeout)
    if not args.tool:
        print(json.dumps(gateway.list_tool_specs(), ensure_ascii=False, indent=2))
        return
    arguments = json.loads(args.arguments)
    if not isinstance(arguments, dict):
        raise TypeError("--arguments 必须是 JSON object")
    print(json.dumps(gateway.call_tool(args.tool, arguments), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
