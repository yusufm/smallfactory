from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import os

from smallfactory.core.v1.config import get_datarepo_path
from smallfactory.core.v1.entities import get_entity, list_entities, resolved_bom_view
from smallfactory.core.v1.inventory import inventory_onhand_readonly


SMALLFACTORY_MCP_INSTRUCTIONS = """
You are connected to a read-only SmallFactory repository.

Data model (canonical):
- Entities are identified by `sfid`.
- Prefixes:
  - `p_*` = part
  - `b_*` = build record
  - `l_*` = location
- Build records (`b_*`) usually link to a part through `part_sfid`.
- Build event history is attached to each build and includes:
  - `event_id`, `ts`, `tags`, `message`, optional `files`.
- Inventory on-hand is derived from journals and reported by part and location.
- BOM resolution starts from a root part (`p_*`) and returns resolved child lines.

How concepts relate:
- part (`p_*`) -> can have BOM children (other parts/components).
- build (`b_*`) -> records production/assembly activity for a part via `part_sfid`.
- build events -> operational history across one build or all builds of a part.
- locations (`l_*`) -> where inventory quantities are tracked.

Query strategy:
1) Use `entities_search` to identify candidate SFIDs.
2) Use `entity_get` for authoritative metadata on one entity.
3) Use `build_events_list` for event-level detail.
4) Use `analytics_query` for grouped counts/trends.
5) Use `inventory_onhand` and `bom_resolved` for stock and structure context.

Tooling contract:
- Do not invent fields not returned by tools.
- Prefer citing SFIDs and returned counts exactly.
- If filters produce no data, report that directly and suggest adjacent queries.
""".strip()


def _resolve_datarepo_path(explicit_repo: Optional[str] = None) -> Path:
    if explicit_repo:
        return Path(explicit_repo).expanduser().resolve()
    env_repo = (os.getenv("SF_DATAREPO") or "").strip()
    if env_repo:
        return Path(env_repo).expanduser().resolve()
    return get_datarepo_path()


