from client import MCPClient

import asyncio

async def main():
    if len(sys.argv) < 3:
        print("Usage: python main.py <server_id> <path_to_server_script> [<server_id2> <path_to_server_script2> ...]")
        print("Example: python main.py calculator calculator_server.py search search_server.py")
        sys.exit(1)

    # Check if we have pairs of server_id and script_path
    if len(sys.argv) % 2 != 1:
        print("Error: Each server must have both an ID and a script path.")
        sys.exit(1)

    client = MCPClient()
    try:
        # Connect to all specified servers
        for i in range(1, len(sys.argv), 2):
            server_id = sys.argv[i]
            script_path = sys.argv[i+1]
            await client.connect_to_server(server_id, script_path)
            
        await client.chat_loop()
    finally:
        await client.cleanup()

if __name__ == "__main__":
    import sys
    asyncio.run(main())