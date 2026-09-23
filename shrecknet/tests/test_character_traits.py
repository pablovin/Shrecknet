"""Regression coverage for trait contracts, confounds, accumulation and chronology."""
import json
import math
import pytest
from pydantic import ValidationError
from app.schemas.character_traits import (ANCHORS, DIRECTIONAL_TRAITS, TRAIT_DEFINITIONS, TRAIT_BY_KEY,
    TraitEdit, TraitEstimate, TraitObservation, TraitProfile, TraitProposal)
from app.services.character_trait_service import (apply_manual_edits, chunk_source_scenes,
    ground_observations, merge_evidence, update_profile)


def observation(scene='s1', trait='integrity', expression_z=1.2, **changes):
    data = dict(trait=trait, situation_type=TRAIT_BY_KEY[trait].diagnostic_situations[0],
        direction='low' if expression_z < 0 else 'high' if expression_z > 0 else 'midpoint', expression_z=expression_z,
        confidence=.9, diagnosticity=.9, behavior='Had a meaningful choice and acted.',
        justification='Choice reveals this construct.', evidence_ids=[f'scene:{scene}'],
        episode_id=f'scene:{scene}', available_after_scene_id=scene,
        conditions={name: {'status':'supported', 'justification':'Established by narrative.'}
                    for name in ('knowledge','capability','options','freedom')},
        comparison_context='same stakes, relationship, role and available options')
    data.update(changes)
    return TraitObservation(**data)


def evidence(values=(1.2,1.2,1.2), trait='integrity', source='source', offset=0):
    scenes=[f's{offset+i}' for i in range(len(values))]
    return ground_observations([observation(s,trait,p) for s,p in zip(scenes,values)],
                               scene_ids=scenes,source_group_id=source,offset=offset)


def proposal(items):
    return TraitProposal(trait=items[0].trait,observation_ids=[i.id for i in items if i.eligible],
        justification='Cumulative choices.',addresses_contradictions='Reviewed opposing evidence.')


def test_registry_constructs_poles_and_spread_kind():
    assert len(TRAIT_DEFINITIONS)==9 and len(DIRECTIONAL_TRAITS)==8
    assert 'steadiness' not in DIRECTIONAL_TRAITS
    for d in TRAIT_DEFINITIONS:
        assert all((d.construct,d.low_pole,d.high_pole,d.definition,d.boundary_notes))
    assert TRAIT_BY_KEY['steadiness'].diagnostic_situations==()



def test_unknown_is_not_midpoint_and_storage_uses_z():
    state=TraitProfile()
    assert state.dispositional_traits['integrity'].z is None
    assert TraitEstimate(z=0,status='supported').z == 0
    assert 'point' not in json.loads(state.storage_json())['steadiness']
    assert TraitProfile.model_validate_json(state.storage_json())==state
    with pytest.raises(ValidationError): TraitEstimate(z=0)
    with pytest.raises(ValidationError): TraitEstimate(z=True,status='supported')


@pytest.mark.parametrize('trait,situation', [('integrity','exploitation'),('sharing','resource_allocation'),
    ('forbearance','retaliation'),('presence','social_visibility'),('diligence','unattended_duty'),
    ('curiosity','exploration'),('caution','uncertain_dependence'),('restlessness','value_conflict')])
def test_diagnostic_mappings(trait,situation):
    assert observation(trait=trait,situation_type=situation).trait==trait


@pytest.mark.parametrize('trait,situation',[('integrity','resource_allocation'),('sharing','exploitation'),
    ('integrity','retaliation'),('forbearance','exploitation'),('presence','dominance'),
    ('diligence','competence'),('curiosity','intelligence'),('restlessness','exploration'),
    ('caution','exploration'),('steadiness','retaliation')])
def test_discriminant_cases(trait,situation):
    data=observation().model_dump();data.update(trait=trait,situation_type=situation)
    with pytest.raises(ValidationError): TraitObservation(**data)


