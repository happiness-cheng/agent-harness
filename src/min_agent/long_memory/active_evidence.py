"""仅实验注册只读取证工具；复用现有Runtime工具循环，不改变生产绑定。"""
import json
from time import perf_counter
from pydantic import BaseModel,ConfigDict,Field,ValidationError

from min_agent.budget import BudgetLedger,BudgetLimits
from min_agent.context import ContextLimit
from min_agent.models import Message
from min_agent.runtime import Runtime
from min_agent.tools.registry import ToolRegistry,ToolDefinition
from min_agent.long_memory.answer_eval import make_messages,encoded
from min_agent.long_memory.integration import bind_runtime_memory
from min_agent.long_memory.models import Query
from min_agent.long_memory.retrieval import eligible_for_query
from min_agent.long_memory.retrieval_quality import body
from min_agent.long_memory.task_evidence import decorate_entry,evidence_view


class EvidenceAnswer(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    answer:str=Field(min_length=1)
    used_evidence_ids:list[str]
    uncertainties:list[str]


class ReadEvidence(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    memory_ids:list[str]=Field(min_length=1,max_length=8)


def validate_answer(raw,visible_ids):
    base=dict(status='NOT_RUN',json_valid=False,schema_valid=False,citation_valid=False,
              unsupported_ids=[],duplicate_ids=[],semantic_support='NOT_RUN_SOURCE_REVIEW_REQUIRED')
    if raw is None:return base
    try:data=json.loads(raw)
    except (ValueError,TypeError):return dict(base,status='FORMAT_ERROR')
    base['json_valid']=True
    try:answer=EvidenceAnswer.model_validate(data)
    except ValidationError:return dict(base,status='SCHEMA_ERROR')
    ids=answer.used_evidence_ids
    unknown=sorted(set(ids)-set(visible_ids));duplicates=sorted({i for i in ids if ids.count(i)>1})
    return dict(base,status='STRUCTURE_VALID' if not unknown and not duplicates else 'CITATION_ERROR',
                schema_valid=True,citation_valid=not unknown and not duplicates,
                unsupported_ids=unknown,duplicate_ids=duplicates,parsed=answer.model_dump())


def group_messages(question,as_of,view):
    base=make_messages(question,as_of,view)
    system=base['messages'][0]['content'].replace(
        'This is one read-only memory question-answering turn. No tools are available.',
        'This is a read-only memory task. Only registered read-only memory tools may be used if provided.')
    system+='\nThe visible material is a partial view, not proof that the entire history lacks a fact. Do not fill gaps using unstated rules, prices, fees, or status. Return only the exact three JSON fields requested. Citation IDs must occur in visible evidence, not merely in search results. If tools are provided, you have at most 3 model turns and 4 tool calls; search for missing evidence, read discovered evidence IDs, then answer. Do not execute business actions.'
    base['messages'][0]['content']=system
    return [Message.model_validate(m) for m in base['messages']]


def visible_ids(view):
    ids=set()
    for e in view.get('evidence',[]):
        ids.add(e.get('source',e.get('id','')));ids.update(e.get('evidence',[]))
    for lane in ('stable_core','active_core','retrieved'):
        for e in view.get(lane,[]):ids.add(e['id']);ids.update(e.get('evidence_refs',[]))
    return ids-{''}


def track_dependencies(memory,context,ids):
    snapshot=memory.sessions.snapshot(context)
    if snapshot.task is None:return
    task_id=str(snapshot.task.task_id);previous=snapshot.extensions.get('long_memory_run_dependencies',{})
    retained=set(previous.get('memory_ids',[])) if previous.get('task_id')==task_id else set()
    retained.update(ids)
    retained.update(i for d in snapshot.extensions.get('long_memory_dependencies',[]) for i in d['memory_ids'])
    memory.sessions.set_extension(context,'long_memory_run_dependencies',dict(task_id=task_id,memory_ids=sorted(retained)))


def active_registry(memory,context,query,*,budget=4096):
    # 仅历史评测Fixture使用固定时点；生产入口不会调用本函数。
    memory.clock=lambda:query.as_of
    original=ToolRegistry(sessions=memory.sessions,context=context)
    bind_runtime_memory(original,memory,context,budget=budget)
    registry=ToolRegistry(sessions=memory.sessions,context=context)
    state=dict(discovered_ids=[],loaded_ids=[],scenario_ids=list(query.scenario_ids),view={},tool_events=[])
    for name in ('search_long_memory','read_long_memory_topic'):
        tool=original.get(name)
        def handler(_tool=tool,**kwargs):
            result=_tool.handler(**kwargs);parsed=json.loads(result)
            state['discovered_ids']=list(dict.fromkeys(state['discovered_ids']+parsed['memory_ids']))
            state['scenario_ids']=kwargs.get('scenario_ids',[])
            state['tool_events'].append(dict(name=_tool.name,arguments=kwargs,returned_ids=parsed['memory_ids']))
            return result
        # handler的默认_tool由闭包绑定，不在模型参数Schema中。
        registry.register(ToolDefinition(name,tool.description,tool.parameters,handler))
    def legal():
        memory.reconcile_deletions(context)
        q=query.model_copy(update={'scenario_ids':state['scenario_ids']})
        return {m.id:m for m in eligible_for_query(memory,context,q)[0]}
    def read(memory_ids):
        pool=legal();ids=list(dict.fromkeys(memory_ids))
        if any(i not in state['discovered_ids'] or i not in pool for i in ids):
            raise PermissionError('ID未披露或当前已不可读；不返回正文')
        state['loaded_ids']=list(dict.fromkeys(state['loaded_ids']+ids))
        state['tool_events'].append(dict(name='read_long_memory_evidence',arguments={'memory_ids':ids},returned_ids=ids))
        return json.dumps(dict(memory_ids=ids,delivery='next_dynamic_context_within_budget'))
    registry.register(ToolDefinition('read_long_memory_evidence',
        '读取search_long_memory或read_long_memory_topic已返回的Memory ID。只读；再次校验owner、合法时点和撤回状态。正文在下一轮预算内动态上下文提供，搜索结果ID本身不代表已读证据。',ReadEvidence,read))
    def build(messages):
        pool=legal();entries=[];included=[]
        for identity in state['loaded_ids']:
            item=pool.get(identity)
            if item is None:continue
            text=body(item)
            entry=decorate_entry(memory,context,query,item,dict(source=item.id,time=item.observed_at.isoformat(),
                scene=item.session_id,evidence_type=item.evidence_type,evidence=item.evidence_refs,
                valid_from=item.effective_from.isoformat(),valid_until=item.valid_until.isoformat() if item.valid_until else None,
                quotes=[dict(start=0,end=len(text),text=text)]))
            proposal=evidence_view(entries+[entry])
            if len(encoded(proposal))<=budget:entries.append(entry);included.append(identity)
        view=evidence_view(entries)
        if not view:
            doc=memory.document(context);topic_ids={t for m in pool.values() for t in m.topic_ids}
            topics=[dict(id=t.id,name=t.name) for t in doc.topics.values() if t.id in topic_ids and t.created_at<=query.as_of]
            view=dict(reference_only=True,coverage='index_only_not_full_history',root_index=[])
            for topic in topics:
                proposal=dict(view,root_index=view['root_index']+[topic])
                if len(encoded(proposal))<=budget:view=proposal
            if not view['root_index']:view={}
            else:
                shown={t['id'] for t in view['root_index']}
                included.extend(m.id for m in pool.values() if shown & set(m.topic_ids))
        state['view']=view;track_dependencies(memory,context,included)
        return view or None
    registry._long_memory_builder=build
    return registry,state


class ProjectedRuntime(Runtime):
    """只替换实验上下文投影；工具分发、错误处理、Session和预算均复用Runtime。"""
    def __init__(self,*args,query,view_builder,**kwargs):
        super().__init__(*args,**kwargs);self.query=query;self.view_builder=view_builder
    def _model_messages(self,messages):
        view=self.view_builder(messages) or {}
        base=group_messages(self.query.text,self.query.as_of,view)
        return [base[0]]+[m.model_copy(deep=True) for m in messages]+[base[-1]]


def run_active(memory,context,query,*,model,max_model_calls=3,max_tool_calls=4,budget=4096):
    registry,state=active_registry(memory,context,query,budget=budget)
    limits=BudgetLimits(model_calls=max_model_calls,tool_calls=max_tool_calls)
    ledger=BudgetLedger(limits);segment=ledger.start_segment(limits)
    calls=[];reserved_units=0
    class Capture:
        def generate(self,messages,*,tools=None):
            nonlocal reserved_units
            units=len(encoded([m.model_dump(mode='json') for m in messages]))+len(encoded(tools or []))+8192
            if units>32768 or reserved_units+units>65536:raise ContextLimit('实验总Token字节预留上限')
            reserved_units+=units
            calls.append(dict(messages=[m.model_dump(mode='json',exclude={'provider_state'}) for m in messages],
                              tools=tools,reference=state['view']))
            return model.generate(messages,tools=tools)
    runtime=ProjectedRuntime(Capture(),registry,budget=segment,query=query,view_builder=registry.memory_view)
    started=perf_counter();result=runtime.run_session(query.text,store=memory.sessions,context=context)
    raw=result.response.content if result.status=='completed' and result.response else None
    return dict(runtime_status=result.status,error_code=result.error_code,
        model_calls=ledger.usage.model_calls,tool_calls=ledger.usage.tool_calls,
        latency_ms=(perf_counter()-started)*1000,raw_answer=raw,requests=calls,
        tool_events=state['tool_events'],final_reference=state['view'],reserved_token_upper_units=reserved_units,
        validation=validate_answer(raw,visible_ids(state['view'])),
        trace=[e.model_dump(mode='json') for e in runtime.trace.events])
