"""Graph retrieval as a DERIVED, optional index. Not a source of truth.

Nodes mirror existing records (experiences, goals, curiosities, knowledge
claims, topics/tags); edges mirror existing links (experience `related`
metadata, knowledge supersede/contradict chains, shared topics). Every edge
carries provenance event ids. The ledger and KnowledgeStore stay
authoritative — this index is rebuilt from them and explains itself.
Contradictions are preserved as edges, never erased.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .experience import RELATIONSHIPS
from .memory import EventLedger, tokenize


@dataclass(frozen=True)
class GraphNode:
    kind: str  # experience | goal | curiosity | knowledge | topic
    key: str
    label: str


@dataclass(frozen=True)
class GraphEdge:
    relation: str
    source_ids: tuple[int, ...]


@dataclass(frozen=True)
class GraphPath:
    nodes: tuple[GraphNode, ...]
    relations: tuple[str, ...]
    provenance: tuple[int, ...]


@dataclass
class GraphIndex:
    nodes: dict[tuple[str, str], GraphNode] = field(default_factory=dict)
    edges: dict[tuple[tuple[str, str], tuple[str, str]], list[GraphEdge]] = field(default_factory=dict)

    def add_edge(self, left: GraphNode, relation: str, right: GraphNode,
                 source_ids: tuple[int, ...]) -> None:
        self.nodes[(left.kind, left.key)] = left
        self.nodes[(right.kind, right.key)] = right
        self.edges.setdefault(((left.kind, left.key), (right.kind, right.key)), []).append(
            GraphEdge(relation, tuple(source_ids)))

    def query(self, kind: str, key: str, *, relation: str | None = None,
              depth: int = 2, limit: int = 10) -> list[GraphPath]:
        """BFS from a node. Bounded depth/traversal; every path has provenance.

        Edges are stored in assertion direction (e.g. B -[follow_up_of]-> A
        means "B follows A") but traversed BOTH ways: reverse hops are
        labeled "<relation" ("A -[<follow_up_of]-> B" reads "A is followed
        up by B"). Provenance is unaffected by direction.
        """
        if depth < 1:
            raise ValueError("depth must be at least 1")
        if limit < 1:
            raise ValueError("limit must be at least 1")
        start = (kind, key)
        if start not in self.nodes:
            return []
        paths: list[GraphPath] = []
        visited: set[tuple[tuple[str, str], ...]] = set()
        frontier: list[tuple[tuple[tuple[str, str], ...], tuple[str, ...], list[int]]] = [((start,), (), [])]
        while frontier and len(paths) < limit:
            node_path, relations, provenance = frontier.pop(0)
            if node_path in visited or len(node_path) - 1 > depth:
                continue
            visited.add(node_path)
            if len(node_path) > 1:
                paths.append(GraphPath(
                    tuple(self.nodes[n] for n in node_path), tuple(relations), tuple(provenance)))
                if len(paths) >= limit:
                    break
            if len(node_path) - 1 == depth:
                continue
            current = node_path[-1]
            for (left, right), edge_list in self.edges.items():
                hops: list[tuple[tuple[str, str], str]] = []
                if left == current:
                    hops = [(right, "")]
                elif right == current:
                    hops = [(left, "<")]
                for target, marker in hops:
                    if target in node_path:
                        continue  # no cycles within a path
                    for edge in edge_list:
                        label = edge.relation if not marker else marker + edge.relation
                        if relation is not None and label != relation and edge.relation != relation:
                            continue
                        frontier.append((node_path + (target,), relations + (label,),
                                         provenance + list(edge.source_ids)))
        return paths


def _topic_node(name: str) -> GraphNode:
    return GraphNode("topic", name, name)


def build_index(ledger: EventLedger, *, knowledge=None, curiosities=None,
                event_limit: int = 500) -> GraphIndex:
    """Derive the full index from existing stores. Read-only over them."""
    index = GraphIndex()
    experiences = ledger.events_of_kind("experience", limit=event_limit)
    by_id = {e.id: e for e in experiences}

    def exp_node(event) -> GraphNode:
        return GraphNode("experience", str(event.id), event.text[:80])

    for event in experiences:
        node = exp_node(event)
        index.nodes[("experience", str(event.id))] = node
        for relation, ids in (event.metadata.get("related") or {}).items():
            if relation not in RELATIONSHIPS:
                continue
            for target_id in ids:
                if target_id in by_id:
                    index.add_edge(node, relation, exp_node(by_id[target_id]), (event.id,))
        for tag in (event.metadata.get("interest_tags") or []):
            if isinstance(tag, str) and tag.strip():
                index.add_edge(node, "related_to", _topic_node(tag.strip().lower()), (event.id,))

    for event in ledger.events_of_kind("goal_opened", limit=event_limit):
        node = GraphNode("goal", str(event.id), event.text[:80])
        index.nodes[("goal", str(event.id))] = node
        for word in set(tokenize(event.text)) - set(tokenize("goal")):
            index.add_edge(node, "related_to", _topic_node(word), (event.id,))

    if curiosities is not None:
        for item in curiosities.all(limit=event_limit):
            node = GraphNode("curiosity", str(item.id), item.question[:80])
            index.nodes[("curiosity", str(item.id))] = node
            index.add_edge(node, "related_to",
                            _topic_node(item.topic or "general"), (item.origin_event_id,))
            for word in set(tokenize(item.question)):
                index.add_edge(node, "related_to", _topic_node(word), (item.origin_event_id,))

    if knowledge is not None:
        records = list(knowledge.history(limit=event_limit))
        for record in records:
            node = GraphNode("knowledge", str(record.id), record.statement[:80])
            index.nodes[("knowledge", str(record.id))] = node
        for record in records:
            node = index.nodes[("knowledge", str(record.id))]
            for source_id in record.source_ids:
                source_key = ("experience", str(source_id))
                if source_key in index.nodes:
                    index.add_edge(index.nodes[source_key], "confirms", node, (source_id,))
            if record.supersedes_id is not None:
                older = ("knowledge", str(record.supersedes_id))
                if older in index.nodes:
                    try:
                        older_status = knowledge.get(record.supersedes_id).status
                    except ValueError:
                        older_status = ""
                    relation = "contradicts" if older_status == "contradicted" else "refines"
                    index.add_edge(index.nodes[older], relation, node, tuple(record.source_ids))
            for tag in record.tags:
                index.add_edge(node, "related_to", _topic_node(tag), tuple(record.source_ids))

    # Second pass: experience -> knowledge links (knowledge nodes exist only
    # after the block above). An experiment's result colors the relation.
    for event in experiences:
        node = exp_node(event)
        for knowledge_id in (event.metadata.get("knowledge_ids") or []):
            knowledge_key = ("knowledge", str(knowledge_id))
            if knowledge_key not in index.nodes:
                continue
            if event.metadata.get("result") == "success":
                relation = "confirms"
            elif event.metadata.get("result") == "failure":
                relation = "contradicts"
            else:
                relation = "tested"
            index.add_edge(node, relation, index.nodes[knowledge_key], (event.id,))
    return index


def format_paths(paths: list[GraphPath], *, max_paths: int = 5) -> str:
    lines: list[str] = []
    for path in paths[:max_paths]:
        hops: list[str] = [f"{path.nodes[0].kind}#{path.nodes[0].key}"]
        for node, relation in zip(path.nodes[1:], path.relations):
            hops.append(f"-[{relation}]-> {node.kind}#{node.key}")
        lines.append("".join(hops) + f" (via E{', E'.join(str(i) for i in path.provenance)})")
    return "\n".join(lines) if lines else "(no paths)"


@dataclass(frozen=True)
class Association:
    event_id: int
    kind: str
    snippet: str
    via: str  # semantic | graph | session | project
    provenance: tuple[int, ...]


def associate(ledger: EventLedger, event_id: int, *, index=None,
              limit_per_source: int = 3, limit: int = 8) -> list[Association]:
    """Retrieve related previous experiences through every available channel.

    Combines semantic recall (of the event text), graph traversal (from the
    event node), same-session neighbors, and shared-goal experiences. Each
    association names its channel — a single experience may simply remain
    an experience when nothing relevant exists. The event itself is excluded.
    """
    row = ledger._connection.execute(
        "SELECT kind, text, metadata_json FROM events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown event: {event_id}")
    import json as json_lib

    metadata = json_lib.loads(row["metadata_json"])
    found: dict[int, Association] = {}

    def add(other_id: int, via: str, provenance: tuple[int, ...]) -> None:
        if other_id == event_id or other_id in found:
            return
        other = ledger._connection.execute(
            "SELECT kind, text FROM events WHERE id = ?", (other_id,)).fetchone()
        if other is not None:
            found[other_id] = Association(other_id, other["kind"], other["text"][:160],
                                          via, tuple(provenance))

    for hit, _score in ledger.recall_with_scores(row["text"], limit=limit_per_source + 1):
        if hit.id != event_id:
            add(hit.id, "semantic", (hit.id,))
    if index is not None:
        for path in index.query("experience", str(event_id), depth=2, limit=limit_per_source * 2):
            for node in path.nodes[1:]:
                if node.kind == "experience" and node.key.isdigit():
                    add(int(node.key), "graph", path.provenance)
    session_id = metadata.get("session_id")
    if session_id:
        from .experience import experiences_of_session

        for exp in experiences_of_session(ledger, session_id, limit=limit_per_source * 2):
            add(exp.id, "session", (exp.id,))
    goal_id = metadata.get("goal_id")
    if goal_id is not None:
        for exp in ledger.events_of_kind("experience", limit=200):
            if exp.metadata.get("goal_id") == goal_id:
                add(exp.id, "project", (exp.id,))
    # Relevance order first (semantic channel), then stable id order.
    # dict preserves insertion: semantic hits were added first, in rank order.
    return list(found.values())[:limit]
