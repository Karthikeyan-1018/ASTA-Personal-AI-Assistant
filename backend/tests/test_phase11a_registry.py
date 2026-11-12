"""Phase 11A.3 tests for the central tool registry.

The registry is deliberately tested without a database. These tests cover
registration, duplicate prevention, discovery, schema projection, disabled
tools, permission metadata, batch atomicity, and async compatibility.
"""

from __future__ import annotations

import inspect
import unittest

from pydantic import BaseModel

from app.tools.base import BaseTool, ToolContext
from app.tools.enums import ToolCategory
from app.tools.exceptions import ToolNotFoundError, ToolValidationError
from app.tools.registry import (
    ToolRegistry,
    ensure_builtin_tools,
    tool_registry,
)
from app.tools.schemas import StrictModel, ToolDefinition, ToolResult


class EchoInput(StrictModel):
    value: int = 1


class EmptyInput(StrictModel):
    pass


class EchoTool(BaseTool):
    name = "echo_tool"
    description = "Echo a value."
    category = ToolCategory.READ_ONLY
    input_model = EchoInput

    async def execute(
        self,
        context: ToolContext,
        arguments: EchoInput,
    ) -> ToolResult:
        return ToolResult(
            success=True,
            tool_name=self.name,
            data={"value": arguments.value},
        )


class DisabledEchoTool(EchoTool):
    name = "disabled_echo"

    @property
    def enabled(self) -> bool:
        return False


class PrivateEchoTool(EchoTool):
    name = "private_echo"
    allowed_in_private = True


class ReversibleEchoTool(EchoTool):
    name = "reversible_echo"
    category = ToolCategory.REVERSIBLE
    requires_confirmation = True


class SensitiveEchoTool(EchoTool):
    name = "sensitive_echo"
    category = ToolCategory.SENSITIVE


class BadNameTool(EchoTool):
    name = "Bad-Name"


class ShortNameTool(EchoTool):
    name = "x"


class EmptyDescriptionTool(EchoTool):
    description = "   "


class MissingCategoryTool(BaseTool):
    name = "missing_category"
    description = "A tool without a category."
    input_model = EmptyInput

    async def execute(
        self,
        context: ToolContext,
        arguments: EmptyInput,
    ) -> ToolResult:
        return ToolResult(success=True, tool_name=self.name)


class StringCategoryTool(EchoTool):
    name = "string_category"
    category = "read_only"


class NonModelInputTool(EchoTool):
    name = "non_model_input"
    input_model = int


class NotClassInputTool(EchoTool):
    name = "not_class_input"
    input_model = "not-a-class"


class BadOutputSchemaTool(EchoTool):
    name = "bad_output_schema"
    output_schema = "not-a-dictionary"


class BadConfirmationFlagTool(EchoTool):
    name = "bad_confirmation_flag"
    requires_confirmation = "yes"


class BadPrivateFlagTool(EchoTool):
    name = "bad_private_flag"
    allowed_in_private = 1


class BadProviderLabelTool(EchoTool):
    name = "bad_provider_label"
    provider_label = 123


class BrokenSchemaInput(BaseModel):
    value: int = 1

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        raise ValueError("cannot build schema")


class BrokenSchemaTool(EchoTool):
    name = "broken_schema"
    input_model = BrokenSchemaInput


