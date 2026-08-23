import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .generator import DiscoveryResult, GeneratorEngine
from .transports.base import Transport

SEVERITY_LEVELS = {"high": 3, "medium": 2, "low": 1, "info": 0}

SENSITIVE_ROOTS = {"token", "secret", "password", "credential", "apikey", "bearer", "key"}
BENIGN_METADATA_SUFFIXES = {
    "count", "limit", "num", "total", "index", "length", "size",
    "type", "format", "version", "timestamp", "time", "date", "status",
    "state", "list", "name", "url", "path", "id", "hash"
}
BENIGN_METADATA_PREFIXES = {
    "max", "min", "total", "num", "number", "sum", "average", "avg", "count"
}

# Injection indicators in tool descriptions
INJECTION_PATTERNS = [
    (re.compile(r"\bignore\s+previous\b", re.IGNORECASE), "instruction override 'ignore previous'"),
    (re.compile(r"\bdisregard\s+(?:all\s+)?previous\b", re.IGNORECASE), "instruction override 'disregard previous'"),
    (re.compile(r"\byou\s+are\s+now\b", re.IGNORECASE), "role override 'you are now'"),
    (re.compile(r"\bsystem\s+prompt\b", re.IGNORECASE), "system prompt injection indicator"),
    (re.compile(r"\bdo\s+not\s+follow\b", re.IGNORECASE), "instruction override 'do not follow'"),
    (re.compile(r"\bforget\s+(?:your\s+)?instructions\b", re.IGNORECASE), "instruction override 'forget instructions'"),
    (re.compile(r"<(?:script|iframe|img\s+onerror)\b", re.IGNORECASE), "HTML code injection element"),
    (re.compile(r"javascript:", re.IGNORECASE), "javascript URI scheme"),
    (re.compile(r"\[\]\(https?://[^\s\)]+\)", re.IGNORECASE), "deceptive empty-label markdown link"),
    (re.compile(r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]"), "hidden/directional unicode control character"),
]

# Literal secret regexes
SECRET_REGEXES = [
    (re.compile(r"sk-[a-zA-Z0-9_-]{20,}"), "OpenAI/Provider API Key"),
    (re.compile(r"Bearer\s+eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]*"), "JWT Bearer Token"),
    (re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}"), "AWS Access Key ID"),
    (re.compile(r"(?:api[_-]?key|secret|password)\s*[:=]\s*[\"']?([a-zA-Z0-9_\-]{16,})[\"']?", re.IGNORECASE), "Key-Value Secret Assignment"),
]

PLACEHOLDER_SUBSTRINGS = {
    "your", "key", "here", "changeme", "example", "placeholder",
    "rotate", "dummy", "xxx", "12345678", "<", ">", "foo", "bar"
}


def normalize_property_name(prop_name: str) -> Tuple[str, List[str]]:
    """Normalize property name into snake_case and tokens with acronym support."""
    s0 = re.sub(r"(?i)OAuth", "oauth", prop_name)
    s1 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s0)
    s2 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s1)
    snake_case = s2.lower().replace("-", "_")
    raw_tokens = [t for t in snake_case.split("_") if t]
    tokens = [
        t[:-1] if t.endswith("s") and len(t) > 1 and t[:-1] in SENSITIVE_ROOTS else t
        for t in raw_tokens
    ]
    return snake_case, tokens


def is_sensitive_property_name(prop_name: str) -> bool:
    """Return True if property name indicates a secret input material rather than benign metadata."""
    snake_case, tokens = normalize_property_name(prop_name)

    for i, token in enumerate(tokens):
        is_root = token in SENSITIVE_ROOTS or (
            token == "api" and i + 1 < len(tokens) and tokens[i + 1] == "key"
        )
        if is_root:
            prev_token = tokens[i - 1] if i > 0 else None
            next_idx = i + 2 if (token == "api" and i + 1 < len(tokens) and tokens[i + 1] == "key") else i + 1
            next_token = tokens[next_idx] if next_idx < len(tokens) else None

            if prev_token and prev_token in BENIGN_METADATA_PREFIXES:
                continue
            if next_token and next_token in BENIGN_METADATA_SUFFIXES:
                continue
            return True

    return False



def calculate_shannon_entropy(text: str) -> float:
    """Calculate Shannon entropy of a string (bits per character)."""
    if not text:
        return 0.0
    prob = [float(text.count(c)) / len(text) for c in set(text)]
    return -sum(p * math.log2(p) for p in prob)


