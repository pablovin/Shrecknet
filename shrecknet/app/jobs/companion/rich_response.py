"""Deterministic presentation from resolved citations and authorized source fields.

Markdown is the complete readable answer. Optional cards, source excerpts, tables
and media never require model claims, verification or additional model calls.
Only cited evidence may contribute blocks. Missing artwork is independently pruned.
"""
from __future__ import annotations

import json
import re
from app.integrations.companion.references import stable_id, target_for
from app.schemas.companion_rich_response import RichResponse, Asset


def display(value):
    return json.dumps(value, ensure_ascii=False) if value is None or isinstance(value, bool) else str(value)


def compose(segments, evidence, catalog, *, query=''):
    """Project a few relevant source-owned fields; never copy narrative into cards."""
    sources = {source['evidence_id']: source for source in evidence}
    used = list(dict.fromkeys(eid for segment in segments for eid in segment['evidence_ids'] if eid in sources))
    blocks = [{'id': 'narrative', 'type': 'narrative', 'segment_ids': [s['id'] for s in segments],
               'evidence_ids': used, 'target_id': None}]
    targets, assets = [], []
    text = '\n\n'.join(segment['text'] for segment in segments)
    table_added = False
    # Excerpts are opt-in by user intent, never an automatic copy of all book hits.
    excerpts_requested = bool(re.search(r'\b(quote|quotation|excerpt|passage|verbatim)\b', query, re.I))
    for eid in used:
        if len(blocks) >= 5:
            break
        source = sources[eid]
        target = target_for(source)
        if not target:
            continue
        tid = target['id']
        candidates = catalog.get(eid, [])[:max(0, 4-len(assets))]
        resolved = []
        for candidate in candidates:
            try:
                asset = Asset.model_validate({**candidate, 'target_id': tid, 'evidence_ids': [eid]}).model_dump()
            except ValueError:
                continue  # A broken optional asset cannot remove a valid text/card.
            if asset['id'] not in {a['id'] for a in assets}:
                assets.append(asset)
                resolved.append(asset)
        asset_id = resolved[0]['id'] if resolved else None
        optional = []
        kind = source['source_type']
        if kind == 'atlas':
            name = source['title'][:255]
            # A short source description is safe to project; never manufacture one
            # from model prose or append the entire source excerpt as a card.
            description = (source.get('structured_evidence') or {}).get('description')
            if not isinstance(description, str) or not 0 < len(description) <= 300 or description in text:
                description = None
            optional.append({'type': 'entity_card', 'entity_ref': target['reference'], 'name': name,
                             'description': description, 'asset_id': asset_id, 'fallback_text': name})
        elif kind == 'shreckworld' and excerpts_requested and source.get('excerpt', '').strip():
            quote = source['excerpt'][:600]
            label = target['reference'].get('page_label') or str(target['reference']['page_number'])
            optional.append({'type': 'book_excerpt', 'quote': quote, 'title': source['title'][:255],
                             'document_ref': target['reference'], 'asset_id': asset_id,
                             'fallback_text': f"{source['title'][:255]}, p. {label}:\n{quote}"})
        elif resolved:
            optional.append({'type': 'image', 'asset_id': asset_id, 'alt': source['title'][:255],
                             'caption': None, 'fallback_text': source['title'][:255]})
        if len(resolved) > 1:
            optional.append({'type': 'gallery', 'items': [
                {'asset_id': asset['id'], 'alt': source['title'][:255], 'caption': None,
                 'evidence_ids': [eid], 'target_id': tid} for asset in resolved[1:]],
                'fallback_text': source['title'][:255]})
        fields = (source.get('structured_evidence') or {}).get('fields', [])
        if fields and not table_added and kind in {'foundry', 'atlas'}:
            bounded = []
            for field in fields:
                if (not isinstance(field, dict) or not isinstance(field.get('label'), str) or
                    not field['label'].strip() or len(field['label']) > 80 or
                    field['label'].casefold() not in text.casefold() or
                    (field.get('value') is not None and type(field['value']) not in {str, int, float, bool}) or
                    len(str(field.get('value'))) > 500):
                    continue
                candidate = bounded + [field]
                if len(candidate) > 20 or len('\n'.join(f"{f['label']}: {display(f.get('value'))}" for f in candidate)) > 4000:
                    break
                bounded = candidate
            if bounded:
                optional.append({'type': 'table', 'title': source['title'][:255],
                    'columns': [{'id': 'property', 'label': 'Property'}, {'id': 'value', 'label': 'Value'}],
                    'rows': [{'id': f'r{i}', 'cells': [{'value': f['label'], 'evidence_ids': [eid]},
                             {'value': f.get('value'), 'evidence_ids': [eid]}],
                             'evidence_ids': [eid], 'target_id': tid} for i, f in enumerate(bounded)],
                    'fallback_text': '\n'.join(f"{f['label']}: {display(f.get('value'))}" for f in bounded)})
                table_added = True
        # Keep navigation even for cited books/Chronicles without optional blocks.
        targets.append(target)
        for index, block in enumerate(optional):
            if len(blocks) >= 5:
                break
            blocks.append({**block, 'id': stable_id('b', eid+str(index)+block['type']),
                           'evidence_ids': [eid], 'target_id': tid})
    used_assets = {block['asset_id'] for block in blocks if block.get('asset_id')}
    used_assets.update(item['asset_id'] for block in blocks if block['type'] == 'gallery' for item in block['items'])
    return RichResponse.model_validate({'version': 1, 'blocks': blocks,
        'assets': [asset for asset in assets if asset['id'] in used_assets], 'targets': targets[:20]}).model_dump()


def prune_assets(rich, admitted):
    """Missing optional artwork never replaces a valid text/card/excerpt answer."""
    blocks = []
    for original in rich['blocks']:
        block = dict(original)
        if block['type'] == 'image' and block['asset_id'] not in admitted:
            continue
        if block['type'] == 'gallery':
            block['items'] = [item for item in block['items'] if item['asset_id'] in admitted]
            if not block['items']:
                continue
        elif block.get('asset_id') and block['asset_id'] not in admitted:
            block['asset_id'] = None
        blocks.append(block)
    return RichResponse.model_validate({**rich, 'blocks': blocks,
        'assets': [asset for asset in rich['assets'] if asset['id'] in admitted]}).model_dump()
