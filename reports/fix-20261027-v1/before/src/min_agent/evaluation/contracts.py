"""冻结任务、观察与三态检查契约；不包含可执行插件或任意路径。"""
from hashlib import sha256
import json
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,JsonValue,model_validator

Verdict=Literal['PASS','FAIL','UNKNOWN']
Category=Literal['research','memory','tools','runtime','security']


def digest(value):
    if isinstance(value,BaseModel):value=value.model_dump(mode='json')
    return sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


class Contract(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True,allow_inf_nan=False)


class CheckSpec(Contract):
    id:str=Field(min_length=1,pattern=r'^[a-zA-Z0-9_.-]+$')
    layer:Literal['outcome','process','evidence','semantic']='outcome'
    kind:Literal['todo_count','todo_title','todo_status','final_contains','final_excludes','runtime_status',
        'checkpoint','tool_error','tool_count','memory_delivery','citation_valid','artifact_quotes',
        'research_conflict','semantic_support','effect_known','recovered_same_budget','calculator_value','test_exit']
    expected:JsonValue=None
    hard_safety:bool=False


class TaskSpec(Contract):
    id:str=Field(min_length=1,pattern=r'^[a-zA-Z0-9_.-]+$')
    category:Category
    prompt:str=Field(min_length=1,max_length=20000)
    evaluation:Literal['deterministic','semantic','human']='deterministic'
    data_classification:Literal['synthetic','public','private']='synthetic'
    profile:Literal['plain','calculator','add','update','false_success','memory','research','bad_quote',
        'denied','injection','cross_session','invalid_arguments','budget','recover','unknown','delay']='plain'
    parameters:dict[str,JsonValue]=Field(default_factory=dict)
    mode:Literal['plan','build']='plan'
    actions:list[dict[str,JsonValue]]=Field(default_factory=list,max_length=12)
    tools:list[str]=Field(default_factory=list,max_length=30)
    fixture:Literal['empty','todo','memory','revoked','foreign','research','cross_session']='empty'
    max_model_calls:int=Field(default=8,ge=1,le=40)
    segment_model_calls:int=Field(default=8,ge=1,le=40)
    max_tool_calls:int=Field(default=12,ge=0,le=60)
    max_rounds:int=Field(default=8,ge=1,le=40)
    timeout_seconds:float=Field(default=40,gt=0,le=240)
    recovery:bool=False
    live_eligible:bool=False
    model:Literal['deepseek-flash']='deepseek-flash'
    reasoning_effort:Literal['high']='high'
    output_tokens:int|None=Field(default=None,ge=1)
    checks:list[CheckSpec]=Field(min_length=1,max_length=30)
    expected_verdict:Verdict='PASS' # 仅suite元验收使用，不参与Verifier判定。

    @model_validator(mode='after')
    def distinct(self):
        if len({c.id for c in self.checks})!=len(self.checks):raise ValueError('检查ID重复')
        if self.live_eligible and self.data_classification=='private':raise ValueError('私人数据不能进入live评估')
        return self


class Observation(Contract):
    run_id:str
    task_id:str
    version_id:str
    session_id:str=''
    transport_status:Literal['ok','timeout','worker_error']='ok'
    execution_status:str='unknown'
    answer:str=''
    todos:list[dict]=Field(default_factory=list)
    initial_todos:list[dict]=Field(default_factory=list)
    tools:list[dict]=Field(default_factory=list)
    trace:list[dict]=Field(default_factory=list)
    artifacts:dict[str,dict]=Field(default_factory=dict)
    budget:dict=Field(default_factory=dict)
    checkpoint:dict|None=None
    memory_delivery:dict=Field(default_factory=dict)
    citation:dict=Field(default_factory=dict)
    model_requests:int=0
    usage:list[dict]=Field(default_factory=list)
    elapsed_seconds:float=0
    extra:dict=Field(default_factory=dict)


class CheckResult(Contract):
    id:str
    layer:Literal['outcome','process','evidence','semantic']
    verdict:Verdict
    reason:str
    evidence_refs:list[str]=Field(default_factory=list)
    hard_safety:bool=False


class EvaluationResult(Contract):
    task_id:str
    spec_sha256:str
    verdict:Verdict
    safety:Verdict
    honesty:Verdict
    execution_completed:bool
    checks:list[CheckResult]
    failures:list[str]
    observation:Observation
