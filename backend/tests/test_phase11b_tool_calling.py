"""Phase 11B tests for provider-independent LLM function calling.

No real provider is contacted. Groq and Ollama SDK clients are replaced with
mocked responses shaped like the real SDK return values. The tests cover
argument parsing, provider-specific extraction, the public ``ToolCall`` schema,
registry-to-provider schema conversion, message translation, router routing and
fallback, and the guarantee that Phase 11B never executes a tool.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.core.config import settings
from app.exceptions.llm_exceptions import (
    LLMProviderError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.llm import tool_calling
from app.llm.groq_provider import groq_provider
from app.llm.llm_router import llm_router
from app.llm.ollama_provider import ollama_provider
from app.llm.tool_calling import (
    MAX_CALL_ID_CHARS,
    MAX_TOOL_ARGUMENT_CHARS,
    MAX_TOOL_CALLS_PER_TURN,
    MAX_TOOL_NAME_CHARS,
    UNKNOWN_TOOL_NAME,
    NormalizedToolCall,
    ProviderToolTurn,
    assistant_message,
    extract_groq_tool_calls,
    extract_ollama_tool_calls,
    normalize_tool_definitions,
    parse_tool_arguments,
    to_groq_messages,
    to_groq_tools,
    to_ollama_messages,
    to_ollama_tools,
    tool_message,
)
from app.tools.registry import tool_registry
from app.tools.schemas import ToolCall


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "task_list",
            "description": "List tasks.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
                "additionalProperties": False,
            },
        },
    }
]


def groq_message(content="", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def groq_tool_call(
    name="task_list",
    arguments='{"limit": 5}',
    call_id="call_1",
):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def groq_response(content="", tool_calls=None, usage=True):
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=groq_message(content, tool_calls),
            )
        ],
    )

    if usage:
        response.usage = SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )

    return response


def ollama_response(content="", tool_calls=None, counts=True):
    response = SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=tool_calls),
    )

    if counts:
        response.prompt_eval_count = 7
        response.eval_count = 3

    return response


def ollama_tool_call(name="task_list", arguments=None, call_id=None):
    call = SimpleNamespace(
        function=SimpleNamespace(
            name=name,
            arguments=arguments if arguments is not None else {"limit": 5},
        )
    )

    if call_id is not None:
        call.id = call_id

    return call


class ParseToolArgumentsTests(unittest.TestCase):
    def test_none_and_empty_string_mean_no_arguments(self):
        self.assertEqual(parse_tool_arguments(None), ({}, None))
        self.assertEqual(parse_tool_arguments("   "), ({}, None))

    def test_mapping_is_passed_through(self):
        arguments = {"limit": 5}

        parsed, error = parse_tool_arguments(arguments)

        self.assertIs(parsed, arguments)
        self.assertIsNone(error)

    def test_json_object_string_is_parsed(self):
        parsed, error = parse_tool_arguments('{"limit": 5}')

        self.assertEqual(parsed, {"limit": 5})
        self.assertIsNone(error)

    def test_invalid_json_is_reported_without_values(self):
        parsed, error = parse_tool_arguments("{not-json")

        self.assertEqual(parsed, {})
        self.assertIn("not valid JSON", error)
        self.assertNotIn("not-json", error)

    def test_json_array_is_rejected(self):
        parsed, error = parse_tool_arguments("[1, 2, 3]")

        self.assertEqual(parsed, {})
        self.assertIn("JSON object", error)

    def test_oversized_payload_is_rejected(self):
        raw = '{"value": "' + ("x" * MAX_TOOL_ARGUMENT_CHARS) + '"}'

        parsed, error = parse_tool_arguments(raw)

        self.assertEqual(parsed, {})
        self.assertIn("size limit", error)

    def test_unsupported_type_is_rejected(self):
        parsed, error = parse_tool_arguments(42)

        self.assertEqual(parsed, {})
        self.assertIn("unsupported type", error)


class NormalizedToolCallTests(unittest.TestCase):
    def test_valid_call_converts_to_public_schema(self):
        call = NormalizedToolCall(
            id="call_1",
            name="task_list",
            arguments={"limit": 5},
        )

        schema = call.to_tool_call()

        self.assertIsInstance(schema, ToolCall)
        self.assertEqual(schema.call_id, "call_1")
        self.assertEqual(schema.name, "task_list")
        self.assertEqual(schema.arguments, {"limit": 5})
        self.assertTrue(call.is_valid)

    def test_malformed_call_does_not_convert(self):
        call = NormalizedToolCall(
            id="call_1",
            name="task_list",
            error="Tool arguments were not valid JSON.",
        )

        self.assertIsNone(call.to_tool_call())
        self.assertFalse(call.is_valid)

    def test_nameless_call_does_not_convert(self):
        call = NormalizedToolCall(id="call_1", name="")

        self.assertIsNone(call.to_tool_call())
        self.assertFalse(call.is_valid)

    def test_public_schema_rejects_blank_name(self):
        with self.assertRaises(ValueError):
            ToolCall(name="   ")

    def test_neutral_round_trip_preserves_fields(self):
        call = NormalizedToolCall(
            id="call_1",
            name="task_list",
            arguments={"limit": 5},
        )

        self.assertEqual(
            call.as_neutral(),
            {"id": "call_1", "name": "task_list", "arguments": {"limit": 5}},
        )

    def test_turn_exposes_only_valid_tool_call_schemas(self):
        turn = ProviderToolTurn(
            content="",
            tool_calls=[
                NormalizedToolCall(id="call_1", name="task_list"),
                NormalizedToolCall(id="call_2", name="", error="bad"),
                NormalizedToolCall(id="call_3", name="task_get", error="bad"),
            ],
        )

        schemas = turn.tool_call_schemas

        self.assertEqual(len(schemas), 1)
        self.assertEqual(schemas[0].name, "task_list")


class GroqExtractionTests(unittest.TestCase):
    def test_missing_tool_calls_returns_empty_list(self):
        self.assertEqual(extract_groq_tool_calls(object()), [])
        self.assertEqual(extract_groq_tool_calls({"content": "hi"}), [])

    def test_valid_call_parses_json_string_arguments(self):
        calls = extract_groq_tool_calls(
            groq_message(tool_calls=[groq_tool_call()])
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "task_list")
        self.assertEqual(calls[0].arguments, {"limit": 5})
        self.assertIsNone(calls[0].error)

    def test_missing_id_generates_one(self):
        calls = extract_groq_tool_calls(
            groq_message(tool_calls=[groq_tool_call(call_id=None)])
        )

        self.assertTrue(calls[0].id.startswith("call_"))
        self.assertIsNone(calls[0].error)

    def test_missing_name_is_recorded_as_error(self):
        calls = extract_groq_tool_calls(
            groq_message(tool_calls=[groq_tool_call(name="")])
        )

        self.assertEqual(calls[0].name, "")
        self.assertIn("without a name", calls[0].error)
        self.assertIsNone(calls[0].to_tool_call())

    def test_malformed_arguments_are_recorded_not_raised(self):
        calls = extract_groq_tool_calls(
            groq_message(
                tool_calls=[groq_tool_call(arguments="{not-json")]
            )
        )

        self.assertEqual(calls[0].name, "task_list")
        self.assertEqual(calls[0].arguments, {})
        self.assertIn("not valid JSON", calls[0].error)

    def test_non_object_arguments_are_recorded(self):
        calls = extract_groq_tool_calls(
            groq_message(tool_calls=[groq_tool_call(arguments="[1, 2]")])
        )

        self.assertIn("JSON object", calls[0].error)

    def test_single_mapping_is_accepted(self):
        calls = extract_groq_tool_calls(
            {
                "tool_calls": {
                    "id": "call_1",
                    "function": {
                        "name": "task_list",
                        "arguments": '{"limit": 1}',
                    },
                }
            }
        )

        self.assertEqual(calls[0].name, "task_list")
        self.assertEqual(calls[0].arguments, {"limit": 1})

    def test_unsupported_tool_calls_shape_is_reported(self):
        calls = extract_groq_tool_calls({"tool_calls": "not-a-list"})

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "")
        self.assertIn("unsupported shape", calls[0].error)

    def test_duplicate_ids_are_made_unique(self):
        calls = extract_groq_tool_calls(
            groq_message(
                tool_calls=[
                    groq_tool_call(call_id="same"),
                    groq_tool_call(call_id="same"),
                ]
            )
        )

        self.assertNotEqual(calls[0].id, calls[1].id)
        self.assertEqual(calls[0].id, "same")

    def test_oversized_id_is_replaced(self):
        oversized = "c" * (MAX_CALL_ID_CHARS + 1)
        calls = extract_groq_tool_calls(
            groq_message(tool_calls=[groq_tool_call(call_id=oversized)])
        )

        self.assertNotEqual(calls[0].id, oversized)
        self.assertTrue(calls[0].id.startswith("call_"))

    def test_oversized_name_is_rejected(self):
        calls = extract_groq_tool_calls(
            groq_message(
                tool_calls=[groq_tool_call(name="n" * (MAX_TOOL_NAME_CHARS + 1))]
            )
        )

        self.assertIn("too long", calls[0].error)
        self.assertIsNone(calls[0].to_tool_call())

    def test_excess_calls_are_bounded(self):
        raw_calls = [
            groq_tool_call(name="task_list", call_id=f"call_{index}")
            for index in range(MAX_TOOL_CALLS_PER_TURN + 5)
        ]

        calls = extract_groq_tool_calls(groq_message(tool_calls=raw_calls))

        self.assertEqual(len(calls), MAX_TOOL_CALLS_PER_TURN + 1)
        self.assertIn("too many", calls[-1].error)

    def test_partial_valid_and_invalid_calls_are_both_returned(self):
        calls = extract_groq_tool_calls(
            groq_message(
                tool_calls=[
                    groq_tool_call(name="task_list"),
                    groq_tool_call(name="", call_id="call_2"),
                ]
            )
        )

        self.assertIsNone(calls[0].error)
        self.assertIsNotNone(calls[1].error)


class OllamaExtractionTests(unittest.TestCase):
    def test_mapping_arguments_are_accepted(self):
        calls = extract_ollama_tool_calls(
            SimpleNamespace(tool_calls=[ollama_tool_call()])
        )

        self.assertEqual(calls[0].name, "task_list")
        self.assertEqual(calls[0].arguments, {"limit": 5})
        self.assertIsNone(calls[0].error)

    def test_json_string_arguments_are_accepted(self):
        calls = extract_ollama_tool_calls(
            SimpleNamespace(
                tool_calls=[
                    ollama_tool_call(arguments='{"limit": 2}')
                ]
            )
        )

        self.assertEqual(calls[0].arguments, {"limit": 2})

    def test_dict_message_is_supported(self):
        calls = extract_ollama_tool_calls(
            {
                "tool_calls": [
                    {
                        "function": {
                            "name": "task_get",
                            "arguments": {"task_id": 3},
                        }
                    }
                ]
            }
        )

        self.assertEqual(calls[0].name, "task_get")
        self.assertEqual(calls[0].arguments, {"task_id": 3})

    def test_missing_name_is_recorded(self):
        calls = extract_ollama_tool_calls(
            SimpleNamespace(tool_calls=[ollama_tool_call(name="")])
        )

        self.assertIn("without a name", calls[0].error)


class ToolDefinitionTests(unittest.TestCase):
    def test_valid_definition_is_normalized(self):
        normalized = normalize_tool_definitions(TOOLS)

        self.assertEqual(normalized, TOOLS)

    def test_invalid_entries_are_dropped(self):
        definitions = [
            None,
            "not-a-definition",
            {"type": "function"},
            {"type": "function", "function": {}},
            {"type": "function", "function": {"name": "   "}},
            {"type": "function", "function": {"name": "x" * 150}},
            {"type": "text", "function": {"name": "wrong_type"}},
            TOOLS[0],
        ]

        normalized = normalize_tool_definitions(definitions)

        self.assertEqual(
            [item["function"]["name"] for item in normalized],
            ["task_list"],
        )

    def test_duplicate_names_are_dropped(self):
        normalized = normalize_tool_definitions([TOOLS[0], TOOLS[0]])

        self.assertEqual(len(normalized), 1)

    def test_missing_parameters_default_to_empty_object_schema(self):
        normalized = normalize_tool_definitions(
            [
                {
                    "type": "function",
                    "function": {"name": "no_args", "description": "x"},
                }
            ]
        )

        self.assertEqual(
            normalized[0]["function"]["parameters"],
            {"type": "object", "properties": {}},
        )

    def test_non_string_description_becomes_empty(self):
        normalized = normalize_tool_definitions(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "no_description",
                        "description": 12,
                        "parameters": {"type": "object"},
                    },
                }
            ]
        )

        self.assertEqual(normalized[0]["function"]["description"], "")

    def test_non_list_input_is_ignored(self):
        self.assertEqual(normalize_tool_definitions(None), [])
        self.assertEqual(normalize_tool_definitions("tools"), [])
        self.assertEqual(normalize_tool_definitions({"name": "x"}), [])

    def test_output_is_independent_of_input(self):
        normalized = normalize_tool_definitions(TOOLS)
        normalized[0]["function"]["parameters"]["properties"]["limit"][
            "type"
        ] = "string"

        self.assertEqual(
            TOOLS[0]["function"]["parameters"]["properties"]["limit"]["type"],
            "integer",
        )

    def test_groq_and_ollama_tools_match(self):
        self.assertEqual(to_groq_tools(TOOLS), to_ollama_tools(TOOLS))

    def test_registry_definitions_convert_for_both_providers(self):
        definitions = tool_registry.llm_definitions()

        self.assertTrue(definitions)

        groq_tools = to_groq_tools(definitions)
        ollama_tools = to_ollama_tools(definitions)

        self.assertEqual(groq_tools, ollama_tools)
        self.assertEqual(len(groq_tools), len(definitions))

        for item in groq_tools:
            self.assertEqual(item["type"], "function")
            self.assertEqual(
                set(item["function"]),
                {"name", "description", "parameters"},
            )
            self.assertIsInstance(item["function"]["parameters"], dict)


class MessageTranslationTests(unittest.TestCase):
    def test_groq_translation_preserves_plain_messages(self):
        messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "hello"},
        ]

        self.assertEqual(to_groq_messages(messages), messages)
        self.assertEqual(to_ollama_messages(messages), messages)

    def test_groq_translation_serializes_arguments(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "task_list",
                        "arguments": {"limit": 5},
                    }
                ],
            }
        ]

        translated = to_groq_messages(messages)

        call = translated[0]["tool_calls"][0]
        self.assertEqual(call["id"], "call_1")
        self.assertEqual(call["type"], "function")
        self.assertEqual(call["function"]["name"], "task_list")
        self.assertEqual(
            json.loads(call["function"]["arguments"]),
            {"limit": 5},
        )

    def test_ollama_translation_keeps_arguments_as_mapping(self):
        messages = [
            assistant_message(
                "",
                [
                    NormalizedToolCall(
                        id="call_1",
                        name="task_list",
                        arguments={"limit": 5},
                    )
                ],
            )
        ]

        translated = to_ollama_messages(messages)

        call = translated[0]["tool_calls"][0]
        self.assertEqual(call["function"]["name"], "task_list")
        self.assertEqual(call["function"]["arguments"], {"limit": 5})

    def test_tool_results_translate_for_both_providers(self):
        message = tool_message(
            call_id="call_1",
            name="task_list",
            content="result",
        )

        groq = to_groq_messages([message])
        ollama = to_ollama_messages([message])

        self.assertEqual(groq[0]["tool_call_id"], "call_1")
        self.assertEqual(groq[0]["name"], "task_list")
        self.assertEqual(ollama[0]["tool_name"], "task_list")
        self.assertEqual(ollama[0]["content"], "result")

    def test_malformed_assistant_calls_are_filtered(self):
        messages = [
            {
                "role": "assistant",
                "content": "text",
                "tool_calls": ["bad", {"name": ""}, {"arguments": {}}],
            }
        ]

        groq = to_groq_messages(messages)
        ollama = to_ollama_messages(messages)

        self.assertEqual(groq[0], {"role": "assistant", "content": "text"})
        self.assertEqual(ollama[0], {"role": "assistant", "content": "text"})

    def test_nameless_call_with_id_stays_paired_on_replay(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_1", "name": "", "arguments": {}}
                ],
            },
            tool_message(
                call_id="call_1",
                name="",
                content="malformed",
            ),
        ]

        groq = to_groq_messages(messages)
        ollama = to_ollama_messages(messages)

        self.assertEqual(
            groq[0]["tool_calls"][0]["function"]["name"],
            UNKNOWN_TOOL_NAME,
        )
        self.assertEqual(groq[1]["tool_call_id"], "call_1")
        self.assertEqual(
            ollama[0]["tool_calls"][0]["function"]["name"],
            UNKNOWN_TOOL_NAME,
        )
        self.assertEqual(ollama[1]["content"], "malformed")

    def test_non_mapping_messages_are_skipped(self):
        self.assertEqual(to_groq_messages(["bad"]), [])
        self.assertEqual(to_ollama_messages(["bad"]), [])

    def test_non_string_content_is_json_encoded(self):
        messages = [{"role": "tool", "content": {"ok": True}}]

        self.assertEqual(
            to_groq_messages(messages)[0]["content"],
            '{"ok": true}',
        )


class GroqProviderToolCallTests(unittest.IsolatedAsyncioTestCase):
    def patch_create(self, **kwargs):
        mock = AsyncMock(**kwargs)
        patcher = patch.object(
            groq_provider.client.chat.completions,
            "create",
            new=mock,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    async def test_tool_call_response_is_normalized(self):
        create = self.patch_create(
            return_value=groq_response(
                content="",
                tool_calls=[groq_tool_call()],
            )
        )

        turn = await groq_provider.generate_with_tools(
            [{"role": "user", "content": "list tasks"}],
            tools=TOOLS,
        )

        self.assertEqual(turn.content, "")
        self.assertEqual(len(turn.tool_calls), 1)
        self.assertEqual(turn.tool_calls[0].name, "task_list")
        self.assertEqual(turn.tool_calls[0].arguments, {"limit": 5})
        self.assertEqual(
            turn.usage,
            {
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "source": "provider",
            },
        )

        kwargs = create.await_args.kwargs
        self.assertEqual(kwargs["tools"], TOOLS)
        self.assertEqual(kwargs["tool_choice"], "auto")

    async def test_text_only_response_is_preserved(self):
        self.patch_create(
            return_value=groq_response(content="plain answer")
        )

        turn = await groq_provider.generate_with_tools(
            [{"role": "user", "content": "hi"}],
            tools=TOOLS,
        )

        self.assertEqual(turn.content, "plain answer")
        self.assertEqual(turn.tool_calls, [])

    async def test_omits_tools_when_none_are_valid(self):
        create = self.patch_create(
            return_value=groq_response(content="plain answer")
        )

        await groq_provider.generate_with_tools(
            [{"role": "user", "content": "hi"}],
            tools=[],
        )

        kwargs = create.await_args.kwargs
        self.assertNotIn("tools", kwargs)
        self.assertNotIn("tool_choice", kwargs)

    async def test_invalid_definitions_are_not_sent(self):
        create = self.patch_create(
            return_value=groq_response(content="plain answer")
        )

        await groq_provider.generate_with_tools(
            [{"role": "user", "content": "hi"}],
            tools=[None, {"type": "function", "function": {}}],
        )

        self.assertNotIn("tools", create.await_args.kwargs)

    async def test_malformed_arguments_do_not_raise(self):
        self.patch_create(
            return_value=groq_response(
                tool_calls=[groq_tool_call(arguments="{not-json")],
            )
        )

        turn = await groq_provider.generate_with_tools(
            [{"role": "user", "content": "hi"}],
            tools=TOOLS,
        )

        self.assertIn("not valid JSON", turn.tool_calls[0].error)
        self.assertEqual(turn.tool_call_schemas, [])

    async def test_empty_choices_raise_provider_error(self):
        self.patch_create(
            return_value=SimpleNamespace(choices=[], usage=None)
        )

        with self.assertRaises(LLMProviderError):
            await groq_provider.generate_with_tools(
                [{"role": "user", "content": "hi"}],
                tools=TOOLS,
            )

    async def test_timeout_maps_to_timeout_error(self):
        self.patch_create(side_effect=httpx.TimeoutException("slow"))

        with self.assertRaises(LLMTimeoutError):
            await groq_provider.generate_with_tools(
                [{"role": "user", "content": "hi"}],
                tools=TOOLS,
            )


class OllamaProviderToolCallTests(unittest.IsolatedAsyncioTestCase):
    def patch_chat(self, **kwargs):
        mock = AsyncMock(**kwargs)
        patcher = patch.object(
            ollama_provider.client,
            "chat",
            new=mock,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    async def test_tool_call_response_is_normalized(self):
        chat = self.patch_chat(
            return_value=ollama_response(
                tool_calls=[ollama_tool_call()],
            )
        )

        turn = await ollama_provider.generate_with_tools(
            [{"role": "user", "content": "list tasks"}],
            tools=TOOLS,
        )

        self.assertEqual(turn.tool_calls[0].name, "task_list")
        self.assertEqual(turn.tool_calls[0].arguments, {"limit": 5})
        self.assertEqual(turn.usage["input_tokens"], 7)
        self.assertEqual(turn.usage["output_tokens"], 3)
        self.assertEqual(chat.await_args.kwargs["tools"], TOOLS)

    async def test_text_only_response_is_preserved(self):
        self.patch_chat(
            return_value=ollama_response(content="plain answer")
        )

        turn = await ollama_provider.generate_with_tools(
            [{"role": "user", "content": "hi"}],
            tools=TOOLS,
        )

        self.assertEqual(turn.content, "plain answer")
        self.assertEqual(turn.tool_calls, [])

    async def test_omits_tools_when_none_are_valid(self):
        chat = self.patch_chat(
            return_value=ollama_response(content="plain answer")
        )

        await ollama_provider.generate_with_tools(
            [{"role": "user", "content": "hi"}],
            tools=[],
        )

        self.assertNotIn("tools", chat.await_args.kwargs)

    async def test_mapping_response_shape_is_supported(self):
        self.patch_chat(
            return_value={
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "task_get",
                                "arguments": {"task_id": 3},
                            }
                        }
                    ],
                },
                "prompt_eval_count": 2,
                "eval_count": 1,
            }
        )

        turn = await ollama_provider.generate_with_tools(
            [{"role": "user", "content": "hi"}],
            tools=TOOLS,
        )

        self.assertEqual(turn.tool_calls[0].name, "task_get")
        self.assertEqual(turn.tool_calls[0].arguments, {"task_id": 3})

    async def test_timeout_maps_to_timeout_error(self):
        self.patch_chat(side_effect=asyncio.TimeoutError())

        with self.assertRaises(LLMTimeoutError):
            await ollama_provider.generate_with_tools(
                [{"role": "user", "content": "hi"}],
                tools=TOOLS,
            )


class RouterToolCallTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.policy = patch.object(
            settings,
            "AI_ROUTING_POLICY",
            "groq_first",
        )
        self.policy.start()
        self.addCleanup(self.policy.stop)

    def stub(self, provider, *, health=True, turn=None, error=None):
        health_mock = AsyncMock(return_value=health)

        if error is not None:
            generate_mock = AsyncMock(side_effect=error)
        else:
            generate_mock = AsyncMock(return_value=turn)

        for name, mock in (
            ("health_check", health_mock),
            ("generate_with_tools", generate_mock),
        ):
            patcher = patch.object(provider, name, new=mock)
            patcher.start()
            self.addCleanup(patcher.stop)

        return health_mock, generate_mock

    async def test_text_only_response_is_preserved(self):
        self.stub(
            groq_provider,
            turn=ProviderToolTurn(content="plain answer"),
        )
        self.stub(
            ollama_provider,
            turn=ProviderToolTurn(content="fallback answer"),
        )

        result = await llm_router.generate_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=TOOLS,
        )

        self.assertEqual(result["provider"], "groq")
        self.assertEqual(result["response"], "plain answer")
        self.assertEqual(result["tool_calls"], [])
        self.assertEqual(result["tool_call_schemas"], [])

    async def test_tool_call_response_is_returned_with_public_schema(self):
        call = NormalizedToolCall(
            id="call_1",
            name="task_list",
            arguments={"limit": 5},
        )
        self.stub(
            groq_provider,
            turn=ProviderToolTurn(content="", tool_calls=[call]),
        )
        self.stub(
            ollama_provider,
            turn=ProviderToolTurn(content="fallback"),
        )

        result = await llm_router.generate_with_tools(
            messages=[{"role": "user", "content": "list tasks"}],
            tools=TOOLS,
        )

        self.assertEqual(result["tool_calls"], [call])
        self.assertEqual(len(result["tool_call_schemas"]), 1)
        self.assertIsInstance(result["tool_call_schemas"][0], ToolCall)
        self.assertEqual(result["tool_call_schemas"][0].name, "task_list")

    async def test_malformed_call_is_returned_not_raised(self):
        call = NormalizedToolCall(
            id="call_1",
            name="",
            error="The provider returned a tool call without a name.",
        )
        self.stub(
            groq_provider,
            turn=ProviderToolTurn(tool_calls=[call]),
        )
        self.stub(
            ollama_provider,
            turn=ProviderToolTurn(content="fallback"),
        )

        result = await llm_router.generate_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=TOOLS,
        )

        self.assertEqual(result["tool_calls"][0].error, call.error)
        self.assertEqual(result["tool_call_schemas"], [])

    async def test_private_request_never_contacts_groq_and_hides_tools(self):
        groq_health, groq_generate = self.stub(
            groq_provider,
            turn=ProviderToolTurn(content="cloud answer"),
        )
        _, ollama_generate = self.stub(
            ollama_provider,
            turn=ProviderToolTurn(content="local answer"),
        )

        result = await llm_router.generate_with_tools(
            messages=[{"role": "user", "content": "secret"}],
            tools=TOOLS,
            private=True,
        )

        self.assertEqual(result["provider"], "ollama")
        self.assertEqual(result["response"], "local answer")
        groq_health.assert_not_awaited()
        groq_generate.assert_not_awaited()
        self.assertEqual(ollama_generate.await_args.kwargs["tools"], [])

    async def test_private_empty_response_raises(self):
        self.stub(
            groq_provider,
            turn=ProviderToolTurn(content="cloud answer"),
        )
        self.stub(
            ollama_provider,
            turn=ProviderToolTurn(content=""),
        )

        with self.assertRaises(LLMUnavailableError):
            await llm_router.generate_with_tools(
                messages=[{"role": "user", "content": "secret"}],
                tools=TOOLS,
                private=True,
            )

    async def test_groq_failure_falls_back_to_ollama(self):
        self.stub(
            groq_provider,
            health=True,
            error=LLMProviderError("groq failed"),
        )
        self.stub(
            ollama_provider,
            health=True,
            turn=ProviderToolTurn(content="fallback answer"),
        )

        result = await llm_router.generate_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=TOOLS,
        )

        self.assertEqual(result["provider"], "ollama")
        self.assertEqual(result["response"], "fallback answer")
        self.assertTrue(result["fallback_used"])

    async def test_all_providers_unavailable_raises(self):
        self.stub(groq_provider, health=False)
        self.stub(ollama_provider, health=False)

        with self.assertRaises(LLMUnavailableError):
            await llm_router.generate_with_tools(
                messages=[{"role": "user", "content": "hi"}],
                tools=TOOLS,
            )

    async def test_empty_responses_from_both_providers_raise(self):
        self.stub(
            groq_provider,
            health=True,
            turn=ProviderToolTurn(content=""),
        )
        self.stub(
            ollama_provider,
            health=True,
            turn=ProviderToolTurn(content=""),
        )

        with self.assertRaises(LLMUnavailableError):
            await llm_router.generate_with_tools(
                messages=[{"role": "user", "content": "hi"}],
                tools=TOOLS,
            )

    async def test_tool_calling_does_not_execute_tools(self):
        from app.tools.executor import tool_executor

        self.stub(
            groq_provider,
            turn=ProviderToolTurn(
                tool_calls=[
                    NormalizedToolCall(
                        id="call_1",
                        name="task_list",
                        arguments={"limit": 5},
                    )
                ]
            ),
        )
        self.stub(
            ollama_provider,
            turn=ProviderToolTurn(content="fallback"),
        )

        with patch.object(
            tool_executor,
            "execute",
            new=AsyncMock(),
        ) as execute:
            await llm_router.generate_with_tools(
                messages=[{"role": "user", "content": "list tasks"}],
                tools=TOOLS,
            )

        execute.assert_not_awaited()

    async def test_legacy_text_generation_is_unchanged(self):
        health = AsyncMock(return_value=True)
        generate = AsyncMock(return_value="legacy answer")

        for name, mock in (
            ("health_check", health),
            ("generate_response", generate),
        ):
            patcher = patch.object(groq_provider, name, new=mock)
            patcher.start()
            self.addCleanup(patcher.stop)

        result = await llm_router.generate_response("hi")

        self.assertEqual(result["response"], "legacy answer")
        self.assertNotIn("tool_calls", result)


class ModuleBoundaryTests(unittest.TestCase):
    def test_tool_calling_exports_provider_converters(self):
        for name in (
            "extract_groq_tool_calls",
            "extract_ollama_tool_calls",
            "to_groq_tools",
            "to_ollama_tools",
            "to_groq_messages",
            "to_ollama_messages",
        ):
            self.assertIn(name, tool_calling.__all__)


if __name__ == "__main__":
    unittest.main()