def is_placeholder_secret(text: str) -> bool:
    """Check if matched string is a documentation placeholder rather than a real secret."""
    lowered = text.lower()
    if any(p in lowered for p in PLACEHOLDER_SUBSTRINGS):
        return True
    if len(text) >= 16 and calculate_shannon_entropy(text) < 3.0:
        return True
    return False


@dataclass
class AuditFinding:
    check: str
    severity: str
    tool: Optional[str]
    message: str
    detail: Optional[str] = None


@dataclass
class AuditResult:
    server_info: Dict[str, Any]
    protocol_version: str
    tools_discovered: int
    severity_filter: str
    findings: List[AuditFinding]
    checks_run: List[str]
    summary: Dict[str, int]
    raw_summary: Dict[str, int]
    exit_code: int


def _extract_schema_properties(schema: Dict[str, Any], path_prefix: str = "") -> List[Tuple[str, Dict[str, Any]]]:
    """Recursively traverse a JSON Schema dictionary and return (property_path, prop_schema) pairs."""
    results: List[Tuple[str, Dict[str, Any]]] = []
    if not isinstance(schema, dict):
        return results

    props = schema.get("properties")
    if isinstance(props, dict):
        for p_name, p_schema in props.items():
            if isinstance(p_schema, dict):
                current_path = f"{path_prefix}.{p_name}" if path_prefix else str(p_name)
                results.append((current_path, p_schema))
                # Recurse into property schema (handles nested properties, items, composition, etc.)
                results.extend(_extract_schema_properties(p_schema, path_prefix=current_path))

    items = schema.get("items")
    if isinstance(items, dict):
        results.extend(_extract_schema_properties(items, path_prefix=f"{path_prefix}[]" if path_prefix else "[]"))

    for comp_key in ("allOf", "anyOf", "oneOf"):
        comp_list = schema.get(comp_key)
        if isinstance(comp_list, list):
            for idx, comp_schema in enumerate(comp_list):
                if isinstance(comp_schema, dict):
                    comp_path = f"{path_prefix}.{comp_key}[{idx}]" if path_prefix else f"{comp_key}[{idx}]"
                    results.extend(_extract_schema_properties(comp_schema, path_prefix=comp_path))

    for defs_key in ("$defs", "definitions"):
        defs = schema.get(defs_key)
        if isinstance(defs, dict):
            for def_name, def_schema in defs.items():
                if isinstance(def_schema, dict):
                    def_path = f"{defs_key}.{def_name}"
                    results.extend(_extract_schema_properties(def_schema, path_prefix=def_path))

    return results



def check_description_injection(tools: List[Dict[str, Any]]) -> List[AuditFinding]:
    """Scan tool name, description, and input schema descriptions for prompt injection indicators."""
    findings: List[AuditFinding] = []
    for tool in tools:
        t_name = tool.get("name", "unnamed")
        fields_to_check = [
            ("tool description", tool.get("description", "")),
        ]
        schema = tool.get("inputSchema", {})
        if isinstance(schema, dict):
            extracted = _extract_schema_properties(schema)
            for p_path, p_schema in extracted:
                if "description" in p_schema and isinstance(p_schema["description"], str):
                    fields_to_check.append(
                        (f"property '{p_path}' description", p_schema["description"])
                    )

        for field_label, text in fields_to_check:
            if not text or not isinstance(text, str):
                continue
            for pattern, desc in INJECTION_PATTERNS:
                match = pattern.search(text)
                if match:
                    matched_snippet = match.group(0)
                    findings.append(
                        AuditFinding(
                            check="description-injection",
                            severity="high",
                            tool=t_name,
                            message=f"Tool {field_label} contains prompt injection pattern ({desc})",
                            detail=f"matched: \"{matched_snippet}\"",
                        )
                    )
    return findings


