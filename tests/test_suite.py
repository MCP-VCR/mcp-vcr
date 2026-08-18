import sys
from pathlib import Path

import pytest
import yaml

from mcp_vcr.suite import SuiteManifest, SuiteResult, SuiteRunner
from mcp_vcr.validator import validate_file


def test_load_suite_manifest(tmp_path: Path):
    suite_dir = tmp_path / "my_suite"
    suite_dir.mkdir()
    manifest_file = suite_dir / "suite.yaml"
    manifest_file.write_text(
        yaml.dump(
            {
                "name": "custom_suite",
                "description": "A custom test suite",
                "server_package": "custom-pkg",
                "server_hint": "python my_server.py",
                "protocol_version": "2024-11-05",
                "transport": "stdio",
                "tags": ["custom", "test"],
                "transcripts": ["t1.yaml", "t2.yaml"],
            }
        ),
        encoding="utf-8",
    )

    runner = SuiteRunner()
    manifest = runner.load_suite(suite_dir)

    assert manifest.name == "custom_suite"
    assert manifest.description == "A custom test suite"
    assert manifest.server_package == "custom-pkg"
    assert manifest.server_hint == "python my_server.py"
    assert manifest.protocol_version == "2024-11-05"
    assert manifest.transport == "stdio"
    assert manifest.tags == ["custom", "test"]
    assert manifest.transcripts == ["t1.yaml", "t2.yaml"]
    assert manifest.suite_dir == suite_dir


def test_load_suite_preserves_server_hint_and_top_level_override(tmp_path: Path):
    suites_dir = tmp_path / "suites"
    suites_dir.mkdir()

    # Suite A has server_hint in its own suite.yaml
    suite_a_dir = suites_dir / "suite_a"
    suite_a_dir.mkdir()
    (suite_a_dir / "suite.yaml").write_text(
        yaml.dump({
            "name": "suite_a",
            "description": "Suite A",
            "server_hint": "suite_level_hint_a",
            "transcripts": ["t.yaml"]
        }),
        encoding="utf-8"
    )

    # Suite B has server_hint in its own suite.yaml, but top-level manifest overrides it
    suite_b_dir = suites_dir / "suite_b"
    suite_b_dir.mkdir()
    (suite_b_dir / "suite.yaml").write_text(
        yaml.dump({
            "name": "suite_b",
            "description": "Suite B",
            "server_hint": "suite_level_hint_b",
            "transcripts": ["t.yaml"]
        }),
        encoding="utf-8"
    )

    (suites_dir / "manifest.yaml").write_text(
        yaml.dump({
            "suites": [
                {"name": "suite_a", "path": "suite_a"},
                {"name": "suite_b", "path": "suite_b", "server_hint": "top_level_override_b"}
            ]
        }),
        encoding="utf-8"
    )

    runner = SuiteRunner()

    # Direct load_suite preserves suite.yaml server_hint
    direct_a = runner.load_suite(suite_a_dir)
    assert direct_a.server_hint == "suite_level_hint_a"

    direct_b = runner.load_suite(suite_b_dir)
    assert direct_b.server_hint == "suite_level_hint_b"

    # list_suites discovers both, retains suite_a hint and applies top_level_override_b
    discovered = runner.list_suites(suites_dir=suites_dir)
    sm_a = next(s for s in discovered if s.name == "suite_a")
    sm_b = next(s for s in discovered if s.name == "suite_b")

    assert sm_a.server_hint == "suite_level_hint_a"
    assert sm_b.server_hint == "top_level_override_b"

    # find_suite also gets the proper hints
    found_a = runner.find_suite("suite_a", suites_dir=suites_dir)
    found_b = runner.find_suite("suite_b", suites_dir=suites_dir)
    assert found_a.server_hint == "suite_level_hint_a"
    assert found_b.server_hint == "top_level_override_b"


def test_load_suite_manifest_missing_transcripts(tmp_path: Path):
    suite_dir = tmp_path / "bad_suite"
    suite_dir.mkdir()
    manifest_file = suite_dir / "suite.yaml"
    manifest_file.write_text(
        yaml.dump(
            {
                "name": "bad_suite",
                "description": "Missing transcripts list",
            }
        ),
        encoding="utf-8",
    )

    runner = SuiteRunner()
    with pytest.raises(ValueError, match="Invalid suite manifest"):
        runner.load_suite(suite_dir)


