"""Compile semantic research tasks into scoped, bounded executable calls.

The caller resolves scope and supplies authorized internal tools
and a memory relevance decision. No model controls identifiers or retrieval options.
"""
from app.jobs.companion.query_contracts import Need, PlannedCall, QueryPlan


def compile_plan(proposal, available, *, memory_relevant=False):
    """Normalize identical tasks and reject unavailable or inappropriate sources."""
    trace = {'contract_version':'simple_research_v1', 'rejected_sources':[], 'merged_tasks':0, 'fallback_used':False}
    needs, calls, seen = [], [], {}
    if proposal.mode == 'chat':
        return QueryPlan(action='conversation', ontology_update=None, needs=[], calls=[], clarification_questions=[]), trace
    for task in proposal.tasks:
        key = ' '.join(task.question.casefold().split())
        if key in seen:
            need = seen[key]
            trace['merged_tasks'] += 1
        else:
            need = Need(id=f'n{len(needs)+1}', question=task.question)
            needs.append(need)
            seen[key] = need
        sources = list(dict.fromkeys(task.sources))
        accepted = []
        for source in sources:
            reason = ('web_recovery_only' if source == 'web' else
                      'unavailable' if source not in available else
                      'memory_not_relevant' if source == 'character_memory' and not memory_relevant else None)
            if reason:
                trace['rejected_sources'].append({'need_id':need.id, 'source':source, 'reason':reason})
            else:
                accepted.append(source)
        if not accepted:
            accepted = [source for source in ('shreckworld','atlas') if source in available]
        for source in accepted:
            if any(call.tool == source and need.id in call.need_ids for call in calls):
                continue
            calls.append(PlannedCall(id=f'c{len(calls)+1}', tool=source, query=task.question, need_ids=[need.id],
                memory_reason='Original user intent concerns the selected character’s experiences or perspective.' if source == 'character_memory' else None))
    for need in needs:
        if not any(need.id in call.need_ids for call in calls):
            for source in ('shreckworld','atlas'):
                if source in available:
                    calls.append(PlannedCall(id=f'c{len(calls)+1}',tool=source,query=need.question,need_ids=[need.id]))
    # Empty executable search remains a research request, never factual chat.
    plan = QueryPlan(action='retrieve', ontology_update=None, needs=needs, calls=calls,
                     clarification_questions=[], resume_pending=False)
    return plan, trace


def fallback_plan(question, available):
    """Preserve the full goal, split long queries without dropping characters."""
    need = Need(id='n1', question=question)
    calls = []
    chunks = [question[start:start+4000] for start in range(0,len(question),4000)]
    executed_segments = set()
    for index, chunk in enumerate(chunks):
        for source in ('shreckworld','atlas'):
            if source in available and len(calls) < 9:
                calls.append(PlannedCall(id=f'c{len(calls)+1}',tool=source,query=chunk,need_ids=['n1']))
                executed_segments.add(index)
    return QueryPlan(action='retrieve',ontology_update=None,needs=[need],calls=calls,
        clarification_questions=[],resume_pending=False), {'contract_version':'simple_research_v1',
        'fallback_used':True,'query_segments':len(chunks),'executed_segments':len(executed_segments),
        'deferred_segments':max(0,len(chunks)-len(executed_segments))}
