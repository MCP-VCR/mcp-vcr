import json
import sys
import time


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        if "method" in req:
            method = req["method"]
            req_id = req.get("id")

            if method == "initialize":
                if req_id is None:
                    continue
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "toy-server-fragile", "version": "1.0.0"},
                    },
                }
                print(json.dumps(resp), flush=True)

            elif method == "notifications/initialized":
                pass

            elif method == "tools/list":
                if req_id is None:
                    continue
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tools": [
                            {
                                "name": "read_file",
                                "description": "Reads a file from disk",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "path": {"type": "string"},
                                        "max_bytes": {"type": "integer"},
                                    },
                                    "required": ["path"],
                                },
                            }
                        ]
                    },
                }
                print(json.dumps(resp), flush=True)

            elif method == "tools/call":
                if req_id is None:
                    continue

                params = req.get("params", {})
                tool_name = params.get("name")
                args = params.get("arguments", {})

                if tool_name == "read_file":
                    # Behavior 1: Unhandled KeyError on missing required field 'path'
                    path_val = args["path"]  # Raises KeyError if missing!

                    # Behavior 3: Hang forever on empty string input
                    if path_val == "":
                        while True:
                            time.sleep(1)

                    # Behavior 2: Return success for wrong-type arguments (e.g. integer 42)
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {"type": "text", "text": f"Contents of {path_val}"}
                            ]
                        },
                    }
                    print(json.dumps(resp), flush=True)
                else:
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32601, "message": "Unknown tool"},
                    }
                    print(json.dumps(resp), flush=True)
            else:
                if req_id is not None:
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32601, "message": "Method not found"},
                    }
                    print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    main()
