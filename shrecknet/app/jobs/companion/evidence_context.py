"""Compact model evidence and deterministic extractive rendering.

Full evidence remains server-side for navigation, access/revision checks and the
Answer Breakdown. Model projections omit navigation URL fields, geometry, document blobs
and embeddings; stable IDs and exact passages remain unchanged. Extraction attributes source text; it does not verify its claims.
"""
from __future__ import annotations

import html
import json
import re

from app.jobs.elder.context_budget import estimate_tokens
from app.core.config_store import COMPANION_ASSESSMENT_RETRY_TIMEOUT_S

ASSESSMENT_RETRY_TIMEOUT_S = COMPANION_ASSESSMENT_RETRY_TIMEOUT_S
ASSESSMENT_RETRY_EVIDENCE_TOKENS = 3000
ASSESSMENT_RETRY_RECORDS = 4
SOURCE_LABELS = {
    'canonical_world': 'world source', 'document_evidence': 'document excerpt',
    'external_web': 'public web source', 'chronicle_record': 'chronicle note',
    'subjective_memory': 'subjective character memory', 'user_provided': 'user-provided material',
}


def compact_structured(source):
    structured = source.get('structured_evidence') or {}
    result = {}
    # These are the structures emitted by the existing Companion adapters.
    for key, allowed in {
        'fields': ('label', 'value'),
        'emotions': ('description', 'emotion', 'intensity'),
        'beliefs': ('statement', 'confidence'),
        'impacts': ('impact_type', 'direction', 'description', 'target_name'),
        'evidence_groups': ('evidence_id', 'need_ids'),
    }.items():
        if isinstance(structured.get(key), list):
            result[key] = [{name: row[name] for name in allowed if name in row}
                           for row in structured[key] if isinstance(row, dict)]
    return result or None


def compact_evidence(source, need_ids=()):
    reference = source.get('reference') or {}
    return {
        'evidence_id': source['evidence_id'], 'source_type': source['source_type'],
        'title': source['title'], 'page_number': reference.get('page_number'),
        'page_label': reference.get('page_label'), 'excerpt': source['excerpt'],
        'structured_evidence': compact_structured(source),
        'need_ids': sorted(need_ids),
        'scope': {key: value for key, value in (source.get('scope') or {}).items()
                  if key in {'ontology_id', 'shreckworld_id', 'character_agent_id', 'chronicle_id'}},
        'source_context': {key: reference[key] for key in
                           ('node_id', 'node_label', 'library_item_id', 'system', 'edition', 'rpg_system',
                            'authority_tier', 'authority_priority')
                           if key in reference},
        'epistemic_category': source.get('epistemic_category'),
        'coverage': {key: value for key, value in (source.get('coverage') or {}).items()
                     if key in {'status', 'warnings'}},
    }


def compact_outcomes(outcomes):
    result = []
    for row in outcomes:
        item = {key: row[key] for key in
                ('id', 'tool', 'status', 'need_ids', 'reason', 'recovery', 'retrieval_depth',
                 'source_coverage', 'source_readiness') if key in row}
        trace = row.get('source_trace') or {}
        if trace:
            item['source_trace'] = {
                'followup_rounds': (trace.get('v3_trace') or {}).get('followup_rounds', 0),
                'omitted_oversized_units': trace.get('omitted_oversized_units', 0),
                'evidence_coverage': {'status': (trace.get('evidence_coverage') or {}).get('status')},
            }
        result.append(item)
    return result


def compact_rich_response(proposal):
    """Keep visible block claims/provenance, leaving delivery/navigation server-side."""
    if proposal is None:
        return None
    allowed = {'ontology_id','shreckworld_id','library_item_id','node_id','node_label',
               'page_number','page_label','foundry_world_id','uuid','chronicle_id'}
    blocks = []
    for block in proposal['blocks']:
        row = {key:value for key,value in block.items() if key not in {'document_ref','entity_ref'}}
        for key in ('document_ref','entity_ref'):
            if key in block:
                row[key] = {k:v for k,v in block[key].items() if k in allowed}
        blocks.append(row)
    return {'version':proposal['version'], 'blocks':blocks,
            'assets':[{key:value for key,value in row.items() if key in
                       {'id','source_type','evidence_ids','provenance_kind','attribution','target_id'}}
                      for row in proposal['assets']],
            'targets':[{'id':row['id'], 'kind':row['kind'], 'reference':{
                key:value for key,value in row['reference'].items() if key in allowed}}
                       for row in proposal['targets']]}


