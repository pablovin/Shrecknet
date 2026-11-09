"""Compact model evidence and need-balanced context selection.

Full evidence remains server-side for navigation, access/revision checks and the
Answer Breakdown. Model projections omit navigation URL fields, geometry, document blobs
and embeddings; stable IDs and exact passages remain unchanged.
"""
from __future__ import annotations

import json
import re

from app.jobs.elder.context_budget import estimate_tokens


def compact_structured(source):
    structured = source.get('structured_evidence') or {}
    result = {}
    if isinstance(structured.get('description'), str) and len(structured['description']) <= 300:
        result['description'] = structured['description']
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
        'relevance': {key: value for key, value in (source.get('relevance') or {}).items()
                      if key in {'rank', 'score', 'method'}},
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