class RegistrationTests(unittest.TestCase):
    def test_register_and_discover_by_name(self):
        registry = ToolRegistry()
        tool = EchoTool()

        registered = registry.register(tool)

        self.assertIs(registered, tool)
        self.assertIs(registry.get("echo_tool"), tool)
        self.assertIs(registry.maybe_get("echo_tool"), tool)
        self.assertIsNone(registry.maybe_get("missing_tool"))
        self.assertEqual(registry.names(), ["echo_tool"])
        self.assertEqual(registry.all(), [tool])
        self.assertEqual(len(registry), 1)
        self.assertIn("echo_tool", registry)
        self.assertNotIn("missing_tool", registry)
        self.assertNotIn(123, registry)
        self.assertEqual([item.name for item in registry], ["echo_tool"])

    def test_unknown_tool_raises_not_found(self):
        registry = ToolRegistry()

        with self.assertRaises(ToolNotFoundError):
            registry.get("missing_tool")

    def test_discovery_is_sorted(self):
        registry = ToolRegistry()
        registry.register_all([EchoTool(), PrivateEchoTool()])

        self.assertEqual(
            registry.names(),
            ["echo_tool", "private_echo"],
        )
        self.assertEqual(
            [tool.name for tool in registry],
            ["echo_tool", "private_echo"],
        )

    def test_register_all_registers_every_valid_tool(self):
        registry = ToolRegistry()

        registry.register_all([EchoTool(), PrivateEchoTool()])

        self.assertEqual(
            registry.names(),
            ["echo_tool", "private_echo"],
        )


class DuplicatePreventionTests(unittest.TestCase):
    def test_duplicate_registration_is_rejected(self):
        registry = ToolRegistry()
        first = registry.register(EchoTool())

        with self.assertRaises(ToolValidationError):
            registry.register(EchoTool())

        self.assertIs(registry.get("echo_tool"), first)
        self.assertEqual(registry.names(), ["echo_tool"])

    def test_duplicate_in_batch_is_rejected_atomically(self):
        registry = ToolRegistry()

        with self.assertRaises(ToolValidationError):
            registry.register_all([EchoTool(), EchoTool()])

        self.assertEqual(registry.names(), [])

    def test_existing_name_in_batch_prevents_partial_registration(self):
        registry = ToolRegistry()
        registry.register(EchoTool())

        with self.assertRaises(ToolValidationError):
            registry.register_all([PrivateEchoTool(), EchoTool()])

        self.assertEqual(registry.names(), ["echo_tool"])


class InvalidDefinitionTests(unittest.TestCase):
    def test_non_base_tool_is_rejected(self):
        with self.assertRaises(ToolValidationError):
            ToolRegistry().register(object())

    def test_invalid_metadata_is_rejected(self):
        invalid_tools = (
            BadNameTool(),
            ShortNameTool(),
            EmptyDescriptionTool(),
            MissingCategoryTool(),
            StringCategoryTool(),
            NonModelInputTool(),
            NotClassInputTool(),
            BadOutputSchemaTool(),
            BadConfirmationFlagTool(),
            BadPrivateFlagTool(),
            BadProviderLabelTool(),
            BrokenSchemaTool(),
        )

        for tool in invalid_tools:
            with self.subTest(tool=type(tool).__name__):
                with self.assertRaises(ToolValidationError):
                    ToolRegistry().register(tool)

    def test_invalid_batch_is_rejected_atomically(self):
        registry = ToolRegistry()

        with self.assertRaises(ToolValidationError):
            registry.register_all([EchoTool(), BadNameTool()])

        self.assertEqual(registry.names(), [])


