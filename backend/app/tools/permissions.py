"""Server-side authorization policy for tool execution.

The LLM can request a tool, but it cannot decide whether the request is
authorized. This module is the single policy point used by the executor and is
re-evaluated immediately before execution, including after a confirmation was
approved.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.tools.base import BaseTool
from app.tools.enums import ToolCategory, ToolErrorCategory
from app.tools.exceptions import (
    ToolDisabledError,
    ToolPermissionError,
    ToolPrivateRequestError,
)


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    allowed: bool
    requires_confirmation: bool
    category: ToolCategory
    reason: str | None = None
    error_category: ToolErrorCategory | None = None


class PermissionPolicy:
    """Category-based policy for a single-owner deployment."""

    def evaluate(
        self,
        tool: BaseTool,
        *,
        private: bool,
    ) -> PermissionDecision:

        if not tool.enabled:
            return PermissionDecision(
                allowed=False,
                requires_confirmation=False,
                category=tool.category,
                reason="This tool is disabled.",
                error_category=ToolErrorCategory.DISABLED,
            )

        if private and not tool.allowed_in_private:
            return PermissionDecision(
                allowed=False,
                requires_confirmation=False,
                category=tool.category,
                reason=(
                    "This tool is not available for private requests."
                ),
                error_category=ToolErrorCategory.PERMISSION,
            )

        if tool.category == ToolCategory.SENSITIVE:
            if not settings.TOOL_ALLOW_SENSITIVE:
                return PermissionDecision(
                    allowed=False,
                    requires_confirmation=True,
                    category=tool.category,
                    reason=(
                        "Sensitive tools are disabled by the deployment "
                        "policy."
                    ),
                    error_category=ToolErrorCategory.PERMISSION,
                )

            return PermissionDecision(
                allowed=True,
                requires_confirmation=True,
                category=tool.category,
                reason="Sensitive tools require explicit confirmation.",
            )

        if tool.category == ToolCategory.REVERSIBLE:
            return PermissionDecision(
                allowed=True,
                requires_confirmation=bool(tool.requires_confirmation),
                category=tool.category,
                reason=(
                    "This action requires explicit confirmation."
                    if tool.requires_confirmation
                    else None
                ),
            )

        return PermissionDecision(
            allowed=True,
            requires_confirmation=bool(tool.requires_confirmation),
            category=tool.category,
            reason=(
                "This action requires explicit confirmation."
                if tool.requires_confirmation
                else None
            ),
        )

    def require_allowed(
        self,
        tool: BaseTool,
        *,
        private: bool,
    ) -> PermissionDecision:
        """Return a decision or raise the matching permission error."""

        decision = self.evaluate(tool, private=private)

        if decision.allowed:
            return decision

        if decision.error_category == ToolErrorCategory.DISABLED:
            raise ToolDisabledError(decision.reason or "Tool is disabled.")

        if private and not tool.allowed_in_private:
            raise ToolPrivateRequestError(
                decision.reason
                or "This tool is not available for private requests."
            )

        raise ToolPermissionError(
            decision.reason or "This tool is not permitted."
        )


permission_policy = PermissionPolicy()


__all__ = ["PermissionDecision", "PermissionPolicy", "permission_policy"]
