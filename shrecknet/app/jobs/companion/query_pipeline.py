"""One conversational pipeline: scope, router, parallel tools, Markdown, rendering.

Generation remains private until deterministic citation resolution, required source
and attachment checks and leased atomic completion. Rapport runs after commit.
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
from uuid import uuid4
from app.jobs.companion import query_prompts as prompts
from app.jobs.companion import rich_response
from app.jobs.companion import evidence_context, planning
from app.integrations.companion import media as companion_media
from app.core.config_store import COMPANION_MODEL_FIELDS, COMPANION_TOOL_EVIDENCE_LIMIT
from app.schemas.character_companion import COMPANION_ACKNOWLEDGEMENT
from app.jobs.companion.query_contracts import (
    OntologyDecision, SimpleCompanionPlan, ScopeIntent,
)
from app.jobs.companion.contracts import build_answer
from app.jobs.companion.errors import CompanionExecutionError
from app.jobs.elder.context_budget import estimate_tokens
from app.integrations.companion.retrieval import CompanionRetrieval, web_available
from app.models.character_companion import CompanionTurn, CompanionAttachment
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
    if not web_available(settings):
        candidates.discard('web')
    return sorted(candidates)


async def run(turn, lease, *, actor, identity, rapport, recent, images, image_sources, llm, settings, metrics, started):
    # Import runtime transaction/lease primitives only after runner initialization.
    from app.jobs.companion.runner import database, mutate, load_identity
    original_query = turn.request['text']
    query = original_query
    scope = dict(turn.execution_context if turn.execution_context is not None else (turn.request.get('context') or {}))
    ontology = None
    resolution_source = 'not_needed'
    evidence, outcomes = [], []
    evidence_needs = {}
    evidence_ranks = {}
    diagnostics = {'version': 'markdown_v3'}
    diagnostics['deadlines'] = {name: getattr(settings, name) for name in (
        'companion_tool_timeout_s', 'companion_web_timeout_s', 'companion_routing_timeout_s',
        'companion_evidence_timeout_s', 'companion_retrieval_timeout_s', 'companion_turn_timeout_s')}
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
        clarification_started = time.monotonic()
        try:
            text = await asyncio.wait_for(llm.chat(model=settings.model_companion_synthesis.model_copy(update={'web_search_enabled': False}),
                messages=[{'role':'system','content':prompts.CLARIFY}, {'role':'user','content':json.dumps({'identity_description':identity['character_agent']['identity_description'], 'questions':questions})}],
                usage_tag='companion.clarification', max_tokens=250),
                min(settings.companion_routing_timeout_s, max(0.01, deadline-time.monotonic())))
            if not text.strip() or len(text) > 2000:
                raise ValueError('invalid_clarification')
        except Exception:
            text = ' '.join(questions)
            diagnostics['clarification_outcome'] = 'deterministic_questions'
        finally:
            metrics.setdefault('stages', []).append({'stage': 'companion.clarification',
                'elapsed_ms': round((time.monotonic()-clarification_started)*1000, 2)})
        return await finish(text, outcome='needs_clarification', extra={'clarification':pending})

    async def finish(text, *, outcome='answered', extra=None, citation_evidence=None):
        await progress('validating', coarse='validating')
        # Source revalidation remains fail-closed; a revoked source is not silently
        # replaced by an otherwise plausible answer.
        validation_started = time.monotonic()
        try:
            await asyncio.wait_for(load_identity(turn), min(settings.companion_tool_timeout_s,
                                                          max(0.01, deadline-time.monotonic())))
            if evidence:
                validator = CompanionRetrieval(turn, actor)
                validator.settings = settings
                await asyncio.wait_for(validator.validate_sources(evidence),
                    min(settings.companion_tool_timeout_s, max(0.01, deadline-time.monotonic())))
        except Exception as exc:
            status = getattr(exc, 'status_code', None) or getattr(getattr(exc, 'response', None), 'status_code', None)
            code = ('source_changed' if status == 409 or isinstance(exc, ValueError) and str(exc) == 'source_changed'
                    else 'source_access_denied' if status in {401, 403, 404} else 'source_validation_failed')
            raise CompanionExecutionError(code) from exc
        finally:
            metrics['source_validation_ms'] = round((time.monotonic()-validation_started)*1000, 2)
        result = build_answer(text, evidence, outcomes, query=query, citation_evidence=citation_evidence)
        result['outcome'] = outcome
        result['diagnostics'] = diagnostics
        result['assessment'] = None  # Historic result compatibility; no assessment runs.
        result['model_targets'] = {field:getattr(settings,field).model_dump() for field in COMPANION_MODEL_FIELDS}
        if extra:
            result.update(extra)
        for citation in result['citations']:
            citation['segment_ids'] = [s['id'] for s in result['segments'] if citation['evidence_id'] in s['evidence_ids']]

        metrics['execution_ms'] = round((time.monotonic()-started)*1000, 2)
        metrics['response_characters'] = len(result['text'])
        metrics['response_words'] = len(result['text'].split())
        metrics['citation_validation'] = result['citation_validation']
        if hasattr(llm, 'get_usage_summary'):
            metrics['llm_usage'] = llm.get_usage_summary()
            result['usage'] = metrics['llm_usage']
        async with database() as session:
            current = await session.get(CompanionTurn, turn.id)
            current.metrics = metrics
            for identifier in turn.request['attachment_ids']:
                attachment = await CompanionRepository(session).owned(CompanionAttachment, identifier, turn.user_id)
                if attachment.deleted:
                    raise CompanionExecutionError('attachment_deleted', retryable=False)
            await CompanionRepository(session).terminal(current, 'completed', result=result, lease=lease)
            await session.commit()
        return result

    async def plan_for():
        available = available_tools(turn, settings, scope)
        diagnostics['available_tools'] = available
        data = {'query': query, 'recent': recent,
                'context': routing_context(scope, turn.request.get('foundry_context') if not override else None),
                'ontology': ontology, 'character_identity': identity['character_agent']['identity_description'],
                'available_tools': available}
        try:
            proposal = await structured(llm, settings.model_companion_routing, prompts.PLANNER, data,
                SimpleCompanionPlan, settings.companion_routing_timeout_s, 'companion.query_planner')
            diagnostics['research_plan'] = proposal.model_dump()
            compiled, trace = planning.compile_plan(proposal, available)
        except Exception as exc:
            compiled, trace = planning.fallback_plan(query, available)
            trace['error_type'] = type(exc).__name__
        diagnostics['planning'] = trace
        metrics['selected_tools'] = list(dict.fromkeys(call.tool for call in compiled.calls))
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
            return await clarify(['Which world should I use?'],kind='ontology')
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
                return await clarify([f'Your page uses {ontology["name"]}. Switch this chat to {target["name"]}, or keep the page world?'],kind='scope_conflict',target_id=target['id'])
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
            _diag(turn.id, 'ontology_error', {'error_type': type(exc).__name__})
            return await clarify(['Which world should I use?'], kind='ontology')
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
                return await clarify(['Which world should I use?'], kind='ontology')
            async with database() as session:
                ontology = await CompanionScopeService(session).get(decision.ontology_id)
            changed = bool(scope.get('ontology_id') and scope['ontology_id'] != ontology['id'])
            override = bool(pending and pending['kind'] == 'scope_conflict' and ontology['id'] == pending.get('target_id'))
            scope = scoped_context(scope, ontology, changed=changed or override)
            if pending and (decision.resume_pending or turn.request.get('clarification_id')):
                query = pending['query'] + '\nClarification: ' + original_query
        elif decision.status != 'not_needed':
            names = ', '.join(o['name'] for o in candidates[:6])
            return await clarify([f'Which world do you mean?{(" Available worlds include: " + names) if names else ""}'], kind='ontology')
        else:
            scope.pop('ontology_id', None)
    if ontology:
        ontology = {**ontology, 'source':resolution_source}
    diagnostics['scope_resolution'] = {'source':resolution_source, 'ontology':ontology}
    await save_scope(ontology=ontology, context=scope, pending=pending, override=override)
    await progress('planning')
    plan = await plan_for()
    diagnostics['plan'] = plan.model_dump()
    if plan.action == 'clarify':
        return await clarify(plan.clarification_questions)
    await save_scope(context=scope, pending=None)

    retrieval = CompanionRetrieval(turn, actor)
    retrieval.settings = settings
    evidence = list(image_sources)
    # A scope override must not present a different Foundry world as evidence for
    # the newly selected ontology. The immutable snapshot stays in user history.
    if not override:
        evidence += foundry_evidence(turn.request.get('foundry_context'))
    observations = None
    if images and plan.calls:
        from app.jobs.companion.prompts import IMAGE_INTERPRETATION
        image_started = time.monotonic()
        try:
            raw = await asyncio.wait_for(llm.chat(model=settings.model_companion_synthesis.model_copy(update={'web_search_enabled':False}),
                messages=[{'role':'system','content':IMAGE_INTERPRETATION},{'role':'user','content':[{'type':'text','text':json.dumps({'query':query,'context':scope})},*images]}],
                usage_tag='companion.image_interpretation', max_tokens=1200),
                min(settings.companion_evidence_timeout_s, max(0.01, deadline-time.monotonic())))
            observations = json.loads(raw)
            if not isinstance(observations, dict) or set(observations) != {'observations','uncertainties','retrieval_terms'} or any(not isinstance(v,list) or len(v)>10 or any(not isinstance(s,str) or len(s)>300 for s in v) for v in observations.values()):
                raise ValueError('invalid_image_interpretation')
            for call in plan.calls:
                call.query = (call.query + ' ' + ' '.join(observations['retrieval_terms']))[
                    :500 if call.tool == 'web' else 4000]
        except Exception as exc:
            # Images still reach multimodal synthesis; optional search enrichment
            # cannot turn a valid image turn into a generic evidence refusal.
            observations = None
            diagnostics['image_interpretation_error'] = type(exc).__name__
        finally:
            metrics.setdefault('stages', []).append({'stage': 'companion.image_interpretation',
                'elapsed_ms': round((time.monotonic()-image_started)*1000, 2)})

    async def retrieve(calls):
        phase = 'retrieving'
        async def one(call):
            call_started = time.monotonic()
            await progress(phase, coarse='retrieving', tool=call.tool, status='started')
            try:
                options = {'planned_call':call} if call.retrieval_depth == 'deep' or call.atlas_plan is not None else {}
                item = await retrieval.retrieve(call.tool, call.query, **options)
            except Exception as exc:
                _diag(turn.id, 'tool_error', {'tool': call.tool, 'error_type': type(exc).__name__})
                item = {'tool':call.tool,'status':'unavailable','evidence':[]}
            _diag(turn.id, 'tool_response', {'call_id': call.id, 'tool': call.tool,
                  'status': item.get('status'), 'evidence_count': len(item.get('evidence') or [])})
            item.setdefault('elapsed_ms', round((time.monotonic()-call_started)*1000, 2))
            return {**item,'id':call.id,'query':call.query,'need_ids':call.need_ids,
                    'retrieval_depth':call.retrieval_depth,'recovery':False}

        async def collect(row):
            outcomes.append(row)
            metrics.setdefault('tools', []).append({key: row[key] for key in
                ('id', 'tool', 'status', 'elapsed_ms', 'timeout_layer') if key in row})
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
                                   'status':'timed_out','evidence':[],'recovery':False,
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

    await retrieve(plan.calls)
    metrics['web_llm_usage'] = getattr(retrieval, 'llm_usage', [])
    synthesis_records, exclusions = evidence_context.select_records(model_evidence(), plan.needs,
        limit=20, token_budget=settings.companion_max_evidence_tokens, ranks=evidence_ranks)
    omitted('synthesis', exclusions)
    source_map = {row['evidence_id']: row for row in evidence}
    # Alias order is exactly the model projection, not the larger persisted pool.
    synthesis_evidence = [source_map[row['evidence_id']] for row in synthesis_records]
    for index, record in enumerate(synthesis_records, 1):
        record['citation_id'] = str(index)
    data = {'original_query': original_query, 'query': query, 'recent': list(recent), 'context': scope,
            'identity_description': identity['character_agent']['identity_description'],
            'personality_context': identity.get('personality_context'), 'rapport': rapport,
            'evidence': synthesis_records, 'needs': [{'id': n.id, 'question': n.question} for n in plan.needs],
            'outcomes': evidence_context.compact_outcomes(outcomes),
            'planning_limitations': diagnostics['planning'].get('rejected_sources', []),
            'evidence_omitted': bool(diagnostics.get('evidence_omissions')),
            'general_knowledge_allowed': settings.companion_general_knowledge_policy == 'labeled_opt_in'
                and turn.request.get('general_knowledge_opt_in', False),
            'image_observations': observations}
    # Drop complete oldest exchanges; never slice messages or silently lose identity.
    while data['recent'] and estimate_tokens(json.dumps(data)) > settings.companion_max_evidence_tokens+12000:
        data['recent'] = data['recent'][2:]
    if estimate_tokens(json.dumps(data)) > settings.companion_max_evidence_tokens+12000:
        raise CompanionExecutionError('synthesis_context_budget_exceeded')
    metrics['synthesis_evidence_count'] = len(synthesis_evidence)
    await progress('synthesizing', coarse='synthesizing')
    raw = None
    for attempt in range(2):
        stage_started = time.monotonic()
        stage = {'stage': 'companion.grounded_synthesis', 'attempt': attempt+1}
        try:
            content = json.dumps(data, ensure_ascii=False)
            response = await asyncio.wait_for(llm.chat(
                model=settings.model_companion_synthesis.model_copy(update={'web_search_enabled': False}),
                messages=[{'role': 'system', 'content': prompts.SYNTHESIZE_SHORT if attempt else prompts.SYNTHESIZE},
                          {'role': 'user', 'content': [{'type': 'text', 'text': content}, *images] if images else content}],
                usage_tag='companion.grounded_synthesis', max_tokens=settings.companion_output_tokens,
                return_metadata=True), min(settings.companion_tool_timeout_s, max(0.01, deadline-time.monotonic())))
            if not isinstance(response, dict):
                raise CompanionExecutionError('invalid_synthesis_response')
            reason = (response.get('response_metadata') or {}).get('finish_reason')
            stage['finish_reason'] = reason
            if response.get('usage'):
                stage['usage'] = response['usage']
            if reason in {'length', 'max_tokens', 'MAX_TOKENS', 'max_output_tokens'}:
                diagnostics['synthesis_truncations'] = attempt+1
                if attempt == 0:
                    continue
                raise CompanionExecutionError('synthesis_truncated')
            if reason in {'content_filter', 'refusal', 'error'}:
                raise CompanionExecutionError('synthesis_generation_failed')
            raw = response.get('text')
            if not isinstance(raw, str) or not raw.strip():
                raise CompanionExecutionError('empty_synthesis')
            if len(raw) > 160000:
                raise CompanionExecutionError('synthesis_output_too_large')
            break
        except CompanionExecutionError as exc:
            stage['error_code'] = exc.code
            raise
        except Exception as exc:
            stage['error_type'] = type(exc).__name__
            raise CompanionExecutionError('synthesis_generation_failed') from exc
        finally:
            stage['elapsed_ms'] = round((time.monotonic()-stage_started)*1000, 2)
            metrics.setdefault('stages', []).append(stage)

    # Resolve citations before choosing presentation; optional components see cited sources only.
    answer = build_answer(raw, evidence, outcomes, query=query, citation_evidence=synthesis_evidence)
    diagnostics['citation_validation'] = answer['citation_validation']
    if not synthesis_evidence and plan.needs:
        diagnostics['evidence_status'] = 'none_returned' if outcomes else 'no_executable_tools'
    rich = None
    if settings.companion_rich_response_active and answer['citations']:
        cited_sources = [source_map[citation['evidence_id']] for citation in answer['citations']]
        await progress('resolving_media', coarse='synthesizing')
        media_started = time.monotonic()
        try:
            catalog = await asyncio.wait_for(companion_media.resolve(turn, actor, cited_sources, settings),
                min(settings.companion_rich_response_timeout_s, max(0.01, deadline-time.monotonic())))
        except Exception:
            catalog = {}
            diagnostics['media_outcome'] = 'unavailable'
        metrics['media_ms'] = round((time.monotonic()-media_started)*1000, 2)
        try:
            rich = rich_response.compose(answer['segments'], cited_sources, catalog, query=query)
            try:
                remaining_media = min(settings.companion_rich_response_timeout_s - (time.monotonic()-media_started),
                                      deadline-time.monotonic())
                admitted = (await asyncio.wait_for(companion_media.revalidate(turn, rich, settings), remaining_media)
                            if remaining_media > 0 else set())
            except Exception:
                admitted = set()
                diagnostics['media_outcome'] = 'unavailable'
            rich = rich_response.prune_assets(rich, admitted)
        except Exception:
            rich = None
            diagnostics['composition_outcome'] = 'text_only'
    return await finish(raw,
        outcome='ontology_updated' if diagnostics.get('ontology_update') and plan.action == 'conversation'
            else 'limited' if plan.needs and not synthesis_evidence else 'answered',
        citation_evidence=synthesis_evidence, extra={'rich_response': rich, 'rejected_claims': []})
