"""Phase 12E tests for secure clipboard, keyboard, and mouse interaction.

Every Windows clipboard and input API is mocked. No real clipboard read or
write, key press, pointer movement, or click is performed. The suite covers the
allowlists and bounds, confirmation and permission integration, private-mode
denial, unsupported platforms, metadata-only history, and source-level checks
that the module cannot capture the screen, log input, install hooks, elevate
privileges, or perform unrestricted automation.
"""

from __future__ import annotations

import ctypes
import json
import os
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import select

from app.core.config import settings
from app.models.conversation import Conversation
from app.models.tool_execution import ToolConfirmation, ToolExecution
from app.services.tool_confirmation_service import (
    tool_confirmation_service,
)
from app.tools.base import ToolContext
from app.tools.definitions import BUILTIN_TOOLS
from app.tools.enums import (
    ConfirmationState,
    ToolCategory,
    ToolConfirmationStatus,
    ToolErrorCategory,
    ToolExecutionStatus,
)
from app.tools.exceptions import (
    ToolConfigurationError,
    ToolExecutionError,
    ToolSizeLimitError,
    ToolValidationError,
)
from app.tools.executor import tool_executor
from app.tools.implementations import desktop_interaction
from app.tools.implementations.desktop_interaction import (
    ALLOWED_KEYS,
    CF_UNICODETEXT,
    ClipboardRead,
    DESKTOP_INTERACTION_TOOLS,
    KEYEVENTF_EXTENDEDKEY,
    KEYEVENTF_KEYUP,
    KEY_NAMES,
    MAX_CLIPBOARD_WRITE_CHARS,
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    ScreenBounds,
    SystemClipboardReadTool,
    SystemClipboardWriteTool,
    SystemKeyboardPressTool,
    SystemMouseClickTool,
    SystemMouseMoveTool,
    SystemMousePositionTool,
)
from app.tools.permissions import permission_policy
from app.tools.registry import tool_registry
from tests.tool_test_support import ToolEngineTestBase


TOOL_NAMES = {
    "system_clipboard_read",
    "system_clipboard_write",
    "system_keyboard_press",
    "system_mouse_position",
    "system_mouse_move",
    "system_mouse_click",
}

CONFIRMATION_TOOLS = TOOL_NAMES - {"system_mouse_position"}

_MISSING = object()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class DesktopInteractionTestBase(unittest.IsolatedAsyncioTestCase):
    """All capabilities enabled, a fixed screen, and mocked Windows calls."""

    def setUp(self):
        self.configure()
        self.patch_object(
            desktop_interaction,
            "_is_supported_platform",
            return_value=True,
        )
        self.screen = ScreenBounds(left=0, top=0, width=1920, height=1080)
        self.patch_object(
            desktop_interaction,
            "_screen_bounds",
            return_value=self.screen,
        )

        self.read_clipboard = Mock(
            return_value=ClipboardRead(available=True, text="hello")
        )
        self.write_clipboard = Mock(return_value=None)
        self.get_position = Mock(return_value=(100, 200))
        self.set_position = Mock(return_value=True)
        self.send_key = Mock(return_value=True)
        self.send_click = Mock(return_value=True)

        for name, mock in (
            ("_read_clipboard_text", self.read_clipboard),
            ("_write_clipboard_text", self.write_clipboard),
            ("_get_cursor_position", self.get_position),
            ("_set_cursor_position", self.set_position),
            ("_send_key", self.send_key),
            ("_send_left_click", self.send_click),
        ):
            self.patch_object(desktop_interaction, name, mock)

    def configure(self, **overrides):
        values = {
            "TOOL_OS_AUTOMATION_ENABLED": True,
            "TOOL_CLIPBOARD_ENABLED": True,
            "TOOL_KEYBOARD_ENABLED": True,
            "TOOL_MOUSE_ENABLED": True,
            "TOOL_CLIPBOARD_MAX_READ_CHARS": 4000,
            "TOOL_CLIPBOARD_MAX_WRITE_CHARS": 4000,
        }
        values.update(overrides)

        for name, value in values.items():
            patcher = patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def patch_object(self, target, name, new=_MISSING, **kwargs):
        if new is _MISSING:
            patcher = patch.object(target, name, **kwargs)
        else:
            patcher = patch.object(target, name, new, **kwargs)

        patcher.start()
        self.addCleanup(patcher.stop)

    async def run_tool(self, tool, arguments=None):
        validated = tool.validate_arguments(arguments or {})
        return await tool.execute(None, validated)


# ---------------------------------------------------------------------------
# Configuration and enablement
# ---------------------------------------------------------------------------


