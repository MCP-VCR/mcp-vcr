import sys
import json
import os
import time

def main():
    mode = os.environ.get("TOY_SERVER_MODE", "default")
    
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            # Handle invalid JSON as a JSON-RPC Parse Error
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": "Parse error"
                }
            }
            print(json.dumps(err_resp), flush=True)
            continue
            
        if "method" in req:
            method = req["method"]
            req_id = req.get("id")
            
            # Notifications don't have IDs
            is_notification = req_id is None
            
            resp = {"jsonrpc": "2.0", "id": req_id}
            
            if method == "initialize":
                resp["result"] = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"resources": {}, "tools": {}, "prompts": {}},
                    "serverInfo": {"name": "toy-server", "version": "1.0.0"}
                }
            elif method == "notifications/initialized":
                # Do not reply to notifications
                continue
            elif method == "exit":
                sys.exit(0)
            elif method == "resources/list":
                resp["result"] = {
                    "resources": [
                        {
                            "uri": "file:///toy/resource",
                            "name": "Toy Resource"
                        }
                    ]
                }
            elif method == "resources/read":
                content = "Fixed data" if mode == "default" else "Changed data"
                resp["result"] = {
                    "contents": [
                        {
                            "uri": req["params"]["uri"],
                            "mimeType": "text/plain",
                            "text": content
                        }
                    ]
                }
            elif method == "tools/list":
                resp["result"] = {
                    "tools": [
                        {
                            "name": "toy_tool",
                            "description": "A toy tool",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"arg": {"type": "string"}},
                                "required": ["arg"]
                            }
                        }
                    ]
                }
            elif method == "tools/call":
                if req["params"]["name"] == "toy_tool":
                    val = req["params"]["arguments"].get("arg", "")
                    # Test for redaction and normalization
                    if val == "secret_test":
                        resp["result"] = {
                            "api_key": "sk-secret-123",
                            "nested": {"token": "secret"},
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Safe content"
                                }
                            ],
                            "isError": False
                        }
                    elif val == "norm_test":
                        # Return different UUIDs based on time or random if mode is changing
                        uid = "123e4567" if mode == "default" else "999e8888"
                        ts = "2026-06-20T12:00:00Z" if mode == "default" else "2026-06-21T13:00:00Z"
                        csr = "abc123" if mode == "default" else "xyz999"
                        resp["result"] = {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps({"id": uid, "timestamp": ts, "cursor": csr})
                                }
                            ]
                        }
                    elif val == "large_payload":
                        # Generate 1MB payload to test large messages without crashing completely instantly
                        resp["result"] = {
                            "content": [{"type": "text", "text": "A" * 1024 * 1024}]
                        }
                    elif val == "crash":
                        sys.exit(1)
                    else:
                        text_val = "Fixed tool response" if mode == "default" else "Changed tool response"
                        resp["result"] = {
                            "content": [{"type": "text", "text": text_val}],
                            "isError": False
                        }
                else:
                    resp["error"] = {"code": -32601, "message": "Method not found"}
            else:
                resp["error"] = {"code": -32601, "message": "Method not found"}
                
            if not is_notification:
                print(json.dumps(resp), flush=True)

if __name__ == "__main__":
    main()
