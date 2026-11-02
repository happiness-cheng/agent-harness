"""只读公开目标进度；可信来源由受信适配器负责，绝不执行动作。"""
from hashlib import sha256
import json
from typing import Literal, Protocol
from pydantic import BaseModel,ConfigDict,Field,model_validator

class Contract(BaseModel):
    model_config=ConfigDict(strict=True,frozen=True,extra='forbid',allow_inf_nan=False)
class Scope(Contract):
    task_id:str=Field(min_length=1,max_length=160)
    owner_id:str=Field(min_length=1,max_length=160)
    session_id:str=Field(min_length=1,max_length=160)
class Goal(Contract):
    public_goal_id:str=Field(min_length=1,max_length=100)
    definition_hash:str=Field(pattern=r'^[0-9a-f]{64}$')
    credit_unit:str=Field(min_length=1,max_length=160)
    outcome_type:Literal['BINARY','CONTINUOUS']='BINARY'
    weight:int=Field(default=1,ge=1,le=100000)
    dependencies:tuple[str,...]=()
class GoalSet(Contract):
    schema_version:Literal['1.4-a']='1.4-a'
    scope:Scope
    goal_set_ref:str=Field(min_length=1,max_length=160)
    version:str=Field(min_length=1,max_length=100)
    measurement_version:str=Field(min_length=1,max_length=100)
    goals:tuple[Goal,...]
    complete:bool
    independence_ref:str|None=None
    @model_validator(mode='after')
    def unique(self):
        if len({g.public_goal_id for g in self.goals})!=len(self.goals):raise ValueError('duplicate goal IDs')
        return self

