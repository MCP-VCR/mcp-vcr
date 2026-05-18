import sys
import json

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            method = data.get("method")
            msg_id = data.get("id")
            
            if msg_id is not None:
                if method == "initialize":
                    resp = {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "test-server", "version": "1.0.0"}
                        }
                    }
                elif method == "tools/list":
                    resp = {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "tools": [{"name": "toolA", "description": "A tool"}]
                        }
                    }
                else:
                    resp = {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {"echo": method}
                    }
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError as e:
            sys.stderr.write(f"Invalid JSON input: {e}\n")
            sys.stderr.flush()
        except (IOError, OSError) as e:
            sys.stderr.write(f"I/O error: {e}\n")
            sys.stderr.flush()

if __name__ == "__main__":
    main()