def check_sensitive_field_exposure(tools: List[Dict[str, Any]]) -> List[AuditFinding]:
    """Scan tool input schemas for sensitive property names and embedded literal secrets."""
    findings: List[AuditFinding] = []
    for tool in tools:
        t_name = tool.get("name", "unnamed")
        schema = tool.get("inputSchema", {})
        if not isinstance(schema, dict):
            continue

        extracted = _extract_schema_properties(schema)
        for p_path, p_schema in extracted:
            leaf_name = p_path.split(".")[-1].replace("[]", "")
            if is_sensitive_property_name(leaf_name):
                findings.append(
                    AuditFinding(
                        check="sensitive-field-exposure",
                        severity="medium",
                        tool=t_name,
                        message=f"Input schema property '{p_path}' indicates secret handling",
                        detail=f"Property name '{p_path}' matches sensitive field patterns",
                    )
                )

            # Check defaults and descriptions for literal secrets
            values_to_check = []
            if "default" in p_schema and isinstance(p_schema["default"], str):
                values_to_check.append(("default value", p_schema["default"]))
            if "description" in p_schema and isinstance(p_schema["description"], str):
                values_to_check.append(("description", p_schema["description"]))

            for source_label, text_val in values_to_check:
                for pattern, secret_type in SECRET_REGEXES:
                    for match in pattern.finditer(text_val):
                        candidate = match.group(1) if match.groups() else match.group(0)
                        if not is_placeholder_secret(candidate):
                            findings.append(
                                AuditFinding(
                                    check="sensitive-field-exposure",
                                    severity="high",
                                    tool=t_name,
                                    message=f"Property '{p_path}' {source_label} contains literal {secret_type}",
                                    detail=f"Found unredacted {secret_type}",
                                )
                            )

    return findings



def check_capability_declarations(capabilities: Dict[str, Any]) -> List[AuditFinding]:
    """Report advertised capabilities as informational findings."""
    findings: List[AuditFinding] = []
    if not isinstance(capabilities, dict):
        return findings

    if "resources" in capabilities and isinstance(capabilities["resources"], dict):
        if capabilities["resources"].get("subscribe"):
            findings.append(
                AuditFinding(
                    check="capability-declaration",
                    severity="info",
                    tool=None,
                    message="Server advertises resources.subscribe capability. Unverified in passive mode.",
                )
            )

    if "tools" in capabilities and isinstance(capabilities["tools"], dict):
        if capabilities["tools"].get("listChanged"):
            findings.append(
                AuditFinding(
                    check="capability-declaration",
                    severity="info",
                    tool=None,
                    message="Server advertises tools.listChanged capability. Unverified in passive mode.",
                )
            )

    if "logging" in capabilities:
        findings.append(
            AuditFinding(
                check="capability-declaration",
                severity="info",
                tool=None,
                message="Server advertises logging capability.",
            )
        )

    return findings


class AuditEngine:
    """Orchestrates passive security audit against an MCP server."""

    def __init__(self, timeout_ms: int = 10000):
        self.timeout_ms = timeout_ms

    async def run_passive_audit(
        self,
        transport: Transport,
        severity_filter: str = "info",
    ) -> AuditResult:
        if severity_filter not in SEVERITY_LEVELS:
            raise ValueError(f"Invalid severity filter '{severity_filter}'. Must be one of: {list(SEVERITY_LEVELS.keys())}")

        generator = GeneratorEngine()
        try:
            discovery = await generator.discover(transport, timeout_ms=self.timeout_ms)
        finally:
            await transport.shutdown()

        all_findings: List[AuditFinding] = []
        all_findings.extend(check_description_injection(discovery.tools))
        all_findings.extend(check_sensitive_field_exposure(discovery.tools))
        all_findings.extend(check_capability_declarations(discovery.capabilities))

        checks_run = ["description-injection", "sensitive-field-exposure", "capability-declaration"]

        # Calculate raw summary
        raw_summary = {"total": len(all_findings), "high": 0, "medium": 0, "low": 0, "info": 0}
        for f in all_findings:
            if f.severity in raw_summary:
                raw_summary[f.severity] += 1

        # Apply severity filter
        min_level = SEVERITY_LEVELS[severity_filter]
        filtered_findings = [f for f in all_findings if SEVERITY_LEVELS.get(f.severity, 0) >= min_level]

        summary = {"total": len(filtered_findings), "high": 0, "medium": 0, "low": 0, "info": 0}
        for f in filtered_findings:
            if f.severity in summary:
                summary[f.severity] += 1

        # Exit code logic: 1 if any high or medium finding in the filtered results
        exit_code = 1 if (summary["high"] > 0 or summary["medium"] > 0) else 0

        return AuditResult(
            server_info=discovery.server_info,
            protocol_version=discovery.protocol_version,
            tools_discovered=len(discovery.tools),
            severity_filter=severity_filter,
            findings=filtered_findings,
            checks_run=checks_run,
            summary=summary,
            raw_summary=raw_summary,
            exit_code=exit_code,
        )
