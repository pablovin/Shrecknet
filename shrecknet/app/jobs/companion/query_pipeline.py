"""Evidence-first Companion orchestration; all durable writes use existing leases.

Explicit/saved scope bypasses inference. The planner may update scope once before
parallel retrieval; coverage/recovery precede private synthesis. Optional media
resolution/composition stays private and its block claims share narrative
verification, followed by source/asset checks and the leased final commit.
"""
from __future__ import annotations
import asyncio
import json
import time
from uuid import uuid4
from app.jobs.companion import query_prompts as prompts
from app.jobs.companion import rich_response
from app.integrations.companion import media as companion_media
from app.core.config_store import COMPANION_MODEL_FIELDS
from app.schemas.character_companion import COMPANION_ACKNOWLEDGEMENT
from app.jobs.companion.query_contracts import OntologyDecision, QueryPlan, Assessment, Draft, Verification
from app.jobs.companion.contracts import build_answer
from app.jobs.elder.context_budget import estimate_tokens
from app.integrations.companion.retrieval import CompanionRetrieval, web_available
from app.models.character_companion import CompanionTurn, CompanionAttachment
from app.repositories.character_companion_repository import CompanionRepository
from app.services.companion_scope_service import CompanionScopeService, scoped_context
from app.services.companion_foundry_context import foundry_evidence


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
    if not settings.companion_shreckworld_url:
        candidates.discard('shreckworld')
    if not scope.get('chronicle_id') or not settings.companion_chronicles_url:
        candidates.discard('chronicle')
    if turn.request['tools']['web'] == 'disabled' or not web_available(settings):
        candidates.discard('web')
    return sorted(candidates)


def validate_plan(plan, available, scope, *, updates=True):
    if any(c.tool not in available for c in plan.calls):
        raise ValueError('unavailable_planned_tool')
    if any(c.tool == 'web' and len(c.query) > 500 for c in plan.calls):
        raise ValueError('web_query_too_long')
    if plan.action == 'ontology_update' and (not updates or not scope.get('ontology_id')):
        raise ValueError('ontology_update_unavailable')
    return plan


def valid_support(support, sources):
    source = sources.get(support.evidence_id)
    if source is None:
        return False
    # Structured Foundry fields can carry the only visible value (for example HP)
    # even when the document has no prose. Semantic verification still checks its
    # meaning and provenance; substring membership alone is never sufficient.
    return (support.quote in source['excerpt'] or
            (source.get('structured_evidence') is not None and
             support.quote in json.dumps(source['structured_evidence'], ensure_ascii=False)))


