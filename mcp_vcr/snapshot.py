import copy
import json
import logging
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import click
import yaml

from .diff import run_diff, format_text_diff, format_json_diff
from .normalizer import NormalizerChain
from .replay import ReplayEngine
from .validator import validate_file

logger = logging.getLogger("mcp-vcr.snapshot")

def normalize_transcript_data(data: Dict[str, Any], chain: Optional[NormalizerChain] = None) -> Dict[str, Any]:
    """Recursively deep-copies and applies the NormalizerChain to all message payloads in a transcript."""
    if chain is None:
        chain = NormalizerChain.from_config()
        
    normalized = copy.deepcopy(data)
    messages = normalized.get("messages", [])
    for msg in messages:
        if "payload" in msg and msg["payload"] is not None:
            msg["payload"] = chain.apply(msg["payload"])
            
    return normalized

def find_source_session(golden_path: Path) -> Optional[Path]:
    """Heuristically locate the original session transcript file."""
    golden_name = golden_path.stem
    if golden_name.endswith("_golden"):
        base_name = golden_name[:-7]
    else:
        base_name = golden_name
        
    for sessions_dir in (golden_path.parent.parent / "sessions", Path("sessions")):
        if not sessions_dir.exists() or not sessions_dir.is_dir():
            continue

        for ext in (".yaml", ".yml"):
            p = sessions_dir / f"{base_name}{ext}"
            if p.exists():
                return p

        for p in sessions_dir.glob("*"):
            if p.is_file() and p.suffix in (".yaml", ".yml"):
                candidate = p.stem
                if candidate.startswith("session_"):
                    candidate = candidate[len("session_"):]
                if "-replay-" in candidate:
                    candidate = candidate.split("-replay-", 1)[0]
                if candidate == base_name or candidate.endswith(f"_{base_name}"):
                    return p
                    
    # Fallback to None with a warning log referencing the golden_path symbol
    logger.warning("Could not find source session transcript for golden snapshot: %s", golden_path)
    return None

