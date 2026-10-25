import json
from datetime import timedelta
import pytest

from min_agent.long_memory.active_evidence import active_registry,run_active,validate_answer
from min_agent.models import ModelResponse,ToolCall
from test_task_evidence_v06 import fixture


class ScriptedSearchRead:
    """只验证接线的脚本控制，不充当真实模型成绩。"""
    def __init__(self):self.requests=[]
    def generate(self,messages,*,tools=None):
        self.requests.append([m.model_dump(mode='json') for m in messages])
        results=[m for m in messages if m.role=='tool']
        if not results:
            question=next(m.content for m in messages if m.role=='user')
            call=ToolCall(id='search1',name='search_long_memory',arguments={'question':question})
        elif len(results)==1:
            ids=json.loads(json.loads(results[-1].content)['result'])['memory_ids']
            call=ToolCall(id='read1',name='read_long_memory_evidence',arguments={'memory_ids':ids[:2]})
        else:
            view=json.loads(messages[-1].content)['untrusted_user_memory']
            return ModelResponse(finish_reason='stop',content=json.dumps(dict(answer='SCRIPTED_CONTROL',
                used_evidence_ids=[e['source'] for e in view['evidence']],uncertainties=[])))
        return ModelResponse(finish_reason='tool_calls',tool_calls=[call])


def test_existing_runtime_executes_search_read_answer_with_three_calls():
    memory,ctx,q=fixture();model=ScriptedSearchRead()
    row=run_active(memory,ctx,q,model=model,max_model_calls=3,max_tool_calls=4)
    assert row['runtime_status']=='completed'
    assert row['model_calls']==3 and row['tool_calls']==2
    assert row['validation']['schema_valid'] and row['validation']['citation_valid']
    assert '792015' not in json.dumps(model.requests[0])
    assert '792015' in json.dumps(model.requests[-1])
    assert all('2026-' not in r[-1]['content'] for r in model.requests)


def test_model_round_limit_stops_before_answer_without_extra_request():
    memory,ctx,q=fixture();model=ScriptedSearchRead()
    row=run_active(memory,ctx,q,model=model,max_model_calls=2,max_tool_calls=4)
    assert row['runtime_status']=='partial' and row['model_calls']==2
    assert row['validation']['status']=='NOT_RUN'


def test_narrow_reader_cannot_guess_or_reuse_expired_ids():
    memory,ctx,q=fixture();registry,state=active_registry(memory,ctx,q)
    read=registry.get('read_long_memory_evidence').handler
    with pytest.raises(PermissionError):read(memory_ids=['guess'])
    result=json.loads(registry.get('search_long_memory').handler(question=q.text))
    item=result['memory_ids'][0]
    # 检索后权威状态变化，读之前必须重新验证；仅在测试Fixture修改有效期。
    def expire(doc,at):
        old=doc.memories[item][-1];doc.memories[item][-1]=old.model_copy(update={'valid_until':q.as_of-timedelta(seconds=1)})
    memory._write(ctx,expire)
    with pytest.raises(PermissionError):read(memory_ids=[item])
    assert not state['loaded_ids']


@pytest.mark.parametrize('raw,json_ok,schema_ok,citation_ok',[
    ('bad JSON',False,False,False),
    ('{"answer":"ok","used_evidence_ids":[1],"uncertainties":[]}',True,False,False),
    ('{"answer":"ok","used_evidence_ids":["fake"],"uncertainties":[]}',True,True,False),
    ('{"answer":"ok","used_evidence_ids":["known"],"uncertainties":[]}',True,True,True),
])
def test_structure_and_current_visible_id_checks_are_independent(raw,json_ok,schema_ok,citation_ok):
    v=validate_answer(raw,{'known'})
    assert (v['json_valid'],v['schema_valid'],v['citation_valid'])==(json_ok,schema_ok,citation_ok)


def test_pending_foreign_and_revoked_records_cannot_be_read_or_persist_derivatives():
    from uuid import uuid4
    from test_long_memory_store import setup_memory,candidate,commit
    from min_agent.long_memory.models import Query
    sessions,ctx,clock,memory=setup_memory()
    q=Query(text='Python',as_of=clock.now)
    pending=candidate(memory,ctx)
    registry,_=active_registry(memory,ctx,q)
    assert json.loads(registry.get('search_long_memory').handler(question='Python'))['memory_ids']==[]
    item=commit(memory,ctx,pending)
    other=sessions.create(uuid4());foreign,_=active_registry(memory,other,q)
    assert json.loads(foreign.get('search_long_memory').handler(question='Python'))['memory_ids']==[]
    with pytest.raises(PermissionError):foreign.get('read_long_memory_evidence').handler(memory_ids=[item.id])
    row=run_active(memory,ctx,q,model=ScriptedSearchRead())
    assert row['runtime_status']=='completed'
    assert 'SCRIPTED_CONTROL' in sessions.snapshot(ctx).model_dump_json()
    memory.delete(ctx,item.id)
    assert 'SCRIPTED_CONTROL' not in sessions.snapshot(ctx).model_dump_json()
    assert json.loads(registry.get('search_long_memory').handler(question='Python'))['memory_ids']==[]


def test_future_content_is_not_disclosed_by_search_or_index():
    memory,ctx,q=fixture()
    early=q.model_copy(update={'as_of':q.as_of-timedelta(days=2)})
    registry,state=active_registry(memory,ctx,early)
    assert json.loads(registry.get('search_long_memory').handler(question='account'))['memory_ids']==[]
    assert registry.memory_view([]) is None
