"""受控英文模板的只读关系实验；未知句不猜，不注册工具、不写长期Memory。"""
from datetime import datetime
from itertools import combinations
import re

from pydantic import BaseModel,ConfigDict,model_validator
from min_agent.long_memory.fact_binding import sentences


class SourceExcerpt(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    id:str
    owner:str
    role:str
    text:str
    observed_at:datetime
    runtime_verified:bool=False
    @model_validator(mode='after')
    def verification_boundary(self):
        if self.observed_at.tzinfo is None:raise ValueError('来源时间必须包含时区')
        if self.role not in {'user','assistant','tool'}:raise ValueError('来源角色无效')
        if self.runtime_verified and self.role!='tool':raise ValueError('历史用户/助手话语不能标为实时核实')
        return self


class FactRelation(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    subject:str
    attribute:str
    value:str
    state:str
    scenario:str
    valid_from:datetime
    valid_until:datetime|None=None
    source:SourceExcerpt
    quote:str
    span:tuple[int,int]
    @property
    def world_verified(self):return self.source.role=='tool' and self.source.runtime_verified
    @property
    def evidence_nature(self):
        return 'runtime_verified' if self.world_verified else 'user_statement' if self.source.role=='user' else 'historical_'+self.source.role+'_statement'


def extract_relations(source,*,scenario='default'):
    source=SourceExcerpt.model_validate(source);result=[]
    for clause in sentences(source.text):
        text=clause['text'].strip()
        # DIRECT只说明记录出自用户。问句、猜测、条件句不能作为断言。
        if text.endswith(('?','？')) or re.search(r'\b(?:guess|maybe|perhaps|if|might|could)\b',text,re.I):continue
        found=[]
        account=re.match(r'^My\s+(?P<s>[A-Za-z][A-Za-z -]{0,30}?account)\s+(?P<a>routing number|number|purpose)\s+is\s+(?P<v>[A-Za-z0-9-]+)',text,re.I)
        if account:found.append((account['s'].casefold(),account['a'].casefold().replace(' ','_'),account['v'],'asserted'))
        holding=re.match(r'^(?:I|You)\s+(?P<verb>hold|own|discussed|applied for|was approved for|cancelled|canceled|do not hold|have not applied for)\s+(?P<s>[A-Za-z]+(?: [A-Za-z]+){0,3} Card)\b',text,re.I)
        if holding:
            states={'hold':'held','own':'held','discussed':'discussed','applied for':'application_pending',
                    'was approved for':'application_approved','cancelled':'cancelled','canceled':'cancelled',
                    'do not hold':'unknown','have not applied for':'unknown'}
            state=states[holding['verb'].casefold()]
            if source.role=='assistant' and text.startswith('I '):state='unknown'
            found.append((holding['s'],'holding_state',state,state))
        benefit=re.match(r'^(?P<s>[A-Za-z]+(?: [A-Za-z]+){0,3} Card)\s+earns\s+(?P<v>\d+x)\s+points on flights',text,re.I)
        if benefit:found.append((benefit['s'],'flight_reward',benefit['v'],'asserted'))
        for subject,attribute,value,state in found:
            result.append(FactRelation(subject=subject,attribute=attribute,value=value,state=state,scenario=scenario,
                valid_from=source.observed_at,source=source,quote=clause['text'],span=(clause['start'],clause['end'])))
    return result


def active(relations,*,owner,as_of):
    if as_of.tzinfo is None:raise ValueError('查询时间必须包含时区')
    return [r for r in relations if r.source.owner==owner and r.source.observed_at<=as_of
            and r.valid_from<=as_of and (r.valid_until is None or as_of<r.valid_until)]


def conflicts(relations,*,owner,as_of):
    pairs=[]
    for a,b in combinations(active(relations,owner=owner,as_of=as_of),2):
        same=(a.subject.casefold(),a.attribute,a.scenario)==(b.subject.casefold(),b.attribute,b.scenario)
        # 状态变化可以是更新；同一时点不同状态才作为待审冲突。
        if a.attribute=='holding_state' and a.source.observed_at!=b.source.observed_at:continue
        if same and a.value.casefold()!=b.value.casefold():
            pairs.append(dict(subject=a.subject,attribute=a.attribute,scenario=a.scenario,
                left_source=a.source.id,right_source=b.source.id,classification='historical_statement_conflict_needs_review'))
    return pairs


def held_products(relations,*,owner,as_of,scenario='default'):
    selected={}
    for r in active(relations,owner=owner,as_of=as_of):
        if r.scenario!=scenario or r.attribute!='holding_state' or not (r.source.role=='user' or r.world_verified):continue
        key=(r.subject.casefold(),r.scenario)
        selected.setdefault(key,[]).append(r)
    held=[]
    for values in selected.values():
        latest=max(r.source.observed_at for r in values);current=[r for r in values if r.source.observed_at==latest]
        if {r.state for r in current}=={'held'}:held.append(current[0].subject)
    return sorted(set(held))