def test_load_suite_manifest_invalid_yaml(tmp_path: Path):
    suite_dir = tmp_path / "corrupt_suite"
    suite_dir.mkdir()
    manifest_file = suite_dir / "suite.yaml"
    manifest_file.write_text("name: corrupt: [unbalanced", encoding="utf-8")

    runner = SuiteRunner()
    with pytest.raises(ValueError, match="Failed to parse YAML"):
        runner.load_suite(suite_dir)


def test_load_suite_manifest_path_traversal_rejected(tmp_path: Path):
    suite_dir = tmp_path / "traversal_suite"
    suite_dir.mkdir()
    manifest_file = suite_dir / "suite.yaml"
    manifest_file.write_text(
        yaml.dump(
            {
                "name": "traversal_suite",
                "description": "Attempts path traversal",
                "transcripts": ["../outside.yaml"],
            }
        ),
        encoding="utf-8",
    )

    runner = SuiteRunner()
    with pytest.raises(ValueError):
        runner.load_suite(suite_dir)



def test_list_suites():
    runner = SuiteRunner()
    suites = runner.list_suites()
    names = [s.name for s in suites]

    assert "filesystem" in names
    assert "memory" in names
    assert "time" in names

    fs_suite = next(s for s in suites if s.name == "filesystem")
    assert len(fs_suite.transcripts) >= 2
    assert fs_suite.transport == "stdio"


def test_list_suites_empty_dir(tmp_path: Path):
    empty_dir = tmp_path / "empty_suites"
    empty_dir.mkdir()

    runner = SuiteRunner()
    suites = runner.list_suites(suites_dir=empty_dir)
    assert suites == []


def test_list_suites_custom_dir_precedence(tmp_path: Path):
    custom_dir = tmp_path / "custom_suites"
    custom_dir.mkdir()

    # Create a custom suite with the same name 'filesystem'
    fs_dir = custom_dir / "filesystem"
    fs_dir.mkdir()
    (fs_dir / "suite.yaml").write_text(
        yaml.dump(
            {
                "name": "filesystem",
                "description": "Overridden custom filesystem suite",
                "transcripts": ["custom_t.yaml"],
            }
        ),
        encoding="utf-8",
    )

    runner = SuiteRunner()
    suites = runner.list_suites(suites_dir=custom_dir)

    # Only the custom suite should be found; bundled memory & time must not be merged in
    assert len(suites) == 1
    assert suites[0].name == "filesystem"
    assert suites[0].description == "Overridden custom filesystem suite"
    assert suites[0].transcripts == ["custom_t.yaml"]


def test_list_suites_directory_matching_suite_name(tmp_path: Path):
    """Verify that a directory named after another suite's manifest name is not skipped."""
    suites_dir = tmp_path / "suites"
    suites_dir.mkdir()

    # Top-level manifest points to 'custom_folder' which defines suite 'alpha'
    (suites_dir / "manifest.yaml").write_text(
        yaml.dump({
            "suites": [
                {"name": "alpha", "path": "custom_folder"}
            ]
        }),
        encoding="utf-8",
    )

    folder_custom = suites_dir / "custom_folder"
    folder_custom.mkdir()
    (folder_custom / "suite.yaml").write_text(
        yaml.dump({
            "name": "alpha",
            "description": "Suite Alpha in custom folder",
            "transcripts": ["t1.yaml"],
        }),
        encoding="utf-8",
    )

    # Subdirectory named 'alpha' containing suite 'beta'
    folder_alpha = suites_dir / "alpha"
    folder_alpha.mkdir()
    (folder_alpha / "suite.yaml").write_text(
        yaml.dump({
            "name": "beta",
            "description": "Suite Beta in alpha folder",
            "transcripts": ["t2.yaml"],
        }),
        encoding="utf-8",
    )

    runner = SuiteRunner()
    suites = runner.list_suites(suites_dir=suites_dir)

    names = [s.name for s in suites]
    assert "alpha" in names
    assert "beta" in names
    assert len(suites) == 2

    # find_suite should locate both
    assert runner.find_suite("alpha", suites_dir=suites_dir).name == "alpha"
    assert runner.find_suite("beta", suites_dir=suites_dir).name == "beta"


