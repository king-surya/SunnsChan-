"""Project continuity: derived cross-session views over existing records.

No second project-management system. A project IS an (open or recently
closed) goal plus everything linked to it: experiences carrying its goal_id,
knowledge sourced from those events, curiosities sharing its topic, and open
questions left behind. Built fresh from the ledger on every call — nothing
to desynchronize.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .memory import EventLedger, tokenize


@dataclass(frozen=True)
class ProjectView:
    project_id: str
    objective: str
    status: str  # active | completed
    goal_ids: tuple[int, ...] = ()
    experience_ids: tuple[int, ...] = ()
    knowledge_ids: tuple[int, ...] = ()
    curiosity_ids: tuple[int, ...] = ()
    open_questions: tuple[str, ...] = ()
    next_threads: tuple[str, ...] = ()


def _goal_events(ledger: EventLedger) -> tuple[list, set[int]]:
    opened = ledger.events_of_kind("goal_opened", limit=200)
    closed = {e.metadata.get("goal_id") for e in ledger.events_of_kind("goal_closed", limit=200)}
    return opened, closed


def list_projects(ledger: EventLedger, *, knowledge=None, curiosities=None,
                  limit: int = 20) -> list[ProjectView]:
    opened, closed = _goal_events(ledger)
    experiences = ledger.events_of_kind("experience", limit=500)
    views: list[ProjectView] = []
    for goal in opened[:limit]:
        active = goal.id not in closed
        linked_exp = [e for e in experiences if e.metadata.get("goal_id") == goal.id]
        project_event_ids = {goal.id} | {e.id for e in linked_exp}
        goal_tokens = set(tokenize(goal.text))
        knowledge_ids: list[int] = []
        if knowledge is not None:
            for record in knowledge.visible(limit=500):
                if set(record.source_ids) & project_event_ids:
                    knowledge_ids.append(record.id)
                elif len(set(tokenize(record.statement)) & goal_tokens) >= 2 \
                        and record.id not in knowledge_ids:
                    knowledge_ids.append(record.id)
        curiosity_ids: list[int] = []
        if curiosities is not None:
            for item in curiosities.all(limit=200):
                if item.origin_event_id in project_event_ids or \
                        len(set(tokenize(item.question)) & goal_tokens) >= 2:
                    curiosity_ids.append(item.id)
        questions: list[str] = []
        for exp in linked_exp:
            questions.extend(exp.metadata.get("unresolved_questions") or [])
        threads = list(dict.fromkeys(
            list(questions[:5]) + [f"goal #{goal.id} still open" if active else f"goal #{goal.id} completed"]))
        views.append(ProjectView(
            project_id=f"goal-{goal.id}",
            objective=goal.text[:160],
            status="active" if active else "completed",
            goal_ids=(goal.id,),
            experience_ids=tuple(e.id for e in linked_exp),
            knowledge_ids=tuple(knowledge_ids),
            curiosity_ids=tuple(curiosity_ids),
            open_questions=tuple(questions[:8]),
            next_threads=tuple(threads[:6]),
        ))
    return views


def format_projects(views: list[ProjectView]) -> str:
    if not views:
        return "(no projects: open a goal to start one)"
    lines: list[str] = []
    for view in views:
        lines.append(
            f"[{view.status}] {view.project_id}: {view.objective} "
            f"(exp:{len(view.experience_ids)} know:{len(view.knowledge_ids)} "
            f"cur:{len(view.curiosity_ids)} open-q:{len(view.open_questions)})")
    return "\n".join(lines)
