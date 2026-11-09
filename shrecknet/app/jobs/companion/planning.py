"""Compile router tasks into scoped, bounded executable calls.

The router chooses queries and sources, including Web and subjective memories.
The backend assigns identifiers and enforces availability and execution limits.
"""
from app.core.config_store import COMPANION_WEB_QUERY_LIMIT
from app.jobs.companion.query_contracts import Need, PlannedCall, QueryPlan


MAX_CALLS = 9


def compile_plan(proposal, available):
    """Deduplicate tasks/source pairs; never restore an excluded source."""
    trace = {'contract_version': 'simple_research_v2', 'rejected_sources': [],
             'merged_tasks': 0, 'fallback_used': False}
    if proposal.mode != 'search':
        return QueryPlan(action='clarify' if proposal.mode == 'clarify' else 'conversation',
                         ontology_update=None, needs=[], calls=[],
                         clarification_questions=proposal.clarification_questions), trace
    needs, calls, seen = [], [], {}
    for task in proposal.tasks:
        key = ' '.join(task.query.casefold().split())
        if key in seen:
            need = seen[key]
            trace['merged_tasks'] += 1
        else:
            need = Need(id=f'n{len(needs)+1}', question=task.query)
            needs.append(need)
            seen[key] = need
        for source in dict.fromkeys(task.sources):
            if any(call.tool == source and need.id in call.need_ids for call in calls):
                continue
            reason = ('unavailable' if source not in available else
                      'call_limit' if len(calls) >= MAX_CALLS else
                      'web_call_limit' if source == 'web' and
                      sum(call.tool == 'web' for call in calls) >= COMPANION_WEB_QUERY_LIMIT else None)
            if reason:
                trace['rejected_sources'].append({'need_id': need.id, 'source': source, 'reason': reason})
                continue
            calls.append(PlannedCall(id=f'c{len(calls)+1}', tool=source, query=task.query,
                need_ids=[need.id], memory_reason='Router selected the character’s experiences or perspective.'
                if source == 'character_memory' else None))
    # Unavailable search remains a research request so synthesis can state its limits.
    return QueryPlan(action='retrieve', ontology_update=None, needs=needs, calls=calls,
                     clarification_questions=[]), trace


def fallback_plan(question, available):
    """Preserve the full goal, split long queries without dropping characters."""
    need = Need(id='n1', question=question)
    calls = []
    chunks = [question[start:start+4000] for start in range(0,len(question),4000)]
    executed_segments = set()
    for index, chunk in enumerate(chunks):
        for source in ('shreckworld','atlas'):
            if source in available and len(calls) < MAX_CALLS:
                calls.append(PlannedCall(id=f'c{len(calls)+1}',tool=source,query=chunk,need_ids=['n1']))
                executed_segments.add(index)
    return QueryPlan(action='retrieve',ontology_update=None,needs=[need],calls=calls,
        clarification_questions=[],resume_pending=False), {'contract_version':'simple_research_v2',
        'fallback_used':True,'query_segments':len(chunks),'executed_segments':len(executed_segments),
        'deferred_segments':max(0,len(chunks)-len(executed_segments))}
