from typing import List, Type, Optional, Any
from typing_extensions import override
from pydantic import BaseModel
from langchain_core.tools import BaseTool, BaseToolkit, ToolException
from mcp import StdioServerParameters, types, ClientSession
from mcp.client.stdio import stdio_client
import pydantic
from pydantic_core import to_json
from jsonschema_pydantic import jsonschema_to_pydantic
import asyncio
import json

from .storage import *

class McpServerConfig(BaseModel):
    """Configuration for an MCP server.
    
    This class represents the configuration needed to connect to and identify an MCP server,
    containing both the server's name and its connection parameters.

    Attributes:
        server_name (str): The name identifier for this MCP server
        server_param (StdioServerParameters): Connection parameters for the server, including
            command, arguments and environment variables
        exclude_tools (list[str]): List of tool names to exclude from this server
    """
    
    server_name: str
    server_param: StdioServerParameters
    exclude_tools: list[str] = []

class McpToolkit(BaseToolkit):
    name: str
    server_param: StdioServerParameters
    exclude_tools: list[str] = []
    _session: Optional[ClientSession] = None
    _tools: List[BaseTool] = []
    _client = None
    _init_lock: asyncio.Lock = None

    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, **data):
        super().__init__(**data)
        self._init_lock = asyncio.Lock()

    async def _start_session(self):
        async with self._init_lock:
            if self._session:
                return self._session

            self._client = stdio_client(self.server_param)
            read, write = await self._client.__aenter__()
            self._session = ClientSession(read, write)
            await self._session.__aenter__()
            await self._session.initialize()
            return self._session

    async def initialize(self, force_refresh: bool = False):
        if self._tools and not force_refresh:
            return

        print(f"[McpToolkit:{self.name}] Initializing tools (force_refresh={force_refresh})...")
        cached_tools = get_cached_tools(self.server_param)
        if cached_tools and not force_refresh:
            print(f"[McpToolkit:{self.name}] Using cached tools.")
            for tool in cached_tools:
                if tool.name in self.exclude_tools:
                    continue
                self._tools.append(create_langchain_tool(tool, self._session, self))
            return

        try:
            print(f"[McpToolkit:{self.name}] Starting session...")
            await self._start_session()
            print(f"[McpToolkit:{self.name}] Session started. Listing tools...")
            tools: types.ListToolsResult = await self._session.list_tools()
            save_tools_cache(self.server_param, tools.tools)
            print(f"[McpToolkit:{self.name}] Found {len(tools.tools)} tools. Creating LangChain tools...")
            for tool in tools.tools:
                if tool.name in self.exclude_tools:
                    continue
                self._tools.append(create_langchain_tool(tool, self._session, self))
        except Exception as e:
            print(f"Error gathering tools for {self.server_param.command} {' '.join(self.server_param.args)}: {e}")
            raise e
        
    async def close(self):
        print(f"[McpToolkit:{self.name}] Closing session...")
        try:
            if self._session:
                await self._session.__aexit__(None, None, None)
        except:
            # Currently above code doesn't really works and not closing the session
            # But it's not a big deal as we are exiting anyway
            # TODO find a way to cleanly close the session
            pass
        try:
            if self._client:
                await self._client.__aexit__(None, None, None)
        except:
            # TODO find a way to cleanly close the client
            pass
        print(f"[McpToolkit:{self.name}] Session closed.")

    def get_tools(self) -> List[BaseTool]:
        return self._tools


class McpTool(BaseTool):
    toolkit_name: str
    name: str
    description: str
    args_schema: Type[BaseModel]
    session: Optional[ClientSession]
    toolkit: McpToolkit

    handle_tool_error: bool = True

    def _run(self, **kwargs):
        raise NotImplementedError("Only async operations are supported")

    async def _arun(self, **kwargs):
        if not self.session:
            self.session = await self.toolkit._start_session()

        tool_name = self.name
        tool_args = kwargs
        print(f"[McpTool:{tool_name}] Calling tool with args: {json.dumps(tool_args)}")
        try:
            result = await self.session.call_tool(self.name, arguments=kwargs)
            content = to_json(result.content).decode()
            print(f"[McpTool:{tool_name}] Received result: isError={result.isError}, content={content[:200]}...")
            if result.isError:
                raise ToolException(content)
            return content
        except Exception as e:
            print(f"[McpTool:{tool_name}] Error during execution: {e!r}")
            raise

def create_langchain_tool(
    tool_schema: types.Tool,
    session: ClientSession,
    toolkit: McpToolkit,
) -> BaseTool:
    """Create a LangChain tool from MCP tool schema.
    
    Args:
        tool_schema (types.Tool): The MCP tool schema.
        session (ClientSession): The session for the tool.
    
    Returns:
        BaseTool: The created LangChain tool.
    """
    return McpTool(
        name=tool_schema.name,
        description=tool_schema.description or "(No description provided)",
        args_schema=jsonschema_to_pydantic(tool_schema.inputSchema),
        session=session,
        toolkit=toolkit,
        toolkit_name=toolkit.name,
    )


async def convert_mcp_to_langchain_tools(server_config: McpServerConfig, force_refresh: bool = False) -> McpToolkit:
    """Convert MCP tools to LangChain tools and create a toolkit.
    
    Args:
        server_config (McpServerConfig): Configuration for the MCP server including name and parameters.
        force_refresh (bool, optional): Whether to force refresh the tools cache. Defaults to False.
    
    Returns:
        McpToolkit: A toolkit containing the converted LangChain tools.
    """
    toolkit = McpToolkit(
        name=server_config.server_name, 
        server_param=server_config.server_param,
        exclude_tools=server_config.exclude_tools
    )
    await toolkit.initialize(force_refresh=force_refresh)
    return toolkit
