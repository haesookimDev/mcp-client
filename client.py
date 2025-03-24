import asyncio
from typing import Optional, List, Dict
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters, Tool
from mcp.client.stdio import stdio_client

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()  # load environment variables from .env

class ServerConnection:
    """Class to manage individual server connections"""
    def __init__(self, server_id: str, session: ClientSession, stdio, write):
        self.server_id = server_id
        self.session = session
        self.stdio = stdio
        self.write = write
        self.tools: List[Tool] = []

    async def list_tools(self):
        """List available tools for this server"""
        response = await self.session.list_tools()
        self.tools = response.tools
        return self.tools
    
    async def call_tool(self, tool_name: str, tool_args: dict):
        """Call a tool on this server"""
        return await self.session.call_tool(tool_name, tool_args)
    
class MCPClient:
    def __init__(self):
        # Initialize session and client objects
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()
        self.anthropic = Anthropic()
        self.servers: Dict[str, ServerConnection] = {}
        self.current_server_id: Optional[str] = None

    async def connect_to_server(self, server_id: str, server_script_path: str):
        """Connect to an MCP server

        Args:
            server_id: Unique identifier for this server connection
            server_script_path: Path to the server script (.py or .js)
        """
        if server_id in self.servers:
            print(f"Server with ID '{server_id}' already exists.")
            return
        
        is_python = server_script_path.endswith('.py')
        is_js = server_script_path.endswith('.js')
        if not (is_python or is_js):
            raise ValueError("Server script must be a .py or .js file")

        command = "python" if is_python else "node"
        server_params = StdioServerParameters(
            command=command,
            args=[server_script_path],
            env=None
        )

        stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        self.stdio, self.write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(ClientSession(self.stdio, self.write))

        await self.session.initialize()

        server_conn = ServerConnection(server_id, self.session, self.stdio, self.write)
        self.servers[server_id] = server_conn

        if self.current_server_id is None:
            self.current_server_id = server_id

        tools = await server_conn.list_tools()
        print("\nConnected to server with tools:", [tool.name for tool in tools])

    def switch_server(self, server_id: str) -> bool:
        """Switch the current active server"""
        if server_id in self.servers:
            self.current_server_id = server_id
            print(f"Switched to server: {server_id}")
            return True
        else:
            print(f"Server '{server_id}' not found.")
            return False
        
    def list_servers(self):
        """List all connected servers"""
        if not self.servers:
            print("No servers connected")
            return
        
        print("\nConnected servers:")
        for server_id, server in self.servers.items():
            status = "ACTIVE" if server_id == self.current_server_id else "CONNECTED"
            tool_names = [tool.name for tool in server.tools]
            print(f"  - {server_id} [{status}: Tools: {tool_names}]")

    async def disconnected_server(self, server_id:str):
        """Disconnect from a specific server"""
        if server_id not in self.servers:
            print(f"Server '{server_id}' not found")
            return False

        del self.servers[server_id]

        if server_id == self.current_server_id:
            if self.servers:
                self.current_server_id = next(iter(self.servers.keys()))
            else:
                self.current_server_id = None
        
        print(f"Disconnected from server: {server_id}")
        return True

    async def process_query(self, query: str) -> str:
        """Process a query using Claude and available tools"""
        if not self.current_server_id:
            return " No active server. Please to at least one server first"
        
        messages = [
            {
                "role": "user",
                "content": query
            }
        ]

        all_tools = []
        server_tool_map = {}

        for server_id, server in self.servers.items():
            tools = await server.list_tools()
            for tool in tools:
                tool_dict = {
                    "name": tool.name,
                    "description": f"[Server: {server_id}] {tool.description}",
                    "input_schema": tool.inputSchema
                }
                all_tools.append(tool_dict)
                server_tool_map[tool.name] = server_id

         # Initial Claude API call
        response = self.anthropic.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1000,
            messages=messages,
            tools=all_tools
        )

        # Process response and handle tool calls
        final_text = []

        assistant_message_content = []
        for content in response.content:
            if content.type == 'text':
                final_text.append(content.text)
                assistant_message_content.append(content)
            elif content.type == 'tool_use':
                tool_name = content.name
                tool_args = content.input
                
                # Find which server this tool belongs to
                if tool_name in server_tool_map:
                    server_id = server_tool_map[tool_name]
                    server = self.servers[server_id]
                    
                    # Execute tool call on the appropriate server
                    result = await server.call_tool(tool_name, tool_args)
                    final_text.append(f"[Calling tool {tool_name} on server {server_id} with args {tool_args}]")

                    assistant_message_content.append(content)
                    messages.append({
                        "role": "assistant",
                        "content": assistant_message_content
                    })
                    messages.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": content.id,
                                "content": result.content
                            }
                        ]
                    })

                    # Get next response from Claude
                    response = self.anthropic.messages.create(
                        model="claude-3-5-sonnet-20241022",
                        max_tokens=1000,
                        messages=messages,
                        tools=all_tools
                    )

                    final_text.append(response.content[0].text)
                else:
                    final_text.append(f"Error: Tool {tool_name} not found on any connected server.")

        return "\n".join(final_text)
    
    async def chat_loop(self):
        """Run an interactive chat loop"""
        print("\nMCP Client Started!")
        print("Type your queries or use commands:")
        print("  /connect <server_id> <script_path> - Connect to a new server")
        print("  /switch <server_id> - Switch active server")
        print("  /list - List connected servers")
        print("  /disconnect <server_id> - Disconnect from a server")
        print("  /quit - Exit the client")

        while True:
            try:
                user_input = input("\nQuery or command: ").strip()

                if user_input.lower() == '/quit':
                    break
                    
                # Handle commands
                if user_input.startswith('/'):
                    parts = user_input.split()
                    command = parts[0].lower()
                    
                    if command == '/connect' and len(parts) >= 3:
                        server_id = parts[1]
                        script_path = parts[2]
                        await self.connect_to_server(server_id, script_path)
                    elif command == '/switch' and len(parts) >= 2:
                        self.switch_server(parts[1])
                    elif command == '/list':
                        self.list_servers()
                    elif command == '/disconnect' and len(parts) >= 2:
                        await self.disconnect_server(parts[1])
                    else:
                        print("Invalid command format. Type /help for available commands.")
                else:
                    # Process regular query
                    response = await self.process_query(user_input)
                    print("\n" + response)

            except Exception as e:
                print(f"\nError: {str(e)}")

    async def cleanup(self):
        """Clean up resources"""
        await self.exit_stack.aclose()


    