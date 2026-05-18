import copy
import click
import sys
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional
from .normalizer import NormalizerChain
from .replay import ReplayEngine
from .diff import run_diff, format_text_diff
from .validator import validate_file

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

def find_source_session(golden_path: Path) -> Path:
    """Heuristically locate the original session transcript file. Fallback to the golden snapshot itself."""
    golden_name = golden_path.stem
    if golden_name.endswith("_golden"):
        base_name = golden_name[:-7]
    else:
        base_name = golden_name
        
    sessions_dir = Path("sessions")
    if sessions_dir.exists() and sessions_dir.is_dir():
        # 1. Check exact match: sessions/base_name.yaml or .yml
        for ext in (".yaml", ".yml"):
            p = sessions_dir / f"{base_name}{ext}"
            if p.exists():
                return p
                
        # 2. Check prefix/suffix matches
        for p in sessions_dir.glob("*"):
            if p.is_file() and p.suffix in (".yaml", ".yml"):
                if p.stem.endswith(f"_{base_name}") or p.stem == base_name:
                    return p
                    
    # Fallback to the golden snapshot file itself
    return golden_path

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
            
    golden_path = snapshots_dir / f"{name}_golden.yaml"
    
    # Write full, deterministic transcript with stable key ordering
    with open(golden_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(normalized_data, f, sort_keys=True, default_flow_style=False)
        
    # Verify golden against the schema
    validate_file(golden_path)
    
    return golden_path

async def run_verify(
    snapshots_dir: Path,
    server_args: Optional[List[str]] = None,
    update: bool = False
) -> int:
    """Replay sessions against a server, diff normalized results vs golden snapshots, and report regressions or update goldens."""
    p_snapshots = Path(snapshots_dir)
    
    golden_files = sorted(list(p_snapshots.glob("*_golden.yaml")) + list(p_snapshots.glob("*_golden.yml")))
    if not golden_files:
        click.secho(f"No golden snapshots found in '{snapshots_dir}'", fg="yellow")
        return 0
        
    engine = ReplayEngine()
    
    passed_count = 0
    failed_count = 0
    updated_count = 0
    unchanged_count = 0
    
    results = {}
    
    for golden_path in golden_files:
        source_path = find_source_session(golden_path)
        click.secho(f"Verifying snapshot: {golden_path.name} (source: {source_path.name})", fg="cyan")
        
        try:
            # 1. Replay original transcript
            replay_path = await engine.run_replay(source_path, server_args=server_args)
            
            with open(replay_path, "r", encoding="utf-8") as f:
                replay_data = yaml.safe_load(f) or {}
                
            # 2. Check if replay was incomplete (treated as failure)
            if replay_data.get("meta", {}).get("incomplete"):
                reason = replay_data["meta"].get("incomplete_reason", "unknown")
                results[golden_path] = ("fail", f"Replay was incomplete due to: {reason}", None)
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
                    # Overwrite golden file
                    with open(golden_path, "w", encoding="utf-8") as f:
                        yaml.safe_dump(normalized_replay_data, f, sort_keys=True, default_flow_style=False)
                    results[golden_path] = ("updated", "Golden snapshot updated with new replayed responses", None)
                    updated_count += 1
                else:
                    results[golden_path] = ("unchanged", "Golden snapshot unchanged", None)
                    unchanged_count += 1
            else:
                if has_changes:
                    diff_text = format_text_diff(changes)
                    results[golden_path] = ("fail", "Regression detected", diff_text)
                    failed_count += 1
                else:
                    results[golden_path] = ("pass", "Golden snapshot matches replayed responses", None)
                    passed_count += 1
                    
        except Exception as e:
            results[golden_path] = ("fail", f"Verification encountered an error: {e}", None)
            failed_count += 1
            
    # Print results and summary
    click.echo("\n--- Snapshot Summary ---")
    for golden, (status, msg, detail) in results.items():
        if status == "pass":
            click.secho(f"PASS: {golden.name}", fg="green")
        elif status == "updated":
            click.secho(f"UPDATED: {golden.name} ({msg})", fg="yellow")
        elif status == "unchanged":
            click.secho(f"UNCHANGED: {golden.name}", fg="green")
        else:
            click.secho(f"FAIL: {golden.name} ({msg})", fg="red", err=True)
            if detail:
                click.echo(detail, err=True)
                
    if update:
        if failed_count > 0:
            click.secho(f"\n{failed_count} snapshots failed during update. {updated_count} updated, {unchanged_count} unchanged.", fg="red", err=True)
            return 1
        click.secho(f"\nAll snapshots processed in update mode. {updated_count} updated, {unchanged_count} unchanged.", fg="green")
        return 0
    else:
        if failed_count > 0:
            click.secho(f"\nFAIL: {failed_count} of {len(golden_files)} snapshots failed verification.", fg="red", err=True)
            return 1
        else:
            click.secho(f"\nAll {passed_count} snapshots passed.", fg="green")
            return 0
