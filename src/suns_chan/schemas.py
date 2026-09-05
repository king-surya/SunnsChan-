"""Strict decision schema for model output.

Shape validation lives here; authority stays with PolicyGate (security) and
ToolRegistry (approval). Anything failing this schema raises
DecisionValidationError and must become a persisted event, never an action.
"""

from __future__ import annotations

import json
from typing import Any

from .core import Decision, GoalProposal
from .policy import ActionRequest


class DecisionValidationError(ValueError):
    pass


def _require_str(data: dict[str, Any], key: str, *, allow_empty: bool = False) -> str:
    value = data.get(key)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise DecisionValidationError(f"'{key}' must be a non-empty string")
    return value


def _require_confidence(value: Any, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionValidationError(f"'{key}' must be a number between 0 and 1")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise DecisionValidationError(f"'{key}' must be between 0 and 1")
    return round(number, 4)


def _parse_action(data: Any) -> ActionRequest | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise DecisionValidationError("'action' must be an object or null")
    name = _require_str(data, "name")
    risk = _require_str(data, "risk")
    description = _require_str(data, "description")
    scope = data.get("scope", "external")
    if not isinstance(scope, str) or not scope.strip():
        raise DecisionValidationError("'action.scope' must be a non-empty string")
    cost = data.get("estimated_cost", 1)
    if isinstance(cost, bool) or not isinstance(cost, int):
        raise DecisionValidationError("'action.estimated_cost' must be an integer")
    return ActionRequest(name=name, risk=risk, description=description, scope=scope, estimated_cost=cost)


def _parse_goals(data: Any) -> tuple[GoalProposal, ...]:
    if data is None:
        return ()
    if not isinstance(data, list):
        raise DecisionValidationError("'goals' must be a list")
    goals: list[GoalProposal] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise DecisionValidationError(f"'goals[{index}]' must be an object")
        title = _require_str(item, "title")
        reason = item.get("reason", "")
        if not isinstance(reason, str):
            raise DecisionValidationError(f"'goals[{index}].reason' must be a string")
        priority = _require_confidence(item.get("priority", 0.5), f"'goals[{index}].priority'")
        goals.append(GoalProposal(title=title, reason=reason, priority=priority))
    return tuple(goals)


def _parse_tags(data: Any) -> tuple[str, ...]:
    if data is None:
        return ()
    if not isinstance(data, list) or any(not isinstance(t, str) for t in data):
        raise DecisionValidationError("'interest_tags' must be a list of strings")
    return tuple(t for t in data if t.strip())


def parse_decision(data: dict[str, Any]) -> Decision:
    """Validate a decoded JSON object into a Decision. Raises on any violation."""
    if not isinstance(data, dict):
        raise DecisionValidationError("decision must be a JSON object")
    response = _require_str(data, "response")
    return Decision(
        response=response,
        action=_parse_action(data.get("action")),
        goals=_parse_goals(data.get("goals")),
        interest_tags=_parse_tags(data.get("interest_tags")),
        confidence=_require_confidence(data.get("confidence", 0.5), "confidence"),
        expected_outcome=data.get("expected_outcome", ""),
    )


def parse_decision_json(text: str) -> Decision:
    """Parse raw model text into a Decision. Raises DecisionValidationError."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DecisionValidationError(f"decision is not valid JSON: {exc}") from exc
    decision = parse_decision(data)
    if not isinstance(decision.expected_outcome, str):
        raise DecisionValidationError("'expected_outcome' must be a string")
    return decision


DECISION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["response"],
    "properties": {
        "response": {"type": "string", "minLength": 1},
        "action": {
            "type": ["object", "null"],
            "required": ["name", "risk", "description"],
            "properties": {
                "name": {"type": "string"},
                "risk": {"type": "string"},
                "description": {"type": "string"},
                "scope": {"type": "string", "default": "external"},
                "estimated_cost": {"type": "integer", "default": 1},
            },
        },
        "goals": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title"],
                "properties": {
                    "title": {"type": "string"},
                    "reason": {"type": "string", "default": ""},
                    "priority": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
                },
            },
        },
        "interest_tags": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
        "expected_outcome": {"type": "string", "default": ""},
    },
}
