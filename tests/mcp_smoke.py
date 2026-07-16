"""Local MCP initialization smoke test; it does not call TIMI CC."""

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "timicc_worker.server"],
    )
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()
            print(initialized.serverInfo.name, flush=True)
            print(",".join(tool.name for tool in tools.tools), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