class DefinitionDiscoveryTests(unittest.TestCase):
    def test_definitions_include_validated_metadata_and_schema(self):
        registry = ToolRegistry()
        registry.register(EchoTool())

        definitions = registry.definitions()

        self.assertEqual(len(definitions), 1)
        self.assertIsInstance(definitions[0], ToolDefinition)
        definition = definitions[0]
        self.assertEqual(definition.name, "echo_tool")
        self.assertEqual(definition.description, "Echo a value.")
        self.assertEqual(definition.category, ToolCategory.READ_ONLY)
        self.assertTrue(definition.enabled)
        self.assertEqual(definition.parameters["type"], "object")
        self.assertIs(
            definition.parameters["additionalProperties"],
            False,
        )
        self.assertIn("value", definition.parameters["properties"])
        self.assertEqual(
            definition.provider_schema()["function"]["name"],
            "echo_tool",
        )

    def test_disabled_tools_are_filtered_by_default(self):
        registry = ToolRegistry()
        registry.register(EchoTool())
        registry.register(DisabledEchoTool())

        self.assertEqual(
            [definition.name for definition in registry.definitions()],
            ["echo_tool"],
        )
        self.assertEqual(
            [
                definition.name
                for definition in registry.definitions(
                    include_disabled=True
                )
            ],
            ["disabled_echo", "echo_tool"],
        )

    def test_enabled_and_disabled_discovery(self):
        registry = ToolRegistry()
        registry.register(EchoTool())
        registry.register(DisabledEchoTool())

        self.assertTrue(registry.is_enabled("echo_tool"))
        self.assertFalse(registry.is_enabled("disabled_echo"))
        self.assertEqual(registry.enabled_names(), ["echo_tool"])
        self.assertEqual(registry.disabled_names(), ["disabled_echo"])
        self.assertEqual(
            [tool.name for tool in registry.enabled_tools()],
            ["echo_tool"],
        )
        self.assertEqual(
            [tool.name for tool in registry.disabled_tools()],
            ["disabled_echo"],
        )

        with self.assertRaises(ToolNotFoundError):
            registry.is_enabled("missing_tool")

    def test_private_filter_uses_permission_metadata(self):
        registry = ToolRegistry()
        registry.register(EchoTool())
        registry.register(PrivateEchoTool())

        self.assertEqual(
            [
                definition.name
                for definition in registry.definitions(private=True)
            ],
            ["private_echo"],
        )

    def test_llm_definitions_exclude_disabled_and_filter_private(self):
        registry = ToolRegistry()
        registry.register(EchoTool())
        registry.register(DisabledEchoTool())
        registry.register(PrivateEchoTool())
        registry.register(ReversibleEchoTool())

        public = registry.llm_definitions(private=False)
        public_names = [
            definition["function"]["name"] for definition in public
        ]

        self.assertIn("echo_tool", public_names)
        self.assertIn("private_echo", public_names)
        self.assertIn("reversible_echo", public_names)
        self.assertNotIn("disabled_echo", public_names)

        for definition in public:
            self.assertEqual(definition["type"], "function")
            self.assertEqual(
                set(definition["function"]),
                {"name", "description", "parameters"},
            )

        private = registry.llm_definitions(private=True)
        self.assertEqual(
            [definition["function"]["name"] for definition in private],
            ["private_echo"],
        )

    def test_definitions_are_rebuilt_on_each_discovery(self):
        registry = ToolRegistry()
        registry.register(EchoTool())

        first = registry.definitions()[0]
        second = registry.definitions()[0]

        self.assertIsNot(first, second)
        self.assertEqual(first.name, second.name)


class AsyncRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_tool_retains_async_execution(self):
        registry = ToolRegistry()
        tool = registry.register(EchoTool())

        self.assertTrue(inspect.iscoroutinefunction(tool.execute))

        context = ToolContext(session=None, user_id=1)
        arguments = tool.validate_arguments({"value": 9})
        result = await tool.execute(context, arguments)

        self.assertTrue(result.success)
        self.assertEqual(result.data, {"value": 9})


class BuiltinRegistryIntegrationTests(unittest.TestCase):
    def test_builtins_are_loaded_once_and_discoverable(self):
        ensure_builtin_tools()
        first = tool_registry.names()
        ensure_builtin_tools()

        self.assertEqual(tool_registry.names(), first)
        self.assertIn("task_list", first)
        self.assertEqual(len(first), len(set(first)))

        definitions = tool_registry.definitions(include_disabled=True)

        self.assertTrue(definitions)
        self.assertEqual(
            {definition.name for definition in definitions},
            set(first),
        )
        self.assertTrue(
            all(isinstance(item, ToolDefinition) for item in definitions)
        )

        for definition in definitions:
            provider = definition.provider_schema()
            self.assertEqual(provider["type"], "function")
            self.assertEqual(
                provider["function"]["name"],
                definition.name,
            )

    def test_builtin_duplicate_registration_is_rejected(self):
        ensure_builtin_tools()
        existing = tool_registry.get("task_list")

        with self.assertRaises(ToolValidationError):
            tool_registry.register(existing)

        self.assertIs(tool_registry.get("task_list"), existing)


if __name__ == "__main__":
    unittest.main()