@pytest.mark.parametrize('condition',['knowledge','capability','options','freedom'])
@pytest.mark.parametrize('status',['unknown','contradicted'])
def test_confounds_prevent_updates(condition,status):
    item=observation();getattr(item.conditions,condition).status=status
    records=ground_observations([item],scene_ids=['s1'],source_group_id='source')
    assert not records[0].eligible
    state,_=update_profile(TraitProfile(),records,[])
    assert state.dispositional_traits['integrity'].z is None


def test_required_provenance_and_availability():
    with pytest.raises(ValidationError): observation(evidence_ids=[])
    with pytest.raises(ValueError):
        ground_observations([observation(evidence_ids=['scene:foreign'])],scene_ids=['s1'],source_group_id='source')
    with pytest.raises(ValueError):
        ground_observations([observation(evidence_ids=['scene:s1','scene:s2'])],scene_ids=['s1','s2'],source_group_id='source')


def test_conservative_changes_and_deduplication():
    items=evidence()
    state,_=update_profile(TraitProfile(),items[:1],[proposal(items[:1])],source_group_id='source')
    assert state.dispositional_traits['integrity'].z==.1
    state,_=update_profile(state,items,[proposal(items)],source_group_id='source')
    assert state.dispositional_traits['integrity'].z==.1
    replay,_=update_profile(state,items,[proposal(items)],source_group_id='source')
    assert replay.dispositional_traits['integrity'].z==.1
    all_items=merge_evidence(items,evidence((1.2,1.2),source='next',offset=3))
    changed,_=update_profile(state,all_items,[proposal(all_items)],source_group_id='next')
    assert changed.dispositional_traits['integrity'].z==.2
    assert len(merge_evidence(items,items))==3
    with pytest.raises(ValueError):
        ground_observations([observation(),observation()],scene_ids=['s1'],source_group_id='source')


def test_opposing_extremes_are_contested_not_midpoint():
    items=evidence((-1.9,1.9,-1.9,1.9))
    state,_=update_profile(TraitProfile(),items,[proposal(items)])
    assert state.dispositional_traits['integrity'].status=='contested'
    assert state.dispositional_traits['integrity'].z is None


def test_restlessness_can_update_from_one_eligible_value_choice():
    items=evidence((1.2,),trait='restlessness')
    state,_=update_profile(TraitProfile(),items,[proposal(items)],source_group_id='source')
    assert state.dispositional_traits['restlessness'].z==.1


def test_authored_evidence_is_provisional():
    item=observation(evidence_kind='authored_disposition',evidence_ids=['identity:e'],
        episode_id='identity:e',available_after_scene_id=None)
    records=ground_observations([item],scene_ids=[],source_group_id='e',authored_evidence_ids={'identity:e'})
    state,_=update_profile(TraitProfile(),records,[proposal(records)])
    assert state.dispositional_traits['integrity'].status=='provisional'
    assert state.dispositional_traits['integrity'].qualifying_count==0
    assert state.steadiness.z is None


def test_steadiness_requires_comparable_repetition_not_morality():
    retaliation=evidence((-1.9,-1.9,-1.9),trait='forbearance')
    state,_=update_profile(TraitProfile(),retaliation,[proposal(retaliation)])
    assert state.steadiness.z is None
    allocation=evidence((1.9,1.9,1.9),trait='sharing',offset=3)
    state,_=update_profile(state,retaliation+allocation,[proposal(allocation)])
    assert state.dispositional_traits['forbearance'].z==-.1 and state.steadiness.z==1.9
    varied=evidence((-1.9,1.9,-1.9),trait='forbearance')+evidence((1.9,-1.9,1.9),trait='sharing',offset=3)
    state,_=update_profile(TraitProfile(),varied,[])
    assert state.steadiness.z<=-1.2
    for item in varied: item.comparison_context=None
    state,_=update_profile(TraitProfile(),varied,[])
    assert state.steadiness.z is None


def test_manual_override_and_clear():
    items=evidence()
    state=apply_manual_edits(TraitProfile(),{'integrity':TraitEdit(z=-1.2,reason='Authored sheet.')})
    state,_=update_profile(state,items,[proposal(items)])
    assert state.dispositional_traits['integrity'].z==-1.2
    assert state.inferred_traits['integrity'].z==.1
    state=apply_manual_edits(state,{'integrity':TraitEdit(z=None,reason='Resume inference.')})
    assert state.dispositional_traits['integrity'].z==.1 and not state.overrides


