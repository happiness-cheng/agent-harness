import pytest

from min_agent.long_memory.answer_scoring import stage,answer_metrics,validate_review


@pytest.mark.parametrize('change,expected',[
    ({'saved':False},'STORAGE'),({'candidate':False,'visible':False},'RETRIEVAL'),({'topk':False,'visible':False},'RANKING'),
    ({'visible':False},'PACKING'),({'reviewed_failure':True},'REASONING'),
    ({'reviewed_failure':True,'conflict':True},'CONFLICT'),({'evaluation_issue':True},'EVALUATION')])
def test_failure_stage_is_not_inferred_from_a_keyword_miss(change,expected):
    base=dict(history_exists=True,saved=True,candidate=True,topk=True,visible=True)
    assert stage(**(base|change))==expected


def test_without_real_response_all_model_scores_are_not_run():
    case=dict(groups={'A':dict(visible_ids=[])},gold=dict(required_facts=['LUMEN-72']))
    result=answer_metrics(case,'A')
    assert result['fact_surface_recall']=='NOT_RUN' and result['book_scores']['reasoning']=='NOT_RUN'
    assert result['bottleneck']=='NOT_DETERMINED'


def test_citation_presence_is_checked_but_not_called_semantic_accuracy():
    case=dict(groups={'B':dict(visible_ids=['known'])},gold=dict(required_facts=['LUMEN-72']))
    response=dict(status='ANSWERED',parsed=dict(answer='LUMEN-72',used_evidence_ids=['invented'],uncertainties=[]))
    result=answer_metrics(case,'B',response)
    assert result['unsupported_or_invisible_citations']==['invented']
    assert result['fact_surface_recall']==1 and result['dimensions']['fact_accuracy']=='NOT_RUN'


def test_core_memory_can_bypass_fts_so_visible_evidence_is_not_a_retrieval_failure():
    assert stage(history_exists=True,saved=True,candidate=False,topk=False,visible=True,reviewed_failure=True)=='REASONING'
