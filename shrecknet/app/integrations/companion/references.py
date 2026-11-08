"""Normalize admitted source references into typed presentation targets and IDs."""
import hashlib
from pydantic import TypeAdapter, ValidationError
from app.schemas.companion_rich_response import Target

TARGET = TypeAdapter(Target)


def stable_id(prefix, value):
    return prefix + hashlib.sha256(value.encode()).hexdigest()[:20]


def target_for(source):
    ref, scope, kind = source['reference'], source['scope'], source['source_type']
    try:
        if kind == 'atlas':
            if ref.get('node_label') == 'Milestone':
                return None  # No standalone milestone reader; retain narrative citation.
            reference = {'ontology_id':scope['ontology_id'], 'node_id':ref['node_id'], 'node_label':ref['node_label']}
        elif kind == 'shreckworld':
            reference = {key:ref.get(key) for key in ('library_item_id','document_id','source_sha256','page_number','page_label')}
            reference.update({'shreckworld_id':scope['shreckworld_id']} if scope.get('shreckworld_id') else {'ontology_id':scope['ontology_id']})
        elif kind == 'foundry':
            reference = {key:ref.get(key) for key in ('foundry_world_id','uuid','source_revision')}
        elif kind == 'chronicle':
            reference = {'chronicle_id':ref['chronicle_id'],'revision':ref.get('revision',ref.get('chronicle_revision')),'section_id':ref.get('section_id')}
        elif kind == 'web':
            reference = {'url':ref['url']}
        else:
            return None
        return TARGET.validate_python({'id':stable_id('t',source['evidence_id']),'kind':kind,'reference':reference}).model_dump()
    except (KeyError, TypeError, ValidationError):
        return None

