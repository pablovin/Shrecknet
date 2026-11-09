"""Bounded recovery selection shared by operational failures and evidence gaps.

The backend decides whether Web is needed. Models only render public queries or
review evidence; neither is required to vote for Web a second time.
"""
from __future__ import annotations

from app.jobs.companion.query_contracts import PlannedCall


def recovery_needs(needs, outcomes, assessment=None):
    """Prioritize missing evidence, preserving original order for equal severity."""
    if assessment is not None:
        states = {row.need_id: row.status for row in assessment.needs}
        eligible = [need for need in needs if states.get(need.id) != 'supported']
        return sorted(eligible, key=lambda n: states.get(n.id) not in {'missing', 'unverified'})
    # An available sibling may already answer the need; let the reviewer decide.
    return [need for need in needs
            if any(need.id in row['need_ids'] and row['tool'] != 'web' and
                   row['status'] in {'empty', 'unavailable', 'timed_out', 'failed'} for row in outcomes)
            and not any(need.id in row['need_ids'] and row['status'] == 'ok' and
                        row.get('evidence') for row in outcomes)]


def web_candidates(needs, outcomes, public_queries):
    """One public attempt per need; never repeat an equivalent executed search."""
    calls = []
    used_ids = {row['id'] for row in outcomes}
    for need in needs:
        query = public_queries.get(need.id)
        if not query or not query.strip():
            continue
        if any(row['tool'] == 'web' and need.id in row['need_ids'] for row in outcomes):
            continue
        normalized = ' '.join(query.casefold().split())
        if any(row['tool'] == 'web' and ' '.join(row['query'].casefold().split()) == normalized for row in outcomes):
            continue
        existing = next((call for call in calls if ' '.join(call.query.casefold().split()) == normalized), None)
        if existing:
            existing.need_ids.append(need.id)
            continue
        identifier = 'web-recovery-' + str(len(calls) + 1)
        while identifier in used_ids:
            identifier += '-r'
        used_ids.add(identifier)
        calls.append(PlannedCall(id=identifier, tool='web', query=query, need_ids=[need.id]))
    return calls


def select_recovery(automatic, proposed, outcomes, missing, available, limit):
    """Automatic Web gets priority; all sources share a single bounded batch."""
    selected = []
    ids = {row['id'] for row in outcomes}
    searches = {(row['tool'], ' '.join(row['query'].casefold().split()),
                 row.get('retrieval_depth', 'light')) for row in outcomes}
    for call in [*automatic, *proposed]:
        if call.tool not in available or not set(call.need_ids) <= missing:
            continue
        if call.tool == 'web' and len(call.query) > 500:
            continue
        key = (call.tool, ' '.join(call.query.casefold().split()), call.retrieval_depth)
        if key in searches:
            continue
        identifier = call.id
        suffix = 0
        while identifier in ids:
            suffix += 1
            identifier = f'r{suffix}-' + call.id[:30]
        ids.add(identifier)
        searches.add(key)
        selected.append(call.model_copy(update={'id': identifier}))
        if len(selected) >= limit:
            break
    return selected