class ConfigurationTests(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.patch_object(
            desktop_interaction,
            "_is_supported_platform",
            return_value=True,
        )

    def patch_object(self, target, name, new=_MISSING, **kwargs):
        if new is _MISSING:
            patcher = patch.object(target, name, **kwargs)
        else:
            patcher = patch.object(target, name, new, **kwargs)

        patcher.start()
        self.addCleanup(patcher.stop)

    def configure(self, **overrides):
        values = {
            "TOOL_OS_AUTOMATION_ENABLED": True,
            "TOOL_CLIPBOARD_ENABLED": False,
            "TOOL_KEYBOARD_ENABLED": False,
            "TOOL_MOUSE_ENABLED": False,
        }
        values.update(overrides)

        for name, value in values.items():
            patcher = patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_every_tool_is_disabled_by_default(self):
        self.configure()

        for tool in DESKTOP_INTERACTION_TOOLS:
            with self.subTest(tool=tool.name):
                self.assertFalse(tool.enabled)

    def test_each_capability_is_independently_enabled(self):
        self.configure(
            TOOL_CLIPBOARD_ENABLED=True,
            TOOL_KEYBOARD_ENABLED=False,
            TOOL_MOUSE_ENABLED=False,
        )

        self.assertTrue(SystemClipboardReadTool().enabled)
        self.assertTrue(SystemClipboardWriteTool().enabled)
        self.assertFalse(SystemKeyboardPressTool().enabled)
        self.assertFalse(SystemMousePositionTool().enabled)
        self.assertFalse(SystemMouseMoveTool().enabled)
        self.assertFalse(SystemMouseClickTool().enabled)

        self.configure(
            TOOL_CLIPBOARD_ENABLED=False,
            TOOL_KEYBOARD_ENABLED=True,
            TOOL_MOUSE_ENABLED=False,
        )
        self.assertFalse(SystemClipboardReadTool().enabled)
        self.assertTrue(SystemKeyboardPressTool().enabled)
        self.assertFalse(SystemMousePositionTool().enabled)

        self.configure(
            TOOL_CLIPBOARD_ENABLED=False,
            TOOL_KEYBOARD_ENABLED=False,
            TOOL_MOUSE_ENABLED=True,
        )
        self.assertFalse(SystemClipboardReadTool().enabled)
        self.assertFalse(SystemKeyboardPressTool().enabled)
        self.assertTrue(SystemMousePositionTool().enabled)

    def test_unsupported_platform_disables_every_tool(self):
        self.configure(
            TOOL_CLIPBOARD_ENABLED=True,
            TOOL_KEYBOARD_ENABLED=True,
            TOOL_MOUSE_ENABLED=True,
        )
        self.patch_object(
            desktop_interaction,
            "_is_supported_platform",
            return_value=False,
        )

        for tool in DESKTOP_INTERACTION_TOOLS:
            with self.subTest(tool=tool.name):
                self.assertFalse(tool.enabled)

    def test_low_level_calls_fail_closed_on_unsupported_platform(self):
        self.patch_object(
            desktop_interaction,
            "_is_supported_platform",
            return_value=False,
        )

        for operation in (
            lambda: desktop_interaction._read_clipboard_text(10),
            lambda: desktop_interaction._write_clipboard_text("text"),
            desktop_interaction._get_cursor_position,
            lambda: desktop_interaction._set_cursor_position(1, 1),
            lambda: desktop_interaction._send_key(0x0D, False),
            desktop_interaction._send_left_click,
            desktop_interaction._screen_bounds,
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(ToolConfigurationError):
                    operation()

    def test_configuration_attributes_exist(self):
        for name in (
            "TOOL_CLIPBOARD_ENABLED",
            "TOOL_CLIPBOARD_MAX_READ_CHARS",
            "TOOL_CLIPBOARD_MAX_WRITE_CHARS",
            "TOOL_KEYBOARD_ENABLED",
            "TOOL_MOUSE_ENABLED",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(settings, name))


# ---------------------------------------------------------------------------
# Clipboard read
# ---------------------------------------------------------------------------


class ClipboardReadTests(DesktopInteractionTestBase):

    async def test_plain_text_is_returned_and_marked_untrusted(self):
        result = await self.run_tool(SystemClipboardReadTool())

        self.assertTrue(result.success)
        self.assertEqual(result.data["text"], "hello")
        self.assertEqual(result.data["char_count"], 5)
        self.assertFalse(result.data["truncated"])
        self.assertTrue(result.data["untrusted"])
        self.assertEqual(result.data["format"], "text/plain")
        self.assertIn("untrusted", result.data["notice"].lower())

    async def test_empty_clipboard_is_reported_without_text(self):
        self.read_clipboard.return_value = ClipboardRead(
            available=False,
            reason="empty",
        )

        result = await self.run_tool(SystemClipboardReadTool())

        self.assertTrue(result.success)
        self.assertFalse(result.data["available"])
        self.assertEqual(result.data["reason"], "empty")
        self.assertIsNone(result.data["text"])
        self.assertEqual(result.data["char_count"], 0)
        self.assertTrue(result.data["untrusted"])

    async def test_truncation_is_reported(self):
        self.read_clipboard.return_value = ClipboardRead(
            available=True,
            text="abcd",
            truncated=True,
        )

        result = await self.run_tool(SystemClipboardReadTool())

        self.assertTrue(result.data["truncated"])
        self.assertEqual(result.data["text"], "abcd")

    async def test_configured_read_limit_is_passed_to_the_api(self):
        self.configure(TOOL_CLIPBOARD_MAX_READ_CHARS=7)

        await self.run_tool(SystemClipboardReadTool())

        self.read_clipboard.assert_called_once_with(7)

    async def test_configured_read_limit_is_clamped(self):
        self.configure(TOOL_CLIPBOARD_MAX_READ_CHARS=10_000_000)

        await self.run_tool(SystemClipboardReadTool())

        self.read_clipboard.assert_called_once_with(
            desktop_interaction.MAX_CLIPBOARD_READ_CHARS
        )

    async def test_unavailable_clipboard_fails_safely(self):
        self.read_clipboard.side_effect = ToolExecutionError(
            "The clipboard is currently unavailable."
        )

        with self.assertRaises(ToolExecutionError) as raised:
            await self.run_tool(SystemClipboardReadTool())

        self.assertNotIn("Traceback", str(raised.exception))

    def test_summarize_does_not_include_content(self):
        tool = SystemClipboardReadTool()
        preview = tool.summarize(tool.validate_arguments({}))

        self.assertIn("clipboard", preview.lower())
        self.assertIn("sensitive", preview.lower())

    def test_safe_truncate_does_not_split_a_surrogate_pair(self):
        text = "a\ud83d\ude00b"

        self.assertEqual(
            desktop_interaction._safe_truncate(text, 2),
            "a",
        )
        self.assertEqual(
            desktop_interaction._safe_truncate(text, 3),
            "a\ud83d\ude00",
        )


# ---------------------------------------------------------------------------
# Clipboard write
# ---------------------------------------------------------------------------


class ClipboardWriteTests(DesktopInteractionTestBase):

    async def test_plain_text_is_written_without_echoing_it(self):
        result = await self.run_tool(
            SystemClipboardWriteTool(),
            {"text": "hello clipboard"},
        )

        self.assertTrue(result.success)
        self.write_clipboard.assert_called_once_with("hello clipboard")
        self.assertTrue(result.data["written"])
        self.assertEqual(result.data["char_count"], 15)
        self.assertTrue(result.data["replaced_previous"])
        self.assertEqual(result.data["format"], "text/plain")
        self.assertNotIn("hello clipboard", json.dumps(result.data))

    async def test_configured_write_limit_is_enforced(self):
        self.configure(TOOL_CLIPBOARD_MAX_WRITE_CHARS=5)

        with self.assertRaises(ToolSizeLimitError):
            SystemClipboardWriteTool().validate_arguments(
                {"text": "123456"}
            )

        self.write_clipboard.assert_not_called()

    async def test_hard_write_limit_is_enforced(self):
        with self.assertRaises(ToolValidationError):
            SystemClipboardWriteTool().validate_arguments(
                {"text": "a" * (MAX_CLIPBOARD_WRITE_CHARS + 1)}
            )

    async def test_binary_and_control_content_is_rejected(self):
        for text in (
            "a\x00b",
            "a\x01b",
            "a\x1fb",
            "\ud800",
        ):
            with self.subTest(text=repr(text)):
                with self.assertRaises(ToolValidationError):
                    SystemClipboardWriteTool().validate_arguments(
                        {"text": text}
                    )

    async def test_plain_whitespace_is_accepted(self):
        validated = SystemClipboardWriteTool().validate_arguments(
            {"text": "line one\nline two\tend\r\n"}
        )

        self.assertEqual(validated.text, "line one\nline two\tend\r\n")

    async def test_empty_and_non_string_content_is_rejected(self):
        for arguments in (
            {"text": ""},
            {"text": 123},
            {"text": None},
            {"text": ["a"]},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ToolValidationError):
                    SystemClipboardWriteTool().validate_arguments(arguments)

    async def test_unsupported_extra_arguments_are_rejected(self):
        for extra in (
            {"format": "html"},
            {"binary": True},
            {"text": "hello", "append": True},
        ):
            with self.subTest(extra=extra):
                with self.assertRaises(ToolValidationError):
                    SystemClipboardWriteTool().validate_arguments(extra)

    async def test_summarize_hides_the_text(self):
        tool = SystemClipboardWriteTool()
        secret = "sensitive-clipboard-value"
        validated = tool.validate_arguments({"text": secret})
        preview = tool.summarize(validated)

        self.assertNotIn(secret, preview)
        self.assertIn("characters", preview)
        self.assertIn("overwritten", preview)

    async def test_write_failure_is_safe(self):
        self.write_clipboard.side_effect = ToolExecutionError(
            "The clipboard could not be updated."
        )

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(
                SystemClipboardWriteTool(),
                {"text": "hello"},
            )


# ---------------------------------------------------------------------------
# Keyboard
# ---------------------------------------------------------------------------


class KeyboardTests(DesktopInteractionTestBase):

    async def test_every_allowed_key_presses_once(self):
        tool = SystemKeyboardPressTool()

        for key, (virtual_key, extended) in ALLOWED_KEYS.items():
            with self.subTest(key=key):
                self.send_key.reset_mock()
                self.send_key.return_value = True

                result = await self.run_tool(tool, {"key": key})

                self.assertTrue(result.success)
                self.assertEqual(result.data["key"], key)
                self.assertEqual(result.data["event_count"], 1)
                self.send_key.assert_called_once_with(
                    virtual_key,
                    extended,
                )

    async def test_key_names_are_normalized(self):
        tool = SystemKeyboardPressTool()

        for supplied, expected in (
            ("enter", "ENTER"),
            (" page up ", "PAGE_UP"),
            ("page-down", "PAGE_DOWN"),
            ("Esc", "ESC"),
        ):
            with self.subTest(supplied=supplied):
                validated = tool.validate_arguments({"key": supplied})
                self.assertEqual(validated.key, expected)

    async def test_unknown_and_combined_keys_are_rejected(self):
        for key in (
            "F1",
            "A",
            "CTRL+C",
            "ENTER+TAB",
            "ALT+F4",
            "VOLUME_UP",
            "PRINT_SCREEN",
            "WIN",
            "",
            "   ",
            "ENTER\x00",
        ):
            with self.subTest(key=key):
                with self.assertRaises(ToolValidationError):
                    SystemKeyboardPressTool().validate_arguments(
                        {"key": key}
                    )

        self.send_key.assert_not_called()

    async def test_held_and_repeated_arguments_are_rejected(self):
        for arguments in (
            {"key": "ENTER", "hold": True},
            {"key": "ENTER", "repeat": 2},
            {"key": "ENTER", "count": 2},
            {"key": "ENTER", "modifiers": ["CTRL"]},
            {"key": "ENTER", "text": "typed"},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ToolValidationError):
                    SystemKeyboardPressTool().validate_arguments(arguments)

    async def test_key_send_failure_is_safe(self):
        self.send_key.return_value = False

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(
                SystemKeyboardPressTool(),
                {"key": "ENTER"},
            )

    def test_summarize_names_only_the_allowed_key(self):
        tool = SystemKeyboardPressTool()
        preview = tool.summarize(tool.validate_arguments({"key": "ENTER"}))

        self.assertEqual(preview, "Press the ENTER key once.")


# ---------------------------------------------------------------------------
# Mouse
# ---------------------------------------------------------------------------


class MouseTests(DesktopInteractionTestBase):

    async def test_current_position_is_returned(self):
        result = await self.run_tool(SystemMousePositionTool())

        self.assertTrue(result.success)
        self.assertEqual(result.data, {"x": 100, "y": 200})
        self.get_position.assert_called_once_with()

    async def test_position_failure_is_safe(self):
        self.get_position.return_value = None

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(SystemMousePositionTool())

    async def test_move_uses_validated_coordinates(self):
        result = await self.run_tool(
            SystemMouseMoveTool(),
            {"x": 100, "y": 200},
        )

        self.assertTrue(result.success)
        self.set_position.assert_called_once_with(100, 200)
        self.assertTrue(result.data["moved"])
        self.assertEqual(result.data["x"], 100)
        self.assertEqual(result.data["y"], 200)

    async def test_move_rejects_out_of_bounds_coordinates(self):
        for arguments in (
            {"x": -1, "y": 0},
            {"x": 0, "y": -1},
            {"x": 1920, "y": 0},
            {"x": 0, "y": 1080},
            {"x": 5000, "y": 5000},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ToolValidationError):
                    SystemMouseMoveTool().validate_arguments(arguments)

        self.set_position.assert_not_called()

    async def test_move_accepts_negative_origin_bounds(self):
        self.screen = ScreenBounds(
            left=-100,
            top=-100,
            width=200,
            height=200,
        )
        self.patch_object(
            desktop_interaction,
            "_screen_bounds",
            return_value=self.screen,
        )

        for x, y in ((-100, -100), (99, 99), (0, 0)):
            with self.subTest(x=x, y=y):
                validated = SystemMouseMoveTool().validate_arguments(
                    {"x": x, "y": y}
                )
                self.assertEqual((validated.x, validated.y), (x, y))

        for x, y in ((-101, 0), (0, -101), (100, 0), (0, 100)):
            with self.subTest(x=x, y=y):
                with self.assertRaises(ToolValidationError):
                    SystemMouseMoveTool().validate_arguments(
                        {"x": x, "y": y}
                    )

    async def test_move_rejects_invalid_coordinate_types(self):
        for arguments in (
            {"x": "abc", "y": 0},
            {"x": 1.5, "y": 0},
            {"x": True, "y": 0},
            {"x": 0},
            {"y": 0},
            {"x": 0, "y": 0, "relative": True},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ToolValidationError):
                    SystemMouseMoveTool().validate_arguments(arguments)

    async def test_move_failure_is_safe(self):
        self.set_position.return_value = False

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(
                SystemMouseMoveTool(),
                {"x": 10, "y": 10},
            )

    async def test_single_left_click_uses_validated_coordinates(self):
        result = await self.run_tool(
            SystemMouseClickTool(),
            {"x": 100, "y": 200},
        )

        self.assertTrue(result.success)
        self.set_position.assert_called_once_with(100, 200)
        self.send_click.assert_called_once_with()
        self.assertTrue(result.data["clicked"])
        self.assertEqual(result.data["button"], "left")
        self.assertEqual(result.data["click_count"], 1)

    async def test_click_rejects_out_of_bounds_coordinates(self):
        for arguments in (
            {"x": 1920, "y": 0},
            {"x": 0, "y": 1080},
            {"x": -1, "y": 0},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ToolValidationError):
                    SystemMouseClickTool().validate_arguments(arguments)

        self.set_position.assert_not_called()
        self.send_click.assert_not_called()

    async def test_click_rejects_unsupported_click_types(self):
        for extra in (
            {"button": "right"},
            {"button": "middle"},
            {"double": True},
            {"count": 2},
            {"scroll": 1},
            {"drag": True},
            {"x": 1, "y": 1, "hold": True},
        ):
            with self.subTest(extra=extra):
                arguments = {"x": 1, "y": 1}
                arguments.update(extra)

                with self.assertRaises(ToolValidationError):
                    SystemMouseClickTool().validate_arguments(arguments)

    async def test_click_failure_does_not_click(self):
        self.set_position.return_value = False

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(
                SystemMouseClickTool(),
                {"x": 10, "y": 10},
            )

        self.send_click.assert_not_called()

        self.set_position.return_value = True
        self.send_click.return_value = False

        with self.assertRaises(ToolExecutionError):
            await self.run_tool(
                SystemMouseClickTool(),
                {"x": 10, "y": 10},
            )

    def test_summaries_include_only_coordinates(self):
        move = SystemMouseMoveTool()
        move_preview = move.summarize(
            move.validate_arguments({"x": 10, "y": 20})
        )
        click = SystemMouseClickTool()
        click_preview = click.summarize(
            click.validate_arguments({"x": 30, "y": 40})
        )

        self.assertIn("(10, 20)", move_preview)
        self.assertIn("(30, 40)", click_preview)
        self.assertIn("left click", click_preview.lower())


# ---------------------------------------------------------------------------
# Low-level Windows API wrappers
# ---------------------------------------------------------------------------


class LowLevelWindowsApiTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the narrow ctypes wrappers with fake DLL objects."""

    def setUp(self):
        self.user32 = Mock()
        self.kernel32 = Mock()
        self.patch_object(
            desktop_interaction,
            "_is_supported_platform",
            return_value=True,
        )
        self.patch_object(
            desktop_interaction.ctypes,
            "WinDLL",
            side_effect=self._load_dll,
        )
        self.patch_object(
            desktop_interaction.time,
            "sleep",
            return_value=None,
        )

    def _load_dll(self, name, **_kwargs):
        return self.user32 if name == "user32" else self.kernel32

    def patch_object(self, target, name, new=_MISSING, **kwargs):
        if new is _MISSING:
            patcher = patch.object(target, name, **kwargs)
        else:
            patcher = patch.object(target, name, new, **kwargs)

        patcher.start()
        self.addCleanup(patcher.stop)

    def _clipboard_open(self):
        self.user32.OpenClipboard.return_value = 1
        self.user32.CloseClipboard.return_value = 1
        self.user32.IsClipboardFormatAvailable.return_value = 1
        self.user32.GetClipboardData.return_value = 123
        self.kernel32.GlobalLock.return_value = 0
        self.kernel32.GlobalUnlock.return_value = 1

    def test_read_plain_text(self):
        buffer = ctypes.create_unicode_buffer("hello")
        self._clipboard_open()
        self.kernel32.GlobalLock.return_value = ctypes.addressof(buffer)
        self.kernel32.GlobalSize.return_value = ctypes.sizeof(buffer)

        result = desktop_interaction._read_clipboard_text(100)

        self.assertTrue(result.available)
        self.assertEqual(result.text, "hello")
        self.assertFalse(result.truncated)
        self.user32.IsClipboardFormatAvailable.assert_called_once_with(
            CF_UNICODETEXT
        )
        self.user32.GetClipboardData.assert_called_once_with(CF_UNICODETEXT)
        self.user32.CloseClipboard.assert_called_once_with()

    def test_read_truncates_to_the_configured_limit(self):
        buffer = ctypes.create_unicode_buffer("abcdefgh")
        self._clipboard_open()
        self.kernel32.GlobalLock.return_value = ctypes.addressof(buffer)
        self.kernel32.GlobalSize.return_value = ctypes.sizeof(buffer)

        result = desktop_interaction._read_clipboard_text(4)

        self.assertTrue(result.available)
        self.assertEqual(result.text, "abcd")
        self.assertTrue(result.truncated)

    def test_read_empty_clipboard(self):
        self._clipboard_open()
        self.user32.IsClipboardFormatAvailable.return_value = 0

        result = desktop_interaction._read_clipboard_text(100)

        self.assertFalse(result.available)
        self.assertEqual(result.reason, "empty")
        self.user32.GetClipboardData.assert_not_called()
        self.user32.CloseClipboard.assert_called_once_with()

    def test_read_unavailable_clipboard(self):
        self._clipboard_open()
        self.user32.OpenClipboard.return_value = 0

        with self.assertRaises(ToolExecutionError):
            desktop_interaction._read_clipboard_text(100)

        self.user32.CloseClipboard.assert_not_called()

    def test_read_lock_failure_is_safe(self):
        self._clipboard_open()
        self.kernel32.GlobalLock.return_value = 0

        with self.assertRaises(ToolExecutionError):
            desktop_interaction._read_clipboard_text(100)

        self.user32.CloseClipboard.assert_called_once_with()

    def test_write_plain_text_replaces_clipboard(self):
        target = ctypes.create_unicode_buffer(64)
        self._clipboard_open()
        self.user32.EmptyClipboard.return_value = 1
        self.user32.SetClipboardData.return_value = 456
        self.kernel32.GlobalAlloc.return_value = 456
        self.kernel32.GlobalLock.return_value = ctypes.addressof(target)

        desktop_interaction._write_clipboard_text("hello")

        self.user32.EmptyClipboard.assert_called_once_with()
        self.user32.SetClipboardData.assert_called_once_with(
            CF_UNICODETEXT,
            456,
        )
        self.assertEqual(ctypes.wstring_at(target), "hello")
        self.kernel32.GlobalFree.assert_not_called()
        self.user32.CloseClipboard.assert_called_once_with()

    def test_write_failure_frees_the_buffer(self):
        target = ctypes.create_unicode_buffer(64)
        self._clipboard_open()
        self.user32.EmptyClipboard.return_value = 1
        self.user32.SetClipboardData.return_value = 0
        self.kernel32.GlobalAlloc.return_value = 456
        self.kernel32.GlobalLock.return_value = ctypes.addressof(target)

        with self.assertRaises(ToolExecutionError):
            desktop_interaction._write_clipboard_text("hello")

        self.kernel32.GlobalFree.assert_called_once_with(456)
        self.user32.CloseClipboard.assert_called_once_with()

    def test_write_unavailable_clipboard(self):
        self._clipboard_open()
        self.user32.OpenClipboard.return_value = 0

        with self.assertRaises(ToolExecutionError):
            desktop_interaction._write_clipboard_text("hello")

        self.user32.EmptyClipboard.assert_not_called()

    def test_get_cursor_position(self):
        def _get_cursor_pos(pointer):
            point = ctypes.cast(
                pointer,
                ctypes.POINTER(desktop_interaction._POINT),
            ).contents
            point.x = 42
            point.y = 84
            return 1

        self.user32.GetCursorPos.side_effect = _get_cursor_pos

        self.assertEqual(
            desktop_interaction._get_cursor_position(),
            (42, 84),
        )

    def test_get_cursor_position_failure(self):
        self.user32.GetCursorPos.return_value = 0

        self.assertIsNone(desktop_interaction._get_cursor_position())

    def test_set_cursor_position(self):
        self.user32.SetCursorPos.return_value = 1

        self.assertTrue(
            desktop_interaction._set_cursor_position(10, 20)
        )
        self.user32.SetCursorPos.assert_called_once_with(10, 20)

    def test_set_cursor_position_failure(self):
        self.user32.SetCursorPos.return_value = 0

        self.assertFalse(
            desktop_interaction._set_cursor_position(10, 20)
        )

    def test_send_key_uses_one_down_and_one_up_event(self):
        self.user32.SendInput.return_value = 2

        self.assertTrue(desktop_interaction._send_key(0x0D, False))

        _count, inputs, _size = self.user32.SendInput.call_args.args
        self.assertEqual(inputs[0].type, desktop_interaction.INPUT_KEYBOARD)
        self.assertEqual(inputs[0].union.ki.wVk, 0x0D)
        self.assertEqual(inputs[0].union.ki.dwFlags, 0)
        self.assertEqual(
            inputs[1].union.ki.dwFlags,
            KEYEVENTF_KEYUP,
        )

    def test_send_key_marks_extended_keys(self):
        self.user32.SendInput.return_value = 2

        self.assertTrue(desktop_interaction._send_key(0x26, True))

        _count, inputs, _size = self.user32.SendInput.call_args.args
        self.assertEqual(
            inputs[0].union.ki.dwFlags,
            KEYEVENTF_EXTENDEDKEY,
        )
        self.assertEqual(
            inputs[1].union.ki.dwFlags,
            KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP,
        )

    def test_send_key_failure(self):
        self.user32.SendInput.return_value = 0

        self.assertFalse(desktop_interaction._send_key(0x0D, False))

    def test_send_left_click_uses_only_left_button_events(self):
        self.user32.SendInput.return_value = 2

        self.assertTrue(desktop_interaction._send_left_click())

        _count, inputs, _size = self.user32.SendInput.call_args.args
        self.assertEqual(inputs[0].type, desktop_interaction.INPUT_MOUSE)
        self.assertEqual(
            inputs[0].union.mi.dwFlags,
            MOUSEEVENTF_LEFTDOWN,
        )
        self.assertEqual(
            inputs[1].union.mi.dwFlags,
            MOUSEEVENTF_LEFTUP,
        )

    def test_send_left_click_failure(self):
        self.user32.SendInput.return_value = 1

        self.assertFalse(desktop_interaction._send_left_click())

    def test_screen_bounds(self):
        values = {
            desktop_interaction.SM_XVIRTUALSCREEN: -100,
            desktop_interaction.SM_YVIRTUALSCREEN: -50,
            desktop_interaction.SM_CXVIRTUALSCREEN: 2000,
            desktop_interaction.SM_CYVIRTUALSCREEN: 1200,
        }
        self.user32.GetSystemMetrics.side_effect = values.__getitem__

        self.assertEqual(
            desktop_interaction._screen_bounds(),
            ScreenBounds(left=-100, top=-50, width=2000, height=1200),
        )

    def test_screen_bounds_fail_closed(self):
        self.user32.GetSystemMetrics.return_value = 0

        with self.assertRaises(ToolExecutionError):
            desktop_interaction._screen_bounds()


# ---------------------------------------------------------------------------
# Permission policy
# ---------------------------------------------------------------------------


class PermissionPolicyTests(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        for name, value in (
            ("TOOL_OS_AUTOMATION_ENABLED", True),
            ("TOOL_CLIPBOARD_ENABLED", True),
            ("TOOL_KEYBOARD_ENABLED", True),
            ("TOOL_MOUSE_ENABLED", True),
        ):
            patcher = patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        patcher = patch.object(
            desktop_interaction,
            "_is_supported_platform",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_confirmation_requirements_and_categories(self):
        expected = {
            "system_clipboard_read": (ToolCategory.READ_ONLY, True),
            "system_clipboard_write": (ToolCategory.REVERSIBLE, True),
            "system_keyboard_press": (ToolCategory.REVERSIBLE, True),
            "system_mouse_position": (ToolCategory.READ_ONLY, False),
            "system_mouse_move": (ToolCategory.REVERSIBLE, True),
            "system_mouse_click": (ToolCategory.REVERSIBLE, True),
        }

        for tool in DESKTOP_INTERACTION_TOOLS:
            with self.subTest(tool=tool.name):
                category, requires_confirmation = expected[tool.name]
                decision = permission_policy.evaluate(
                    tool,
                    private=False,
                )

                self.assertTrue(decision.allowed)
                self.assertEqual(decision.category, category)
                self.assertEqual(
                    decision.requires_confirmation,
                    requires_confirmation,
                )

    def test_every_tool_is_private_denied(self):
        for tool in DESKTOP_INTERACTION_TOOLS:
            with self.subTest(tool=tool.name):
                self.assertFalse(tool.allowed_in_private)
                decision = permission_policy.evaluate(tool, private=True)

                self.assertFalse(decision.allowed)
                self.assertEqual(
                    decision.error_category,
                    ToolErrorCategory.PERMISSION,
                )

    def test_disabled_tools_are_denied(self):
        for name in (
            "TOOL_CLIPBOARD_ENABLED",
            "TOOL_KEYBOARD_ENABLED",
            "TOOL_MOUSE_ENABLED",
        ):
            with self.subTest(name=name):
                patcher = patch.object(settings, name, False)
                patcher.start()
                self.addCleanup(patcher.stop)

        for tool in DESKTOP_INTERACTION_TOOLS:
            with self.subTest(tool=tool.name):
                decision = permission_policy.evaluate(
                    tool,
                    private=False,
                )

                self.assertFalse(decision.allowed)
                self.assertEqual(
                    decision.error_category,
                    ToolErrorCategory.DISABLED,
                )

    def test_private_request_raises_private_error(self):
        for tool in DESKTOP_INTERACTION_TOOLS:
            with self.subTest(tool=tool.name):
                with self.assertRaises(Exception) as raised:
                    permission_policy.require_allowed(tool, private=True)

                self.assertEqual(
                    type(raised.exception).__name__,
                    "ToolPrivateRequestError",
                )


# ---------------------------------------------------------------------------
# Registration and discovery
# ---------------------------------------------------------------------------


class RegistrationTests(unittest.TestCase):

    def test_tools_are_registered(self):
        self.assertTrue(TOOL_NAMES.issubset(set(tool_registry.names())))

    def test_builtin_tool_list_contains_each_tool_once(self):
        names = [tool.name for tool in BUILTIN_TOOLS]

        for name in TOOL_NAMES:
            with self.subTest(name=name):
                self.assertEqual(names.count(name), 1)

    def test_existing_phase_tools_are_still_registered(self):
        self.assertTrue(
            {
                "task_list",
                "web_search",
                "fs_read",
                "fs_exists",
                "python_execute",
                "http_request",
                "system_os_info",
                "system_summary",
                "system_launch_application",
                "system_process_list",
                "system_active_window",
            }.issubset(set(tool_registry.names()))
        )

    def test_provider_schema_is_function_calling_compatible(self):
        for name in TOOL_NAMES:
            with self.subTest(name=name):
                schema = tool_registry.get(name).definition().provider_schema()

                self.assertEqual(schema["type"], "function")
                self.assertEqual(schema["function"]["name"], name)
                self.assertEqual(
                    schema["function"]["parameters"]["type"],
                    "object",
                )

    def test_llm_definitions_include_enabled_tools(self):
        with (
            patch.object(settings, "TOOL_OS_AUTOMATION_ENABLED", True),
            patch.object(settings, "TOOL_CLIPBOARD_ENABLED", True),
            patch.object(settings, "TOOL_KEYBOARD_ENABLED", True),
            patch.object(settings, "TOOL_MOUSE_ENABLED", True),
            patch.object(
                desktop_interaction,
                "_is_supported_platform",
                return_value=True,
            ),
        ):
            names = {
                item["function"]["name"]
                for item in tool_registry.llm_definitions(private=False)
            }

        self.assertTrue(TOOL_NAMES.issubset(names))

    def test_llm_definitions_exclude_disabled_tools(self):
        with (
            patch.object(settings, "TOOL_OS_AUTOMATION_ENABLED", True),
            patch.object(settings, "TOOL_CLIPBOARD_ENABLED", False),
            patch.object(settings, "TOOL_KEYBOARD_ENABLED", False),
            patch.object(settings, "TOOL_MOUSE_ENABLED", False),
            patch.object(
                desktop_interaction,
                "_is_supported_platform",
                return_value=True,
            ),
        ):
            names = {
                item["function"]["name"]
                for item in tool_registry.llm_definitions(private=False)
            }

        self.assertFalse(TOOL_NAMES & names)

    def test_llm_definitions_exclude_private_mode(self):
        with (
            patch.object(settings, "TOOL_OS_AUTOMATION_ENABLED", True),
            patch.object(settings, "TOOL_CLIPBOARD_ENABLED", True),
            patch.object(settings, "TOOL_KEYBOARD_ENABLED", True),
            patch.object(settings, "TOOL_MOUSE_ENABLED", True),
            patch.object(
                desktop_interaction,
                "_is_supported_platform",
                return_value=True,
            ),
        ):
            names = {
                item["function"]["name"]
                for item in tool_registry.llm_definitions(private=True)
            }

        self.assertFalse(TOOL_NAMES & names)

    def test_keyboard_schema_documents_the_allowlist(self):
        description = tool_registry.get(
            "system_keyboard_press"
        ).definition().description

        for key in KEY_NAMES:
            with self.subTest(key=key):
                self.assertIn(key, description)

    def test_mouse_schema_documents_the_restriction(self):
        description = tool_registry.get(
            "system_mouse_click"
        ).definition().description.lower()

        self.assertIn("one left click", description)
        self.assertIn("double-click", description)
        self.assertIn("right-click", description)


# ---------------------------------------------------------------------------
# Source-level safety checks
# ---------------------------------------------------------------------------


class SourceSafetyTests(unittest.TestCase):

    def setUp(self):
        self.source = Path(desktop_interaction.__file__).read_text(
            encoding="utf-8"
        )

    def test_no_shell_or_process_execution(self):
        for token in (
            "subprocess",
            "Popen",
            "os.system",
            "os.popen",
            "powershell",
            "cmd.exe",
            "OpenProcess",
            "TerminateProcess",
            "CreateProcess",
            "ShellExecute",
            "WinExec",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, self.source)

    def test_no_screenshot_or_screen_capture(self):
        for token in (
            "BitBlt",
            "PrintWindow",
            "GetDC",
            "GetWindowDC",
            "CreateCompatibleDC",
            "CreateCompatibleBitmap",
            "GetPixel",
            "GetDIBits",
            "CAPTUREBLT",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, self.source)

    def test_no_keylogging_or_input_hooks(self):
        for token in (
            "GetAsyncKeyState",
            "GetKeyState",
            "GetKeyboardState",
            "SetWindowsHookEx",
            "CallNextHookEx",
            "UnhookWindowsHookEx",
            "RegisterRawInputDevices",
            "GetRawInputData",
            "WH_KEYBOARD",
            "WH_MOUSE",
            "RawInput",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, self.source)

    def test_no_secure_desktop_or_elevation(self):
        for token in (
            "OpenInputDesktop",
            "SwitchDesktop",
            "SetThreadDesktop",
            "AdjustTokenPrivileges",
            "SeDebugPrivilege",
            "runas",
            "CreateProcessAsUser",
            "LogonUser",
            "ImpersonateLoggedOnUser",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, self.source)

    def test_no_unrestricted_automation_library(self):
        for token in (
            "pyautogui",
            "PyAutoGUI",
            "pynput",
            "keyboard.",
            "mouse.",
            "AutoHotkey",
            "ahk.",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, self.source)

    def test_no_arbitrary_typing_or_legacy_input(self):
        for token in (
            "VkKeyScan",
            "MapVirtualKey",
            "ToUnicode",
            "keybd_event",
            "mouse_event",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, self.source)

    def test_no_right_middle_double_or_wheel_click(self):
        for token in (
            "MOUSEEVENTF_RIGHTDOWN",
            "MOUSEEVENTF_RIGHTUP",
            "MOUSEEVENTF_MIDDLEDOWN",
            "MOUSEEVENTF_MIDDLEUP",
            "MOUSEEVENTF_WHEEL",
            "MOUSEEVENTF_HWHEEL",
            "MOUSEEVENTF_MOVE",
            "MOUSEEVENTF_ABSOLUTE",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, self.source)

    def test_only_plain_text_clipboard_format_is_used(self):
        self.assertIn("CF_UNICODETEXT", self.source)

        for token in (
            "CF_HTML",
            "CF_BITMAP",
            "CF_HDROP",
            "CF_DIB",
            "CF_TEXT",
            "RegisterClipboardFormat",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, self.source)

    def test_key_allowlist_is_explicit_and_bounded(self):
        self.assertIn("ALLOWED_KEYS", self.source)
        self.assertEqual(len(ALLOWED_KEYS), 14)
        self.assertEqual(
            set(ALLOWED_KEYS),
            {
                "ENTER",
                "ESC",
                "TAB",
                "BACKSPACE",
                "DELETE",
                "SPACE",
                "UP",
                "DOWN",
                "LEFT",
                "RIGHT",
                "HOME",
                "END",
                "PAGE_UP",
                "PAGE_DOWN",
            },
        )

    def test_send_input_is_used_only_for_one_key_or_one_click(self):
        self.assertIn("SendInput", self.source)
        self.assertEqual(self.source.count("SendInput("), 2)


# ---------------------------------------------------------------------------
# Executor integration and confirmation workflow
# ---------------------------------------------------------------------------


class ExecutorIntegrationTests(ToolEngineTestBase):

    async def asyncSetUp(self):
        await super().asyncSetUp()

        self.settings(
            TOOL_OS_AUTOMATION_ENABLED=True,
            TOOL_CLIPBOARD_ENABLED=True,
            TOOL_KEYBOARD_ENABLED=True,
            TOOL_MOUSE_ENABLED=True,
            TOOL_CLIPBOARD_MAX_READ_CHARS=100,
            TOOL_CLIPBOARD_MAX_WRITE_CHARS=100,
        )

        self.screen = ScreenBounds(
            left=0,
            top=0,
            width=1920,
            height=1080,
        )
        self.read_clipboard = Mock(
            return_value=ClipboardRead(
                available=True,
                text="clipboard-secret-value",
            )
        )
        self.write_clipboard = Mock(return_value=None)
        self.get_position = Mock(return_value=(10, 20))
        self.set_position = Mock(return_value=True)
        self.send_key = Mock(return_value=True)
        self.send_click = Mock(return_value=True)

        for target, name, value in (
            (
                desktop_interaction,
                "_is_supported_platform",
                Mock(return_value=True),
            ),
            (
                desktop_interaction,
                "_screen_bounds",
                Mock(return_value=self.screen),
            ),
            (
                desktop_interaction,
                "_read_clipboard_text",
                self.read_clipboard,
            ),
            (
                desktop_interaction,
                "_write_clipboard_text",
                self.write_clipboard,
            ),
            (
                desktop_interaction,
                "_get_cursor_position",
                self.get_position,
            ),
            (
                desktop_interaction,
                "_set_cursor_position",
                self.set_position,
            ),
            (desktop_interaction, "_send_key", self.send_key),
            (desktop_interaction, "_send_left_click", self.send_click),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def run_exec(
        self,
        tool_name,
        arguments=None,
        *,
        private=False,
        user_id=None,
        confirmation_id=None,
        create_confirmation=True,
        conversation_id=None,
    ):
        async with self.SessionLocal() as session:
            context = ToolContext(
                session=session,
                user_id=user_id or self.alice_id,
                conversation_id=conversation_id,
                private=private,
            )
            return await tool_executor.execute(
                tool_name=tool_name,
                arguments=arguments or {},
                context=context,
                confirmation_id=confirmation_id,
                create_confirmation=create_confirmation,
            )

    async def stored_confirmation(self, confirmation_id):
        async with self.SessionLocal() as session:
            return await tool_confirmation_service.get(
                session=session,
                user_id=self.alice_id,
                confirmation_id=confirmation_id,
            )

    async def history_rows(self):
        async with self.SessionLocal() as session:
            return (
                await session.execute(select(ToolExecution))
            ).scalars().all()

    async def confirmation_rows(self):
        async with self.SessionLocal() as session:
            return (
                await session.execute(select(ToolConfirmation))
            ).scalars().all()

    async def create_conversation(self, user_id=None):
        async with self.SessionLocal() as session:
            conversation = Conversation(
                user_id=user_id or self.alice_id,
                title="Phase 12E",
            )
            session.add(conversation)
            await session.commit()
            await session.refresh(conversation)
            return conversation.id

    async def test_conversation_mismatch_is_denied(self):
        first = await self.create_conversation()
        second = await self.create_conversation()
        pending = await self.run_exec(
            "system_clipboard_read",
            conversation_id=first,
        )

        result = await self.run_exec(
            "system_clipboard_read",
            conversation_id=second,
            confirmation_id=pending.confirmation_id,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.read_clipboard.assert_not_called()

    async def test_clipboard_read_requires_confirmation_and_does_not_read(self):
        result = await self.run_exec("system_clipboard_read")

        self.assertFalse(result.success)
        self.assertEqual(
            result.status,
            ToolExecutionStatus.CONFIRMATION_REQUIRED,
        )
        self.assertTrue(result.confirmation_required)
        self.assertEqual(
            result.confirmation_status,
            ConfirmationState.PENDING,
        )
        self.read_clipboard.assert_not_called()

    async def test_approved_clipboard_read_runs_once(self):
        pending = await self.run_exec("system_clipboard_read")

        approved = await self.run_exec(
            "system_clipboard_read",
            confirmation_id=pending.confirmation_id,
        )

        self.assertTrue(approved.success)
        self.assertEqual(
            approved.confirmation_status,
            ConfirmationState.CONFIRMED,
        )
        self.assertEqual(
            approved.data["text"],
            "clipboard-secret-value",
        )
        self.read_clipboard.assert_called_once_with(100)

    async def test_replayed_clipboard_read_is_denied(self):
        pending = await self.run_exec("system_clipboard_read")
        first = await self.run_exec(
            "system_clipboard_read",
            confirmation_id=pending.confirmation_id,
        )
        replay = await self.run_exec(
            "system_clipboard_read",
            confirmation_id=pending.confirmation_id,
        )

        self.assertTrue(first.success)
        self.assertFalse(replay.success)
        self.assertEqual(replay.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            replay.error_category,
            ToolErrorCategory.CONFIRMATION,
        )
        self.assertEqual(self.read_clipboard.call_count, 1)

    async def test_rejected_clipboard_read_does_not_read(self):
        pending = await self.run_exec("system_clipboard_read")

        async with self.SessionLocal() as session:
            await tool_confirmation_service.reject(
                session=session,
                user_id=self.alice_id,
                confirmation_id=pending.confirmation_id,
            )

        result = await self.run_exec(
            "system_clipboard_read",
            confirmation_id=pending.confirmation_id,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.read_clipboard.assert_not_called()

    async def test_expired_clipboard_read_does_not_read(self):
        async with self.SessionLocal() as session:
            pending = await tool_confirmation_service.create_pending(
                session=session,
                user_id=self.alice_id,
                conversation_id=None,
                tool_name="system_clipboard_read",
                category=ToolCategory.READ_ONLY,
                arguments={},
                arguments_preview="read clipboard",
                ttl_seconds=30,
            )

        from app.services import tool_confirmation_service as service_module

        original_now = service_module._utcnow()

        with patch.object(
            service_module,
            "_utcnow",
            return_value=original_now + timedelta(hours=1),
        ):
            result = await self.run_exec(
                "system_clipboard_read",
                confirmation_id=pending.id,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.confirmation_status,
            ConfirmationState.EXPIRED,
        )
        self.read_clipboard.assert_not_called()

    async def test_owner_mismatch_is_denied(self):
        pending = await self.run_exec("system_clipboard_read")

        result = await self.run_exec(
            "system_clipboard_read",
            user_id=self.bob_id,
            confirmation_id=pending.confirmation_id,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.read_clipboard.assert_not_called()

    async def test_tool_mismatch_is_denied(self):
        pending = await self.run_exec("system_keyboard_press", {"key": "ENTER"})

        result = await self.run_exec(
            "system_clipboard_write",
            {"text": "hello"},
            confirmation_id=pending.confirmation_id,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.write_clipboard.assert_not_called()
        self.send_key.assert_not_called()

    async def test_clipboard_write_confirmation_preview_hides_text(self):
        secret = "confirmation-secret-value"
        pending = await self.run_exec(
            "system_clipboard_write",
            {"text": secret},
        )

        self.assertFalse(pending.success)
        self.assertEqual(
            pending.status,
            ToolExecutionStatus.CONFIRMATION_REQUIRED,
        )
        self.assertNotIn(
            secret,
            json.dumps(pending.metadata, default=str),
        )
        stored = await self.stored_confirmation(pending.confirmation_id)
        self.assertNotIn(secret, stored.arguments_preview)
        self.write_clipboard.assert_not_called()

    async def test_approved_clipboard_write_runs_once(self):
        pending = await self.run_exec(
            "system_clipboard_write",
            {"text": "hello"},
        )
        approved = await self.run_exec(
            "system_clipboard_write",
            {"text": "hello"},
            confirmation_id=pending.confirmation_id,
        )

        self.assertTrue(approved.success)
        self.write_clipboard.assert_called_once_with("hello")
        self.assertNotIn("hello", json.dumps(approved.data))

    async def test_altered_clipboard_write_is_denied(self):
        pending = await self.run_exec(
            "system_clipboard_write",
            {"text": "alpha"},
        )

        altered = await self.run_exec(
            "system_clipboard_write",
            {"text": "beta"},
            confirmation_id=pending.confirmation_id,
        )

        self.assertFalse(altered.success)
        self.assertEqual(altered.status, ToolExecutionStatus.DENIED)
        self.write_clipboard.assert_not_called()

        approved = await self.run_exec(
            "system_clipboard_write",
            {"text": "alpha"},
            confirmation_id=pending.confirmation_id,
        )

        self.assertTrue(approved.success)
        self.write_clipboard.assert_called_once_with("alpha")

    async def test_clipboard_write_replay_is_denied(self):
        pending = await self.run_exec(
            "system_clipboard_write",
            {"text": "hello"},
        )
        first = await self.run_exec(
            "system_clipboard_write",
            {"text": "hello"},
            confirmation_id=pending.confirmation_id,
        )
        replay = await self.run_exec(
            "system_clipboard_write",
            {"text": "hello"},
            confirmation_id=pending.confirmation_id,
        )

        self.assertTrue(first.success)
        self.assertFalse(replay.success)
        self.assertEqual(replay.status, ToolExecutionStatus.DENIED)
        self.assertEqual(self.write_clipboard.call_count, 1)

    async def test_clipboard_write_history_and_confirmation_do_not_store_text(self):
        secret = "history-secret-value"
        pending = await self.run_exec(
            "system_clipboard_write",
            {"text": secret},
        )
        result = await self.run_exec(
            "system_clipboard_write",
            {"text": secret},
            confirmation_id=pending.confirmation_id,
        )

        self.assertTrue(result.success)
        self.assertIsNotNone(result.execution_id)

        for row in await self.history_rows():
            with self.subTest(history=row.id):
                for column in row.__table__.columns:
                    value = getattr(row, column.name)
                    self.assertNotIn(secret, repr(value))

        for row in await self.confirmation_rows():
            with self.subTest(confirmation=row.id):
                self.assertNotIn(secret, repr(row.arguments))
                self.assertNotIn(secret, row.arguments_preview)

        stored = await self.stored_confirmation(pending.confirmation_id)
        self.assertEqual(stored.status, ToolConfirmationStatus.EXECUTED)
        self.assertFalse(stored.arguments)

    async def test_clipboard_write_rejected_confirmation_does_not_write(self):
        pending = await self.run_exec(
            "system_clipboard_write",
            {"text": "hello"},
        )

        async with self.SessionLocal() as session:
            await tool_confirmation_service.reject(
                session=session,
                user_id=self.alice_id,
                confirmation_id=pending.confirmation_id,
            )

        result = await self.run_exec(
            "system_clipboard_write",
            {"text": "hello"},
            confirmation_id=pending.confirmation_id,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.write_clipboard.assert_not_called()

    async def test_clipboard_write_expired_confirmation_does_not_write(self):
        async with self.SessionLocal() as session:
            pending = await tool_confirmation_service.create_pending(
                session=session,
                user_id=self.alice_id,
                conversation_id=None,
                tool_name="system_clipboard_write",
                category=ToolCategory.REVERSIBLE,
                arguments={"text": "hello"},
                arguments_preview="write 5 characters",
                ttl_seconds=30,
            )

        from app.services import tool_confirmation_service as service_module

        original_now = service_module._utcnow()

        with patch.object(
            service_module,
            "_utcnow",
            return_value=original_now + timedelta(hours=1),
        ):
            result = await self.run_exec(
                "system_clipboard_write",
                {"text": "hello"},
                confirmation_id=pending.id,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolExecutionStatus.DENIED)
        self.assertEqual(
            result.confirmation_status,
            ConfirmationState.EXPIRED,
        )
        self.write_clipboard.assert_not_called()

    async def test_keyboard_requires_confirmation_and_runs_once(self):
        pending = await self.run_exec(
            "system_keyboard_press",
            {"key": "ENTER"},
        )

        self.assertFalse(pending.success)
        self.assertEqual(
            pending.status,
            ToolExecutionStatus.CONFIRMATION_REQUIRED,
        )
        self.send_key.assert_not_called()

        approved = await self.run_exec(
            "system_keyboard_press",
            {"key": "ENTER"},
            confirmation_id=pending.confirmation_id,
        )

        self.assertTrue(approved.success)
        self.send_key.assert_called_once_with(0x0D, False)

    async def test_keyboard_replay_is_denied(self):
        pending = await self.run_exec(
            "system_keyboard_press",
            {"key": "ENTER"},
        )
        first = await self.run_exec(
            "system_keyboard_press",
            {"key": "ENTER"},
            confirmation_id=pending.confirmation_id,
        )
        replay = await self.run_exec(
            "system_keyboard_press",
            {"key": "ENTER"},
            confirmation_id=pending.confirmation_id,
        )

        self.assertTrue(first.success)
        self.assertFalse(replay.success)
        self.assertEqual(self.send_key.call_count, 1)

    async def test_unknown_key_is_rejected_before_confirmation(self):
        result = await self.run_exec(
            "system_keyboard_press",
            {"key": "CTRL+C"},
        )

        self.assertFalse(result.success)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.VALIDATION,
        )
        self.assertIsNone(result.confirmation_id)
        self.send_key.assert_not_called()

    async def test_mouse_position_needs_no_confirmation(self):
        result = await self.run_exec("system_mouse_position")

        self.assertTrue(result.success)
        self.assertEqual(
            result.confirmation_status,
            ConfirmationState.NOT_REQUIRED,
        )
        self.assertEqual(result.data, {"x": 10, "y": 20})
        self.get_position.assert_called_once_with()

    async def test_mouse_move_requires_confirmation_and_runs_once(self):
        pending = await self.run_exec(
            "system_mouse_move",
            {"x": 100, "y": 200},
        )

        self.assertFalse(pending.success)
        self.assertEqual(
            pending.status,
            ToolExecutionStatus.CONFIRMATION_REQUIRED,
        )
        self.set_position.assert_not_called()

        approved = await self.run_exec(
            "system_mouse_move",
            {"x": 100, "y": 200},
            confirmation_id=pending.confirmation_id,
        )

        self.assertTrue(approved.success)
        self.set_position.assert_called_once_with(100, 200)

    async def test_mouse_move_out_of_bounds_is_rejected_before_confirmation(self):
        result = await self.run_exec(
            "system_mouse_move",
            {"x": 5000, "y": 5000},
        )

        self.assertFalse(result.success)
        self.assertEqual(
            result.error_category,
            ToolErrorCategory.VALIDATION,
        )
        self.assertIsNone(result.confirmation_id)
        self.set_position.assert_not_called()

    async def test_mouse_click_requires_confirmation_and_runs_once(self):
        pending = await self.run_exec(
            "system_mouse_click",
            {"x": 100, "y": 200},
        )

        self.assertFalse(pending.success)
        self.assertEqual(
            pending.status,
            ToolExecutionStatus.CONFIRMATION_REQUIRED,
        )
        self.set_position.assert_not_called()
        self.send_click.assert_not_called()

        approved = await self.run_exec(
            "system_mouse_click",
            {"x": 100, "y": 200},
            confirmation_id=pending.confirmation_id,
        )

        self.assertTrue(approved.success)
        self.set_position.assert_called_once_with(100, 200)
        self.send_click.assert_called_once_with()

    async def test_mouse_click_replay_is_denied(self):
        pending = await self.run_exec(
            "system_mouse_click",
            {"x": 100, "y": 200},
        )
        first = await self.run_exec(
            "system_mouse_click",
            {"x": 100, "y": 200},
            confirmation_id=pending.confirmation_id,
        )
        replay = await self.run_exec(
            "system_mouse_click",
            {"x": 100, "y": 200},
            confirmation_id=pending.confirmation_id,
        )

        self.assertTrue(first.success)
        self.assertFalse(replay.success)
        self.assertEqual(self.send_click.call_count, 1)

    async def test_private_requests_are_denied_for_every_tool(self):
        arguments = {
            "system_clipboard_read": {},
            "system_clipboard_write": {"text": "hello"},
            "system_keyboard_press": {"key": "ENTER"},
            "system_mouse_position": {},
            "system_mouse_move": {"x": 1, "y": 1},
            "system_mouse_click": {"x": 1, "y": 1},
        }

        for tool_name in TOOL_NAMES:
            with self.subTest(tool=tool_name):
                result = await self.run_exec(
                    tool_name,
                    arguments[tool_name],
                    private=True,
                )

                self.assertFalse(result.success)
                self.assertEqual(
                    result.status,
                    ToolExecutionStatus.DENIED,
                )
                self.assertEqual(
                    result.error_category,
                    ToolErrorCategory.PERMISSION,
                )

        self.read_clipboard.assert_not_called()
        self.write_clipboard.assert_not_called()
        self.get_position.assert_not_called()
        self.set_position.assert_not_called()
        self.send_key.assert_not_called()
        self.send_click.assert_not_called()

    async def test_disabled_tools_are_denied(self):
        self.settings(
            TOOL_CLIPBOARD_ENABLED=False,
            TOOL_KEYBOARD_ENABLED=False,
            TOOL_MOUSE_ENABLED=False,
        )

        for tool_name in TOOL_NAMES:
            with self.subTest(tool=tool_name):
                result = await self.run_exec(tool_name)

                self.assertFalse(result.success)
                self.assertEqual(
                    result.status,
                    ToolExecutionStatus.DENIED,
                )
                self.assertEqual(
                    result.error_category,
                    ToolErrorCategory.DISABLED,
                )

    async def test_execution_history_records_the_desktop_provider(self):
        pending = await self.run_exec("system_clipboard_read")
        result = await self.run_exec(
            "system_clipboard_read",
            confirmation_id=pending.confirmation_id,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "desktop_interaction")
        self.assertIsNotNone(result.execution_id)

        rows = await self.history_rows()
        self.assertTrue(
            any(row.provider == "desktop_interaction" for row in rows)
        )

    async def test_all_confirmation_tools_require_confirmation(self):
        arguments = {
            "system_clipboard_read": {},
            "system_clipboard_write": {"text": "hello"},
            "system_keyboard_press": {"key": "ENTER"},
            "system_mouse_move": {"x": 1, "y": 1},
            "system_mouse_click": {"x": 1, "y": 1},
        }

        for tool_name in CONFIRMATION_TOOLS:
            with self.subTest(tool=tool_name):
                result = await self.run_exec(
                    tool_name,
                    arguments[tool_name],
                )

                self.assertFalse(result.success)
                self.assertEqual(
                    result.status,
                    ToolExecutionStatus.CONFIRMATION_REQUIRED,
                )
                self.assertIsNotNone(result.confirmation_id)


if __name__ == "__main__":
    unittest.main()
