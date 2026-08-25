from .schema import Transcript, Message, Metadata, Direction
from .validator import validate_file, validate_transcript
from .interceptor import MessageInterceptor
from .redactor import Redactor
from .normalizer import (
    Normalizer,
    NormalizerChain,
    TimestampNormalizer,
    RequestIdNormalizer,
    UuidNormalizer,
    CursorNormalizer
)
from .config import Config, ConfigError
from .formats import detect_format, iter_messages, load_meta
from .transports import (
    Transport,
    StdioTransport,
    run_proxy_with_transport,
)

def __getattr__(name: str):
    if name == "SseTransport":
        from .transports import SseTransport
        return SseTransport
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
from .transports.stdio import (
    get_stdin_reader,
    launch_server,
    pump_c2s,
    pump_s2c,
    pump_stderr,
    run_proxy,
)
from .replay import ReplayEngine
from .generator import GeneratorEngine, DiscoveryResult, ToolCallResult
from .auditor import (
    AuditEngine,
    AuditFinding,
    AuditResult,
    is_sensitive_property_name,
    normalize_property_name,
)
from .mutators import Mutation, MutationSet, generate_mutations
from .fuzzer import FuzzEngine, FuzzResult, FuzzCaseResult

__all__ = [
    "Transcript",
    "Message",
    "Metadata",
    "Direction",
    "validate_file",
    "validate_transcript",
    "MessageInterceptor",
    "Redactor",
    "Normalizer",
    "NormalizerChain",
    "TimestampNormalizer",
    "RequestIdNormalizer",
    "UuidNormalizer",
    "CursorNormalizer",
    "Config",
    "ConfigError",
    "detect_format",
    "iter_messages",
    "load_meta",
    "Transport",
    "StdioTransport",
    "SseTransport",
    "run_proxy_with_transport",
    "get_stdin_reader",
    "launch_server",
    "pump_c2s",
    "pump_s2c",
    "pump_stderr",
    "run_proxy",
    "ReplayEngine",
    "GeneratorEngine",
    "DiscoveryResult",
    "ToolCallResult",
    "AuditEngine",
    "AuditFinding",
    "AuditResult",
    "is_sensitive_property_name",
    "normalize_property_name",
    "Mutation",
    "MutationSet",
    "generate_mutations",
    "FuzzEngine",
    "FuzzResult",
    "FuzzCaseResult",
]