def goal_set_digest(goals):
    return sha256(json.dumps(goals.model_dump(mode='json'),sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
class GoalState(Contract):
    public_goal_id:str
    status:Literal['MET','NOT_MET','UNKNOWN']
class ScoringInput(Contract):
    goal_set:GoalSet
    observation_ref:str=Field(min_length=1,max_length=160)
    revision:int=Field(ge=1)
    sequence:int=Field(ge=1)
    execution_start:int=Field(ge=1)
    freeze_seq:int=Field(ge=1)
    states:tuple[GoalState,...]
    unresolved_effect:bool=False

def _display(numerator,denominator,upper=False):
    tenths=(numerator*10+denominator-1)//denominator if upper else numerator*10//denominator
    return str(tenths//10) if tenths%10==0 else f'{tenths//10}.{tenths%10}'
class Progress(Contract):
    met:int=Field(ge=0)
    not_met:int=Field(ge=0)
    unknown:int=Field(ge=0)
    total:int=Field(ge=1)
    lower_numerator:int=Field(ge=0)
    upper_numerator:int=Field(ge=0)
    denominator:int=Field(ge=1)
    lower_percent:str
    upper_percent:str
    @model_validator(mode='after')
    def arithmetic(self):
        if self.met+self.not_met+self.unknown!=self.total or self.denominator!=self.total:raise ValueError('invalid denominator')
        if self.lower_numerator!=100*self.met or self.upper_numerator!=100*(self.met+self.unknown):raise ValueError('invalid numerator')
        if self.lower_percent!=_display(self.lower_numerator,self.denominator) or self.upper_percent!=_display(self.upper_numerator,self.denominator,True):raise ValueError('invalid display')
        return self
class ProgressScore(Contract):
    schema_version:Literal['1.4-a']='1.4-a'
    metric:Literal['Verified Public Goal Progress']='Verified Public Goal Progress'
    trust_assumption:Literal['OFFLINE_TRUSTED_ADAPTER']='OFFLINE_TRUSTED_ADAPTER'
    score_status:Literal['AVAILABLE','WITHHELD','NOT_APPLICABLE']
    reason_code:Literal['VERIFIED_PUBLIC_GOALS','UNVERIFIED_INPUT','UNSUPPORTED_GOAL_MODEL']
    progress:Progress|None=None
    execution_authorization:Literal['NOT_GRANTED']='NOT_GRANTED'
    @model_validator(mode='after')
    def available(self):
        if (self.score_status=='AVAILABLE')!=(self.progress is not None):raise ValueError('availability mismatch')
        expected={'AVAILABLE':'VERIFIED_PUBLIC_GOALS','WITHHELD':'UNVERIFIED_INPUT','NOT_APPLICABLE':'UNSUPPORTED_GOAL_MODEL'}[self.score_status]
        if self.reason_code!=expected:raise ValueError('reason mismatch')
        return self
class Transition(Contract):
    public_goal_id:str
    change:str
class ProgressDelta(Contract):
    status:Literal['COMPARABLE','INCOMPARABLE']
    transitions:tuple[Transition,...]=()
    causal_attribution:Literal['NOT_ESTABLISHED']='NOT_ESTABLISHED'
    execution_authorization:Literal['NOT_GRANTED']='NOT_GRANTED'

class TrustedScoringAdapter(Protocol):
    # adapter安装权限不属于模型；返回DTO本身不赋予信任。
    def read_verified(self,goal_ref:str,observation_ref:str,scope:Scope)->ScoringInput:...
    def read_historical(self,goal_ref:str,observation_ref:str,scope:Scope)->ScoringInput:...

def validate_goal_set(data,scope):
    g=data.goal_set
    if (g.scope!=scope or not g.goals or not g.complete or not g.independence_ref
        or data.freeze_seq>=data.execution_start or data.sequence<=data.execution_start or data.unresolved_effect):return 'WITHHELD'
    if tuple(s.public_goal_id for s in data.states)!=tuple(x.public_goal_id for x in g.goals):return 'WITHHELD'
    if (len({x.credit_unit for x in g.goals})!=len(g.goals) or len({x.definition_hash for x in g.goals})!=len(g.goals)
        or any(x.outcome_type!='BINARY' or x.weight!=1 or x.dependencies for x in g.goals)):return 'NOT_APPLICABLE'
    return 'AVAILABLE'

def _score(data,scope):
    status=validate_goal_set(data,scope)
    if status!='AVAILABLE':return ProgressScore(score_status=status,reason_code='UNVERIFIED_INPUT' if status=='WITHHELD' else 'UNSUPPORTED_GOAL_MODEL')
    m=sum(s.status=='MET' for s in data.states);u=sum(s.status=='UNKNOWN' for s in data.states);n=len(data.states)
    p=Progress(met=m,not_met=n-m-u,unknown=u,total=n,lower_numerator=100*m,upper_numerator=100*(m+u),denominator=n,
               lower_percent=_display(100*m,n),upper_percent=_display(100*(m+u),n,True))
    return ProgressScore(score_status='AVAILABLE',reason_code='VERIFIED_PUBLIC_GOALS',progress=p)

class ProgressScorer:
    def __init__(self,adapter:TrustedScoringAdapter,scope:Scope):
        self._adapter=adapter;self._scope=Scope.model_validate(scope.model_dump())
    def _read(self,goal_ref,observation_ref,*,historical=False):
        if not isinstance(goal_ref,str) or not isinstance(observation_ref,str):raise ValueError('references required')
        loader=self._adapter.read_historical if historical else self._adapter.read_verified
        raw=loader(goal_ref,observation_ref,self._scope)
        if not isinstance(raw,ScoringInput):raise ValueError('unverified input')
        data=ScoringInput.model_validate(raw.model_dump())
        if data.goal_set.goal_set_ref!=goal_ref or data.observation_ref!=observation_ref:raise ValueError('reference binding mismatch')
        return data
    def score(self,goal_ref,observation_ref):
        try:return _score(self._read(goal_ref,observation_ref),self._scope)
        except Exception:return ProgressScore(score_status='WITHHELD',reason_code='UNVERIFIED_INPUT')
    def compare(self,goal_ref,before_ref,after_ref):
        try:
            a=self._read(goal_ref,before_ref,historical=True);b=self._read(goal_ref,after_ref,historical=True)
            if (validate_goal_set(a,self._scope)!='AVAILABLE' or validate_goal_set(b,self._scope)!='AVAILABLE'
                or goal_set_digest(a.goal_set)!=goal_set_digest(b.goal_set) or a.revision>=b.revision or a.sequence>=b.sequence
                or a.execution_start!=b.execution_start or a.freeze_seq!=b.freeze_seq or before_ref==after_ref):raise ValueError('incomparable')
            transitions=tuple(Transition(public_goal_id=x.public_goal_id,change=x.status+'_TO_'+y.status) for x,y in zip(a.states,b.states) if x.status!=y.status)
            return ProgressDelta(status='COMPARABLE',transitions=transitions)
        except Exception:return ProgressDelta(status='INCOMPARABLE')

def score_public_goals(scorer,goal_ref,observation_ref):
    return scorer.score(goal_ref,observation_ref)
def compare_progress(scorer,goal_ref,before_ref,after_ref):
    return scorer.compare(goal_ref,before_ref,after_ref)
def serialize_progress(result):
    checked=ProgressScore.model_validate(result.model_dump())
    return json.dumps(checked.model_dump(mode='json'),sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
