"""Built-in tool registrations.

Adding a new tool only requires a tool class under ``implementations`` and one
entry in ``register_all``. The execution engine is never modified.
"""

from __future__ import annotations

from app.tools.implementations.application_launcher import (
    LaunchApplicationTool,
)
from app.tools.implementations.desktop_interaction import (
    DESKTOP_INTERACTION_TOOLS,
)
from app.tools.implementations.filesystem import FS_ALL_TOOLS
from app.tools.implementations.http_api import HttpRequestTool
from app.tools.implementations.process_inspection import (
    PROCESS_INSPECTION_TOOLS,
)
from app.tools.implementations.python_runner import PythonExecuteTool
from app.tools.implementations.system_info import SYSTEM_INFO_TOOLS
from app.tools.implementations.tasks import TASK_TOOLS
from app.tools.implementations.web_search import WebSearchTool
from app.tools.registry import ToolRegistry


BUILTIN_TOOLS = (
    *TASK_TOOLS,
    WebSearchTool(),
    *FS_ALL_TOOLS,
    PythonExecuteTool(),
    HttpRequestTool(),
    *SYSTEM_INFO_TOOLS,
    LaunchApplicationTool(),
    *PROCESS_INSPECTION_TOOLS,
    *DESKTOP_INTERACTION_TOOLS,
)


def register_all(registry: ToolRegistry) -> None:
    """Register every built-in tool exactly once."""

    for tool in BUILTIN_TOOLS:
        registry.register(tool)


__all__ = ["BUILTIN_TOOLS", "register_all"]
