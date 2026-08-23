import json
import sys


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
            if req_id is None:
                continue

            resp = {"jsonrpc": "2.0", "id": req_id}

            if method == "initialize":
                resp["result"] = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"resources": {}, "tools": {}, "prompts": {}},
                    "serverInfo": {"name": "toy-server-benign-jargon", "version": "1.0.0"},
                }
            elif method == "tools/list":
                resp["result"] = {
                    "tools": [
                        {
                            "name": "analyze_auth_flow",
                            "description": "Analyzes the OAuth2 authentication token exchange flow, inspecting the authorization header structure and validating token expiry timestamps against the server clock.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "token_count": {"type": "integer"},
                                    "credential_count": {"type": "integer"},
                                    "token_type": {"type": "string"},
                                },
                            },
                        },
                        {
                            "name": "sanitize_html",
                            "description": "Sanitizes HTML input by stripping dangerous tags including script, iframe, and object elements. Returns clean HTML safe for rendering.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "html_input": {
                                        "type": "string",
                                        "description": "Example format: api_key: your_key_here_1234567890",
                                    }
                                },
                            },
                        },
                        {
                            "name": "persona_manager",
                            "description": "Configures system prompts and AI character personas for conversational agents.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "persona_name": {"type": "string"},
                                },
                            },
                        },
                    ]
                }
            else:
                resp["error"] = {"code": -32601, "message": "Method not found"}

            print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    main()
