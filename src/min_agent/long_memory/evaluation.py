"""离线源证据评测：批准模式与生产隔离；不导入在线客户端或读取凭据。"""
from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from time import perf_counter
from uuid import NAMESPACE_URL, uuid5

from min_agent.long_memory import LongMemory, Query
from min_agent.long_memory.models import Evidence, Memory, MemoryEdge, Topic
from min_agent.long_memory.selection import SelectionPolicy, serialized
from min_agent.models import Message
from min_agent.sessions import SessionStore


def timestamp(value):
    if isinstance(value,datetime): result=value
    else: result=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def normalized(text):
    return re.sub(r'\s+',' ',str(text).casefold()).strip()


def _has_fact(text,fact):
    variants=fact if isinstance(fact,list) else [fact]
    return any(normalized(v) in normalized(text) for v in variants)


def build_approved_fixture(case,*,approval,probe=False,budget=2048):
    if approval!='approved_fixture': raise PermissionError('仅显式approved_fixture可模拟批准，不是生产默认')
    store=SessionStore();owner=uuid5(NAMESPACE_URL,'book-memory:'+case['test_id']);context=store.create(owner)
    memory=LongMemory(store)
    histories=sorted(case['conversation_histories'],key=lambda h:timestamp(h['timestamp']))
    roles=Counter(); units=0; replay=[]
    for number,h in enumerate(histories):
        at=timestamp(h['timestamp'])
        memory.clock=lambda at=at:at
        business=str(h.get('metadata',{}).get('business',case['category']))
        topic_id=str(uuid5(owner,'topic:'+business))
        origin=str(uuid5(owner,'session:'+str(h['conversation_id'])))
        def action(doc,clock):
            nonlocal units
            doc.topics.setdefault(topic_id,Topic(id=topic_id,user_id=str(owner),name=business,keywords=[business],created_at=at))
            for index,message in enumerate(h['messages']):
                role=message['role']
                if role not in {'user','assistant','tool'}: continue
                content=str(message.get('content',''))
                if not content: continue
                roles[role]+=1
                source_id=str(uuid5(owner,f'{origin}:{index}'))
                classification='DIRECT' if role=='user' else 'INFERRED'
                doc.evidence[source_id]=Evidence(id=source_id,user_id=str(owner),session_id=origin,
                    message_ref=index,role=role,observed_at=at,source_hash=sha256(content.encode()).hexdigest(),
                    excerpt_or_pointer=f'book://{case["test_id"]}/{origin}/{index}',classification=classification)
                # 长段按字符范围保留全部页；不把截断后的头部当完整记忆。
                for start in range(0,len(content),5000):
                    identity=str(uuid5(owner,f'memory:{origin}:{index}:{start}'))
                    text=f'[{role};session={h["conversation_id"]};range={start}:{min(start+5000,len(content))}] '+content[start:start+5000]
                    cost=3 if re.search(r'\b(must|never|deadline|cannot|allerg|expire|cancel)\w*\b|不要|必须|禁止|截止',text,re.I) else 1
                    item=Memory(id=identity,user_id=str(owner),session_id=origin,kind='episode',content=text,
                        evidence_type=classification,evidence_refs=[source_id],observed_at=at,effective_from=at,
                        created_at=at,updated_at=at,confirmed_at=at,confirmation_ref='approved_fixture:'+source_id,
                        topic_ids=[topic_id],decision_cost=cost)
                    doc.memories[identity]=[item]
                    edge_id=str(uuid5(owner,'edge:'+identity))
                    doc.edges[edge_id]=MemoryEdge(id=edge_id,user_id=str(owner),source_id=identity,target_id=topic_id,
                        type='BELONGS_TO',evidence_refs=[source_id],observed_at=at)
                    # 频率只用截至当前段已发生的相同原句，不偷看后续会话提及次数。
                    occurrences=sum(normalized(m.get('content',''))==normalized(content)
                        for previous in histories[:number+1] for m in previous['messages'])
                    for n in range(max(0,occurrences-1)):
                        doc.usages.append(dict(memory_id=identity,event_id=f'exact_historical_mention:{n}',at=at.isoformat()))
                    units+=1
        memory._write(context,action)
        if probe and number+1<len(histories):
            next_history=histories[number+1]
            text=next((m['content'] for m in next_history['messages'] if m['role']=='user'),'后续问题')
            query=Query(text=text[:20000],as_of=timestamp(next_history['timestamp']))
            view=memory.rank_for_core(context,query,budget=budget)
            doc=memory.document(context)
            available=all(doc.memories[i][-1].confirmed_at<=query.as_of for i in view['selected_ids'])
            replay.append(dict(after_session=number+1,before_session=number+2,available_units=units,
                               selected_ids=view['selected_ids'],no_future_memory=available,
                               quality='NOT_RUN'))
    return memory,context,dict(simulated_approval_ratio=1,approval='approved_fixture',
        representation='raw_source_episodes_not_semantic_extraction',units=units,roles=dict(roles),
        goal_labels='NOT_AVAILABLE',time_assumption='session timestamp is coarse availability; naive timestamps treated as UTC',
        replay=replay,last_time=timestamp(histories[-1]['timestamp']))


