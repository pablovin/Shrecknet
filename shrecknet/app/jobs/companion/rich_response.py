"""Deterministic private presentation: compose, verify with narrative, then prune.

The query job supplies admitted evidence and bounded, source-resolved assets.
Composition projects exact source fields/quotes into optional blocks, returning
private Segment checks alongside the envelope. The existing evidence verifier
reviews these checks with the narrative; only accepted blocks survive finalize.
No prompt, additional retrieval, or model-generated identifiers are used here.
"""
from __future__ import annotations

import json
from app.jobs.companion.query_contracts import Segment, Support
from app.integrations.companion.references import stable_id, target_for
from app.schemas.companion_rich_response import RichResponse, Asset


def display(value):
    return json.dumps(value, ensure_ascii=False) if value is None or isinstance(value,bool) else str(value)


def compose(draft, evidence, catalog):
    """Select only cited sources; every optional factual block has a verifier segment."""
    sources = {e['evidence_id']:e for e in evidence}
    used = list(dict.fromkeys(s.evidence_id for segment in draft.segments for s in segment.supports if s.evidence_id in sources))
    blocks, targets, assets, checks = [], [], [], []
    dependencies = {}
    table_added = False
    for eid in used:
        source = sources[eid]
        target = target_for(source)
        if not target:
            continue
        targets.append(target)
        tid = target['id']
        resolved = catalog.get(eid, [])[:max(0,4-len(assets))]
        for candidate in resolved:
            asset = Asset.model_validate({**candidate, 'target_id':tid, 'evidence_ids':[eid]})
            assets.append(asset.model_dump())
        asset_id = resolved[0]['id'] if resolved else None
        optional = []
        kind = source['source_type']
        supporting_quotes = [s.quote for segment in draft.segments for s in segment.supports if s.evidence_id == eid and s.quote in source['excerpt']]
        quote = max(supporting_quotes, key=len, default='')[:2000]
        if kind == 'atlas':
            name = source['title'][:255]
            description = next((segment.text for segment in draft.segments if segment.kind == 'factual' and len(segment.text) <= 1000 and any(s.evidence_id == eid for s in segment.supports)), None)
            optional.append({'type':'entity_card','entity_ref':target['reference'],'name':name,'description':description,
                             'asset_id':asset_id,'fallback_text':name + ('\n' + description if description else '')})
        elif kind == 'shreckworld' and quote:
            title=source['title'][:255]
            label=target['reference'].get('page_label') or str(target['reference']['page_number'])
            optional.append({'type':'book_excerpt','quote':quote,'title':title,'document_ref':target['reference'],
                             'asset_id':asset_id,'fallback_text':f'{title}, p. {label}:\n{quote}'})
        elif resolved:
            optional.append({'type':'image','asset_id':asset_id,'alt':'Source image','caption':None,
                             'fallback_text':source['title'][:255]})
        if len(resolved)>1:
            # The first visual is already in the card/excerpt; gallery adds the rest.
            optional.append({'type':'gallery','items':[{'asset_id':a['id'],'alt':'Source image','caption':None,
                             'evidence_ids':[eid],'target_id':tid} for a in resolved[1:]],'fallback_text':source['title'][:255]})
        fields=(source.get('structured_evidence') or {}).get('fields', [])
        supported_text=' '.join(segment.text for segment in draft.segments if any(s.evidence_id == eid for s in segment.supports)).casefold()
        if fields and not table_added and kind in {'foundry','atlas'}:
            fields=[f for f in fields if isinstance(f,dict) and isinstance(f.get('label'),str) and
                    f['label'].strip() and len(f['label'])<=80 and f['label'].casefold() in supported_text and (f.get('value') is None or type(f.get('value')) in {str,int,float,bool}) and len(str(f.get('value')))<=500][:20]
            bounded_fields=[]
            for field in fields:
                candidate=bounded_fields+[field]
                if len('\n'.join(f"{f['label']}: {display(f.get('value'))}" for f in candidate))>4000:
                    break
                bounded_fields=candidate
            fields=bounded_fields
            if fields:
                rows=[{'id':f'r{i}','cells':[{'value':f['label'],'evidence_ids':[eid]},{'value':f.get('value'),'evidence_ids':[eid]}],
                       'evidence_ids':[eid],'target_id':tid} for i,f in enumerate(fields)]
                optional.append({'type':'table','title':source['title'][:255],
                    'columns':[{'id':'property','label':'Property'},{'id':'value','label':'Value'}], 'rows':rows,
                    'fallback_text':'\n'.join(f"{f['label']}: {display(f.get('value'))}" for f in fields)})
                table_added=True
        for index, block in enumerate(optional):
            if len(blocks)>=7:
                break
            identifier=stable_id('b',eid+str(index)+block['type'])
            if identifier in {segment.id for segment in draft.segments}:
                continue
            block.update(id=identifier,evidence_ids=[eid],target_id=tid)
            # Support is exact retrieved prose or the complete projected structured fields.
            structured_quote = (json.dumps(source['structured_evidence'], ensure_ascii=False)[:2000]
                                if source.get('structured_evidence') is not None else '')
            support_quote = structured_quote if block['type']=='table' else quote or structured_quote
            if not support_quote:
                continue
            text=block['fallback_text']
            checks.append(Segment(id=identifier,kind='factual',text=text,
                claims=[text],supports=[Support(evidence_id=eid,quote=support_quote)]))
            blocks.append(block)
            dependencies[identifier] = [segment.id for segment in draft.segments if any(s.evidence_id == eid for s in segment.supports)]
    return {'version':1,'blocks':blocks,'assets':assets,'targets':targets,'dependencies':dependencies}, checks