def _parse_iso8601(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_tags(tags: Optional[Iterable[str]]) -> List[str]:
    if not tags:
        return []
    out: List[str] = []
    seen = set()
    for t in tags:
        s = str(t).strip().lower()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _iter_build_entities(datarepo_path: Path) -> Iterable[dict]:
    for ent in list_entities(datarepo_path):
        if not isinstance(ent, dict):
            continue
        sfid = str(ent.get("sfid", "")).strip()
        if sfid.startswith("b_"):
            yield ent


def _part_entities_by_sfid(datarepo_path: Path) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for ent in list_entities(datarepo_path):
        if not isinstance(ent, dict):
            continue
        sfid = str(ent.get("sfid", "")).strip()
        if sfid.startswith("p_"):
            out[sfid] = ent
    return out


def _inventory_onhand_with_zero_parts(
    datarepo_path: Path,
    *,
    part_sfid: Optional[str],
    location_sfid: Optional[str],
    include_zero_parts: bool,
) -> Dict[str, Any]:
    base = inventory_onhand_readonly(
        datarepo_path,
        part=part_sfid,
        location=location_sfid,
    )
    # If querying one part, the core API already computes from journal and returns 0 total
    # for existing parts with no stock movements.
    if part_sfid or not include_zero_parts:
        return base

    parts_meta = _part_entities_by_sfid(datarepo_path)
    all_part_sfids = sorted(parts_meta.keys())

    if location_sfid:
        existing = base.get("parts")
        existing_map = existing if isinstance(existing, dict) else {}
        merged = {sfid: int(existing_map.get(sfid, 0) or 0) for sfid in all_part_sfids}
        base["parts"] = merged
        base["total"] = int(sum(merged.values()))
        base["parts_count"] = len(merged)
        return base

    existing_rows = base.get("parts")
    rows_by_sfid: Dict[str, Dict[str, Any]] = {}
    if isinstance(existing_rows, list):
        for row in existing_rows:
            if not isinstance(row, dict):
                continue
            sfid = str(row.get("sfid", "")).strip()
            if sfid:
                rows_by_sfid[sfid] = dict(row)

    merged_rows: List[Dict[str, Any]] = []
    for sfid in all_part_sfids:
        row = rows_by_sfid.get(sfid)
        if row is None:
            ent = parts_meta.get(sfid) or {}
            row = {
                "sfid": sfid,
                "uom": ent.get("uom", "ea") or "ea",
                "total": 0,
                "by_location": {},
                "as_of": base.get("as_of"),
            }
        merged_rows.append(row)

    base["parts"] = merged_rows
    base["total"] = int(sum(int((r or {}).get("total", 0) or 0) for r in merged_rows))
    base["parts_count"] = len(merged_rows)
    return base


def _collect_build_events(
    datarepo_path: Path,
    *,
    build_sfid: Optional[str] = None,
    part_sfid: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
) -> List[Dict[str, Any]]:
    wanted_tags = set(_normalize_tags(tags))
    start_dt = _parse_iso8601(start_ts)
    end_dt = _parse_iso8601(end_ts)

    out: List[Dict[str, Any]] = []
    if build_sfid:
        candidates = [get_entity(datarepo_path, build_sfid)]
    else:
        candidates = list(_iter_build_entities(datarepo_path))

    for build in candidates:
        if not isinstance(build, dict):
            continue
        b_sfid = str(build.get("sfid", "")).strip()
        if not b_sfid.startswith("b_"):
            continue
        # Ensure events are loaded from events.jsonl for list-entities candidates.
        if not isinstance(build.get("events"), list):
            build = get_entity(datarepo_path, b_sfid)
        linked_part = str(build.get("part_sfid", "")).strip() or None
        if part_sfid and linked_part != part_sfid:
            continue

        events = build.get("events")
        if not isinstance(events, list):
            events = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            ev_tags = _normalize_tags(ev.get("tags") or [])
            if wanted_tags and wanted_tags.isdisjoint(set(ev_tags)):
                continue
            ev_dt = _parse_iso8601(ev.get("ts"))
            if start_dt and ev_dt and ev_dt < start_dt:
                continue
            if end_dt and ev_dt and ev_dt > end_dt:
                continue
            out.append(
                {
                    "build_sfid": b_sfid,
                    "part_sfid": linked_part,
                    "event_id": ev.get("id"),
                    "ts": ev.get("ts"),
                    "tags": ev_tags,
                    "message": ev.get("message"),
                    "files": ev.get("files") if isinstance(ev.get("files"), list) else [],
                }
            )

    out.sort(key=lambda x: (str(x.get("ts") or ""), str(x.get("build_sfid") or ""), str(x.get("event_id") or "")), reverse=True)
    return out


def _entities_search_impl(
    datarepo_path: Path,
    *,
    query: str = "",
    type_prefix: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    q = str(query or "").strip().lower()
    wanted_tags = set(_normalize_tags(tags))

    tp = str(type_prefix or "").strip().lower()
    if tp.endswith("_"):
        tp = tp[:-1]
    prefix = f"{tp}_" if tp else None

    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    results: List[Dict[str, Any]] = []
    for ent in list_entities(datarepo_path):
        if not isinstance(ent, dict):
            continue
        sfid = str(ent.get("sfid", "")).strip()
        if not sfid:
            continue
        if prefix and not sfid.startswith(prefix):
            continue

        name = str(ent.get("name", "")).strip()
        if q:
            hay = f"{sfid} {name}".lower()
            if q not in hay:
                continue

        ent_tags = _normalize_tags(ent.get("tags") if isinstance(ent.get("tags"), list) else [])
        if wanted_tags and wanted_tags.isdisjoint(set(ent_tags)):
            continue

        results.append(
            {
                "sfid": sfid,
                "name": name or None,
                "tags": ent_tags,
                "type": sfid.split("_", 1)[0] if "_" in sfid else None,
            }
        )

    results.sort(key=lambda x: str(x.get("sfid") or ""))
    return {"results": results[:limit], "count": len(results[:limit]), "total_matches": len(results)}


def _analytics_query_impl(
    datarepo_path: Path,
    *,
    subject: str = "build_events",
    group_by: str = "tag",
    part_sfid: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    if str(subject).strip().lower() != "build_events":
        raise ValueError("Only subject='build_events' is currently supported")

    events = _collect_build_events(
        datarepo_path,
        build_sfid=None,
        part_sfid=part_sfid,
        tags=tags,
        start_ts=start_ts,
        end_ts=end_ts,
    )

    mode = str(group_by or "").strip().lower()
    counts: Counter[str] = Counter()
    for ev in events:
        if mode in ("tag", "tags"):
            t = ev.get("tags") if isinstance(ev.get("tags"), list) else []
            if not t:
                counts["untagged"] += 1
            else:
                for tag in t:
                    counts[str(tag)] += 1
        elif mode in ("part", "part_sfid"):
            counts[str(ev.get("part_sfid") or "unknown")] += 1
        elif mode in ("build", "build_sfid"):
            counts[str(ev.get("build_sfid") or "unknown")] += 1
        elif mode in ("day", "date"):
            ts = str(ev.get("ts") or "")
            day = ts[:10] if len(ts) >= 10 else "unknown"
            counts[day] += 1
        else:
            raise ValueError("group_by must be one of: tag, part_sfid, build_sfid, day")

    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    rows = [{"key": k, "count": int(v)} for k, v in counts.most_common(limit)]
    return {
        "subject": "build_events",
        "group_by": mode,
        "rows": rows,
        "rows_count": len(rows),
        "total_events_considered": len(events),
        "filters": {
            "part_sfid": part_sfid,
            "tags": _normalize_tags(tags),
            "start_ts": start_ts,
            "end_ts": end_ts,
        },
    }


def run_mcp_server(*, repo: Optional[str] = None, transport: str = "stdio") -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:
        raise RuntimeError(
            "MCP dependency is missing. Install with: pip install mcp"
        ) from exc

    datarepo_path = _resolve_datarepo_path(repo)
    server = FastMCP(
        "smallfactory",
        instructions=SMALLFACTORY_MCP_INSTRUCTIONS,
    )

    @server.tool()
    def repo_info() -> Dict[str, Any]:
        """Return repository-level connection context.

        Use when you need to confirm which datarepo this MCP session is using.
        """
        return {"datarepo_path": str(datarepo_path)}

    @server.tool()
    def data_model_guide() -> Dict[str, Any]:
        """Return a compact ontology for SmallFactory concepts and relationships.

        Use before complex multi-tool reasoning when entity relationships are unclear.
        """
        return {
            "entity_prefixes": {
                "p_*": "part",
                "b_*": "build record",
                "l_*": "location",
            },
            "relationships": [
                "build.part_sfid -> part.sfid",
                "build events belong to a build record (b_*)",
                "inventory quantities are tracked by part and location",
                "BOM resolution starts from a part root and expands children",
                "inventory_onhand(include_zero_parts=true) returns all parts with explicit zero totals when no stock exists",
            ],
            "build_event_fields": ["build_sfid", "part_sfid", "event_id", "ts", "tags", "message", "files"],
            "analytics_subjects": ["build_events"],
            "analytics_group_by": ["tag", "part_sfid", "build_sfid", "day"],
        }

    @server.tool()
    def entities_search(
        query: str = "",
        type_prefix: str = "",
        tags: Optional[List[str]] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Search entities by SFID/name with optional type and tag filters.

        Use as the first discovery step before calling `entity_get`, `bom_resolved`,
        `build_events_list`, or `inventory_onhand`.
        """
        return _entities_search_impl(
            datarepo_path,
            query=query,
            type_prefix=(type_prefix or None),
            tags=tags,
            limit=limit,
        )

    @server.tool()
    def entity_get(sfid: str) -> Dict[str, Any]:
        """Get one entity by SFID with canonical metadata.

        Use for authoritative details after discovery with `entities_search`.
        For builds (`b_*`), this includes parsed events.
        """
        return get_entity(datarepo_path, sfid)

    @server.tool()
    def inventory_onhand(
        part_sfid: str = "",
        location_sfid: str = "",
        include_zero_parts: bool = True,
    ) -> Dict[str, Any]:
        """Return current on-hand inventory, optionally filtered by part or location.

        Use to answer stock questions and to contextualize BOM/build analyses.
        By default (`include_zero_parts=true`), summary/location queries include all
        part entities with explicit zero quantities where applicable.
        """
        return _inventory_onhand_with_zero_parts(
            datarepo_path,
            part_sfid=(part_sfid or None),
            location_sfid=(location_sfid or None),
            include_zero_parts=bool(include_zero_parts),
        )

    @server.tool()
    def bom_resolved(root_sfid: str, max_depth: int = 12) -> Dict[str, Any]:
        """Resolve a part BOM tree from a root SFID.

        Use for structure and dependency questions (components, alternates, depth).
        `root_sfid` should be a part (`p_*`).
        """
        depth = max(0, min(int(max_depth), 32))
        rows = resolved_bom_view(datarepo_path, root_sfid, max_depth=depth)
        return {"root_sfid": root_sfid, "max_depth": depth, "rows": rows, "count": len(rows)}

    @server.tool()
    def build_events_list(
        build_sfid: str = "",
        part_sfid: str = "",
        tags: Optional[List[str]] = None,
        start_ts: str = "",
        end_ts: str = "",
        limit: int = 200,
    ) -> Dict[str, Any]:
        """List build events with optional filters for build, part, tags, and time window.

        Use for event-level evidence before aggregating with `analytics_query`.
        """
        if limit < 1:
            limit = 1
        if limit > 1000:
            limit = 1000
        events = _collect_build_events(
            datarepo_path,
            build_sfid=(build_sfid or None),
            part_sfid=(part_sfid or None),
            tags=tags,
            start_ts=(start_ts or None),
            end_ts=(end_ts or None),
        )
        return {"events": events[:limit], "count": len(events[:limit]), "total_matches": len(events)}

    @server.tool()
    def analytics_query(
        subject: str = "build_events",
        group_by: str = "tag",
        part_sfid: str = "",
        tags: Optional[List[str]] = None,
        start_ts: str = "",
        end_ts: str = "",
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Run grouped read-only analytics over build events.

        Supported:
        - subject: `build_events`
        - group_by: `tag`, `part_sfid`, `build_sfid`, `day`
        Use this for ranking/trend questions (e.g., most common repair tags).
        """
        return _analytics_query_impl(
            datarepo_path,
            subject=subject,
            group_by=group_by,
            part_sfid=(part_sfid or None),
            tags=tags,
            start_ts=(start_ts or None),
            end_ts=(end_ts or None),
            limit=limit,
        )

    try:
        server.run(transport=transport)
    except TypeError:
        server.run()
