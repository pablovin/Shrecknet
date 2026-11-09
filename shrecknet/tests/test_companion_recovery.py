"""Backend recovery shares budgets and preserves need/scope identity."""
from types import SimpleNamespace

from app.jobs.companion.query_contracts import Need, PlannedCall
from app.jobs.companion.recovery import recovery_needs, web_candidates, select_recovery


def test_recovery_prioritizes_missing_over_partial_and_skips_supported_needs():
    needs=[Need(id=str(i),question='Question') for i in range(3)]
    assessment=SimpleNamespace(needs=[SimpleNamespace(need_id=str(i),status=status)
        for i,status in enumerate(['partial','supported','missing'])])
    assert [n.id for n in recovery_needs(needs,[],assessment)]==['2','0']


def test_web_recovery_deduplicates_shared_queries_and_previously_attempted_needs():
    needs=[Need(id=str(i),question='Question') for i in range(3)]
    outcomes=[{'id':'c1','tool':'web','query':'already searched','need_ids':['0']}]
    calls=web_candidates(needs,outcomes,{'0':'different query','1':' public  rule ','2':'public rule'})
    assert len(calls)==1 and calls[0].need_ids==['1','2']
    assert not web_candidates(needs,[{'id':'c2','tool':'web','query':'PUBLIC RULE','need_ids':['x']}],
                              {'1':'public rule'})


def test_automatic_web_takes_shared_budget_and_deep_escalation_is_not_duplicate():
    automatic=PlannedCall(id='c1',tool='web',query='public rule',need_ids=['n1'])
    deep=PlannedCall(id='deep',tool='shreckworld',query='same rule',need_ids=['n1'],
                     retrieval_depth='deep',depth_reason='Interacting exceptions')
    attempted=[{'id':'c1','tool':'shreckworld','query':'same rule','need_ids':['n1']}]
    selected=select_recovery([automatic],[deep,deep],attempted,{'n1'},['web','shreckworld'],2)
    assert len(selected)==2 and selected[0].tool=='web' and selected[1].retrieval_depth=='deep'
    assert selected[0].id!='c1'
    assert len(select_recovery([automatic],[deep],attempted,{'n1'},['web','shreckworld'],1))==1
    assert not select_recovery([automatic],[deep],attempted,{'other'},['web','shreckworld'],2)