def test_suite_runner_respects_config_timeout(tmp_path: Path):
    """Verify that SuiteRunner with timeout_ms=None preserves configuration timeout precedence."""
    config_data = {
        "replay": {
            "timeout_ms": 3500,
        }
    }
    config_file = tmp_path / ".mcp-vcr.yaml"
    config_file.write_text(yaml.dump(config_data), encoding="utf-8")

    runner = SuiteRunner(config_path=config_file, timeout_ms=None)
    from mcp_vcr.replay import ReplayEngine
    engine = ReplayEngine(config_path=runner.config_path, timeout_ms=runner.timeout_ms)
    assert engine.timeout_ms == 3500


def test_suite_transcript_validation():
    runner = SuiteRunner()
    suites = runner.list_suites()

    for suite in suites:
        for t_rel in suite.transcripts:
            t_path = suite.suite_dir / t_rel
            assert t_path.exists(), f"Transcript file {t_path} missing in suite {suite.name}"
            # Validate against v1 transcript schema
            transcript = validate_file(t_path, allow_v0=False)
            assert transcript.meta.version == 1
            assert len(transcript.messages) > 0


@pytest.mark.asyncio
async def test_suite_runner_with_toy_server(tmp_path: Path):
    # Construct a 2-transcript test suite:
    # 1. initialize_and_tools_list.yaml matching toy_server.py (should pass)
    # 2. tool_call_differ.yaml with a different schema/response (should fail structural diff)
    suite_dir = tmp_path / "toy_test_suite"
    suite_dir.mkdir()

    t1_content = {
        "meta": {
            "version": 1,
            "recorded_at": "2026-08-16T12:00:00.000Z",
            "session_id": "aa11bb22",
            "server_command": ["python", "tests/integration/toy_server.py"],
            "protocol_version": "2024-11-05",
            "client_hint": "pytest",
            "schema_version": "1.0",
        },
        "messages": [
            {
                "t": 0,
                "dir": "c2s",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1.0"},
                    },
                },
            },
            {
                "t": 25,
                "dir": "s2c",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"resources": {}, "tools": {}, "prompts": {}},
                        "serverInfo": {"name": "toy-server", "version": "1.0.0"},
                    },
                },
            },
            {
                "t": 30,
                "dir": "c2s",
                "payload": {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                },
            },
            {
                "t": 40,
                "dir": "c2s",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                },
            },
            {
                "t": 65,
                "dir": "s2c",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "tools": [
                            {
                                "name": "toy_tool",
                                "description": "A toy tool",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"arg": {"type": "string"}},
                                    "required": ["arg"],
                                },
                            }
                        ]
                    },
                },
            },
        ],
    }

    t2_content = {
        "meta": {
            "version": 1,
            "recorded_at": "2026-08-16T12:00:00.000Z",
            "session_id": "cc33dd44",
            "server_command": ["python", "tests/integration/toy_server.py"],
            "protocol_version": "2024-11-05",
            "client_hint": "pytest",
            "schema_version": "1.0",
        },
        "messages": [
            {
                "t": 0,
                "dir": "c2s",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1.0"},
                    },
                },
            },
            {
                "t": 25,
                "dir": "s2c",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"resources": {}, "tools": {}, "prompts": {}},
                        "serverInfo": {"name": "toy-server", "version": "1.0.0"},
                    },
                },
            },
            {
                "t": 30,
                "dir": "c2s",
                "payload": {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                },
            },
            {
                "t": 40,
                "dir": "c2s",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "toy_tool",
                        "arguments": {"arg": "hello"},
                    },
                },
            },
            {
                "t": 65,
                "dir": "s2c",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 2,
                    # Expecting numeric result field structure, but toy server returns content array of dicts
                    "result": {
                        "numeric_output": 12345
                    },
                },
            },
        ],
    }

    (suite_dir / "t1_pass.yaml").write_text(yaml.dump(t1_content), encoding="utf-8")
    (suite_dir / "t2_fail.yaml").write_text(yaml.dump(t2_content), encoding="utf-8")

    (suite_dir / "suite.yaml").write_text(
        yaml.dump(
            {
                "name": "toy_suite",
                "description": "Suite testing pass and fail paths on toy server",
                "transcripts": ["t1_pass.yaml", "t2_fail.yaml"],
            }
        ),
        encoding="utf-8",
    )

    runner = SuiteRunner()
    manifest = runner.load_suite(suite_dir)

    server_cmd = [sys.executable, str(Path(__file__).parent / "integration" / "toy_server.py")]
    res = await runner.run_suite(manifest, server_args=server_cmd, diff_mode="structural")

    # Assert exact expected SuiteResult counts
    assert res.suite_name == "toy_suite"
    assert res.total == 2
    assert res.passed == 1
    assert res.failed == 1
    assert res.skipped == 0
    assert res.exit_code == 1

    assert res.results[0]["transcript"] == "t1_pass.yaml"
    assert res.results[0]["status"] == "pass"

    assert res.results[1]["transcript"] == "t2_fail.yaml"
    assert res.results[1]["status"] == "fail"
    assert res.results[1]["diff"] is not None

    # Assert no replay artifacts are left behind in the suite directory
    leftover_replays = list(suite_dir.glob("*-replay-*.yaml")) + list(suite_dir.glob("*-replay-*.yml"))
    assert leftover_replays == []