def run_snapshot(session_yaml_path: Path) -> Path:
    """Apply normalizer chain and write golden snapshot to snapshots/ directory."""
    with open(session_yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
        
    normalized_data = normalize_transcript_data(data)
    
    snapshots_dir = Path("snapshots")
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    
    stem = session_yaml_path.stem
    if stem.startswith("session_"):
        session_id = data.get("meta", {}).get("session_id", stem)
        if "-replay-" in session_id:
            session_id = session_id.split("-replay-")[0]
        name = session_id
    else:
        name = stem
        if "-replay-" in name:
            name = name.split("-replay-")[0]

    if Path(name).name != name or name in {"", ".", ".."}:
        raise click.ClickException(f"Invalid snapshot name derived from session: {name!r}")

    golden_path = snapshots_dir / f"{name}_golden.yaml"
    
    # Write full, deterministic transcript with stable key ordering
    temp_path = golden_path.with_name(f".{golden_path.name}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(normalized_data, f, sort_keys=True, default_flow_style=False)
        validate_file(temp_path)
        temp_path.replace(golden_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    
    return golden_path


async def _run_verify_impl(
    snapshots_dir: Path,
    server_args: Optional[List[str]] = None,
    update: bool = False,
    timing_faithful: Optional[bool] = None,
    config_path: Optional[Path] = None
) -> Dict[str, Any]:
    """Core verification logic. Returns structured results dictionary."""
    p_snapshots = Path(snapshots_dir)
    if not p_snapshots.exists():
        click.secho(f"ERROR: Path '{snapshots_dir}' does not exist.", fg="red", err=True)
        return {
            "results": [],
            "summary": {"total": 0, "passed": 0, "failed": 0, "updated": 0, "unchanged": 0},
            "exit_code": 1,
            "error": f"Path '{snapshots_dir}' does not exist."
        }
        
    if p_snapshots.is_file():
        golden_files = [p_snapshots]
    else:
        golden_files = sorted(list(p_snapshots.glob("*_golden.yaml")) + list(p_snapshots.glob("*_golden.yml")))
        if not golden_files:
            # Fallback to standard yaml files
            golden_files = sorted(list(p_snapshots.glob("*.yaml")) + list(p_snapshots.glob("*.yml")))
            
    if not golden_files:
        click.secho(f"ERROR: No snapshots found in '{snapshots_dir}'", fg="red", err=True)
        return {
            "results": [],
            "summary": {"total": 0, "passed": 0, "failed": 0, "updated": 0, "unchanged": 0},
            "exit_code": 1,
            "error": f"No snapshots found in '{snapshots_dir}'"
        }
        
    engine = ReplayEngine(config_path=config_path, timing_faithful=timing_faithful)
    
    passed_count = 0
    failed_count = 0
    updated_count = 0
    unchanged_count = 0
    
    structured_results: List[Dict[str, Any]] = []
    
    for golden_path in golden_files:
        source_path = find_source_session(golden_path)
        if source_path is None:
            click.secho(f"WARNING: Source session for {golden_path.name} not found in sessions/. Replaying golden snapshot itself as fallback.", fg="yellow", err=True)
            source_path = golden_path
            
        click.secho(f"Verifying snapshot: {golden_path.name} (source: {source_path.name})", fg="cyan", err=True)
        
        replay_path = None
        normalized_replay_path = None
        try:
            # 1. Replay original transcript
            replay_path = await engine.run_replay(source_path, server_args=server_args)
            
            with open(replay_path, "r", encoding="utf-8") as f:
                replay_data = yaml.safe_load(f) or {}
                
            # 2. Check if replay was incomplete (treated as failure)
            if replay_data.get("meta", {}).get("incomplete"):
                reason = replay_data["meta"].get("incomplete_reason", "unknown")
                structured_results.append({
                    "snapshot": golden_path.name,
                    "source": source_path.name,
                    "status": "fail",
                    "message": f"Replay was incomplete due to: {reason}",
                    "diff": None,
                    "detail": None
                })
                failed_count += 1
                continue
                
            # 3. Normalize the replayed transcript
            normalized_replay_data = normalize_transcript_data(replay_data)
            
            # Save temporary normalized transcript
            normalized_replay_path = replay_path.parent / f"{replay_path.stem}_normalized.yaml"
            with open(normalized_replay_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(normalized_replay_data, f, sort_keys=True, default_flow_style=False)
                
            # 4. Diff normalized replay vs golden snapshot
            changes = run_diff(golden_path, normalized_replay_path, mode="semantic")
            has_changes = any(group["changes"] for group in changes.values())
            
            if update:
                if has_changes:
                    # Reconstruct full transcript by merging original C2S with new S2C
                    with open(source_path, "r", encoding="utf-8") as f:
                        source_data = yaml.safe_load(f) or {}
                        
                    merged_messages = []
                    for msg in source_data.get("messages", []):
                        if msg.get("dir") == "c2s":
                            merged_messages.append(msg)
                            
                    merged_messages.extend([m for m in normalized_replay_data.get("messages", []) if m.get("dir") == "s2c"])
                    merged_messages.sort(key=lambda x: x.get("t", 0))
                    
                    new_golden_data = copy.deepcopy(normalized_replay_data)
                    new_golden_data["messages"] = merged_messages
                    
                    # Overwrite golden file
                    temp_path = golden_path.with_name(f".{golden_path.name}.tmp")
                    try:
                        with open(temp_path, "w", encoding="utf-8") as f:
                            yaml.safe_dump(new_golden_data, f, sort_keys=True, default_flow_style=False)
                        validate_file(temp_path)
                        temp_path.replace(golden_path)
                    except Exception:
                        temp_path.unlink(missing_ok=True)
                        raise
                    structured_results.append({
                        "snapshot": golden_path.name,
                        "source": source_path.name,
                        "status": "updated",
                        "message": "Golden snapshot updated with new replayed responses",
                        "diff": None,
                        "detail": None
                    })
                    updated_count += 1
                else:
                    structured_results.append({
                        "snapshot": golden_path.name,
                        "source": source_path.name,
                        "status": "unchanged",
                        "message": "Golden snapshot unchanged",
                        "diff": None,
                        "detail": None
                    })
                    unchanged_count += 1
            else:
                if has_changes:
                    diff_text = format_text_diff(changes)
                    diff_dict = json.loads(format_json_diff(changes))
                    structured_results.append({
                        "snapshot": golden_path.name,
                        "source": source_path.name,
                        "status": "fail",
                        "message": "Regression detected",
                        "diff": diff_dict,
                        "detail": diff_text
                    })
                    failed_count += 1
                else:
                    structured_results.append({
                        "snapshot": golden_path.name,
                        "source": source_path.name,
                        "status": "pass",
                        "message": "Golden snapshot matches replayed responses",
                        "diff": None,
                        "detail": None
                    })
                    passed_count += 1
                    
        except Exception as e:
            tb = traceback.format_exc()
            structured_results.append({
                "snapshot": golden_path.name,
                "source": source_path.name if source_path else None,
                "status": "fail",
                "message": f"Verification encountered an error: {e}",
                "diff": None,
                "detail": tb
            })
            failed_count += 1
        finally:
            if replay_path is not None:
                p = Path(replay_path)
                if p.exists():
                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass
            if normalized_replay_path is not None:
                p_norm = Path(normalized_replay_path)
                if p_norm.exists():
                    try:
                        p_norm.unlink(missing_ok=True)
                    except Exception:
                        pass

    exit_code = 1 if failed_count > 0 else 0
    return {
        "results": structured_results,
        "summary": {
            "total": len(golden_files),
            "passed": passed_count,
            "failed": failed_count,
            "updated": updated_count,
            "unchanged": unchanged_count
        },
        "exit_code": exit_code
    }


def _print_verify_summary(result_data: Dict[str, Any], update: bool = False) -> None:
    """Print human-readable final summary."""
    click.echo("\n--- Snapshot Summary ---")
    for item in result_data.get("results", []):
        name = item.get("snapshot", "")
        status = item.get("status", "")
        msg = item.get("message", "")
        detail = item.get("detail", "")
        if status == "pass":
            click.secho(f"PASS: {name}", fg="green")
        elif status == "updated":
            click.secho(f"UPDATED: {name} ({msg})", fg="yellow")
        elif status == "unchanged":
            click.secho(f"UNCHANGED: {name}", fg="green")
        else:
            click.secho(f"FAIL: {name} ({msg})", fg="red", err=True)
            if detail:
                click.echo(detail, err=True)
                
    summary = result_data.get("summary", {})
    failed_count = summary.get("failed", 0)
    updated_count = summary.get("updated", 0)
    unchanged_count = summary.get("unchanged", 0)
    passed_count = summary.get("passed", 0)
    total = summary.get("total", 0)

    if update:
        if failed_count > 0:
            click.secho(f"\n{failed_count} snapshots failed during update. {updated_count} updated, {unchanged_count} unchanged.", fg="red", err=True)
        else:
            click.secho(f"\nAll snapshots processed in update mode. {updated_count} updated, {unchanged_count} unchanged.", fg="green")
    else:
        if failed_count > 0:
            click.secho(f"\nFAIL: {failed_count} of {total} snapshots failed verification.", fg="red", err=True)
        else:
            click.secho(f"\nAll {passed_count} snapshots passed.", fg="green")


async def run_verify(
    snapshots_dir: Path,
    server_args: Optional[List[str]] = None,
    update: bool = False,
    timing_faithful: Optional[bool] = None,
    config_path: Optional[Path] = None
) -> int:
    """Backward-compatible wrapper. Returns exit code only."""
    result = await _run_verify_impl(
        snapshots_dir=snapshots_dir,
        server_args=server_args,
        update=update,
        timing_faithful=timing_faithful,
        config_path=config_path
    )
    if "error" not in result or result["results"]:
        _print_verify_summary(result, update=update)
    return result["exit_code"]