def validate_assessment(review, needs, evidence):
    wanted = {n.id for n in needs}
    if len(review.needs) != len(wanted) or {n.need_id for n in review.needs} != wanted:
        raise ValueError('incomplete_need_assessment')
    sources = {e['evidence_id']: e for e in evidence}
    for need in review.needs:
        if any(not valid_support(s, sources) for s in need.supports):
            raise ValueError('invalid_assessment_support')
        if not set(need.conflicting_evidence_ids) <= sources.keys() or (need.status == 'supported' and not need.supports):
            raise ValueError('unsupported_assessment')
    return review


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
    plan = None
    diagnostics = {'version': 'evidence_v2'}
    skit_task = None
    max_messages = settings.companion_interim_max_messages
    deadline = started + settings.companion_turn_timeout_s

    async def structured(llm, target, prompt, data, schema, timeout, tag, tokens=2500):
        before = time.monotonic()
        try:
            return await request_structured(llm, target, prompt, data, schema,
                min(timeout, max(0.01, deadline-time.monotonic())), tag, tokens)
        finally:
            metrics.setdefault('stages', []).append({'stage':tag, 'elapsed_ms':round((time.monotonic()-before)*1000,2)})

    async def progress(stage, *, coarse='routing', **extra):
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
        await progress('validating', coarse='validating')
        # Source revalidation remains fail-closed; a revoked source is not silently
        # replaced by an otherwise plausible answer.
        await load_identity(turn)
        if evidence:
            validator = CompanionRetrieval(turn, actor)
            validator.settings = settings
            await validator.validate_sources(evidence)
        result = build_answer(text, evidence, outcomes, query=query)
        result['outcome'] = outcome
        result['diagnostics'] = diagnostics
        result['assessment'] = assessment.model_dump() if assessment else None
        result['coverage'] = 'partial' if outcomes or evidence else 'unknown'
        result['model_targets'] = {field:getattr(settings,field).model_dump() for field in COMPANION_MODEL_FIELDS}
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

    async def plan_for(updates=True):
        available = available_tools(turn, settings, scope)
        data = {'query': query, 'recent':recent, 'context':scope, 'ontology':ontology,
                'available_tools':available, 'allow_ontology_update':updates and bool(ontology), 'pending':pending}
        data['context'] = routing_context(scope, turn.request.get('foundry_context') if not override else None)
        result = await structured(llm, settings.model_companion_routing, prompts.PLANNER, data, QueryPlan,
                                  settings.companion_routing_timeout_s, 'companion.query_planner')
        return validate_plan(result, available, scope, updates=updates)

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

    if ontology is None:
        try:
            decision = await structured(llm, settings.model_companion_routing, prompts.ONTOLOGY,
                {'query':query, 'recent':recent, 'context':routing_context(scope,turn.request.get('foundry_context')), 'candidates':candidates, 'pending':pending},
                OntologyDecision, settings.companion_routing_timeout_s, 'companion.ontology')
        except Exception:
            return await clarify(['Which ontology (world name) should I use?'], kind='ontology')
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
            if pending:
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
    try:
        await progress('planning')
        plan = await plan_for()
        if plan.resume_pending and pending:
            query = pending['query'] + '\nClarification: ' + original_query
        if plan.action == 'ontology_update':
            await progress('ontology_update')
            async with database() as session:
                matches = await CompanionScopeService(session).exact(plan.ontology_update.ontology_name)
            if len(matches) != 1:
                if matches:
                    options = '; '.join(f"{index}. {o['name']} — {o['description'] or o['rpg_system'] or 'no description'}" for index,o in enumerate(matches[:6],1))
                    question = f'Which ontology do you mean? {options}'
                else:
                    question = f'I could not identify an ontology named {plan.ontology_update.ontology_name}. What is its exact world name?'
                return await clarify([question], kind='ontology')
            target = {**matches[0], 'source':'router_update'}
            if explicit and explicit != target['id'] and not override:
                return await clarify([f'Your page uses {ontology["name"]}. Switch this chat to {target["name"]}, or keep the page ontology?'], kind='scope_conflict', target_id=target['id'])
            old = ontology
            ontology = target
            scope = scoped_context(scope, ontology, changed=True)
            await save_scope(ontology=ontology, context=scope, pending=None, override=True,
                event=('ontology.updated', {'previous':old, 'ontology':ontology}))
            diagnostics['ontology_update'] = {'previous':old, 'ontology':ontology}
            pending = None
            await progress('replanning', ontology_name=ontology['name'])
            plan = await plan_for(updates=False)
        diagnostics['plan'] = plan.model_dump()
        if plan.action == 'clarify':
            return await clarify(plan.clarification_questions)
        await save_scope(context=scope, pending=None)
    except Exception:
        return await clarify(['Could you clarify the information you want me to find?'])

    if turn.request['tools']['web'] == 'require' and not any(c.tool == 'web' for c in plan.calls):
        return await finish('I could not plan the required web search. Please clarify what you want searched.', outcome='limited')

    retrieval = CompanionRetrieval(turn, actor)
    retrieval.settings = settings
    evidence = list(image_sources)
    # A scope override must not present a different Foundry world as evidence for
    # the newly selected ontology. The immutable snapshot stays in user history.
    if not override:
        evidence += foundry_evidence(turn.request.get('foundry_context'))
    if sum(estimate_tokens(json.dumps(e)) for e in evidence) > settings.companion_max_evidence_tokens:
        raise ValueError('context_budget_exceeded')
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
                item = await retrieval.retrieve(call.tool, call.query)
            except Exception:
                item = {'tool':call.tool,'status':'unavailable','evidence':[]}
            return {**item,'id':call.id,'query':call.query,'need_ids':call.need_ids,'recovery':recovery}

        async def collect(row):
            outcomes.append(row)
            for source in row['evidence']:
                if source['evidence_id'] in {e['evidence_id'] for e in evidence}:
                    continue
                if len(evidence) >= 20 or sum(estimate_tokens(json.dumps(e)) for e in evidence)+estimate_tokens(json.dumps(source)) > settings.companion_max_evidence_tokens:
                    diagnostics['evidence_budget_omitted'] = True
                    continue
                evidence.append(source)
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
                                   'status':'timed_out','evidence':[],'recovery':recovery})
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)

    async def assess(recovery_allowed):
        await progress('assessing', coarse='retrieving')
        data={'needs':[n.model_dump() for n in plan.needs], 'evidence':evidence,
              'outcomes':[{k:v for k,v in row.items() if k!='evidence'} for row in outcomes],
              'available_tools':available_tools(turn,settings,scope), 'recovery_allowed':recovery_allowed}
        result=await structured(llm,settings.model_companion_evidence,prompts.ASSESS,data,Assessment,
                                settings.companion_evidence_timeout_s,'companion.evidence_assessment')
        return validate_assessment(result,plan.needs,evidence)

    try:
        await retrieve(plan.calls)
        if plan.needs:
            try:
                assessment=await assess(bool(settings.companion_recovery_max_rounds))
                missing={n.need_id for n in assessment.needs if n.status!='supported'}
                recovery=[]
                for call in assessment.follow_up_calls:
                    if call.tool not in available_tools(turn,settings,scope) or not set(call.need_ids)<=missing:
                        continue
                    if any(call.tool==r['tool'] and call.query==r['query'] for r in outcomes):
                        continue
                    if call.tool=='web' and len(call.query)>500:
                        continue
                    recovery.append(call)
                if recovery and settings.companion_recovery_max_rounds and deadline-time.monotonic()>settings.companion_evidence_timeout_s:
                    await retrieve(recovery[:settings.companion_recovery_max_calls],recovery=True)
                    assessment=await assess(False)
            except Exception:
                diagnostics['assessment_error']='unverified'
                return await finish('I could not verify the available evidence. I cannot give a supported factual answer yet.',outcome='limited')

        allow_general=(settings.companion_general_knowledge_policy=='labeled_opt_in' and turn.request.get('general_knowledge_opt_in',False))
        data={'query':query,'recent':recent,'context':scope,'identity_description':identity['character_agent']['identity_description'],
              'personality_context':identity.get('personality_context'),'rapport':rapport,'evidence':evidence,
              'assessment':assessment.model_dump() if assessment else None,
              'outcomes':[{k:v for k,v in r.items() if k!='evidence'} for r in outcomes],
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
                catalog = await asyncio.wait_for(companion_media.resolve(turn, actor, evidence, settings),
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
                                   settings.companion_turn_timeout_s,'companion.grounded_synthesis',settings.companion_output_tokens)
            private_draft = draft
            if rich_enabled:
                await progress('composing_response', coarse='synthesizing')
                try:
                    proposal, block_checks = rich_response.compose(draft, evidence, catalog)
                    private_draft = draft.model_copy(update={'segments':draft.segments + block_checks})
                except Exception:
                    proposal = None
                    diagnostics['composition_outcome'] = 'text_only'
            await progress('verifying_claims',coarse='validating')
            verification=await structured(llm,settings.model_companion_evidence,prompts.VERIFY,
                {'draft':private_draft.model_dump(),'rich_response':{k:v for k,v in proposal.items() if k != 'dependencies'} if proposal else None,'evidence':evidence,'assessment':data['assessment'],'general_knowledge_allowed':allow_general},
                Verification,settings.companion_evidence_timeout_s,'companion.claim_verification')
            all_accepted,all_rejected=accepted_segments(private_draft,verification,evidence,allow_general)
            narrative_ids={s.id for s in draft.segments}
            accepted=[s for s in all_accepted if s.id in narrative_ids]
            rejected=[r for r in all_rejected if r['id'] in narrative_ids]
            rich_accepted={s.id for s in all_accepted if s.id not in narrative_ids}
            if not rejected: break
            data['repair_feedback']=rejected
        if not accepted:
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
        incomplete = bool(rejected or diagnostics.get('evidence_budget_omitted') or
                          any(r['status'] != 'ok' or (r.get('source_trace') or {}).get('omitted_oversized_units') or
                              ((r.get('source_trace') or {}).get('evidence_coverage') or {}).get('status', 'adequate') != 'adequate' for r in outcomes) or
                          (assessment and any(n.status != 'supported' for n in assessment.needs)))
        if incomplete:
            limitation = 'I could not verify all requested details from the available sources.'
            identifier = 'verification-limitations'
            while identifier in {s['id'] for s in segments}:
                identifier += '-1'
            texts.append(limitation)
            segments.append({'id':identifier,'kind':'uncertainty','text':limitation,'evidence_ids':[],'claims':[]})
        complete=bool(assessment and all(n.status=='supported' for n in assessment.needs) and not incomplete and
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
        return await finish('I could not verify a supported answer from the available sources. Please try again or narrow the question.',outcome='limited')
    finally:
        if skit_task:
            skit_task.cancel()
            await asyncio.gather(skit_task,return_exceptions=True)
