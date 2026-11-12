"""Phase 11A.2 tests for the BaseTool contract.

These tests pin the public interface consumed by the registry, permissions,
executor, REST API, and tool implementations. They deliberately avoid the
database and the executor so a failure points at the contract itself.
"""

from __future__ import annotations

import inspect
import unittest
from typing import Any

from pydantic import BaseModel

from app.tools.base import BaseTool, ToolContext, json_preview
from app.tools.enums import ToolCategory
from app.tools.exceptions import ToolConfigurationError, ToolValidationError
from app.tools.schemas import StrictModel, ToolResult


class AddInput(StrictModel):
    a: float
    b: float


class PreviewInput(StrictModel):
    password: str
    content: str
    query: str
    optional: str | None = None


class LooseInput(BaseModel):
    value: int


class CalculatorTool(BaseTool):
    name = "calculator"
    description = "Adds two numbers."
    category = ToolCategory.READ_ONLY
    input_model = AddInput
    output_schema = {
        "type": "object",
        "properties": {"result": {"type": "number"}},
    }

    async def execute(
        self,
        context: ToolContext,
        arguments: AddInput,
    ) -> ToolResult:
        return ToolResult(
            success=True,
            tool_name=self.name,
            data={"result": arguments.a + arguments.b},
        )


class DisabledCalculatorTool(CalculatorTool):
    name = "disabled_calculator"

    @property
    def enabled(self) -> bool:
        return False


class SensitiveCalculatorTool(CalculatorTool):
    name = "sensitive_calculator"
    category = ToolCategory.SENSITIVE
    requires_confirmation = True
    allowed_in_private = True
    provider_label = "test-provider"


class LooseInputTool(BaseTool):
    name = "loose_input"
    description = "Uses a plain Pydantic input model."
    category = ToolCategory.READ_ONLY
    input_model = LooseInput

    async def execute(
        self,
        context: ToolContext,
        arguments: LooseInput,
    ) -> ToolResult:
        return ToolResult(success=True, tool_name=self.name)


class ToolContextTests(unittest.TestCase):
    def test_requires_positive_integer_owner(self):
        context = ToolContext(session=None, user_id=7)

        self.assertEqual(context.require_user_id(), 7)

    def test_rejects_invalid_owner_identities(self):
        for value in (0, -1, None, True, "7"):
            with self.subTest(value=value):
                context = ToolContext(session=None, user_id=value)

                with self.assertRaises(ToolConfigurationError):
                    context.require_user_id()

    def test_defaults_are_server_controlled(self):
        context = ToolContext(session=None, user_id=1)

        self.assertIsNone(context.conversation_id)
        self.assertFalse(context.private)
        self.assertIsNone(context.provider)


class BaseToolDefinitionTests(unittest.TestCase):
    def test_definition_projects_metadata_and_schema(self):
        definition = CalculatorTool().definition()

        self.assertEqual(definition.name, "calculator")
        self.assertEqual(definition.description, "Adds two numbers.")
        self.assertEqual(definition.category, ToolCategory.READ_ONLY)
        self.assertTrue(definition.enabled)
        self.assertFalse(definition.requires_confirmation)
        self.assertFalse(definition.allowed_in_private)
        self.assertEqual(definition.parameters["type"], "object")
        self.assertIs(definition.parameters["additionalProperties"], False)
        self.assertEqual(
            set(definition.parameters["properties"]),
            {"a", "b"},
        )
        self.assertEqual(
            definition.output_schema,
            CalculatorTool.output_schema,
        )
        self.assertIsNot(
            definition.output_schema,
            CalculatorTool.output_schema,
        )

    def test_definition_reflects_disabled_state(self):
        self.assertFalse(DisabledCalculatorTool().definition().enabled)

    def test_definition_reflects_permission_metadata(self):
        definition = SensitiveCalculatorTool().definition()

        self.assertEqual(definition.category, ToolCategory.SENSITIVE)
        self.assertTrue(definition.requires_confirmation)
        self.assertTrue(definition.allowed_in_private)

    def test_definition_adds_strict_top_level_schema(self):
        definition = LooseInputTool().definition()

        self.assertEqual(definition.parameters["type"], "object")
        self.assertIs(definition.parameters["additionalProperties"], False)

    def test_provider_schema_shape(self):
        definition = CalculatorTool().definition()
        provider = definition.provider_schema()

        self.assertEqual(provider["type"], "function")
        self.assertEqual(provider["function"]["name"], "calculator")
        self.assertEqual(
            provider["function"]["description"],
            "Adds two numbers.",
        )
        self.assertEqual(
            provider["function"]["parameters"],
            definition.parameters,
        )


