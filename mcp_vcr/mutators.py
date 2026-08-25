import copy
import json
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class Mutation:
    name: str
    strategy: str
    source_message_index: int
    source_method: str
    payload: Optional[Dict[str, Any]] = None
    raw_bytes: Optional[bytes] = None


@dataclass
class MutationSet:
    """All mutations derived from a single source transcript."""
    source_path: str
    mutations: List[Mutation]


def _build_deep_nested_dict(depth: int = 100) -> Dict[str, Any]:
    curr: Dict[str, Any] = {"deep": "val"}
    for _ in range(depth - 1):
        curr = {"a": curr}
    return curr


def generate_mutations(
    c2s_messages: List[Dict[str, Any]],
    tools_schema: Optional[List[Dict[str, Any]]] = None,
    strategies: Optional[Set[str]] = None,
    max_mutations: Optional[int] = None,
    seed: Optional[int] = None,
) -> List[Mutation]:
    """
    Generate structural and byte mutations from client-to-server (c2s) messages.
    """
    valid_strategies = {"field_removal", "type_confusion", "boundary", "truncated"}
    active_strategies = (
        set(strategies) if strategies else valid_strategies
    ) & valid_strategies

    # Map tool_name -> required fields from tools_schema
    tool_required_map: Dict[str, List[str]] = {}
    if tools_schema:
        for tool in tools_schema:
            if isinstance(tool, dict):
                t_name = tool.get("name")
                schema = tool.get("inputSchema", {})
                if t_name and isinstance(schema, dict):
                    req = schema.get("required", [])
                    if isinstance(req, list):
                        tool_required_map[t_name] = [str(r) for r in req if isinstance(r, str)]

    all_mutations: List[Mutation] = []

    # Process in fixed strategy order for determinism
    strategy_order = ["field_removal", "type_confusion", "boundary", "truncated"]

    for strat in strategy_order:
        if strat not in active_strategies:
            continue

        for idx, msg in enumerate(c2s_messages):
            # Extract payload dict
            payload = msg.get("payload") if isinstance(msg, dict) and "payload" in msg else msg
            if not isinstance(payload, dict):
                continue

            method = payload.get("method", "unknown")

            if strat == "field_removal":
                if method == "initialize":
                    params = payload.get("params")
                    if isinstance(params, dict):
                        for field in ["protocolVersion", "capabilities", "clientInfo"]:
                            if field in params:
                                p_copy = copy.deepcopy(payload)
                                p_copy["params"].pop(field, None)
                                all_mutations.append(
                                    Mutation(
                                        name=f"field_removal:params.{field}",
                                        strategy="field_removal",
                                        source_message_index=idx,
                                        source_method=method,
                                        payload=p_copy,
                                    )
                                )
                elif method == "tools/call":
                    params = payload.get("params")
                    if isinstance(params, dict):
                        t_name = params.get("name")
                        args = params.get("arguments")
                        if t_name and isinstance(args, dict):
                            req_fields = tool_required_map.get(t_name, [])
                            for req_f in req_fields:
                                if req_f in args:
                                    p_copy = copy.deepcopy(payload)
                                    p_copy["params"]["arguments"].pop(req_f, None)
                                    all_mutations.append(
                                        Mutation(
                                            name=f"field_removal:arguments.{req_f}",
                                            strategy="field_removal",
                                            source_message_index=idx,
                                            source_method=method,
                                            payload=p_copy,
                                        )
                                    )

            elif strat == "type_confusion":
                if method == "tools/call":
                    params = payload.get("params")
                    if isinstance(params, dict):
                        args = params.get("arguments")
                        if isinstance(args, dict):
                            for key, val in args.items():
                                confusing_values: List[Tuple[str, Any]] = []
                                if isinstance(val, bool):
                                    confusing_values.append(("str", "true"))
                                elif isinstance(val, (int, float)):
                                    confusing_values.append(("str", "not_a_number"))
                                elif isinstance(val, str):
                                    confusing_values.append(("int", 42))
                                elif isinstance(val, list):
                                    confusing_values.append(("dict", {"unexpected": "dict"}))
                                elif isinstance(val, dict):
                                    confusing_values.append(("list", ["unexpected", "list"]))

                                for type_label, conf_val in confusing_values:
                                    p_copy = copy.deepcopy(payload)
                                    p_copy["params"]["arguments"][key] = conf_val
                                    all_mutations.append(
                                        Mutation(
                                            name=f"type_confusion:arguments.{key}:{type_label}",
                                            strategy="type_confusion",
                                            source_message_index=idx,
                                            source_method=method,
                                            payload=p_copy,
                                        )
                                    )

            elif strat == "boundary":
                if method == "tools/call":
                    params = payload.get("params")
                    if isinstance(params, dict):
                        args = params.get("arguments")
                        if isinstance(args, dict):
                            for key, val in args.items():
                                boundary_values: List[Tuple[str, Any]] = []
                                if isinstance(val, bool):
                                    pass
                                elif isinstance(val, (int, float)):
                                    boundary_values.extend([
                                        ("zero", 0),
                                        ("negative", -1),
                                        ("max_safe_int", 2**53),
                                        ("min_safe_int", -(2**53)),
                                    ])
                                elif isinstance(val, str):
                                    boundary_values.extend([
                                        ("empty_string", ""),
                                        ("1mb_string", "A" * 1_000_000),
                                    ])
                                elif isinstance(val, list):
                                    boundary_values.extend([
                                        ("empty_list", []),
                                        ("oversized_list", [None] * 10_000),
                                    ])
                                elif isinstance(val, dict):
                                    boundary_values.extend([
                                        ("empty_dict", {}),
                                        ("deep_nested", _build_deep_nested_dict(100)),
                                    ])

                                for label, b_val in boundary_values:
                                    p_copy = copy.deepcopy(payload)
                                    p_copy["params"]["arguments"][key] = b_val
                                    all_mutations.append(
                                        Mutation(
                                            name=f"boundary:arguments.{key}:{label}",
                                            strategy="boundary",
                                            source_message_index=idx,
                                            source_method=method,
                                            payload=p_copy,
                                        )
                                    )

            elif strat == "truncated":
                try:
                    raw_str = json.dumps(payload, sort_keys=True)
                    raw_bytes = raw_str.encode("utf-8")
                except Exception:
                    continue

                variants: List[Tuple[str, bytes]] = [
                    ("50percent", raw_bytes[: len(raw_bytes) // 2]),
                    ("after_opening_brace", b"{"),
                    ("empty_bytes", b""),
                    ("trailing_garbage", raw_bytes + b"}}}}"),
                ]

                for label, r_bytes in variants:
                    all_mutations.append(
                        Mutation(
                            name=f"truncated:{label}",
                            strategy="truncated",
                            source_message_index=idx,
                            source_method=method,
                            payload=None,
                            raw_bytes=r_bytes,
                        )
                    )

    if seed is not None:
        rng = random.Random(seed)
        rng.shuffle(all_mutations)

    if max_mutations is not None and max_mutations >= 0:
        all_mutations = all_mutations[:max_mutations]

    return all_mutations