def test_suite_server_hint_loaded_from_top_manifest():
    runner = SuiteRunner()
    suites = runner.list_suites()
    fs_suite = next((s for s in suites if s.name == "filesystem"), None)
    assert fs_suite is not None
    assert fs_suite.server_hint == "npx @modelcontextprotocol/server-filesystem /tmp"

    memory_suite = next((s for s in suites if s.name == "memory"), None)
    assert memory_suite is not None
    assert memory_suite.server_hint == "npx @modelcontextprotocol/server-memory"


def test_is_bundled_suite(tmp_path: Path):
    runner = SuiteRunner()
    bundled_suites = runner.list_suites()
    assert len(bundled_suites) > 0
    for s in bundled_suites:
        assert runner.is_bundled_suite(s) is True

    custom_dir = tmp_path / "custom"
    custom_dir.mkdir()
    (custom_dir / "suite.yaml").write_text(
        yaml.dump({
            "name": "custom",
            "description": "Custom suite",
            "transcripts": ["t.yaml"]
        }),
        encoding="utf-8"
    )
    custom_manifest = runner.load_suite(custom_dir)
    assert runner.is_bundled_suite(custom_manifest) is False


@pytest.mark.asyncio
async def test_run_all_suites_with_toy_server(tmp_path: Path, toy_pass_transcript, write_suite):
    # Create two test suites using shared fixtures
    suite1_dir = write_suite(tmp_path / "suite1", "suite1", toy_pass_transcript, "t1.yaml", "Suite 1 pass")
    suite2_dir = write_suite(tmp_path / "suite2", "suite2", toy_pass_transcript, "t2.yaml", "Suite 2 pass")

    runner = SuiteRunner()
    m1 = runner.load_suite(suite1_dir)
    m2 = runner.load_suite(suite2_dir)

    server_cmd = [sys.executable, str(Path(__file__).parent / "integration" / "toy_server.py")]
    multi_res = await runner.run_all_suites([m1, m2], server_args=server_cmd)

    assert multi_res.suites_total == 2
    assert multi_res.suites_passed == 2
    assert multi_res.suites_failed == 0
    assert multi_res.transcripts_total == 2
    assert multi_res.transcripts_passed == 2
    assert multi_res.transcripts_failed == 0
    assert multi_res.exit_code == 0
    assert len(multi_res.suite_results) == 2
    assert multi_res.suite_results[0].suite_name == "suite1"
    assert multi_res.suite_results[1].suite_name == "suite2"