def finalize(proposal, segments, accepted_ids):
    """Prune unsupported/dependent assets and enforce the public envelope once."""
    narrative_ids = {segment['id'] for segment in segments}
    blocks=[b for b in proposal['blocks'] if b['id'] in accepted_ids and set(proposal.get('dependencies', {}).get(b['id'], [])) <= narrative_ids]
    narrative={'id':'narrative','type':'narrative','segment_ids':[s['id'] for s in segments],
               'evidence_ids':list(dict.fromkeys(eid for s in segments for eid in s['evidence_ids'])),'target_id':None}
    blocks.insert(0,narrative)
    used_assets=set()
    used_targets={b['target_id'] for b in blocks if b.get('target_id')}
    for block in blocks:
        if block.get('asset_id'): used_assets.add(block['asset_id'])
        if block['type']=='gallery': used_assets.update(i['asset_id'] for i in block['items'])
    assets=[a for a in proposal['assets'] if a['id'] in used_assets]
    used_targets.update(a['target_id'] for a in assets)
    return RichResponse.model_validate({'version':1,'blocks':blocks,'assets':assets,
        'targets':[t for t in proposal['targets'] if t['id'] in used_targets]}).model_dump()


def fallback(rich, segments):
    parts=[s['text'] for s in segments]
    for block in rich['blocks']:
        text=block.get('fallback_text')
        if text:
            novel_lines = [line for line in text.splitlines() if line and line not in '\n\n'.join(parts)]
            if novel_lines:
                parts.append('\n'.join(novel_lines))
    return '\n\n'.join(parts)


def prune_assets(rich, admitted):
    """Missing optional artwork never replaces a verified text/card/excerpt answer."""
    blocks=[]
    for original in rich['blocks']:
        block=dict(original)
        if block['type']=='image' and block['asset_id'] not in admitted:
            continue
        if block['type']=='gallery':
            block['items']=[i for i in block['items'] if i['asset_id'] in admitted]
            if not block['items']: continue
        elif block.get('asset_id') and block['asset_id'] not in admitted:
            block['asset_id']=None
        blocks.append(block)
    return RichResponse.model_validate({**rich,'blocks':blocks,'assets':[a for a in rich['assets'] if a['id'] in admitted]}).model_dump()
