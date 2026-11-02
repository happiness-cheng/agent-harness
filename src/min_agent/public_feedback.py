"""离线候选：公开目标与只读反馈；信任源必须由受信应用固定注入。"""
from hashlib import sha256
import json
from typing import Literal, Protocol
from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer


class Contract(BaseModel):
    model_config=ConfigDict(frozen=True,extra='forbid',strict=True,allow_inf_nan=False)


class Scope(Contract):
    task_id: str = Field(min_length=1,max_length=100)
    owner_id: str = Field(min_length=1,max_length=100)
    session_id: str = Field(min_length=1,max_length=100)


class PublicGoal(Contract):
    public_goal_id: str = Field(pattern=r'^goal-[0-9]{3}$')
    kind: Literal['TODO_COUNT','RESEARCH_EVIDENCE','REMOTE_EFFECT']
    source_type: Literal['USER_REQUEST']
    source_ref: str = Field(min_length=1,max_length=160)
    expected_count: int | None = Field(default=None,ge=1,le=100)

    @model_validator(mode='after')
    def check_kind(self):
        if (self.kind=='TODO_COUNT') != (self.expected_count is not None):
            raise ValueError('公开目标的参数与类型不符')
        return self


def goal_digest(scope,freeze_seq,goals):
    payload={'schema_version':'1.3.1','scope':scope.model_dump(mode='json'),'freeze_seq':freeze_seq,
             'goals':[g.model_dump(mode='json') for g in goals]}
    return sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()


class PublicGoalSpec(Contract):
    schema_version: Literal['1.3.1']='1.3.1'
    scope: Scope
    freeze_seq: int = Field(ge=1)
    goals: tuple[PublicGoal,...] = Field(strict=False)
    content_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def distinct(self):
        if len({g.public_goal_id for g in self.goals})!=len(self.goals):raise ValueError('公开目标 ID 重复')
        return self


class GoalObservation(Contract):
    public_goal_id: str = Field(pattern=r'^goal-[0-9]{3}$')
    kind: Literal['TODO_COUNT','RESEARCH_EVIDENCE','REMOTE_EFFECT']
    count: int | None = Field(default=None,ge=0,le=100000)
    research_state: Literal['partial','complete','unknown'] | None = None
    effect_state: Literal['succeeded','failed','unknown'] | None = None

    @model_validator(mode='after')
    def consistent(self):
        if self.kind=='TODO_COUNT' and (self.research_state is not None or self.effect_state is not None):raise ValueError('观察类型不符')
        if self.kind=='RESEARCH_EVIDENCE' and (self.count is not None or self.research_state is None or self.effect_state is not None):raise ValueError('观察类型不符')
        if self.kind=='REMOTE_EFFECT' and (self.count is not None or self.effect_state is None or self.research_state is not None):raise ValueError('观察类型不符')
        return self


class VerifiedSnapshot(Contract):
    """仅受信采集器可作为结果返回；本类型本身不证明可信。"""
    scope: Scope
    goal_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    start_seq: int = Field(ge=1)
    sequence: int = Field(ge=1)
    revision: int = Field(ge=1)
    facts: tuple[GoalObservation,...]


class PublicGoalProgress(Contract):
    public_goal_id: str
    description: str
    status: Literal['MET','NOT_MET','UNKNOWN']
    reason_code: Literal['CONFIRMED_STATE_MATCH','CONFIRMED_STATE_MISMATCH','INSUFFICIENT_EVIDENCE','PARTIAL_EVIDENCE_ONLY','SIDE_EFFECT_STATUS_UNKNOWN']
    expected: str
    observed: str


class GoalCounts(Contract):
    met: int = Field(ge=0)
    not_met: int = Field(ge=0)
    unknown: int = Field(ge=0)
    total: int = Field(ge=1)

    @model_validator(mode='after')
    def consistent(self):
        if self.met+self.not_met+self.unknown!=self.total:raise ValueError('公开目标计数不一致')
        return self


class PublicDiagnosis(Contract):
    first_confirmed_anomaly: Literal['UNKNOWN']='UNKNOWN'
    root_cause_status: Literal['UNKNOWN']='UNKNOWN'


class PublicFeedback(Contract):
    schema_version: Literal['1.3.1']='1.3.1'
    feedback_status: Literal['READY','WITHHELD']
    public_task_status: Literal['MET','NOT_MET','UNKNOWN']
    reason_code: Literal['UNVERIFIED_OBSERVATION'] | None = None
    goal_progress: GoalCounts | None = None
    goals: tuple[PublicGoalProgress,...] | None = None
    diagnosis: PublicDiagnosis | None = None
    next_action_hints: tuple[str,...]
    revalidation_conditions: tuple[str,...] | None = None
    execution_authorization: Literal['NOT_GRANTED']='NOT_GRANTED'

    @model_serializer(mode='wrap')
    def public_fields(self,handler):
        return {key:value for key,value in handler(self).items() if value is not None}


class TrustedCollector(Protocol):
    """受信应用固定的边界：验来源/时间/权限/完整性/时效；不得由模型指定。"""
    def verify_goals(self,scope:Scope,spec:PublicGoalSpec) -> bool: ...
    def read_snapshot(self,scope:Scope,spec:PublicGoalSpec,token:str) -> VerifiedSnapshot: ...