@pytest.mark.parametrize('count', [0, 1, 9, 10, 11, 21])
def test_source_bundle_contains_every_scene(count):
    scenes=[{'scene_id':f's{i:02}','created_at':f'{i:02}','description':'x','name':'Scene'} for i in range(count)]
    groups=[{'source_id':'a','source_alias':'A','scenes':scenes}]
    chunks=chunk_source_scenes(groups)
    assert [len(c['scenes']) for c in chunks] == ([count] if count else [])
    assert chunks==chunk_source_scenes(groups)


def test_source_bundles_group_all_source_scenes_without_dropping_evidence():
    a={'source_id':'a','source_alias':'A','scenes':[{'scene_id':'1','created_at':'1'},{'scene_id':'3','created_at':'3'}]}
    b={'source_id':'b','source_alias':'B','scenes':[{'scene_id':'2','created_at':'2'}]}
    chunks=chunk_source_scenes([a,b])
    assert [c['source_id'] for c in chunks] == ['a', 'b']
    assert [[s['scene_id'] for s in c['scenes']] for c in chunks] == [['1', '3'], ['2']]
    assert [s['scene_id'] for c in chunks for s in c['scenes']] == ['1', '3', '2']


def test_definition_documentation_is_generated_from_registry():
    from pathlib import Path
    page=Path(__file__).resolve().parents[2]/'Documentation/Agents/CharacterAgent/Dispositional Traits.md'
    table=page.read_text().split('<!-- BEGIN GENERATED TRAIT DEFINITIONS -->')[1].split('<!-- END GENERATED TRAIT DEFINITIONS -->')[0]
    for definition in TRAIT_DEFINITIONS:
        for value in (definition.display_name,definition.construct,definition.definition,
                      definition.low_pole,definition.high_pole,definition.boundary_notes):
            assert value in table


def test_clearing_without_override_keeps_inferred_state():
    items=evidence()
    state,_=update_profile(TraitProfile(),items,[proposal(items)])
    clear=apply_manual_edits(state,{'integrity':TraitEdit(z=None,reason='Already inferred.')})
    assert clear.dispositional_traits['integrity'].z==.1


def test_contradictions_change_uncertainty_without_llm_numeric_proposal():
    items=evidence((-1.9,1.9,-1.9,1.9))
    state,_=update_profile(TraitProfile(),items,[])
    assert state.dispositional_traits['integrity'].status=='contested'


def test_evidence_direction_controls_numeric_update():
    items=evidence()
    state,_=update_profile(TraitProfile(),items,[proposal(items)])
    assert state.dispositional_traits['integrity'].z==.1


def test_source_update_averages_intensities_once_per_trait():
    records=ground_observations([
        observation('s1', update_intensity='small'),
        observation('s2', update_intensity='medium'),
        observation('s3', update_intensity='large'),
    ],scene_ids=['s1','s2','s3'],source_group_id='source')
    state,_=update_profile(TraitProfile(),records,[],source_group_id='source')
    assert state.dispositional_traits['integrity'].z==pytest.approx((.05+.1+.2)/3, abs=.0001)
    assert state.dispositional_traits['integrity'].applied_source_ids==['source']
    replay,_=update_profile(state,records,[],source_group_id='source')
    assert replay.dispositional_traits['integrity'].z==state.dispositional_traits['integrity'].z


def test_source_update_marks_opposing_directions_contested_without_stacking():
    records=ground_observations([
        observation('s1', expression_z=1.2, update_intensity='large'),
        observation('s2', expression_z=-1.2, update_intensity='small'),
        observation('s3', expression_z=1.2, update_intensity='medium'),
    ],scene_ids=['s1','s2','s3'],source_group_id='source')
    state,_=update_profile(TraitProfile(),records,[],source_group_id='source')
    # Opposing poles remain a contradiction, not an averaged pseudo-centre.
    assert state.dispositional_traits['integrity'].z is None
    assert state.dispositional_traits['integrity'].status=='contested'
