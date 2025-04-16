# src/mcp_client_cli/agent_runner.py

import asyncio
import os
import uuid
from datetime import datetime
from queue import Queue
from typing import Annotated, TypedDict, Any

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage, AIMessageChunk
from langchain_core.prompts import ChatPromptTemplate
from langgraph.prebuilt import create_react_agent
from langgraph.managed import IsLastStep
from langgraph.graph.message import add_messages
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .config import AppConfig
from .const import SQLITE_DB
from .memory import AgentState, SqliteStore, get_memories, save_memory
from .storage import ConversationManager
from .tool import McpServerConfig, convert_mcp_to_langchain_tools, McpTool, StdioServerParameters, McpToolkit

class AgentRunner:
    def __init__(self, output_queue: Queue):
        self.output_queue = output_queue
        self.app_config = AppConfig.load()
        self.toolkits: list[McpToolkit] = [] # To manage toolkit lifecycle

    async def _load_tools(self, no_tools: bool = False, force_refresh: bool = False) -> tuple[list, list]:
        """Load and convert MCP tools to LangChain tools (adapted from cli.py)."""
        if no_tools:
            return [], []
        
        server_configs = [
            McpServerConfig(
                server_name=name,
                server_param=StdioServerParameters(
                    command=config.command,
                    args=config.args or [],
                    env={**(config.env or {}), **os.environ}
                ),
                exclude_tools=config.exclude_tools or []
            )
            for name, config in self.app_config.get_enabled_servers().items()
        ]
        
        langchain_tools = []
        self.toolkits = [] # Reset toolkits list
        
        async def convert_toolkit_task(server_config: McpServerConfig):
            toolkit = await convert_mcp_to_langchain_tools(server_config, force_refresh)
            self.toolkits.append(toolkit)
            langchain_tools.extend(toolkit.get_tools())

        try:
            # Using asyncio.gather for concurrent tool loading
            await asyncio.gather(*[convert_toolkit_task(sc) for sc in server_configs])
        except Exception as e:
            self._emit_error(f"Error loading tools: {e}")
            # Depending on desired behavior, maybe return empty tools or raise
            return [], [] 
            
        langchain_tools.append(save_memory)
        return self.toolkits, langchain_tools

    async def _cleanup_tools(self):
        """Close toolkit sessions."""
        for toolkit in self.toolkits:
            try:
                await toolkit.close()
            except Exception as e:
                 # Log error, but don't prevent other cleanup
                print(f"Error closing toolkit {toolkit.name}: {e}")
        self.toolkits = []

    def _emit(self, data: dict):
        """Puts data onto the output queue."""
        # Ensure session_id is added if not present (though it should be passed in run)
        if "session_id" not in data:
             print("Warning: session_id missing in emitted data")
        self.output_queue.put(data)

    def _emit_status(self, content: str, session_id: str):
        self._emit({"type": "status", "content": content, "session_id": session_id})

    def _emit_chunk(self, content: str, session_id: str):
        self._emit({"type": "message_chunk", "content": content, "session_id": session_id})

    def _emit_error(self, content: str, session_id: str = "system"):
        self._emit({"type": "error", "content": content, "session_id": session_id})
        
    def _emit_tool_confirm(self, tool_name: str, args: dict, session_id: str):
         self._emit({"type": "tool_confirm", "tool_name": tool_name, "args": args, "session_id": session_id})

    async def run(self, query_text: str, session_id: str, is_continuation: bool = False):
        """Runs the agent for a given query and session, putting results onto the queue."""
        
        self._emit_status("Initializing agent...", session_id)
        try:
            # --- Configuration & Tool Loading ---
            # TODO: Add options from CLI args if needed (e.g., force_refresh, no_tools)
            _toolkits, tools = await self._load_tools()
            if not _toolkits and not tools:
                 self._emit_status("No tools loaded.", session_id)
                 # Continue without tools if needed, or handle as error
            else:
                 self._emit_status(f"Loaded {len(tools)} tools.", session_id)

            # --- Model Initialization ---
            # TODO: Allow model override if needed
            extra_body = {}
            if self.app_config.llm.base_url and "openrouter" in self.app_config.llm.base_url:
                extra_body = {"transforms": ["middle-out"]}
            
            model = init_chat_model(
                model=self.app_config.llm.model,
                model_provider=self.app_config.llm.provider,
                api_key=self.app_config.llm.api_key,
                temperature=self.app_config.llm.temperature,
                base_url=self.app_config.llm.base_url,
                default_headers={
                    "X-Title": "mcp-client-cli-web", # Identify web UI
                    "HTTP-Referer": "https://github.com/adhikasp/mcp-client-cli",
                },
                extra_body=extra_body,
                 # streaming=True # Ensure streaming is enabled for LangChain model
            )
            
            # --- Prompt & Agent Setup ---
            prompt = ChatPromptTemplate.from_messages([
                ("system", self.app_config.system_prompt),
                ("placeholder", "{messages}")
            ])

            conversation_manager = ConversationManager(SQLITE_DB)
            store = SqliteStore(SQLITE_DB) # Assumes DB path from const.py

            async with AsyncSqliteSaver.from_conn_string(str(SQLITE_DB)) as checkpointer:
                # --- Memory & State --- 
                memories = await get_memories(store, user_id=session_id) # Use session_id as user_id
                formatted_memories = "\n".join(f"- {memory}" for memory in memories)
                
                agent_executor = create_react_agent(
                    model, tools, state_schema=AgentState, 
                    state_modifier=prompt, # Changed back from messages_modifier
                    checkpointer=checkpointer, 
                    store=store,
                    # TODO: Add interrupt logic if needed
                )

                # --- Conversation Thread ID --- 
                thread_id = session_id # Use web session ID as the conversation thread ID
                if is_continuation:
                     # Verify thread exists, or start new if not found?
                     # last_id = await conversation_manager.get_last_id() # Original CLI logic
                     # For web, we rely on the session_id provided
                     pass 
                else:
                    # This is likely a new session/conversation on the web
                    pass
                    
                # Save the current ID as the 'last' for potential CLI continuation? Or manage separately?
                # await conversation_manager.save_id(thread_id, db=checkpointer.conn) # Maybe not needed for web?

                # --- Input Preparation ---
                query_message = HumanMessage(content=query_text)
                input_messages = AgentState(
                    messages=[query_message],
                    today_datetime=datetime.now().isoformat(),
                    memories=formatted_memories,
                    # remaining_steps=5 # TODO: Configure max steps?
                )
                
                self._emit_status("Processing message...", session_id)
                config = {"configurable": {"thread_id": thread_id, "user_id": session_id}}

                # --- Streaming Agent Execution --- 
                async for event in agent_executor.astream_events(input_messages, config=config, version="v2"):
                    kind = event["event"]
                    # print(f"DEBUG Event: {kind}, Data: {event[\"data\"]}") # For debugging
                    
                    if kind == "on_chat_model_stream":
                        chunk = event["data"]["chunk"]
                        if isinstance(chunk, AIMessageChunk) and chunk.content:
                            self._emit_chunk(str(chunk.content), session_id)
                            
                    elif kind == "on_tool_start":
                         tool_input = event["data"].get("input")
                         tool_name = event["name"]
                         print(f"[AgentRunner:{session_id}] Event 'on_tool_start': name={tool_name}, input={tool_input}, full_event={event}") # Added log
                         self._emit_status(f"Calling tool: {tool_name}...", session_id)
                         # --- Tool Confirmation Logic --- 
                         # Check if this tool requires confirmation based on app_config
                         # if tool_name in self.app_config.tools_requires_confirmation:
                         #     self._emit_tool_confirm(tool_name, tool_input, session_id)
                         #     # --- PAUSE execution and wait for confirmation ---
                         #     # This requires a mechanism (e.g., asyncio.Event, queue)
                         #     # signaled by the /confirm_tool route in app.py
                         #     # confirmed = await wait_for_confirmation(session_id, tool_name)
                         #     # if not confirmed:
                         #     #     # TODO: Inject a ToolMessage indicating denial? How to interrupt?
                         #     #     self._emit_status(f"Tool call {tool_name} denied by user.", session_id)
                         #     #     # Need a way to stop the agent gracefully here or raise specific exception
                         #     #     raise ToolDeniedException(f"Tool {tool_name} denied.")
                         #     # else:
                         #     #     self._emit_status(f"Tool call {tool_name} confirmed.", session_id)
                         pass # Continue tool execution if confirmed or not needed

                    elif kind == "on_tool_end":
                        tool_output = event["data"].get("output")
                        tool_name = event["name"]
                        print(f"[AgentRunner:{session_id}] Event 'on_tool_end': name={tool_name}, output={tool_output}, full_event={event}") # Added log
                        # Check if output is ToolMessage and status is error?
                        if isinstance(tool_output, ToolMessage) and tool_output.status != 'success':
                            self._emit_status(f"Tool {tool_name} failed: {tool_output.content}", session_id)
                        else:
                            # Optionally show tool output (can be large)
                            # self._emit_status(f"Tool {tool_name} finished.", session_id)
                            pass
                            
                    elif kind == "on_chain_end":
                         # Can check event["name"] == "agent" to confirm it's the main agent loop
                         # print(f"DEBUG Chain End: {event[\"name\"]}")
                         pass
                    
                    elif kind == "on_chain_error" or kind == "on_tool_error" or kind == "on_chat_model_error" or kind == "on_retriever_error":
                         # Handle various errors
                         error_content = event["data"].get("error", "Unknown error")
                         print(f"[AgentRunner:{session_id}] Event 'error': kind={kind}, error={error_content}, full_event={event}") # Added log
                         self._emit_error(f"Error during execution: {error_content}", session_id)
                         # Decide whether to break or continue
                         break

        except Exception as e:
            import traceback
            self._emit_error(f"Agent run failed: {e}\n{traceback.format_exc()}", session_id)
        finally:
            self._emit_status("Finished", session_id)
            # Signal end of stream for this request
            self.output_queue.put(None)
            # Clean up tool sessions
            await self._cleanup_tools()

# Helper class for tool denial (optional)
# class ToolDeniedException(Exception):
#     pass

# Placeholder for confirmation waiting logic (needs implementation)
# async def wait_for_confirmation(session_id: str, tool_name: str) -> bool:
#     # This function needs to wait for an event/message set by the confirm_tool route
#     # Example using asyncio.Event (would need shared state accessible by Flask route)
#     print(f"Waiting for confirmation for {tool_name} in session {session_id}")
#     await asyncio.sleep(10) # Simulate waiting
#     print(f"Confirmation check finished for {tool_name}")
#     return True # Defaulting to confirmed for now 