def evaluate_case(case,*,gold=None,budget=2048,approval='approved_fixture',policy=None):
    memory,context,info=build_approved_fixture(case,approval=approval,probe=True,budget=budget)
    at=info.pop('last_time')+timedelta(microseconds=1)
    query=Query(text=case['user_question'][:20000],as_of=at,top_k=8)
    pool={m.id:m for m in memory.records(context,as_of=at,scenario_ids=[])}
    pool_hash=sha256(serialized(sorted((m.id,m.content) for m in pool.values())).encode()).hexdigest()
    facts=(gold or {}).get('required_facts',[])
    result={}
    shared_search=memory.search(context,query)
    for strategy in ('NONE','RECENCY','FREQUENCY','GOAL_VALUE'):
        started=perf_counter()
        core=memory.rank_for_core(context,query,strategy=strategy,budget=budget,policy=policy)
        selected_text='\n'.join(pool[i].content.split('] ',1)[-1] for i in core['selected_ids'])
        hits=shared_search['hits'] if strategy!='NONE' else []
        retrieved_text='\n'.join(pool[h['memory_id']].content.split('] ',1)[-1] for h in hits)
        matches=[any(_has_fact(pool[h['memory_id']].content.split('] ',1)[-1],f) for f in facts) for h in hits]
        mrr=next((1/(i+1) for i,match in enumerate(matches) if match),0.0) if facts else 'NOT_RUN'
        result[strategy]=dict(pool_hash=pool_hash,core_budget=budget,selected_ids=core['selected_ids'],
            selected_versions={i:pool[i].version for i in core['selected_ids']},
            selected_bytes_upper_bound=core['bytes_upper_bound'],budget_unit=core['budget_unit'],
            core_fact_coverage=sum(_has_fact(selected_text,f) for f in facts)/len(facts) if facts else 'NOT_RUN',
            fact_recall_at_k=sum(_has_fact(retrieved_text,f) for f in facts)/len(facts) if facts else 'NOT_RUN',
            first_relevant_mrr=mrr,top_k_unique_ids=len({h['memory_id'] for h in hits}),
            selection_ms=round((perf_counter()-started)*1000,3),
            quality='NOT_RUN',provider_tokens='NOT_RUN',judge='NOT_RUN',llm_calls=0,
            selection_trace={k:v for k,v in core.items() if k not in {'view','scores'}},
            score_details=core['scores'])
    return dict(case_id=case['test_id'],category=case['category'],facts_annotated=len(facts),
        fixture=info,strategies=result,shared_retrieval_trace=shared_search['trace'],
        retrieval_budget_note='Core主比较的检索工具预算均为0；另行同条件报告同一Top8检索的证据指标，不混作Core或回答收益')


def inspect_private_corpus(root,*,review_output=None):
    """仅本地结构适配；输出中不保留标题、原文、路径或原始会话ID。"""
    root=Path(root).resolve()
    rows=[];digest=sha256();parse_mismatches=0;total_messages=0;total_chars=0;hints=[]
    for index in sorted(root.rglob('_index.json')):
        data=json.loads(index.read_text(encoding='utf-8-sig'))
        if not isinstance(data,list): raise ValueError('私有索引应为列表')
        for item in data:
            path=(index.parent/item['file']).resolve()
            if not path.is_relative_to(root): raise PermissionError('导出引用越出已授权目录')
            text=path.read_text(encoding='utf-8-sig')
            markers=re.findall(r'^##\s+.*?\d+.*?轮.*?(我|DeepSeek|ChatGPT)\s*$',text,re.M)
            expected=item.get('messages')
            if type(expected) is int and len(markers)!=expected: parse_mismatches+=1
            total_messages+=len(markers);total_chars+=len(text)
            digest.update(sha256(text.encode()).digest())
            available=timestamp(item['updated'])
            rows.append((available,len(text)))
            boundaries=list(re.finditer(r'^##\s+.*?\d+.*?轮.*?(我|DeepSeek|ChatGPT)\s*$',text,re.M))
            for position,match in enumerate(boundaries):
                if match.group(1)!='我': continue
                start=match.end();end=boundaries[position+1].start() if position+1<len(boundaries) else len(text)
                body=text[start:end]
                if re.search(r'记住|我(?:希望|喜欢|习惯|更倾向|不喜欢)|以后.{0,16}(?:请|不要)|先.{0,16}再',body):
                    hints.append(dict(source_path=str(path),source_hash=sha256(text.encode()).hexdigest(),
                        start=start,end=end,available_at=available.isoformat(),status='UNREVIEWED_RULE_HINT'))
    rows.sort()
    boundary=rows[int(len(rows)*0.7)][0] if rows else None
    split=sum(at<boundary for at,_ in rows) if boundary else 0
    if review_output:
        output=Path(review_output).resolve()
        project=Path(__file__).resolve().parents[3]
        if output.is_relative_to(project) or output.is_relative_to(root):
            raise PermissionError('私有评审指针必须存于项目和原始导出目录之外')
        with output.open('x',encoding='utf-8') as stream:
            json.dump(dict(status='UNREVIEWED_RULE_HINTS_NOT_FORMAL_MEMORY',source_contains_private_paths=True,
                           items=hints),stream,ensure_ascii=False,indent=2)
    return dict(conversations=len(rows),markdown_characters=total_chars,role_markers=total_messages,
        role_count_mismatch_files=parse_mismatches,corpus_sha256=digest.hexdigest(),
        rule_hint_count=len(hints),rule_hint_quality='NOT_RUN',review_contains='local pointers only; no copied excerpts',
        early_conversations=split,heldout_conversations=len(rows)-split,
        cutoff=rows[split-1][0].isoformat() if split else None,
        heldout_starts_at=boundary.isoformat() if boundary else None,
        time_assumption='whole conversation available at updated; no inferred per-message time',
        formal_memory_imports=0,private_external_requests=0,semantic_extraction='NOT_RUN',
        task_utility='NOT_RUN',personal_gold='NOT_RUN',
        reason='需要用户评审和额外私有数据调用授权；本轮只适配结构/时间切分，不自动写正式记忆')
