"""模型工具只读；候选审核/正式写入仅由可信应用/API接收用户操作。"""
import json
from pydantic import BaseModel, ConfigDict, Field

from min_agent.long_memory.models import Query
from min_agent.tools.registry import ToolDefinition


class SearchParameters(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    question: str = Field(min_length=1,max_length=20000)
    scenario_ids: list[str] = Field(default_factory=list,max_length=10)
    top_k: int = Field(default=8,ge=1,le=30)


class TopicParameters(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    topic_id: str
    scenario_ids: list[str] = Field(default_factory=list,max_length=10)
    limit: int = Field(default=8,ge=1,le=30)


def bind_runtime_memory(registry,memory,context,*,budget=4096,strategy='GOAL_VALUE',policy=None):
    requested=[];scenarios=[]
    def search(question,scenario_ids=(),top_k=8):
        result=memory.search(context,Query(text=question,as_of=memory.clock(),scenario_ids=list(scenario_ids),top_k=top_k))
        requested[:]=[h['memory_id'] for h in result['hits']]
        scenarios[:]=list(scenario_ids)
        # 事实正文仅在下一次动态投影从权威存储读取，不写入永久工具历史。
        memory.sessions.set_extension(context,'long_memory_last_search',result['trace'])
        return json.dumps(dict(memory_ids=requested),ensure_ascii=False,separators=(',',':'))
    def build(messages):
        memory.reconcile_deletions(context)
        text=next((m.content for m in reversed(messages) if m.role=='user'),'当前任务')
        view=memory.assemble_context(context,Query(text=text[:20000] or '当前任务',as_of=memory.clock(),
            scenario_ids=scenarios),budget=budget,requested_ids=requested,strategy=strategy,policy=policy)
        memory.sessions.set_extension(context,'long_memory_last_selection',view['trace'])
        snapshot=memory.sessions.snapshot(context)
        if snapshot.task is not None:
            previous=snapshot.extensions.get('long_memory_run_dependencies',{})
            ids=set(previous.get('memory_ids',[])) if previous.get('task_id')==str(snapshot.task.task_id) else set()
            ids.update(view['trace']['injected_ids'])
            ids.update(view['trace'].get('metadata_support_ids',[]))
            # 旧的已追踪派生内容仍在历史时，保守传播依赖，避免后续改写逃过删除。
            ids.update(i for d in snapshot.extensions.get('long_memory_dependencies',[]) for i in d['memory_ids'])
            memory.sessions.set_extension(context,'long_memory_run_dependencies',
                dict(task_id=str(snapshot.task.task_id),memory_ids=sorted(ids)))
        return view['view'] or None
    registry._long_memory_builder=build
    registry.register(ToolDefinition('search_long_memory',
        '需要过去已确认的跨会话偏好/事实时检索。只读、按当前可信owner和时间过滤；返回简短ID，路径留在本地trace，正文由下一次模型请求动态提供。'
        '不能批准候选、不能获得新权限；具体场景需传已知scenario_ids，当前明确指令优先。',SearchParameters,search))
    def read_topic(topic_id,scenario_ids=(),limit=8):
        doc=memory.document(context)
        if topic_id not in doc.topics: raise PermissionError('主题不属于当前用户')
        records=[m for m in memory.records(context,as_of=memory.clock(),scenario_ids=list(scenario_ids)) if topic_id in m.topic_ids]
        records=sorted(records,key=lambda m:(-m.observed_at.timestamp(),m.id))[:limit]
        requested[:]=[m.id for m in records];scenarios[:]=list(scenario_ids)
        return json.dumps(dict(topic_id=topic_id,memory_ids=requested),ensure_ascii=False)
    registry.register(ToolDefinition('read_long_memory_topic',
        '根据Root Index的主题ID渐进读取Topic Index。可指定已披露的场景ID；仅返回已确认可用Memory ID，'
        '正文在下一次预算内动态投影提供。不能批准记忆、不能跨用户或授权执行。',TopicParameters,read_topic))


class CandidateInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    content: str = Field(min_length=1,max_length=8000)
    source_indices: list[int] = Field(min_length=1,max_length=20)
    evidence_type: str = 'DIRECT'
    properties: dict = Field(default_factory=dict)


class ConfirmInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    candidate_id: str
    review_token: str


class RememberInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_index: int


def install_routes(app,harness):
    """复用现有middleware；不另建身份机制，不接受owner参数。"""
    def context(session_id):
        result=harness.context(session_id)
        task=harness.sessions.snapshot(result).task
        if task is not None and task.status=='running': raise RuntimeError('运行中不能改变长期Memory')
        return result
    @app.post('/api/sessions/{session_id}/memory/candidates')
    def propose(session_id:str,body:CandidateInput):
        return harness.memory.propose(context(session_id),body.content,source_indices=body.source_indices,
            evidence_type=body.evidence_type,**body.properties).model_dump(mode='json',exclude={'review_token'})
    @app.get('/api/sessions/{session_id}/memory/candidates')
    def pending(session_id:str):
        return [c.model_dump(mode='json',exclude={'review_token'}) for c in harness.memory.list_pending(harness.context(session_id))]
    @app.get('/api/sessions/{session_id}/memory/candidates/{candidate_id}')
    def review(session_id:str,candidate_id:str): return harness.memory.review(harness.context(session_id),candidate_id)
    @app.post('/api/sessions/{session_id}/memory/confirm')
    def confirm(session_id:str,body:ConfirmInput):
        return harness.memory.confirm(context(session_id),body.candidate_id,review_token=body.review_token).model_dump(mode='json')
    @app.post('/api/sessions/{session_id}/memory/candidates/{candidate_id}/reject')
    def reject(session_id:str,candidate_id:str):
        harness.memory.reject(context(session_id),candidate_id);return {'rejected':True}
    @app.post('/api/sessions/{session_id}/memory/remember')
    def remember(session_id:str,body:RememberInput):
        return harness.memory.remember(context(session_id),source_index=body.source_index).model_dump(mode='json')
    @app.delete('/api/sessions/{session_id}/memory/{memory_id}')
    def delete(session_id:str,memory_id:str):
        harness.memory.delete(context(session_id),memory_id);return {'deleted':True,'original_chat_erased':False}