def recovery_records(records, token_budget):
    """Retain complete records, preferring representation of distinct needs."""
    remaining = list(records)
    selected, omitted, covered = [], [], set()
    budget = min(token_budget, ASSESSMENT_RETRY_EVIDENCE_TOKENS)
    while remaining:
        row = max(remaining, key=lambda r: len(set(r['need_ids']) - covered))
        remaining.remove(row)
        if len(selected) >= ASSESSMENT_RETRY_RECORDS:
            reason = 'assessment_retry_record_limit'
        elif estimate_tokens(json.dumps([*selected, row], ensure_ascii=False)) > budget:
            reason = 'assessment_retry_token_budget'
        else:
            selected.append(row)
            covered.update(row['need_ids'])
            continue
        omitted.append({'evidence_id': row['evidence_id'], 'reason': reason})
    return selected, omitted


def quoted_text(value):
    """Render source content literally, including hostile citation markers/HTML."""
    escaped = html.escape(value, quote=False)
    return re.sub(r'([\\`*_{}\[\]()#!|])', r'\\\1', escaped)


def extractive_response(evidence, output_tokens, *, assessment_available=False):
    """Only quote supplied text/fields; never generate an inferred factual answer."""
    limitation = (('I could not complete a fully verified answer. ' if assessment_available else
                   'I could not verify how these excerpts answer your question. ') + 'These are retrieved '
                  'source excerpts, not a verified answer to your question. They may be incomplete '
                  'or inapplicable, and do not establish broader claims or resolve conflicting sources.')
    segments = [{'id': 'extraction-limitations', 'kind': 'uncertainty', 'text': limitation,
                 'evidence_ids': [], 'claims': []}]
    texts, omissions = [limitation], []
    limit = min(output_tokens, 2000)
    for index, source in enumerate(evidence, 1):
        passage = source.get('excerpt') or ''
        if not passage.strip():
            fields = compact_structured(source)
            passage = json.dumps(fields, ensure_ascii=False) if fields else ''
        if not passage.strip():
            omissions.append({'evidence_id': source['evidence_id'], 'reason': 'no_extractable_passage'})
            continue
        if len(segments) >= 5:
            omissions.append({'evidence_id': source['evidence_id'], 'reason': 'extractive_record_limit'})
            continue
        reference = source.get('reference') or {}
        title = quoted_text(source['title'])
        page = reference.get('page_label') or reference.get('page_number')
        label = f'{title} (page {quoted_text(str(page))})' if page is not None else title
        category = SOURCE_LABELS.get(source.get('epistemic_category'), 'source excerpt')
        header = f'{label} — {quoted_text(category)}'
        # A contiguous, explicitly shortened quote is still attributable. Keep
        # the original whole passage in evidence; never append invented text.
        prefix = passage[:1600]
        while prefix:
            shortened = len(prefix) < len(passage)
            quote = header + '\n' + '\n'.join('> ' + line for line in quoted_text(prefix).splitlines())
            if shortened:
                quote += '\n[Passage shortened; consult the cited source for full context.]'
            if estimate_tokens('\n\n'.join([*texts, quote])) <= limit:
                break
            prefix = prefix[:len(prefix)//2]
        if not prefix:
            omissions.append({'evidence_id': source['evidence_id'], 'reason': 'extractive_output_budget'})
            continue
        if len(prefix) < len(passage):
            omissions.append({'evidence_id': source['evidence_id'], 'reason': 'extractive_quote_shortened',
                             'original_characters':len(passage), 'quoted_characters':len(prefix)})
        texts.append(quote + f'[[source:{index}]]')
        segments.append({'id': f'excerpt-{index}', 'kind': 'extractive', 'text': quote,
                         'evidence_ids': [source['evidence_id']], 'claims': []})
    return '\n\n'.join(texts), segments, omissions