def _withheld():
    return PublicFeedback(feedback_status='WITHHELD',public_task_status='UNKNOWN',
        reason_code='UNVERIFIED_OBSERVATION',next_action_hints=('REQUEST_AUTHORIZED_REVIEW',))


class PublicFeedbackProjector:
    """collector 和 scope 为受信应用注入的能力，而非调用方或模型提交的数据。"""
    def __init__(self,collector:TrustedCollector,scope:Scope):
        self._collector=collector
        self._scope=Scope.model_validate(scope.model_dump())

    def project(self,spec:PublicGoalSpec,snapshot_token:str) -> PublicFeedback:
        try:
            if not isinstance(spec,PublicGoalSpec) or not isinstance(snapshot_token,str) or not snapshot_token:
                return _withheld()
            spec=PublicGoalSpec.model_validate(spec.model_dump())
            if (not spec.goals or spec.scope!=self._scope or
                goal_digest(spec.scope,spec.freeze_seq,spec.goals)!=spec.content_sha256 or
                self._collector.verify_goals(self._scope,spec) is not True):
                return _withheld()
            raw=self._collector.read_snapshot(self._scope,spec,snapshot_token)
            if not isinstance(raw,VerifiedSnapshot):return _withheld()
            snap=VerifiedSnapshot.model_validate(raw.model_dump())
            if (snap.scope!=self._scope or snap.goal_sha256!=spec.content_sha256 or
                snap.start_seq<=spec.freeze_seq or snap.sequence<=snap.start_seq):return _withheld()
            facts={v.public_goal_id:v for v in snap.facts}
            if len(facts)!=len(snap.facts) or set(facts)!={g.public_goal_id for g in spec.goals}:return _withheld()
            results=[]
            for goal in spec.goals:
                item=facts[goal.public_goal_id]
                if item.kind!=goal.kind:return _withheld()
                if goal.kind=='TODO_COUNT':
                    status='UNKNOWN' if item.count is None else 'MET' if item.count==goal.expected_count else 'NOT_MET'
                    code={'UNKNOWN':'INSUFFICIENT_EVIDENCE','MET':'CONFIRMED_STATE_MATCH','NOT_MET':'CONFIRMED_STATE_MISMATCH'}[status]
                    description='创建用户要求的待办事项';expected=f'{goal.expected_count} 条符合公开请求的记录'
                    observed='记录数量未知' if item.count is None else f'{item.count} 条符合公开请求的记录'
                elif goal.kind=='RESEARCH_EVIDENCE':
                    status='MET' if item.research_state=='complete' else 'UNKNOWN'
                    code='CONFIRMED_STATE_MATCH' if status=='MET' else 'PARTIAL_EVIDENCE_ONLY' if item.research_state=='partial' else 'INSUFFICIENT_EVIDENCE'
                    description='核验研究结果的来源引用';expected='已完成的研究结果包含可核验引用'
                    observed={'complete':'研究结果已完成且引用可核验','partial':'仅有部分证据','unknown':'研究证据状态未知'}[item.research_state]
                else:
                    status={'succeeded':'MET','failed':'NOT_MET','unknown':'UNKNOWN'}[item.effect_state]
                    code={'MET':'CONFIRMED_STATE_MATCH','NOT_MET':'CONFIRMED_STATE_MISMATCH','UNKNOWN':'SIDE_EFFECT_STATUS_UNKNOWN'}[status]
                    description='完成用户授权的远端任务';expected='远端目标状态可确认'
                    observed={'succeeded':'已确认目标状态','failed':'已确认目标状态未满足','unknown':'远端状态未知'}[item.effect_state]
                results.append(PublicGoalProgress(public_goal_id=goal.public_goal_id,description=description,
                    status=status,reason_code=code,expected=expected,observed=observed))
            counts={v:sum(g.status==v for g in results) for v in ('MET','NOT_MET','UNKNOWN')}
            overall='NOT_MET' if counts['NOT_MET'] else 'UNKNOWN' if counts['UNKNOWN'] else 'MET'
            hints=('REQUEST_AUTHORIZED_REVIEW',) if overall=='UNKNOWN' else ('REVIEW_AUTHORIZED_STATE',) if overall=='NOT_MET' else ()
            if any(f.effect_state=='unknown' for f in snap.facts):hints+=('RECONCILE_UNKNOWN_SIDE_EFFECT',)
            conditions=('VERIFY_AUTHORIZED_GOALS_AND_STATE','VERIFY_BUDGET_AND_PERMISSIONS')
            return PublicFeedback(feedback_status='READY',public_task_status=overall,
                goal_progress=GoalCounts(met=counts['MET'],not_met=counts['NOT_MET'],unknown=counts['UNKNOWN'],total=len(results)),
                goals=tuple(results),diagnosis=PublicDiagnosis(),
                next_action_hints=hints,revalidation_conditions=conditions)
        except Exception:
            # 不回传堆栈或未认证数据；不执行补救。
            return _withheld()
