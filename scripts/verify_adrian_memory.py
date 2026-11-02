"""合成材料上的真实安全/机制计数；不调用模型、不打开业务数据库。"""
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from min_agent.long_memory import LongMemory,Query,SearchHit
from min_agent.long_memory.retrieval import fuse_hits
from min_agent.models import Message
from min_agent.sessions import SessionStore
from min_agent.task_state import TaskController,GoalSpec


def run():
    sessions=SessionStore();context=sessions.create(uuid4());time=[datetime(2026,1,1,tzinfo=timezone.utc)]
    control=TaskController(sessions)
    proposal=control.propose(context,GoalSpec(objective='合成Memory控制',completion_criteria=('确认指定候选',)))
    control.confirm(context,proposal.proposal_id);control.select_mode(context,'build')
    task=sessions.begin(context)
    sessions.append(context,task.task_id,[Message(role='user',content='记住：合成偏好SYNTH-ONLY；求职和Adrian同时进行，提高后者优先级不暂停前者。')])
    sessions.finish(context,task.task_id,status='completed')
    memory=LongMemory(sessions,clock=lambda:time[0]);pending=memory.propose(context,'SYNTH-ONLY',source_indices=[0])
    unauthorized=len(memory.records(context,as_of=time[0]))
    denied=0
    try: memory.confirm(context,pending.id,review_token='yes')
    except PermissionError: denied+=1
    item=memory.confirm(context,pending.id,review_token=memory.review(context,pending.id)['review_token'])
    positive=len(memory.records(context,as_of=time[0]))
    foreign=sessions.create(uuid4())
    leakage=len(memory.search(foreign,Query(text='SYNTH',as_of=time[0]))['hits'])
    first=memory.assemble_context(context,Query(text='SYNTH',as_of=time[0]),budget=2048)
    second=memory.assemble_context(context,Query(text='SYNTH',as_of=time[0]),budget=2048,
                                   visible_versions={(item.id,item.version)})
    reload=memory.assemble_context(context,Query(text='SYNTH',as_of=time[0]),budget=2048)
    memory.delete(context,item.id)
    deleted=len(memory.records(context,as_of=time[0]))
    task=sessions.begin(context)
    sessions.append(context,task.task_id,[Message(role='user',content='新的临时合成事实与两个目标，仅用于控制测试。')])
    sessions.finish(context,task.task_id,status='completed')
    candidate=memory.propose(context,'临时事实',source_indices=[1],valid_until=time[0]+timedelta(hours=1))
    memory.confirm(context,candidate.id,review_token=memory.review(context,candidate.id)['review_token'])
    time[0]+=timedelta(hours=2)
    expired=len(memory.records(context,as_of=time[0]))
    job=memory.set_goal(context,'求职',source_index=1,priority=2)
    agent=memory.set_goal(context,'Adrian',source_index=1,priority=1)
    memory.update_goal(context,agent.id,source_index=1,priority=3)
    paused=int(memory.goals(context,as_of=time[0])[job.id].lifecycle!='active')
    hits=[SearchHit(memory_id='m1',source=s,topic_id=t,score=1) for s,t in
          [('group','t1'),('group','t2'),('group','t3'),('bm25',None),('vector',None)]]
    hits.append(SearchHit(memory_id='m2',source='bm25',score=.9))
    fused=fuse_hits(hits,top_k=2)
    result=dict(unconfirmed_long_term_writes=unauthorized,bad_confirmation_denied=denied,
        confirmed_positive_control=positive,cross_owner_returns=leakage,deleted_returns=deleted,
        expired_returns=expired,unauthorized_goal_pauses=paused,
        fusion_raw_paths=len(hits),fusion_unique_ids=len(fused),first_memory_retained_paths=len(fused[0]['paths']),
        first_injection=len(first['trace']['injected_ids']),duplicate_injection=len(second['trace']['injected_ids']),
        reinjection_after_removal=len(reload['trace']['injected_ids']),
        live_model_calls=0,scope='synthetic deterministic mechanism controls; not model-quality or generalized safety proof')
    assert all(result[k]==0 for k in ('unconfirmed_long_term_writes','cross_owner_returns','deleted_returns','expired_returns','unauthorized_goal_pauses','duplicate_injection'))
    assert positive==1 and denied==1 and len(fused)==2 and len(fused[0]['paths'])==5
    assert result['first_injection']==result['reinjection_after_removal']==1
    return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args();result=run()
    with args.report.open('x',encoding='utf-8') as stream: json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False,indent=2))
