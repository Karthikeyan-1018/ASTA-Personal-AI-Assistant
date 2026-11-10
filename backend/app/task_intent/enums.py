"""Intent values understood by the natural-language task engine."""

from __future__ import annotations

import enum


class TaskIntent(str, enum.Enum):
    """A supported task-related user intent."""

    CREATE_TASK = "create_task"
    LIST_TASKS = "list_tasks"
    GET_TASK = "get_task"
    UPDATE_TASK = "update_task"
    COMPLETE_TASK = "complete_task"
    REOPEN_TASK = "reopen_task"
    CANCEL_TASK = "cancel_task"
    DELETE_TASK = "delete_task"
    UPCOMING_TASKS = "upcoming_tasks"
    OVERDUE_TASKS = "overdue_tasks"
    TODAY_TASKS = "today_tasks"
    TOMORROW_TASKS = "tomorrow_tasks"
    TASK_STATUS_QUERY = "task_status_query"
    NO_TASK_ACTION = "no_task_action"


MUTATION_INTENTS = frozenset({
    TaskIntent.CREATE_TASK,
    TaskIntent.UPDATE_TASK,
    TaskIntent.COMPLETE_TASK,
    TaskIntent.REOPEN_TASK,
    TaskIntent.CANCEL_TASK,
    TaskIntent.DELETE_TASK,
})

QUERY_INTENTS = frozenset({
    TaskIntent.LIST_TASKS,
    TaskIntent.GET_TASK,
    TaskIntent.UPCOMING_TASKS,
    TaskIntent.OVERDUE_TASKS,
    TaskIntent.TODAY_TASKS,
    TaskIntent.TOMORROW_TASKS,
    TaskIntent.TASK_STATUS_QUERY,
})
