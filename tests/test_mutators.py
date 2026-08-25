import pytest
from mcp_vcr.mutators import Mutation, generate_mutations


def test_field_removal_generates_one_variant_per_required_field():
    c2s_messages = [
        {
            "payload": {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {
                    "name": "search",
                    "arguments": {"query": "foo", "limit": 5, "filter": "all"},
                },
            }
        }
    ]
    tools_schema = [
        {
            "name": "search",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                    "filter": {"type": "string"},
                },
                "required": ["query", "limit", "filter"],
            },
        }
    ]

    mutations = generate_mutations(
        c2s_messages=c2s_messages,
        tools_schema=tools_schema,
        strategies={"field_removal"},
    )

    assert len(mutations) == 3
    for m in mutations:
        assert m.strategy == "field_removal"
        assert m.source_method == "tools/call"
        assert m.payload is not None
        assert "params" in m.payload
        # Each mutation has exactly 2 arguments left instead of 3
        assert len(m.payload["params"]["arguments"]) == 2

    removed_fields = [m.name.split(".")[-1] for m in mutations]
    assert set(removed_fields) == {"query", "limit", "filter"}


def test_field_removal_skips_messages_without_required_fields():
    c2s_messages = [
        {
            "payload": {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {"name": "search", "arguments": {"query": "foo"}},
            }
        }
    ]
    tools_schema = [
        {
            "name": "search",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": [],
            },
        }
    ]

    mutations = generate_mutations(
        c2s_messages=c2s_messages,
        tools_schema=tools_schema,
        strategies={"field_removal"},
    )
    assert len(mutations) == 0


def test_field_removal_uses_tools_schema_for_tool_lookup():
    c2s_messages = [
        {
            "payload": {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {"name": "tool_b", "arguments": {"arg1": "v1", "arg2": "v2"}},
            }
        }
    ]
    tools_schema = [
        {
            "name": "tool_a",
            "inputSchema": {
                "type": "object",
                "properties": {"arg1": {"type": "string"}},
                "required": ["arg1"],
            },
        },
        {
            "name": "tool_b",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "arg1": {"type": "string"},
                    "arg2": {"type": "string"},
                },
                "required": ["arg2"],
            },
        },
    ]

    mutations = generate_mutations(
        c2s_messages=c2s_messages,
        tools_schema=tools_schema,
        strategies={"field_removal"},
    )
    assert len(mutations) == 1
    assert mutations[0].name == "field_removal:arguments.arg2"


def test_type_confusion_swaps_all_argument_types():
    c2s_messages = [
        {
            "payload": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "test_tool",
                    "arguments": {
                        "str_arg": "hello",
                        "int_arg": 123,
                        "bool_arg": True,
                        "list_arg": [1, 2],
                        "dict_arg": {"a": "b"},
                    },
                },
            }
        }
    ]

    mutations = generate_mutations(
        c2s_messages=c2s_messages,
        strategies={"type_confusion"},
    )

    names = {m.name: m.payload["params"]["arguments"] for m in mutations}
    assert "type_confusion:arguments.str_arg:int" in names
    assert names["type_confusion:arguments.str_arg:int"]["str_arg"] == 42

    assert "type_confusion:arguments.int_arg:str" in names
    assert names["type_confusion:arguments.int_arg:str"]["int_arg"] == "not_a_number"

    assert "type_confusion:arguments.bool_arg:str" in names
    assert names["type_confusion:arguments.bool_arg:str"]["bool_arg"] == "true"

    assert "type_confusion:arguments.list_arg:dict" in names
    assert names["type_confusion:arguments.list_arg:dict"]["list_arg"] == {"unexpected": "dict"}

    assert "type_confusion:arguments.dict_arg:list" in names
    assert names["type_confusion:arguments.dict_arg:list"]["dict_arg"] == ["unexpected", "list"]


def test_boundary_integers():
    c2s_messages = [
        {
            "payload": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "test_tool", "arguments": {"num": 10}},
            }
        }
    ]

    mutations = generate_mutations(
        c2s_messages=c2s_messages,
        strategies={"boundary"},
    )

    vals = [m.payload["params"]["arguments"]["num"] for m in mutations]
    assert 0 in vals
    assert -1 in vals
    assert 2**53 in vals
    assert -(2**53) in vals


def test_boundary_large_string():
    c2s_messages = [
        {
            "payload": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "test_tool", "arguments": {"text": "hello"}},
            }
        }
    ]

    mutations = generate_mutations(
        c2s_messages=c2s_messages,
        strategies={"boundary"},
    )

    str_muts = [m for m in mutations if m.name.endswith(":1mb_string")]
    assert len(str_muts) == 1
    assert len(str_muts[0].payload["params"]["arguments"]["text"]) == 1_000_000


def test_truncated_produces_raw_bytes():
    c2s_messages = [
        {
            "payload": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "test_tool", "arguments": {"text": "hello"}},
            }
        }
    ]

    mutations = generate_mutations(
        c2s_messages=c2s_messages,
        strategies={"truncated"},
    )

    assert len(mutations) == 4
    for m in mutations:
        assert m.strategy == "truncated"
        assert m.payload is None
        assert isinstance(m.raw_bytes, bytes)

    labels = [m.name for m in mutations]
    assert "truncated:50percent" in labels
    assert "truncated:after_opening_brace" in labels
    assert "truncated:empty_bytes" in labels
    assert "truncated:trailing_garbage" in labels


def test_strategy_filtering():
    c2s_messages = [
        {
            "payload": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "test_tool", "arguments": {"text": "hello"}},
            }
        }
    ]

    mutations = generate_mutations(
        c2s_messages=c2s_messages,
        strategies={"type_confusion"},
    )
    for m in mutations:
        assert m.strategy == "type_confusion"


def test_max_mutations_cap_deterministic():
    c2s_messages = [
        {
            "payload": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "test_tool", "arguments": {"text": "hello"}},
            }
        }
    ]

    m1 = generate_mutations(c2s_messages=c2s_messages, max_mutations=3)
    m2 = generate_mutations(c2s_messages=c2s_messages, max_mutations=3)

    assert len(m1) == 3
    assert [m.name for m in m1] == [m.name for m in m2]


def test_max_mutations_cap_seeded():
    c2s_messages = [
        {
            "payload": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "test_tool", "arguments": {"text": "hello", "num": 42}},
            }
        }
    ]

    m1 = generate_mutations(c2s_messages=c2s_messages, seed=42, max_mutations=5)
    m2 = generate_mutations(c2s_messages=c2s_messages, seed=42, max_mutations=5)
    m_diff_seed = generate_mutations(c2s_messages=c2s_messages, seed=99, max_mutations=5)

    assert len(m1) == 5
    assert [m.name for m in m1] == [m.name for m in m2]
    assert [m.name for m in m1] != [m.name for m in m_diff_seed]


def test_initialize_mutations():
    c2s_messages = [
        {
            "payload": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1.0"},
                },
            }
        }
    ]

    mutations = generate_mutations(
        c2s_messages=c2s_messages,
        strategies={"field_removal"},
    )

    names = [m.name for m in mutations]
    assert "field_removal:params.protocolVersion" in names
    assert "field_removal:params.capabilities" in names
    assert "field_removal:params.clientInfo" in names


def test_deterministic_ordering_is_stable():
    c2s_messages = [
        {
            "payload": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "test_tool", "arguments": {"text": "hello"}},
            }
        }
    ]

    m1 = generate_mutations(c2s_messages=c2s_messages)
    m2 = generate_mutations(c2s_messages=c2s_messages)

    assert [m.name for m in m1] == [m.name for m in m2]
