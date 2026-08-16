import asyncio
import importlib.resources
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import jsonschema
import yaml

from .diff import format_json_diff, format_text_diff, run_diff
from .replay import ReplayEngine
from .validator import validate_file

logger = logging.getLogger("mcp-vcr.suite")

_MANIFEST_SCHEMA_CACHE = None


def load_manifest_schema() -> Dict[str, Any]:
    """Load the suite manifest JSON schema."""
    global _MANIFEST_SCHEMA_CACHE
    if _MANIFEST_SCHEMA_CACHE is not None:
        return _MANIFEST_SCHEMA_CACHE
    schema_content = (
        importlib.resources.files("mcp_vcr")
        .joinpath("schemas", "suite-manifest-schema.json")
        .read_text(encoding="utf-8")
    )
    _MANIFEST_SCHEMA_CACHE = json.loads(schema_content)
    return _MANIFEST_SCHEMA_CACHE


def validate_manifest_dict(data: Dict[str, Any], file_path: Optional[Path] = None) -> None:
    """Validate raw manifest data against the suite manifest JSON schema."""
    if not isinstance(data, dict):
        raise ValueError(
            f"Suite manifest at {file_path or 'unknown'} must be a dictionary."
        )
    schema = load_manifest_schema()
    validator = jsonschema.Draft7Validator(schema)
    errors = list(validator.iter_errors(data))
    if errors:
        err_msgs = []
        for err in errors:
            loc = " -> ".join(str(p) for p in err.path) if err.path else "root"
            err_msgs.append(f"{loc}: {err.message}")
        raise ValueError(
            f"Invalid suite manifest at {file_path or 'unknown'}:\n"
            + "\n".join(f"  - {m}" for m in err_msgs)
        )


@dataclass
class SuiteManifest:
    name: str
    description: str
    server_package: str = ""
    protocol_version: str = "2024-11-05"
    transport: str = "stdio"
    tags: List[str] = field(default_factory=list)
    ignore_fields: List[str] = field(default_factory=list)
    transcripts: List[str] = field(default_factory=list)
    suite_dir: Path = field(default_factory=Path)


@dataclass
class SuiteResult:
    """Aggregated outcome of running a suite."""
    suite_name: str
    total: int
    passed: int
    failed: int
    skipped: int  # Reserved for future skip filters (e.g. transport or protocol compatibility gates)
    results: List[Dict[str, Any]]
    exit_code: int


