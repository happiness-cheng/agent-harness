"""v08装配所需的既有纯函数；不包含离线排序器、权重实验或评测运行器。"""

import json

import re

from pydantic import BaseModel, ConfigDict, Field

from min_agent.long_memory.retrieval import terms


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

STOP = set('''a an the i me my mine we our ours you your yours he she it its they their
this that these those is are am was were be been being do does did have has had will
would can could may might must should please help need want know tell remind sure again
also just all any anything what which when where how why for to of in on at by with
from as about and or but if so up get make while looking now go before after new ready
getting based take care supposed hi hello thanks thank yes okay good great perfect'''.split())

def _normal(word):
    if len(word) > 4 and word.endswith('ies'):
        return word[:-3]+'y'
    if len(word) > 4 and word.endswith('s') and not word.endswith('ss'):
        return word[:-1]
    return word

def keywords(text):
    return {_normal(w) for w in terms(text) if w not in STOP}

def body(item):
    match = re.match(r'^\[(?:user|assistant|tool);session=.*?;range=\d+:\d+\] (.*)$', item.content, re.S)
    return match.group(1) if match else item.content

VALUE=r'(?=[A-Za-z0-9-]*\d)[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*'

LABELS={
    'reservation_id':r'(?:confirmation(?:\s+(?:number|code|reference))?|reservation\s+(?:number|code|reference)|booking\s+(?:number|code|reference))',
    'membership_id':r'(?:member(?:ship)?|loyalty|rewards)\s+(?:number|id|reference)',
    'order_id':r'order\s+(?:number|code|reference|id)',
    'room_number':r'room(?:\s+number)?',
    'account_id':r'account\s+(?:number|id)',
    'routing_number':r'routing\s+number',
    'phone_number':r'(?:phone|mobile|telephone|cell)\s+number',
    'security_pin':r'(?:security\s+(?:PIN|code)|PIN|passcode)',
    'discount_code':r'(?:corporate(?:\s+discount)?|discount|promo(?:tional)?)\s+code',
    'seat_number':r'seats?(?:\s+number)?',
}

NEGATIVE=re.compile(r"\b(?:no|not|never|without|except)\b|n't\b|不能|不是|禁止",re.I)

CONDITIONAL=re.compile(r'\b(?:if|unless|only|pending|provided|renewal)\b|仅当|除非',re.I)

def sentences(text):
    spans=[];start=0
    for i,char in enumerate(text):
        if char not in '.!?。！？;；\n':continue
        if char=='.' and i and i+1<len(text) and text[i-1].isdigit() and text[i+1].isdigit():continue
        if char=='.' and re.search(r'\b(?:Mr|Mrs|Ms|Dr|Prof|St)\.$',text[max(0,i-8):i+1],re.I):continue
        end=i+1
        left=start
        while left<end and text[left].isspace():left+=1
        if left<end:spans.append(dict(start=left,end=end,text=text[left:end]))
        start=end
    while start<len(text) and text[start].isspace():start+=1
    if start<len(text):spans.append(dict(start=start,end=len(text),text=text[start:]))
    return spans

def local_bindings(text):
    result=[]
    for unit in sentences(text):
        clause=unit['text']
        for attribute,label in LABELS.items():
            pattern=r'\b(?P<label>'+label+r')\s*(?:(?:is|will be|:|\#)\s*)?(?P<negative>not\s+)?(?P<value>'+VALUE+r')\b'
            for match in re.finditer(pattern,clause,re.I):
                status='question' if clause.rstrip().endswith(('?','？')) else (
                    'negated' if NEGATIVE.search(clause) else 'conditional' if CONDITIONAL.search(clause) else 'asserted')
                result.append(dict(attribute=attribute,value=match['value'],status=status,
                    label_span=[unit['start']+match.start('label'),unit['start']+match.end('label')],
                    value_span=[unit['start']+match.start('value'),unit['start']+match.end('value')],
                    clause_span=[unit['start'],unit['end']],quote=clause,entity='unknown'))
    return result

def requested_attributes(text):
    return [kind for kind,pattern in LABELS.items() if re.search(r'\b'+pattern+r'\b',text,re.I)]

def decorate_entry(memory,context,query,item,entry):
    """确认是写入资格，不等于历史助手的话已经被现实系统核实。"""
    doc=memory.document(context)
    sources=[doc.evidence[r] for r in item.evidence_refs if r in doc.evidence
             and doc.evidence[r].user_id==str(context.owner_id) and doc.evidence[r].observed_at<=query.as_of]
    return dict(entry,role=sources[0].role if sources else 'unknown',
        confirmation_status=item.confirmation_status,
        truth_status='historical_statement_not_live_verified')

class ReadEvidence(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    memory_ids:list[str]=Field(min_length=1,max_length=8)

def track_dependencies(memory,context,ids):
    snapshot=memory.sessions.snapshot(context)
    if snapshot.task is None:return
    task_id=str(snapshot.task.task_id);previous=snapshot.extensions.get('long_memory_run_dependencies',{})
    retained=set(previous.get('memory_ids',[])) if previous.get('task_id')==task_id else set()
    retained.update(ids)
    retained.update(i for d in snapshot.extensions.get('long_memory_dependencies',[]) for i in d['memory_ids'])
    memory.sessions.set_extension(context,'long_memory_run_dependencies',dict(task_id=task_id,memory_ids=sorted(retained)))
