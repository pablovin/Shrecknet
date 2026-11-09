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


def select_records(records, needs, *, limit, token_budget=None, ranks=None):
    """Reserve need coverage, then balance relevance and source diversity.

    Associations describe retrieval intent only. Token omissions affect this
    projection, never the stored evidence. Input order is not a ranking signal.
    """
    ranks = ranks or {}
    unique = {}
    for record in records:
        identifier = record['evidence_id']
        if identifier in unique:
            unique[identifier]['need_ids'] = sorted(set(unique[identifier]['need_ids']) | set(record.get('need_ids', [])))
        else:
            unique[identifier] = {**record, 'need_ids': list(record.get('need_ids', []))}
    remaining = list(unique.values())
    selected, omissions, covered, counts, need_counts = [], [], set(), {}, {}
    stop = {'the', 'a', 'an', 'is', 'are', 'of', 'in', 'to', 'and', 'what', 'who', 'how', 'does'}
    def words(value):
        return set(re.findall(r"\w+", value.casefold())) - stop
    questions = {need.id: words(need.question) for need in needs}
    passages = {r['evidence_id']: words(r.get('title', '') + ' ' + r.get('excerpt', '') + ' ' +
                json.dumps(r.get('structured_evidence'), ensure_ascii=False)) for r in remaining}
    def relevance(record, need_id):
        return (len(questions[need_id] & passages[record['evidence_id']]),
                int(need_id in record['need_ids']))
    def score(record, need_id=None):
        related = relevance(record, need_id) if need_id else max(
            (relevance(record, n) for n in questions), default=(0, 0))
        stable = (ranks.get(record['evidence_id'], 1), record['evidence_id'])
        if need_id:
            return (-related[0], -related[1], counts.get(record['source_type'], 0), *stable)
        associated = [n for n in questions if any(relevance(record, n))]
        represented = min((need_counts.get(n, 0) for n in associated), default=len(selected))
        return (represented, counts.get(record['source_type'], 0), -related[0], -related[1], *stable)
    def admit(record):
        remaining.remove(record)
        if token_budget is not None and estimate_tokens(json.dumps([*selected, record], ensure_ascii=False)) > token_budget:
            omissions.append({'evidence_id': record['evidence_id'], 'reason': 'evidence_token_budget'})
            return False
        selected.append(record)
        covered.update(record['need_ids'])
        for n in questions:
            if any(relevance(record, n)):
                need_counts[n] = need_counts.get(n, 0) + 1
        counts[record['source_type']] = counts.get(record['source_type'], 0) + 1
        return True
    for need in needs:
        if need.id in covered or len(selected) >= limit:
            continue
        candidates = sorted((r for r in remaining if any(relevance(r, need.id))),
                            key=lambda r: score(r, need.id))
        for record in candidates:
            if admit(record):
                covered.add(need.id)
                break
    while remaining and len(selected) < limit:
        admit(min(remaining, key=score))
    omissions.extend({'evidence_id': r['evidence_id'], 'reason': 'record_limit'} for r in remaining)
    return selected, omissions


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