class BaseToolContractTests(unittest.TestCase):
    def test_base_tool_is_abstract(self):
        self.assertTrue(inspect.isabstract(BaseTool))

        with self.assertRaises(TypeError):
            BaseTool()

    def test_execute_is_an_async_method(self):
        self.assertTrue(
            inspect.iscoroutinefunction(CalculatorTool.execute)
        )


class ArgumentValidationTests(unittest.TestCase):
    def setUp(self):
        self.tool = CalculatorTool()

    def test_valid_arguments_return_the_input_model(self):
        validated = self.tool.validate_arguments({"a": 2, "b": 3})

        self.assertIsInstance(validated, AddInput)
        self.assertEqual(validated.a, 2.0)
        self.assertEqual(validated.b, 3.0)

    def test_non_object_arguments_are_rejected(self):
        with self.assertRaises(ToolValidationError) as raised:
            self.tool.validate_arguments(["a", 1])

        self.assertIn("object", str(raised.exception))

    def test_unknown_fields_are_rejected_without_echoing_values(self):
        with self.assertRaises(ToolValidationError) as raised:
            self.tool.validate_arguments(
                {"a": 1, "b": 2, "user_id": 987654}
            )

        message = str(raised.exception)

        self.assertIn("user_id", message)
        self.assertNotIn("987654", message)

    def test_missing_and_invalid_fields_are_reported(self):
        with self.assertRaises(ToolValidationError) as raised:
            self.tool.validate_arguments({"a": "not-a-number"})

        message = str(raised.exception)

        self.assertIn("a", message)
        self.assertIn("b", message)
        self.assertNotIn("not-a-number", message)


class SummarizeTests(unittest.TestCase):
    def test_redacts_sensitive_values_and_omits_none(self):
        arguments = PreviewInput(
            password="hunter2",
            content="secret code",
            query="x" * 200,
        )

        preview = CalculatorTool().summarize(arguments)

        self.assertIn('"password": "<redacted>"', preview)
        self.assertIn('"content": "<redacted>"', preview)
        self.assertNotIn("hunter2", preview)
        self.assertNotIn("secret code", preview)
        self.assertNotIn("optional", preview)
        self.assertIn("x" * 117 + "...", preview)

    def test_short_payloads_are_returned_as_json(self):
        preview = CalculatorTool().summarize(AddInput(a=1, b=2))

        self.assertEqual(preview, '{"a": 1.0, "b": 2.0}')


class JsonPreviewTests(unittest.TestCase):
    def test_short_values_are_serialized(self):
        self.assertEqual(json_preview({"ok": True}), '{"ok": true}')

    def test_long_values_are_truncated(self):
        preview = json_preview({"value": "x" * 200}, limit=40)

        self.assertEqual(len(preview), 40)
        self.assertTrue(preview.endswith("..."))


class AsyncCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_execute_is_awaitable(self):
        tool = CalculatorTool()
        context = ToolContext(session=None, user_id=1)
        arguments = tool.validate_arguments({"a": 2, "b": 3})

        result = await tool.execute(context, arguments)

        self.assertTrue(result.success)
        self.assertEqual(result.data, {"result": 5.0})


if __name__ == "__main__":
    unittest.main()
