# MCP-VCR CI/CD Pipeline Integration Guide

Integrating MCP-VCR into your CI/CD pipelines ensures that every pull request or merge request is automatically tested against your golden snapshots. This guide covers setup for GitHub Actions, GitLab CI/CD, local golden updates, and git version tracking strategies.

---

## 1. CI Pipeline Configurations

Ensure your server dependencies and python virtual environment are set up, then run the `mcp-vcr verify` command.

### GitHub Actions (`.github/workflows/mcp-regression.yml`)

```yaml
name: MCP Server Regression Tests

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]

jobs:
  test-regression:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"  # Speeds up CI runs via dependency caching

      - name: Install Dependencies
        run: |
          python -m pip install --upgrade pip
          pip install mcp-vcr
          # Install your server's own dependencies (example)
          pip install -r requirements.txt

      - name: Verify Golden Snapshots
        run: |
          mcp-vcr verify snapshots/ -- python server.py
```

### GitLab CI/CD (`.gitlab-ci.yml`)

```yaml
stages:
  - test

mcp_regression_tests:
  stage: test
  image: python:3.12
  cache:
    paths:
      - .cache/pip
  variables:
    PIP_CACHE_DIR: "$CI_PROJECT_DIR/.cache/pip"
  before_script:
    - python -m pip install --upgrade pip
    - pip install mcp-vcr -r requirements.txt
  script:
    - mcp-vcr verify snapshots/ -- python server.py
```

---

## 2. Local Golden Snapshot Updates

When you intentionally change your MCP server's schema (e.g., adding a new tool, adding an input parameter, or changing output format), your regression tests will naturally fail in CI. 

To update your golden snapshots to match the new server implementation, run the verify command locally with the `--update` flag:

```bash
mcp-vcr verify --update snapshots/ -- python server.py
```

This command will:
1. Re-run the client messages from each golden snapshot against your local server.
2. Capture the brand new server responses.
3. Automatically apply normalizers (stripping timestamps, request IDs, etc.).
4. Overwrite the old snapshot file with the newly generated golden response payload.

Review the git diff using `git diff snapshots/` to ensure the updates are exactly what you expect before committing.

---

## 3. Git Tracking Strategy & Best Practices

To make snapshot testing seamless and reproducible:

### Commit Your Snapshots
Always check in the `snapshots/` directory to git:

```bash
git add snapshots/*_golden.yaml
git commit -m "test: add golden snapshots for initialize and tools/list"
```

This ensures that any developer checking out your branch can run regression tests instantly offline.

### Exclude Raw Sessions (Optional)
Unlike snapshots, raw recorded session files under the `sessions/` directory contain unnormalized IDs, tokens, or absolute paths, making them noisy for version control. You can add `sessions/` to your `.gitignore`:

```ignore
# .gitignore
sessions/
```

### Keep Commands Deterministic
Ensure that the server arguments passed to `mcp-vcr verify` and `mcp-vcr record` match perfectly. For example, if your server relies on environment variables or specific flags in development, make sure they are also configured in your CI container.
