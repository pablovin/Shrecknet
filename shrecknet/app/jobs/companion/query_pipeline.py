"""Evidence-first Companion orchestration; all durable writes use existing leases.

Explicit/saved scope bypasses inference. A separate scope decision runs before
parallel retrieval; coverage/recovery precede private synthesis. Optional media
resolution/composition stays private and its block claims share narrative
verification, followed by source/asset checks and the leased final commit.
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
from uuid import uuid4
from pydantic import ValidationError
from app.jobs.companion import query_prompts as prompts
from app.jobs.companion import rich_response
from app.jobs.companion import recovery as recovery_policy
from app.jobs.companion import evidence_context, planning
from app.integrations.companion import media as companion_media
from app.core.config_store import (COMPANION_MODEL_FIELDS, COMPANION_TOOL_EVIDENCE_LIMIT,
                                   COMPANION_ASSESSMENT_RECORD_LIMIT)
from app.schemas.character_companion import COMPANION_ACKNOWLEDGEMENT
from app.jobs.companion.query_contracts import (
    OntologyDecision, EvidenceAssessment, SimpleCompanionPlan, ScopeIntent,
    MemoryRelevance, Draft, Verification,
)
from app.jobs.companion.contracts import build_answer
from app.jobs.elder.context_budget import estimate_tokens
from app.integrations.companion.retrieval import CompanionRetrieval, web_available
from app.models.character_companion import CompanionTurn, CompanionAttachment, utcnow
from app.repositories.character_companion_repository import CompanionRepository
from app.services.companion_scope_service import CompanionScopeService, scoped_context
from app.services.companion_foundry_context import foundry_evidence

logger = logging.getLogger(__name__)


def _diag(turn_id, event, payload, limit=6000):
    """Emit bounded, structured companion pipeline diagnostics to the console."""
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except TypeError:
        text = repr(payload)
    logger.info("companion.diag turn=%s event=%s data=%s", turn_id, event, text[:limit])


async def request_structured(llm, target, prompt, data, schema, timeout, tag, tokens=2500):
    raw = await asyncio.wait_for(llm.chat(model=target.model_copy(update={'web_search_enabled': False}),
        messages=[{'role': 'system', 'content': prompt}, {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)}],
        usage_tag=tag, max_tokens=tokens), timeout)
    if not isinstance(raw, str) or len(raw) > 160000:
        raise ValueError('invalid_structured_response')
    return schema.model_validate_json(raw)


def routing_context(scope, snapshot):
    context = dict(scope)
    if snapshot:
        table = snapshot.get('table') or {}
        context['foundry'] = {'table_title':table.get('title'), 'system_title':table.get('system_title'),
                              'documents':[{'kind':doc['kind'],'name':doc['name']} for doc in snapshot.get('documents',[])]}
    return context


def available_tools(turn, settings, scope):
    allowed = turn.request['tools']['allowed']
    candidates = set(allowed if allowed is not None else ['atlas', 'shreckworld', 'web', 'chronicle', 'character_memory'])
    if not scope.get('ontology_id'):
        candidates -= {'atlas', 'shreckworld'}
    if not settings.shreckworld_base_url:
        candidates.discard('shreckworld')
    if not scope.get('chronicle_id') or not settings.shreckrpg_base_url:
        candidates.discard('chronicle')
    if web_available(settings):
        candidates.add('web')
    else:
        candidates.discard('web')
    return sorted(candidates)


def valid_support(support, sources):
    source = sources.get(support.evidence_id)
    if source is None:
        return False
    # Structured Foundry fields can carry the only visible value (for example HP)
    # even when the document has no prose. Semantic verification still checks its
    # meaning and provenance; substring membership alone is never sufficient.
    return (support.quote in source['excerpt'] or
            (source.get('structured_evidence') is not None and
             (support.quote in json.dumps(source['structured_evidence'], ensure_ascii=False) or
              support.quote in json.dumps(evidence_context.compact_structured(source), ensure_ascii=False))))


def accepted_segments(draft, verification, evidence, allow_general):
    ids = {s.id for s in draft.segments}
    if len(verification.segments) != len(ids) or {v.segment_id for v in verification.segments} != ids:
        raise ValueError('incomplete_claim_verification')
    checked = {v.segment_id: v for v in verification.segments}
    sources = {e['evidence_id']: e for e in evidence}
    accepted, rejected = [], []
    for segment in draft.segments:
        valid = checked[segment.id].supported
        valid &= all(valid_support(s, sources) for s in segment.supports)
        if segment.kind == 'factual':
            valid &= bool(segment.claims and segment.supports)
        elif segment.kind == 'general_knowledge':
            valid &= allow_general and not segment.supports
        else:
            valid &= not segment.claims and not segment.supports
        if valid:
            accepted.append(segment)
        else:
            rejected.append({'id': segment.id, 'reason': checked[segment.id].reason})
    return accepted, rejected


async def run(turn, lease, *, actor, identity, rapport, recent, images, image_sources, llm, settings, metrics, started):
    # Import runtime transaction/lease primitives only after runner initialization.
    from app.jobs.companion.runner import database, mutate, load_identity
    original_query = turn.request['text']
    query = original_query
    scope = dict(turn.execution_context if turn.execution_context is not None else (turn.request.get('context') or {}))
    ontology = None
    resolution_source = 'not_needed'
    evidence, outcomes, assessment = [], [], None
    evidence_needs = {}
    evidence_ranks = {}
    plan = None
    diagnostics = {'version': 'evidence_v2', 'assessment_contract_version': 'sufficiency_v1'}
    diagnostics['deadlines'] = {name: getattr(settings, name) for name in (
        'companion_tool_timeout_s', 'companion_web_timeout_s', 'companion_routing_timeout_s',
        'companion_evidence_timeout_s', 'companion_retrieval_timeout_s', 'companion_turn_timeout_s')}
    skit_task = None
    finalizing = False
    max_messages = settings.companion_interim_max_messages
    deadline = started + settings.companion_turn_timeout_s

    async def structured(llm, target, prompt, data, schema, timeout, tag, tokens=2500):
        before = time.monotonic()
        stage = {'stage': tag}
        try:
            return await request_structured(llm, target, prompt, data, schema,
                min(timeout, max(0.01, deadline-time.monotonic())), tag, tokens)
        except asyncio.CancelledError:
            stage['error_type'] = 'CancelledError'
            raise
        except Exception as exc:
            stage['error_type'] = type(exc).__name__
            raise
        finally:
            stage['elapsed_ms'] = round((time.monotonic()-before)*1000,2)
            metrics.setdefault('stages', []).append(stage)

    def model_evidence():
        return [evidence_context.compact_evidence(row, evidence_needs.get(row['evidence_id'], ())) for row in evidence]

    def source_count(records=None):
        identifiers = {row['evidence_id'] for row in records} if records is not None else None
        return len({(row['source_type'], (row.get('reference') or {}).get('library_item_id') or row.get('source_id') or row['evidence_id'])
                    for row in evidence if identifiers is None or row['evidence_id'] in identifiers})

    def omitted(stage, items):
        diagnostics.setdefault('evidence_omissions', []).extend({'stage':stage, **item} for item in items)

    async def progress(stage, *, coarse='routing', **extra):
        _diag(turn.id, 'stage', {'stage': stage, 'coarse': coarse, **extra}, limit=1000)
        await mutate(turn.id, lease, stage=coarse, event='pipeline.status', payload={'stage': stage, **extra})

    async def commentary(text, sequence):
        async with database() as session:
            await CompanionRepository(session).commentary(turn.id, lease, text, sequence)
            await session.commit()

    async def save_scope(**kwargs):
        async with database() as session:
            await CompanionScopeService(session).save(turn, lease, **kwargs)
        if kwargs.get('context') is not None:
            turn.execution_context = kwargs['context']

    async def clarify(questions, *, kind='question', target_id=None):
        pending = {'id': str(uuid4()), 'query': query, 'questions': questions, 'kind': kind, 'target_id': target_id}
        await save_scope(pending=pending)
        try:
            text = await asyncio.wait_for(llm.chat(model=settings.model_companion_synthesis.model_copy(update={'web_search_enabled': False}),
                messages=[{'role':'system','content':prompts.CLARIFY}, {'role':'user','content':json.dumps({'identity_description':identity['character_agent']['identity_description'], 'questions':questions})}],
                usage_tag='companion.clarification', max_tokens=250), settings.companion_routing_timeout_s)
            if not text.strip() or len(text) > 2000:
                raise ValueError('invalid_clarification')
        except Exception:
            text = ' '.join(questions)
        return await finish(text, outcome='needs_clarification', extra={'clarification':pending})

    async def finish(text, *, outcome='answered', extra=None):
        nonlocal finalizing
        finalizing = True
        await progress('validating', coarse='validating')
        # Source revalidation remains fail-closed; a revoked source is not silently
        # replaced by an otherwise plausible answer.
        await asyncio.wait_for(load_identity(turn), settings.companion_tool_timeout_s)
        if evidence:
            validator = CompanionRetrieval(turn, actor)
            validator.settings = settings
            await asyncio.wait_for(validator.validate_sources(evidence), settings.companion_tool_timeout_s)
        result = build_answer(text, evidence, outcomes, query=query)
        result['outcome'] = outcome
        result['diagnostics'] = diagnostics
        result['assessment'] = assessment.model_dump() if assessment else None
        result['coverage'] = 'partial' if outcomes or evidence else 'unknown'
        result['model_targets'] = {field:getattr(settings,field).model_dump() for field in COMPANION_MODEL_FIELDS}
        if diagnostics.get('extractive_fallback'):
            result['warnings'].append('Verification incomplete: excerpts are attributed to sources, not verified factual conclusions.')
        if extra:
            result.update(extra)
        for citation in result['citations']:
            citation['segment_ids'] = [s['id'] for s in result['segments'] if citation['evidence_id'] in s['evidence_ids']]

        metrics['execution_ms'] = round((time.monotonic()-started)*1000, 2)
        async with database() as session:
            current = await session.get(CompanionTurn, turn.id)
            current.metrics = metrics
            for identifier in turn.request['attachment_ids']:
                attachment = await CompanionRepository(session).owned(CompanionAttachment, identifier, turn.user_id)
                if attachment.deleted:
                    raise ValueError('attachment_deleted')
            await CompanionRepository(session).terminal(current, 'completed', result=result, lease=lease)
            await session.commit()
        return result

    async def extractive_fallback():
        diagnostics['extractive_fallback'] = True
        assessment_trace = diagnostics.setdefault('evidence_assessment', {})
        assessment_trace.update(fallback_used=True, retained_evidence_count=len(evidence),
                                retained_source_count=source_count(), sources_retained_for_synthesis=0,
                                evidence_retained_for_synthesis_count=0, fallback_mode='extractive')
        raw, segments, omissions = evidence_context.extractive_response(evidence, settings.companion_output_tokens,
                                                                        assessment_available=assessment is not None)
        omitted('extractive_fallback', omissions)
        quote_ids = {identifier for segment in segments for identifier in segment['evidence_ids']}
        assessment_trace['sources_retained_for_extraction'] = source_count([row for row in evidence if row['evidence_id'] in quote_ids])
        assessment_trace['extractive_evidence_count'] = len(segments)-1
        return await finish(raw, outcome='limited', extra={
            'segments':segments, 'coverage':'partial', 'rich_response':None})

    memory_relevant = False

    async def plan_for():
        nonlocal memory_relevant
        available = [tool for tool in available_tools(turn, settings, scope) if tool != 'web']
        _diag(turn.id,'router_available_tools',{'available_tools':available,
            'allowed':turn.request['tools']['allowed'],'web_available':web_available(settings)})
        data = {'query':query, 'recent':recent,
                'context':routing_context(scope,turn.request.get('foundry_context') if not override else None),
                'ontology':ontology, 'character_identity':identity['character_agent']['identity_description'],
                'available_tools':available}
        try:
            proposal = await structured(llm,settings.model_companion_routing,prompts.PLANNER,data,
                SimpleCompanionPlan,settings.companion_routing_timeout_s,'companion.query_planner')
            diagnostics['research_plan'] = proposal.model_dump()
            _diag(turn.id,'router_response',proposal.model_dump())
            if any('character_memory' in task.sources for task in proposal.tasks):
                try:
                    relevance = await structured(llm,settings.model_companion_routing,prompts.MEMORY_RELEVANCE,
                        {'query':query,'identity':data['character_identity'],'recent':recent},MemoryRelevance,
                        settings.companion_routing_timeout_s,'companion.memory_relevance')
                    memory_relevant = relevance.relevant
                except Exception as exc:
                    diagnostics['memory_relevance_error'] = type(exc).__name__
                diagnostics['memory_relevant'] = memory_relevant
            compiled, trace = planning.compile_plan(proposal,available,memory_relevant=memory_relevant)
        except Exception as exc:
            compiled, trace = planning.fallback_plan(query,available)
            trace['error_type'] = type(exc).__name__
        diagnostics['planning'] = trace
        return compiled

    if max_messages:
        await commentary(COMPANION_ACKNOWLEDGEMENT, 1)
    await progress('ontology_resolution')
    async with database() as session:
        service = CompanionScopeService(session)
        chat = await service.state(turn)
        saved, override, pending = chat.resolved_ontology, chat.scope_override, chat.pending_clarification
        if turn.request.get('use_page_scope'):
            override = False
        if turn.request.get('clarification_id') and (not pending or pending['id'] != turn.request['clarification_id']):
            raise ValueError('stale_clarification')
        candidates = await service.candidates()
        explicit = scope.get('ontology_id')
        if override and saved and not turn.request.get('use_page_scope'):
            resolution_source = 'saved_override'
            ontology = await service.get(saved['id'])
            scope = scoped_context(scope, ontology, changed=True)
        elif explicit:
            resolution_source = 'explicit_context'
            ontology = await service.get(explicit)
            scope = scoped_context(scope, ontology)
            override = False
        elif saved:
            resolution_source = 'saved_chat'
            ontology = await service.get(saved['id'])
            scope = scoped_context(scope, ontology)
        # Pending ontology clarification must interpret the selection, not reuse old scope.
        if pending and pending['kind'] in {'ontology', 'scope_conflict'}:
            ontology = None

    # Scope intent is separate from research planning, including on saved scope.
    if ontology is not None:
        try:
            intent = await structured(llm,settings.model_companion_routing,prompts.SCOPE_INTENT,
                {'query':query,'recent':recent,'ontology':ontology,'pending':pending,'context':scope},
                ScopeIntent,settings.companion_routing_timeout_s,'companion.scope_intent')
        except Exception as exc:
            diagnostics['scope_intent_error'] = type(exc).__name__
            if pending:
                return await clarify(['Are you continuing the previous request, or starting a new question?'])
            intent = ScopeIntent(action='keep',ontology_name=None,resume_pending=False)
        diagnostics['scope_intent'] = intent.model_dump()
        if intent.action == 'clarify':
            return await clarify(['Which ontology (world name) should I use?'],kind='ontology')
        if pending and (intent.resume_pending or turn.request.get('clarification_id')):
            query = pending['query'] + '\nClarification: ' + original_query
        if intent.action == 'switch':
            await progress('ontology_update')
            async with database() as session:
                matches = await CompanionScopeService(session).exact(intent.ontology_name)
            if len(matches) != 1:
                return await clarify([f'What is the exact world name for {intent.ontology_name}?'],kind='ontology')
            target = {**matches[0],'source':'scope_intent'}
            if explicit and explicit != target['id'] and not override:
                return await clarify([f'Your page uses {ontology["name"]}. Switch this chat to {target["name"]}, or keep the page ontology?'],kind='scope_conflict',target_id=target['id'])
            old = ontology
            ontology = target
            scope = scoped_context(scope,ontology,changed=True)
            override = True
            resolution_source = 'scope_intent'
            await save_scope(ontology=ontology,context=scope,pending=None,override=True,
                event=('ontology.updated',{'previous':old,'ontology':ontology}))
            diagnostics['ontology_update'] = {'previous':old,'ontology':ontology}

    if ontology is None:
        try:
            decision = await structured(llm, settings.model_companion_routing, prompts.ONTOLOGY,
                {'query':query, 'recent':recent, 'context':routing_context(scope,turn.request.get('foundry_context')), 'candidates':candidates, 'pending':pending},
                OntologyDecision, settings.companion_routing_timeout_s, 'companion.ontology')
        except Exception as exc:
            _diag(turn.id, 'ontology_error', {'error_type': type(exc).__name__, 'error': str(exc)[:500]})
            return await clarify(['Which ontology (world name) should I use?'], kind='ontology')
        _diag(turn.id, 'ontology_response', decision.model_dump())
        if decision.ontology_name and decision.status == 'unresolved':
            async with database() as session:
                matches = await CompanionScopeService(session).exact(decision.ontology_name)
            if len(matches) == 1:
                candidates = matches
                decision.status = 'resolved'
                decision.ontology_id = matches[0]['id']
        if decision.status == 'resolved':
            resolution_source = 'clarification' if pending else 'inferred'
            if decision.ontology_id not in {o['id'] for o in candidates}:
                return await clarify(['Which ontology (world name) should I use?'], kind='ontology')
            async with database() as session:
                ontology = await CompanionScopeService(session).get(decision.ontology_id)
            changed = bool(scope.get('ontology_id') and scope['ontology_id'] != ontology['id'])
            override = bool(pending and pending['kind'] == 'scope_conflict' and ontology['id'] == pending.get('target_id'))
            scope = scoped_context(scope, ontology, changed=changed or override)
            if pending and (decision.resume_pending or turn.request.get('clarification_id')):
                query = pending['query'] + '\nClarification: ' + original_query
        elif decision.status != 'not_needed':
            names = ', '.join(o['name'] for o in candidates[:6])
            return await clarify([f'Which ontology (world name) do you mean?{(" Available worlds include: " + names) if names else ""}'], kind='ontology')
        else:
            scope.pop('ontology_id', None)
    if ontology:
        ontology = {**ontology, 'source':resolution_source}
    diagnostics['scope_resolution'] = {'source':resolution_source, 'ontology':ontology}
    await save_scope(ontology=ontology, context=scope, pending=pending, override=override)
    await progress('planning')
    plan = await plan_for()
    diagnostics['plan'] = plan.model_dump()
    await save_scope(context=scope, pending=None)

    retrieval = CompanionRetrieval(turn, actor)
    retrieval.settings = settings
    evidence = list(image_sources)
    # A scope override must not present a different Foundry world as evidence for
    # the newly selected ontology. The immutable snapshot stays in user history.
    if not override:
        evidence += foundry_evidence(turn.request.get('foundry_context'))
    if images and plan.calls:
        from app.jobs.companion.prompts import IMAGE_INTERPRETATION
        raw = await asyncio.wait_for(llm.chat(model=settings.model_companion_synthesis.model_copy(update={'web_search_enabled':False}),
            messages=[{'role':'system','content':IMAGE_INTERPRETATION},{'role':'user','content':[{'type':'text','text':json.dumps({'query':query,'context':scope})},*images]}],
            usage_tag='companion.image_interpretation',max_tokens=1200),settings.companion_evidence_timeout_s)
        observations = json.loads(raw)
        if set(observations) != {'observations','uncertainties','retrieval_terms'} or any(not isinstance(v,list) or len(v)>10 or any(not isinstance(s,str) or len(s)>300 for s in v) for v in observations.values()):
            raise ValueError('invalid_image_interpretation')
        for call in plan.calls:
            if call.tool != 'web':
                call.query = (call.query + ' ' + ' '.join(observations['retrieval_terms']))[:4000]

    async def skit():
        from app.jobs.companion.prompts import INTERIM
        for sequence in range(2, max_messages+1):
            await asyncio.sleep(settings.companion_interim_min_interval_s)
            try:
                text = await asyncio.wait_for(llm.chat(model=settings.model_companion_interim.model_copy(update={'web_search_enabled':False}),
                    messages=[{'role':'system','content':INTERIM},{'role':'user','content':json.dumps({'identity_description':identity['character_agent']['identity_description'],'query':original_query})}],
                    usage_tag='companion.interim',max_tokens=settings.companion_interim_output_tokens),settings.companion_routing_timeout_s)
                if text.strip():
                    await commentary(text, sequence)
            except Exception:
                return

    if settings.companion_interim_enabled and max_messages > 1:
        skit_task = asyncio.create_task(skit())

    async def retrieve(calls, *, recovery=False):
        phase = 'recovering' if recovery else 'retrieving'
        async def one(call):
            await progress(phase, coarse='retrieving', tool=call.tool, status='started')
            try:
                options = {'planned_call':call} if call.retrieval_depth == 'deep' or call.atlas_plan is not None else {}
                item = await retrieval.retrieve(call.tool, call.query, **options)
            except Exception as exc:
                _diag(turn.id, 'tool_error', {'tool': call.tool, 'query': call.query,
                      'error_type': type(exc).__name__, 'error': str(exc)[:500]})
                item = {'tool':call.tool,'status':'unavailable','evidence':[]}
            _diag(turn.id, 'tool_response', {'call_id': call.id, 'tool': item.get('tool', call.tool),
                  'status': item.get('status'), 'query': call.query,
                  'reason': item.get('reason'),
                  'evidence_count': len(item.get('evidence') or []),
                  'evidence': item.get('evidence') or []})
            return {**item,'id':call.id,'query':call.query,'need_ids':call.need_ids,
                    'retrieval_depth':call.retrieval_depth,'recovery':recovery}

        async def collect(row):
            outcomes.append(row)
            omitted('retrieval', row.get('evidence_omissions', []))
            omitted('retrieval', (row.get('source_trace') or {}).get('evidence_omissions', []))
            await progress(phase, coarse='retrieving', tool=row['tool'],status=row['status'])

        tasks = [asyncio.create_task(one(call)) for call in calls]
        timeout = min(settings.companion_retrieval_timeout_s, max(0.01, deadline-time.monotonic()))
        completed = set()
        try:
            for future in asyncio.as_completed(tasks, timeout=timeout):
                row = await future
                completed.add(row['id'])
                await collect(row)
        except asyncio.TimeoutError:
            for call in calls:
                if call.id not in completed:
                    await collect({'id':call.id,'tool':call.tool,'query':call.query,'need_ids':call.need_ids,
                                   'status':'timed_out','evidence':[],'recovery':recovery,
                                   'retrieval_depth':call.retrieval_depth,'timeout_layer':'retrieval_round','timeout_s':timeout})
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)

        # Completion order must not decide which needs/sources receive capacity.
        rows = {row['id']: row for row in outcomes}
        by_tool = {}
        ranks = {}
        for call in calls:
            row = rows.get(call.id)
            if not row:
                continue
            for index, source in enumerate(row['evidence'], 1):
                identifier = source['evidence_id']
                evidence_needs.setdefault(identifier, set()).update(call.need_ids)
                ranks[identifier] = min(ranks.get(identifier, index), index)
                evidence_ranks[identifier] = min(evidence_ranks.get(identifier, index), index)
                by_tool.setdefault(call.tool, {})[identifier] = source
        retained_ids = {row['evidence_id'] for row in evidence}
        for tool, sources in by_tool.items():
            candidates = [evidence_context.compact_evidence(source, evidence_needs[identifier])
                          for identifier, source in sources.items() if identifier not in retained_ids]
            capacity = max(0, COMPANION_TOOL_EVIDENCE_LIMIT - sum(row['source_type'] == tool for row in evidence))
            selected, exclusions = evidence_context.select_records(candidates, plan.needs, limit=capacity, ranks=ranks)
            omitted('collection', exclusions)
            if exclusions:
                diagnostics['evidence_budget_omitted'] = True
            evidence.extend(sources[row['evidence_id']] for row in selected)
            retained_ids.update(row['evidence_id'] for row in selected)

    async def assess():
        await progress('assessing', coarse='retrieving')
        trace = diagnostics.setdefault('evidence_assessment', {'attempts':[], 'fallback_used':False})
        metrics['evidence_assessment'] = trace
        records, exclusions = evidence_context.select_records(model_evidence(), plan.needs,
            limit=COMPANION_ASSESSMENT_RECORD_LIMIT, token_budget=settings.companion_max_evidence_tokens,
            ranks=evidence_ranks)
        omitted('assessment', exclusions)
        if exclusions:
            diagnostics['assessment_evidence_omitted'] = True
        trace['unrepresented_need_ids'] = [n.id for n in plan.needs if not any(n.id in r['need_ids'] for r in records)]
        data = {'query': query, 'needs': [{'id': n.id, 'question': n.question} for n in plan.needs],
                'context': scope, 'rpg_system': (ontology or {}).get('rpg_system'),
                'evidence': records, 'outcomes': evidence_context.compact_outcomes(outcomes),
                'web_available': bool(settings.companion_recovery_max_rounds and
                                      'web' in available_tools(turn, settings, scope))}
        for attempt in range(2):
            if attempt:
                if deadline-time.monotonic() <= 1:
                    break
                trace['fallback_used'] = True
                trace['fallback_mode'] = 'same_context_retry'
                await progress('assessing', coarse='retrieving', recovery=True)
            prompt = prompts.ASSESS_RECOVERY if attempt else prompts.ASSESS
            tag = 'companion.evidence_assessment_recovery' if attempt else 'companion.evidence_assessment'
            timeout = min(settings.companion_evidence_timeout_s,
                          evidence_context.ASSESSMENT_RETRY_TIMEOUT_S) if attempt else settings.companion_evidence_timeout_s
            timeout = min(timeout, max(0.01, deadline-time.monotonic()-0.5))
            payload = prompt + json.dumps(data, ensure_ascii=False)
            before = time.monotonic()
            entry = {'stage':tag, 'started_at':utcnow().isoformat(),
                'input_evidence_count':len(records), 'input_source_count':source_count(records),
                'compact_prompt_characters':len(payload), 'compact_prompt_bytes':len(payload.encode('utf-8')),
                'compact_prompt_tokens':estimate_tokens(payload),
                'model':settings.model_companion_evidence.model_copy(update={'web_search_enabled':False}).model_dump(),
                'configured_deadline_s':settings.companion_evidence_timeout_s,
                'effective_deadline_s':timeout, 'structured_response_status':'pending',
                'validation_failures':[], 'fallback_used':bool(attempt)}
            trace['attempts'].append(entry)
            try:
                result = await structured(llm, settings.model_companion_evidence, prompt, data,
                                          EvidenceAssessment, timeout, tag)
                entry['structured_response_status'] = 'valid'
                trace.update(retained_evidence_count=len(evidence), retained_source_count=source_count(),
                             sources_retained_for_synthesis=source_count(), evidence_retained_for_synthesis_count=len(evidence))
                return result
            except asyncio.CancelledError:
                entry.update(error_type='CancelledError', structured_response_status='cancelled')
                raise
            except Exception as exc:
                entry['error_type'] = type(exc).__name__
                if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
                    entry['structured_response_status'] = 'timed_out'
                elif isinstance(exc, ValidationError):
                    entry['structured_response_status'] = 'invalid_structured_output'
                    entry['validation_failures'] = [{'type':e['type'], 'loc':list(e['loc'])} for e in exc.errors(include_input=False, include_context=False)]
                elif isinstance(exc, ValueError) and str(exc)=='invalid_structured_response':
                    entry['structured_response_status'] = 'invalid_structured_output'
                    entry['validation_failures'] = [{'type':'invalid_structured_response'}]
                else:
                    entry['structured_response_status'] = 'failed'
                diagnostics['assessment_error'] = type(exc).__name__
            finally:
                entry.update(ended_at=utcnow().isoformat(), elapsed_ms=round((time.monotonic()-before)*1000,2),
                             retained_evidence_count=len(evidence), retained_source_count=source_count())
                for stage_record in reversed(metrics.get('stages', [])):
                    if stage_record['stage']==tag:
                        stage_record['elapsed_ms'] = entry['elapsed_ms']
                        if entry.get('error_type'):
                            stage_record['error_type'] = entry['error_type']
                        break
                trace.update(started_at=trace['attempts'][0]['started_at'], ended_at=entry['ended_at'],
                             elapsed_ms=round(sum(row.get('elapsed_ms',0) for row in trace['attempts']),2))
                _diag(turn.id, 'evidence_assessment_attempt', entry)
        trace.update(fallback_used=True, retained_evidence_count=len(evidence), retained_source_count=source_count())
        diagnostics['assessment_unavailable'] = True
        trace['fallback_mode'] = 'grounded_synthesis'
        trace['sources_retained_for_synthesis'] = source_count()
        trace['evidence_retained_for_synthesis_count'] = len(evidence)
        return None

    def private_terms():
        # Known private names/IDs are an additional guard, not a semantic classifier.
        terms = [(ontology or {}).get('name'), identity['character_agent'].get('name')]
        terms.extend(scope.get(key) for key in ('chronicle_id', 'character_agent_id', 'foundry_world_id'))
        snapshot = turn.request.get('foundry_context') or {}
        terms.append((snapshot.get('table') or {}).get('title'))
        for document in snapshot.get('documents', []):
            terms.extend([document.get('name'), document.get('uuid')])
        for source in evidence:
            if source['source_type'] in {'atlas', 'chronicle'}:
                if source['title'] not in {'Atlas record', 'Chronicle', 'Source'}:
                    terms.append(source['title'])
            if source['source_type'] in {'atlas', 'chronicle', 'character_memory', 'foundry'}:
                terms.append(source.get('source_id'))
        return terms

    try:
        await retrieve(plan.calls)
        if plan.needs:
            assessment = await assess()
            if not settings.companion_recovery_max_rounds:
                diagnostics['recovery_disabled'] = True
            if assessment and not assessment.sufficient:
                if settings.companion_recovery_max_rounds and 'web' in available_tools(turn, settings, scope):
                    calls, blocked = recovery_policy.web_calls(assessment, plan.needs,
                        limit=settings.companion_recovery_max_calls, private_terms=private_terms())
                    if blocked:
                        diagnostics['web_recovery_blocked'] = {'reason': 'privacy_blocked', 'count': len(blocked)}
                    if calls and deadline-time.monotonic() > 1:
                        diagnostics['recovery_trigger'] = 'assessed_evidence_gap'
                        await retrieve(calls, recovery=True)
                if not assessment.web_queries:
                    diagnostics['web_recovery_blocked'] = {'reason': 'no_public_queries'}

        synthesis_records, exclusions = evidence_context.select_records(model_evidence(), plan.needs,
            limit=len(evidence), token_budget=settings.companion_max_evidence_tokens, ranks=evidence_ranks)
        omitted('synthesis', exclusions)
        if exclusions:
            diagnostics['evidence_budget_omitted'] = True
        projected_ids = {row['evidence_id'] for row in synthesis_records}
        synthesis_evidence = [row for row in evidence if row['evidence_id'] in projected_ids]
        trace = diagnostics.get('evidence_assessment')
        if trace is not None:
            trace.update(sources_retained_for_synthesis=source_count(synthesis_evidence),
                         evidence_retained_for_synthesis_count=len(synthesis_evidence))
        allow_general=(settings.companion_general_knowledge_policy=='labeled_opt_in' and turn.request.get('general_knowledge_opt_in',False))
        data={'query':query,'recent':recent,'context':scope,'identity_description':identity['character_agent']['identity_description'],
              'personality_context':identity.get('personality_context'),'rapport':rapport,'evidence':synthesis_records,
              'needs':[{'id': n.id, 'question': n.question} for n in plan.needs],
              'assessment':assessment.model_dump() if assessment else None,
              'outcomes':evidence_context.compact_outcomes(outcomes),
              'assessment_unavailable':diagnostics.get('assessment_unavailable',False),
              'general_knowledge_allowed':allow_general,'repair_feedback':None}
        # Keep complete newest exchanges; never slice individual messages.
        while data['recent'] and estimate_tokens(json.dumps(data))>settings.companion_max_evidence_tokens+12000:
            data['recent']=data['recent'][2:]
        if estimate_tokens(json.dumps(data))>settings.companion_max_evidence_tokens+12000:
            raise ValueError('synthesis_context_budget_exceeded')
        catalog = {}
        media_spent = 0.0
        rich_enabled = settings.companion_rich_response_active and plan.action == 'retrieve'
        if rich_enabled:
            await progress('resolving_media', coarse='synthesizing')
            media_started = time.monotonic()
            try:
                catalog = await asyncio.wait_for(companion_media.resolve(turn, actor, synthesis_evidence, settings),
                    min(settings.companion_rich_response_timeout_s, max(0.01, deadline-time.monotonic())))
            except Exception:
                diagnostics['media_outcome'] = 'unavailable'
            media_spent = time.monotonic() - media_started
            metrics.setdefault('stages', []).append({'stage':'companion.media_resolution','elapsed_ms':round(media_spent*1000,2)})
        accepted, rejected=[],[]
        proposal, rich_accepted = None, set()
        for attempt in range(2):
            await progress('synthesizing',coarse='synthesizing')
            draft=await structured(llm,settings.model_companion_synthesis,prompts.SYNTHESIZE,data,Draft,
                                   settings.companion_tool_timeout_s,'companion.grounded_synthesis',settings.companion_output_tokens)
            private_draft = draft
            if rich_enabled:
                await progress('composing_response', coarse='synthesizing')
                try:
                    proposal, block_checks = rich_response.compose(draft, synthesis_evidence, catalog)
                    private_draft = draft.model_copy(update={'segments':draft.segments + block_checks})
                except Exception:
                    proposal = None
                    diagnostics['composition_outcome'] = 'text_only'
            await progress('verifying_claims',coarse='validating')
            verification=await structured(llm,settings.model_companion_evidence,prompts.VERIFY,
                {'draft':private_draft.model_dump(),'rich_response':evidence_context.compact_rich_response(proposal),'evidence':synthesis_records,'assessment':data['assessment'],'assessment_unavailable':data['assessment_unavailable'],'general_knowledge_allowed':allow_general},
                Verification,settings.companion_evidence_timeout_s,'companion.claim_verification')
            all_accepted,all_rejected=accepted_segments(private_draft,verification,synthesis_evidence,allow_general)
            narrative_ids={s.id for s in draft.segments}
            accepted=[s for s in all_accepted if s.id in narrative_ids]
            rejected=[r for r in all_rejected if r['id'] in narrative_ids]
            rich_accepted={s.id for s in all_accepted if s.id not in narrative_ids}
            if not rejected: break
            data['repair_feedback']=rejected
        if not accepted:
            if diagnostics.get('evidence_assessment',{}).get('fallback_used'):
                return await extractive_fallback()
            return await finish('I could not support an answer with the available evidence. Please clarify the question or provide another source.',outcome='limited')
        aliases={e['evidence_id']:i for i,e in enumerate(evidence,1)}
        segments=[]
        texts=[]
        for seg in accepted:
            text=seg.text
            if seg.kind=='general_knowledge':
                text='General knowledge — not verified against your world or books: '+text
            ids=list(dict.fromkeys(s.evidence_id for s in seg.supports))
            texts.append(text+''.join(f'[[source:{aliases[eid]}]]' for eid in ids))
            segments.append({'id':seg.id,'kind':seg.kind,'text':text,'evidence_ids':ids,'claims':seg.claims})
        incomplete = bool(rejected or (plan.needs and any(s.kind in {'uncertainty', 'clarification', 'general_knowledge'} for s in accepted)) or diagnostics.get('evidence_budget_omitted') or diagnostics.get('assessment_unavailable') or diagnostics.get('assessment_evidence_omitted') or
                          any(r['status'] != 'ok' or r.get('source_readiness')=='partial' or (r.get('source_trace') or {}).get('omitted_oversized_units') or
                              ((r.get('source_trace') or {}).get('evidence_coverage') or {}).get('status', 'adequate') != 'adequate' for r in outcomes) or
                          (plan.needs and (assessment is None or not assessment.sufficient)))
        if incomplete:
            limitation = 'I could not verify all requested details from the available sources.'
            identifier = 'verification-limitations'
            while identifier in {s['id'] for s in segments}:
                identifier += '-1'
            texts.append(limitation)
            segments.append({'id':identifier,'kind':'uncertainty','text':limitation,'evidence_ids':[],'claims':[]})
        complete=bool(synthesis_evidence and assessment and assessment.sufficient and not incomplete and
                      all(r['status']=='ok' for r in outcomes) and not diagnostics.get('evidence_budget_omitted') and
                      all(e.get('coverage',{}).get('status')!='partial' for e in evidence if e['source_type']=='foundry') and
                      all(not r.get('source_coverage',{}).get('sources_without_ready_index') and not r.get('source_coverage',{}).get('sources_requiring_reindex') for r in outcomes))
        rich = None
        if proposal is not None:
            try:
                rich = rich_response.finalize(proposal, segments, rich_accepted)
                try:
                    remaining_media = min(settings.companion_rich_response_timeout_s-media_spent, deadline-time.monotonic())
                    admitted_assets = (await asyncio.wait_for(companion_media.revalidate(turn, rich, settings), remaining_media)
                                       if remaining_media > 0 else set())
                except Exception:
                    admitted_assets = set()
                rich = rich_response.prune_assets(rich, admitted_assets)
                diagnostics['rich_response'] = {'candidate_blocks':len(proposal['blocks']), 'accepted_blocks':len(rich['blocks'])-1, 'assets':len(rich['assets'])}
            except Exception:
                rich = None
                diagnostics['composition_outcome'] = 'text_only'
        final_text = '\n\n'.join(texts)
        if rich:
            final_text = rich_response.fallback(rich, segments)
            # Keep every used block source in the legacy citation manifest too.
            block_ids = list(dict.fromkeys(eid for block in rich['blocks'] for eid in block['evidence_ids']))
            final_text += ''.join(f'[[source:{aliases[eid]}]]' for eid in block_ids)
        return await finish(final_text,outcome='ontology_updated' if diagnostics.get('ontology_update') and plan.action=='conversation' else 'answered' if not rejected else 'limited',
                            extra={'segments':segments,'coverage':'complete' if complete else 'partial','rejected_claims':rejected,'rich_response':rich})
    except Exception:
        if finalizing:
            raise
        if diagnostics.get('assessment_unavailable') or diagnostics.get('evidence_assessment',{}).get('fallback_used'):
            return await extractive_fallback()
        return await finish('I could not verify a supported answer from the available sources. Please try again or narrow the question.',outcome='limited')
    finally:
        if skit_task:
            skit_task.cancel()
            await asyncio.gather(skit_task,return_exceptions=True)
