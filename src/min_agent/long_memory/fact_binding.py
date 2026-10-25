"""局部证据绑定；未知不从邻句继承，稳定分层不调整v0.3数值权重。"""
from copy import deepcopy
import re

from min_agent.long_memory.retrieval import eligible_for_query
from min_agent.long_memory.retrieval_quality import body,STOP


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
QUALIFIER=re.compile(r"\b(?:no|not|never|without|except|unless|only|if|until|before|after|when|pending|valid|expir\w*|effective|renewal)\b|n't\b|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b|\b\d{1,2}:\d{2}\b|\b20\d{2}\b|不要|禁止|仅当|之前|之后",re.I)


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


def source_scope(doc,item,as_of):
    match=re.match(r'^\[.*?;session=(.*?);range=',item.content)
    first=doc.evidence.get(item.evidence_refs[0]) if item.evidence_refs else None
    return dict(scene=match.group(1) if match else first.session_id if first else 'unknown',
        entity_scope=[doc.topics[t].name for t in item.topic_ids if t in doc.topics and doc.topics[t].created_at<=as_of])


def binding_rerank(memory,context,query,ranked):
    pool={m.id:m for m in eligible_for_query(memory,context,query)[0]};doc=memory.document(context)
    requested=requested_attributes(query.text)
    names={w for w in re.findall(r'\b[A-Z][A-Za-z0-9-]+\b',query.text) if w.casefold() not in STOP}
    features={};valid=[];seen=set()
    for hit in ranked:
        identity=hit['memory_id']
        if identity not in pool or identity in seen:continue
        seen.add(identity);valid.append(deepcopy(hit));item=pool[identity]
        scope=source_scope(doc,item,query.as_of);own=body(item);bindings=local_bindings(own)
        scope_text=' '.join(scope['entity_scope'])
        entity_ok=not names or any(re.search(r'(?<!\w)'+re.escape(n)+r'(?!\w)',scope_text+' '+own,re.I) for n in names)
        matching=[b for b in bindings if b['attribute'] in requested]
        tier=2 if entity_ok and any(b['status']=='asserted' for b in matching) else (
            1 if any(b['status'] in {'asserted','conditional'} for b in matching) else 0)
        features[identity]=dict(tier=tier,requested=requested,bindings=bindings,
            entity_status='source_scope_compatible' if entity_ok else 'unknown',**scope)
    order={h['memory_id']:i for i,h in enumerate(valid)}
    return dict(ranked=sorted(valid,key=lambda h:(-features[h['memory_id']]['tier'],order[h['memory_id']])),features=features)