class SuiteRunner:
    """
    Discovers, validates, and runs MCP community and custom test suites.
    """

    def __init__(
        self,
        config_path: Optional[Path] = None,
        timeout_ms: Optional[int] = None,
        timing_faithful: Optional[bool] = None,
    ):
        self.config_path = config_path
        self.timeout_ms = timeout_ms
        self.timing_faithful = timing_faithful

    @staticmethod
    def get_bundled_suites_dir() -> Path:
        """Locate the bundled mcp_vcr/community directory."""
        ref = importlib.resources.files("mcp_vcr").joinpath("community")
        return Path(str(ref))

    def load_suite(self, suite_dir: Path) -> SuiteManifest:
        """
        Parse and validate a suite manifest from a suite directory.
        """
        resolved_suite_dir = suite_dir.resolve()
        if not resolved_suite_dir.exists() or not resolved_suite_dir.is_dir():
            raise FileNotFoundError(f"Suite directory not found: {suite_dir}")

        manifest_file = resolved_suite_dir / "suite.yaml"
        if not manifest_file.exists():
            manifest_file = resolved_suite_dir / "suite.yml"
            if not manifest_file.exists():
                raise FileNotFoundError(
                    f"No suite.yaml or suite.yml found in {suite_dir}"
                )

        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception as e:
            raise ValueError(f"Failed to parse YAML from {manifest_file}: {e}") from e

        validate_manifest_dict(data, file_path=manifest_file)

        name = data.get("name", suite_dir.name)
        description = data.get("description", "")
        server_package = data.get("server_package", "")
        protocol_version = data.get("protocol_version", "2024-11-05")
        transport = data.get("transport", "stdio")
        tags = data.get("tags", [])
        ignore_fields = data.get("ignore_fields", [])
        transcripts = data.get("transcripts", [])

        # Validate path containment for all declared transcripts
        checked_transcripts: List[str] = []
        for t in transcripts:
            t_str = str(t)
            t_path = (resolved_suite_dir / t_str).resolve()
            if not t_path.is_relative_to(resolved_suite_dir):
                raise ValueError(
                    f"Transcript path '{t_str}' escapes suite directory '{suite_dir}'."
                )
            checked_transcripts.append(t_str)

        return SuiteManifest(
            name=name,
            description=description,
            server_package=server_package,
            protocol_version=protocol_version,
            transport=transport,
            tags=tags if isinstance(tags, list) else [],
            ignore_fields=ignore_fields if isinstance(ignore_fields, list) else [],
            transcripts=checked_transcripts,
            suite_dir=resolved_suite_dir,
        )

    def list_suites(self, suites_dir: Optional[Path] = None) -> List[SuiteManifest]:
        """
        Discover all suites.
        If suites_dir is provided, search exclusively in that directory.
        Otherwise, search exclusively in the bundled community directory.
        """
        target_dir = Path(suites_dir).resolve() if suites_dir else self.get_bundled_suites_dir().resolve()
        if not target_dir.exists() or not target_dir.is_dir():
            return []

        discovered: Dict[str, SuiteManifest] = {}

        # 1. Check top-level manifest.yaml if present for explicit suite paths
        top_manifest = target_dir / "manifest.yaml"
        if not top_manifest.exists():
            top_manifest = target_dir / "manifest.yml"

        if top_manifest.exists():
            try:
                with open(top_manifest, "r", encoding="utf-8") as f:
                    top_data = yaml.safe_load(f) or {}
                suites_list = top_data.get("suites", [])
                if isinstance(suites_list, list):
                    for item in suites_list:
                        if isinstance(item, dict) and "path" in item:
                            s_dir = (target_dir / item["path"]).resolve()
                            if s_dir.is_relative_to(target_dir) and s_dir.is_dir():
                                try:
                                    sm = self.load_suite(s_dir)
                                    discovered[sm.name] = sm
                                except Exception as e:
                                    logger.warning(
                                        f"Failed to load suite from {s_dir}: {e}"
                                    )
            except Exception as e:
                logger.warning(f"Failed to parse top-level manifest {top_manifest}: {e}")

        # 2. Also scan all subdirectories with suite.yaml or suite.yml
        for child in sorted(target_dir.iterdir()):
            if child.is_dir() and child.name not in discovered:
                if (child / "suite.yaml").exists() or (child / "suite.yml").exists():
                    try:
                        sm = self.load_suite(child)
                        discovered[sm.name] = sm
                    except Exception as e:
                        logger.warning(f"Skipping invalid suite in {child}: {e}")

        return sorted(list(discovered.values()), key=lambda s: s.name)

    def find_suite(
        self, name: str, suites_dir: Optional[Path] = None
    ) -> SuiteManifest:
        """
        Locate a specific suite by name.
        If suites_dir is provided, search exclusively in that directory.
        Otherwise, search in the bundled community directory.
        """
        suites = self.list_suites(suites_dir=suites_dir)
        for s in suites:
            if s.name == name:
                return s

        # Also check direct folder path match by name
        target_dir = Path(suites_dir).resolve() if suites_dir else self.get_bundled_suites_dir().resolve()
        direct_dir = (target_dir / name).resolve()
        if direct_dir.is_relative_to(target_dir) and direct_dir.is_dir() and (
            (direct_dir / "suite.yaml").exists() or (direct_dir / "suite.yml").exists()
        ):
            return self.load_suite(direct_dir)

        scope_msg = f"in directory '{target_dir}'" if suites_dir else "in bundled suites"
        raise ValueError(
            f"Suite '{name}' not found {scope_msg}. Use --list-suites to see available suites."
        )

    async def run_suite(
        self,
        manifest: SuiteManifest,
        server_args: List[str],
        diff_mode: str = "structural",
        on_transcript_result: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> SuiteResult:
        """
        Run all transcripts in a suite sequentially against the target server.
        """
        results: List[Dict[str, Any]] = []
        passed_count = 0
        failed_count = 0
        skipped_count = 0

        engine = ReplayEngine(
            config_path=self.config_path,
            timeout_ms=self.timeout_ms,
            timing_faithful=self.timing_faithful,
        )

        for transcript_rel in manifest.transcripts:
            transcript_path = (manifest.suite_dir / transcript_rel).resolve()
            t_name = transcript_rel

            # Security containment check
            if not transcript_path.is_relative_to(manifest.suite_dir.resolve()):
                res = {
                    "transcript": t_name,
                    "status": "fail",
                    "message": f"Transcript path '{transcript_rel}' escapes suite directory",
                    "diff": None,
                    "detail": None,
                }
                results.append(res)
                failed_count += 1
                if on_transcript_result:
                    on_transcript_result(res)
                continue

            if not transcript_path.exists():
                res = {
                    "transcript": t_name,
                    "status": "fail",
                    "message": f"Transcript file not found: {transcript_rel}",
                    "diff": None,
                    "detail": None,
                }
                results.append(res)
                failed_count += 1
                if on_transcript_result:
                    on_transcript_result(res)
                continue

            # Ensure transcript is valid
            try:
                validate_file(transcript_path, allow_v0=True)
            except Exception as e:
                res = {
                    "transcript": t_name,
                    "status": "fail",
                    "message": f"Transcript validation error: {e}",
                    "diff": None,
                    "detail": str(e),
                }
                results.append(res)
                failed_count += 1
                if on_transcript_result:
                    on_transcript_result(res)
                continue

            replay_path: Optional[Path] = None
            try:
                # 1. Replay transcript against server
                replay_path = await engine.run_replay(
                    transcript_path, server_args=server_args
                )

                with open(replay_path, "r", encoding="utf-8") as f:
                    replay_data = yaml.safe_load(f) or {}

                # 2. Check if replay was marked incomplete
                if (
                    isinstance(replay_data, dict)
                    and replay_data.get("meta", {}).get("incomplete")
                ):
                    reason = replay_data["meta"].get("incomplete_reason", "unknown")
                    res = {
                        "transcript": t_name,
                        "status": "fail",
                        "message": f"Replay was incomplete ({reason})",
                        "diff": None,
                        "detail": f"Replay incomplete: {reason}",
                    }
                    results.append(res)
                    failed_count += 1
                    if on_transcript_result:
                        on_transcript_result(res)
                    continue

                # 3. Diff original transcript against replayed output
                ignore_fields_to_pass = manifest.ignore_fields if diff_mode != "strict" else None
                changes = run_diff(
                    transcript_path,
                    replay_path,
                    mode=diff_mode,
                    ignore_fields=ignore_fields_to_pass,
                )
                has_changes = any(group["changes"] for group in changes.values())

                if has_changes:
                    diff_text = format_text_diff(changes)
                    diff_dict = json.loads(format_json_diff(changes))
                    total_changes = len(diff_dict.get("changes", []))
                    res = {
                        "transcript": t_name,
                        "status": "fail",
                        "message": f"Regression detected ({total_changes} change{'s' if total_changes != 1 else ''})",
                        "diff": diff_dict,
                        "detail": diff_text,
                    }
                    results.append(res)
                    failed_count += 1
                else:
                    res = {
                        "transcript": t_name,
                        "status": "pass",
                        "message": f"Passed ({diff_mode} match)",
                        "diff": None,
                        "detail": None,
                    }
                    results.append(res)
                    passed_count += 1

            except Exception as e:
                res = {
                    "transcript": t_name,
                    "status": "fail",
                    "message": f"Error running transcript: {e}",
                    "diff": None,
                    "detail": str(e),
                }
                results.append(res)
                failed_count += 1

            finally:
                # Cleanup generated replay artifact
                if replay_path and isinstance(replay_path, Path) and replay_path.exists():
                    try:
                        replay_path.unlink(missing_ok=True)
                    except Exception:
                        pass

            if on_transcript_result:
                on_transcript_result(res)

        total = len(manifest.transcripts)
        exit_code = 1 if failed_count > 0 else 0

        return SuiteResult(
            suite_name=manifest.name,
            total=total,
            passed=passed_count,
            failed=failed_count,
            skipped=skipped_count,
            results=results,
            exit_code=exit_code,
        )
