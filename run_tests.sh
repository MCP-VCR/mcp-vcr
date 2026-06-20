#!/bin/bash
export PATH="$HOME/.local/bin:$PATH"
uv run pytest tests/integration -v -s