@pytest.mark.asyncio
async def test_run_all_suites_mixed_pass_fail(tmp_path: Path, toy_pass_transcript, write_suite):
    # Suite 1: passes
    suite1_dir = write_suite(tmp_path / "suite1", "suite1_pass", toy_pass_transcript, "t1.yaml")

    # Suite 2: failing tool call response schema
    t2_fail = {
        "meta": {
            "version": 1,
            "recorded_at": "2026-08-16T12:00:00.000Z",
            "session_id": "cc33dd44",
            "server_command": ["python", "tests/integration/toy_server.py"],
            "protocol_version": "2024-11-05",
            "client_hint": "pytest",
            "schema_version": "1.0",
        },
        "messages": [
            {
                "t": 0,
                "dir": "c2s",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1.0"},
                    },
                },
            },
            {
                "t": 25,
                "dir": "s2c",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"resources": {}, "tools": {}, "prompts": {}},
                        "serverInfo": {"name": "toy-server", "version": "1.0.0"},
                    },
                },
            },
            {
                "t": 30,
                "dir": "c2s",
                "payload": {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                },
            },
            {
                "t": 40,
                "dir": "c2s",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "toy_tool",
                        "arguments": {"arg": "hello"},
                    },
                },
            },
            {
                "t": 65,
                "dir": "s2c",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "numeric_output": 12345
                    },
                },
            },
        ],
    }
    suite2_dir = write_suite(tmp_path / "suite2", "suite2_fail", t2_fail, "t2.yaml")

    runner = SuiteRunner()
    m1 = runner.load_suite(suite1_dir)
    m2 = runner.load_suite(suite2_dir)

    server_cmd = [sys.executable, str(Path(__file__).parent / "integration" / "toy_server.py")]
    multi_res = await runner.run_all_suites([m1, m2], server_args=server_cmd)

    assert multi_res.suites_total == 2
    assert multi_res.suites_passed == 1
    assert multi_res.suites_failed == 1
    assert multi_res.exit_code == 1
    assert len(multi_res.suite_results) == 2
    # Preserves sequential execution order
    assert multi_res.suite_results[0].suite_name == "suite1_pass"
    assert multi_res.suite_results[0].exit_code == 0
    assert multi_res.suite_results[1].suite_name == "suite2_fail"
    assert multi_res.suite_results[1].exit_code == 1


@pytest.mark.asyncio
async def test_run_all_suites_exception_isolation(tmp_path: Path, toy_pass_transcript, write_suite, monkeypatch):
    suite1_dir = write_suite(tmp_path / "suite1", "suite1", toy_pass_transcript, "t1.yaml")
    suite2_dir = write_suite(tmp_path / "suite2", "suite2", toy_pass_transcript, "t2.yaml")

    runner = SuiteRunner()
    m1 = runner.load_suite(suite1_dir)
    m2 = runner.load_suite(suite2_dir)

    orig_run_suite = runner.run_suite

    async def mock_run_suite(manifest, *args, **kwargs):
        if manifest.name == "suite1":
            raise RuntimeError("Catastrophic connection failure")
        return await orig_run_suite(manifest, *args, **kwargs)

    monkeypatch.setattr(runner, "run_suite", mock_run_suite)

    server_cmd = [sys.executable, str(Path(__file__).parent / "integration" / "toy_server.py")]
    multi_res = await runner.run_all_suites([m1, m2], server_args=server_cmd)

    assert multi_res.suites_total == 2
    assert multi_res.suites_passed == 1
    assert multi_res.suites_failed == 1
    assert multi_res.exit_code == 1
    assert multi_res.suite_results[0].suite_name == "suite1"
    assert multi_res.suite_results[0].exit_code == 1
    assert multi_res.suite_results[0].results[0]["status"] == "fail"
    assert "Catastrophic connection failure" in multi_res.suite_results[0].results[0]["message"]
    assert multi_res.suite_results[1].suite_name == "suite2"
    assert multi_res.suite_results[1].exit_code == 0


@pytest.mark.asyncio
async def test_run_all_suites_on_suite_start_exception_isolation(tmp_path: Path, toy_pass_transcript, write_suite):
    suite1_dir = write_suite(tmp_path / "suite1", "suite1", toy_pass_transcript, "t1.yaml")
    suite2_dir = write_suite(tmp_path / "suite2", "suite2", toy_pass_transcript, "t2.yaml")

    runner = SuiteRunner()
    m1 = runner.load_suite(suite1_dir)
    m2 = runner.load_suite(suite2_dir)

    def buggy_on_suite_start(manifest: SuiteManifest):
        if manifest.name == "suite1":
            raise ValueError("UI callback crashed on suite1")

    server_cmd = [sys.executable, str(Path(__file__).parent / "integration" / "toy_server.py")]
    multi_res = await runner.run_all_suites(
        [m1, m2],
        server_args=server_cmd,
        on_suite_start=buggy_on_suite_start
    )

    assert multi_res.suites_total == 2
    assert multi_res.suites_passed == 1
    assert multi_res.suites_failed == 1
    assert multi_res.exit_code == 1
    assert multi_res.suite_results[0].suite_name == "suite1"
    assert multi_res.suite_results[0].exit_code == 1
    assert multi_res.suite_results[0].results[0]["status"] == "fail"
    assert "UI callback crashed on suite1" in multi_res.suite_results[0].results[0]["message"]
    assert multi_res.suite_results[1].suite_name == "suite2"
    assert multi_res.suite_results[1].exit_code == 0



