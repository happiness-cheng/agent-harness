"""确认边界与权威文档；所有写入复用SessionStore同一锁和SQLite提交。"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import secrets
from uuid import uuid4, uuid5

from min_agent.long_memory.models import Candidate, Evidence, Goal, Memory, MemoryDocument, MemoryEdge, Scenario, Topic


def now_utc(): return datetime.now(timezone.utc)


class LongMemory:
    def __init__(self, sessions, *, clock=now_utc):
        self.sessions, self.clock = sessions, clock

    def document(self, context):
        return MemoryDocument.model_validate_json(json.dumps(self.sessions.owner_document(context)))

    def _write(self, context, action, *, build=False):
        result = []
        with self.sessions.control(context):
            if build and self.sessions.work_state(context).mode != 'build':
                raise PermissionError('正式Memory写入需要Build及定向确认')
            if build:
                task=self.sessions.snapshot(context).task
                if task is not None and task.status=='running': raise RuntimeError('运行中不允许变更正式Memory')
            def update(raw):
                doc = MemoryDocument.model_validate_json(json.dumps(raw))
                before = doc.model_dump(mode='json')
                result.append(action(doc, self.clock()))
                if doc.model_dump(mode='json') != before: doc.revision += 1
                return doc.model_dump(mode='json')
            self.sessions.owner_document(context, update=update)
        return result[0]

    @staticmethod
    def _event(doc, kind, identity, at, **fields):
        # 不记录原文或完整候选，删除时可擦除受控的全部语义副本。
        doc.events.append(dict(kind=kind, id=identity, at=at.isoformat(), **fields))

    def _source(self, context, index, at, classification):
        messages = self.sessions.snapshot(context).messages
        if type(index) is not int or not 0 <= index < len(messages):
            raise PermissionError('来源不在当前授权会话')
        message = messages[index]
        if message.role not in {'user', 'assistant', 'tool'} or (classification == 'DIRECT' and message.role != 'user'):
            raise PermissionError('助手/工具文字不能直接成为用户明确事实')
        digest = sha256(message.model_dump_json().encode()).hexdigest()
        identity = str(uuid5(context.owner_id, f'{context.session_id}:{index}:{digest}'))
        return Evidence(id=identity, user_id=str(context.owner_id), session_id=str(context.session_id),
            message_ref=index, role=message.role, observed_at=at, source_hash=digest,
            excerpt_or_pointer=f'session:{context.session_id}/message:{index}', classification=classification)

    def propose(self, context, content, *, source_indices, evidence_type='DIRECT', expires_at=None, **properties):
        if not isinstance(content, str) or not content.strip() or not source_indices:
            raise ValueError('候选需要内容和来源')
        permitted = {'scope', 'kind', 'effective_from', 'valid_until', 'topic_ids', 'scenario_ids',
                     'goal_ids', 'conflict_key', 'breadth', 'decision_cost', 'stable', 'supersedes'}
        if set(properties) - permitted: raise ValueError('候选字段不受支持')
        if evidence_type not in {'DIRECT', 'INFERRED'}: raise ValueError('无效证据类型')
        if evidence_type=='INFERRED': properties['lifecycle']='disputed'
        def action(doc, at):
            if at.tzinfo is None: raise ValueError('可信时钟必须包含时区')
            # 以Pydantic校验全部待确认字段，不允许model_construct绕过枚举/范围。
            checked = {k: v for k,v in properties.items() if k != 'supersedes'}
            Memory(id='validation', user_id=str(context.owner_id), session_id=str(context.session_id),
                   content=content, evidence_type=evidence_type, evidence_refs=[], observed_at=at,
                   created_at=at, updated_at=at, confirmed_at=at, confirmation_ref='validation',
                   **{'effective_from': at, **checked})
            for key, collection in [('topic_ids', doc.topics), ('scenario_ids', doc.scenarios), ('goal_ids', doc.goals)]:
                if any(identity not in collection for identity in properties.get(key, [])):
                    raise PermissionError('关联实体不属于当前用户')
            supersedes = properties.get('supersedes')
            if supersedes is not None and supersedes not in doc.memories:
                raise PermissionError('纠正目标不存在或不属于当前用户')
            payload = {'content': content.strip(), 'evidence_type': evidence_type,
                       **json.loads(json.dumps(properties, default=lambda x: x.isoformat()))}
            fingerprint = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if fingerprint in doc.suppressed_fingerprints:
                raise PermissionError('已遗忘的内容禁止自动重新提出')
            refs = []
            sources=[]
            for index in dict.fromkeys(source_indices):
                source = self._source(context, index, at, evidence_type)
                if source.source_hash in doc.blocked_source_hashes:
                    raise PermissionError('已删除来源不可自动重新提取；需要新的明确用户输入')
                sources.append(source)
                refs.append(source.id)
            for old in doc.candidates.values():
                if old.fingerprint != fingerprint: continue
                if old.status=='rejected': return old
                if old.session_id==str(context.session_id) and old.evidence_refs==refs and (
                        old.status=='confirmed' or old.expires_at>at): return old
            for source in sources:
                if source.id not in doc.evidence: doc.evidence[source.id]=source
            item = Candidate(id=str(uuid4()), user_id=str(context.owner_id), session_id=str(context.session_id),
                content=content.strip(), context=payload, evidence_refs=refs, fingerprint=fingerprint,
                expires_at=expires_at or at+timedelta(days=7), proposed_at=at, review_token=secrets.token_urlsafe(24))
            if item.expires_at.tzinfo is None or item.expires_at <= at: raise ValueError('候选有效期已过或无时区')
            doc.candidates[item.id] = item
            self._event(doc, 'candidate_proposed', item.id, at)
            return item
        return self._write(context, action)

    def list_pending(self, context):
        at = self.clock()
        return [c for c in self.document(context).candidates.values() if c.status == 'pending' and c.expires_at > at]

    def review(self, context, candidate_id):
        doc = self.document(context)
        item = doc.candidates.get(candidate_id)
        if item is None: raise PermissionError('候选不存在或不属于当前用户')
        evidence=[]
        from min_agent.sessions import ExecutionContext
        from uuid import UUID
        for identity in item.evidence_refs:
            source=doc.evidence[identity]
            origin=ExecutionContext(owner_id=context.owner_id,session_id=UUID(source.session_id))
            messages=self.sessions.snapshot(origin).messages
            message=messages[source.message_ref]
            if sha256(message.model_dump_json().encode()).hexdigest()!=source.source_hash:
                raise PermissionError('审核来源版本已变化')
            evidence.append({**source.model_dump(mode='json'),'review_excerpt':message.content[:1600],
                             'excerpt_truncated':len(message.content)>1600})
        return dict(candidate=item.model_dump(mode='json', exclude={'review_token'}), review_token=item.review_token,
                    evidence=evidence)

    def confirm(self, context, candidate_id, *, review_token):
        def action(doc, at):
            candidate = doc.candidates.get(candidate_id)
            if candidate is None or not isinstance(review_token, str) or not secrets.compare_digest(candidate.review_token, review_token):
                raise PermissionError('需要候选的精确定向审核凭据')
            if candidate.status == 'confirmed':
                return doc.memories[candidate.memory_id][-1]
            if candidate.status != 'pending' or candidate.expires_at <= at: raise PermissionError('候选未获有效批准')
            if any(doc.evidence[r].source_hash in doc.blocked_source_hashes for r in candidate.evidence_refs):
                raise PermissionError('来源已被遗忘，旧审核凭据失效')
            from min_agent.sessions import ExecutionContext
            from uuid import UUID
            for ref in candidate.evidence_refs:
                evidence=doc.evidence[ref]
                origin=ExecutionContext(owner_id=context.owner_id,session_id=UUID(evidence.session_id))
                original=self.sessions.snapshot(origin).messages[evidence.message_ref]
                if sha256(original.model_dump_json().encode()).hexdigest()!=evidence.source_hash:
                    raise PermissionError('审核后来源发生变化，请重新提出候选')
            properties = {k:v for k,v in candidate.context.items() if k not in {'content','evidence_type','supersedes'}}
            identity = str(uuid4())
            item = Memory.model_validate_json(json.dumps(dict(id=identity, user_id=str(context.owner_id),
                session_id=candidate.session_id, content=candidate.content, evidence_type=candidate.context['evidence_type'],
                evidence_refs=candidate.evidence_refs, observed_at=candidate.proposed_at.isoformat(),
                created_at=at.isoformat(), updated_at=at.isoformat(), confirmed_at=at.isoformat(),
                confirmation_ref=candidate.id, **{'effective_from': candidate.proposed_at.isoformat(), **properties})))
            if item.valid_until is not None and item.valid_until <= at: raise PermissionError('待写事实已经失效')
            supersedes = candidate.context.get('supersedes')
            if supersedes:
                previous = doc.memories[supersedes][-1]
                doc.memories[supersedes].append(previous.model_copy(update=dict(lifecycle='retired',
                    version=previous.version+1, updated_at=at, valid_until=at)))
                self._edge(doc, context, identity, supersedes, 'SUPERSEDES', candidate.evidence_refs, at)
            doc.memories[identity] = [item]
            doc.candidates[candidate.id] = candidate.model_copy(update=dict(status='confirmed', memory_id=identity))
            for kind, targets in [('BELONGS_TO',item.topic_ids),('APPLIES_IN',item.scenario_ids),
                                  ('ABOUT',item.goal_ids),('DERIVED_FROM',item.evidence_refs)]:
                for target in targets: self._edge(doc, context, identity, target, kind, item.evidence_refs, at)
            self._event(doc, 'memory_confirmed', identity, at, candidate_id=candidate.id)
            return item
        return self._write(context, action, build=True)

    @staticmethod
    def _edge(doc, context, source, target, kind, refs, at):
        identity = str(uuid4())
        edge = MemoryEdge(id=identity, user_id=str(context.owner_id), source_id=source, target_id=target,
                          type=kind, evidence_refs=refs, observed_at=at)
        doc.edges[identity] = edge
        return edge

    def reject(self, context, candidate_id):
        def action(doc, at):
            item = doc.candidates.get(candidate_id)
            if item is None or item.status != 'pending': raise PermissionError('候选不可拒绝')
            doc.candidates[item.id] = item.model_copy(update={'status':'rejected'})
            self._event(doc, 'candidate_rejected', item.id, at)
        return self._write(context, action)

    def correct(self, context, memory_id, content, **kwargs):
        return self.propose(context, content, supersedes=memory_id, **kwargs)

    def delete(self, context, memory_id):
        def action(doc, at):
            if memory_id in doc.tombstones:
                if memory_id not in doc.pending_erasure_ids: doc.pending_erasure_ids.append(memory_id)
                return
            if memory_id not in doc.memories: raise PermissionError('Memory不存在或不属于当前用户')
            removing={memory_id};derived_refs=set()
            # 包含从已追踪助手输出再次形成的候选/记忆，避免留下二级派生副本。
            while True:
                positions=set()
                for owner_context in self.sessions.contexts(context.owner_id):
                    snapshot=self.sessions.snapshot(owner_context)
                    for dependency in snapshot.extensions.get('long_memory_dependencies',[]):
                        if removing & set(dependency['memory_ids']):
                            positions.update((str(owner_context.session_id),i) for i in range(
                                dependency['start'],min(dependency['end'],len(snapshot.messages)))
                                if snapshot.messages[i].role!='user')
                derived_refs={r for r,e in doc.evidence.items() if (e.session_id,e.message_ref) in positions}
                expanded=removing|{identity for identity,versions in doc.memories.items()
                                  if any(derived_refs & set(m.evidence_refs) for m in versions)}
                expanded|={e.source_id for e in doc.edges.values() if e.type=='DERIVED_FROM'
                           and e.target_id in removing and e.source_id in doc.memories}
                if expanded==removing: break
                removing=expanded
            erased=set(derived_refs);affected_topics=set();affected_scenarios=set()
            for identity in removing:
                for item in doc.memories.pop(identity):
                    erased.update(item.evidence_refs);affected_topics.update(item.topic_ids)
                    affected_scenarios.update(item.scenario_ids)
            doc.blocked_source_hashes.extend(doc.evidence[r].source_hash for r in erased if r in doc.evidence)
            for identity, item in list(doc.candidates.items()):
                if (item.memory_id in removing or derived_refs & set(item.evidence_refs)
                        or (item.status=='pending' and item.context.get('supersedes') in removing)):
                    doc.suppressed_fingerprints.append(item.fingerprint)
                    erased.update(item.evidence_refs)
                    affected_topics.update(item.context.get('topic_ids',[]))
                    affected_scenarios.update(item.context.get('scenario_ids',[]))
                    del doc.candidates[identity]
            used = {r for versions in doc.memories.values() for m in versions for r in m.evidence_refs}
            used |= {r for c in doc.candidates.values() for r in c.evidence_refs}
            used |= {g.source_ref for versions in doc.goals.values() for g in versions}
            for identity in erased-used: doc.evidence.pop(identity, None)
            used_topics={t for versions in doc.memories.values() for m in versions for t in m.topic_ids}
            used_topics|={t for c in doc.candidates.values() for t in c.context.get('topic_ids',[])}
            used_scenarios={s for versions in doc.memories.values() for m in versions for s in m.scenario_ids}
            used_scenarios|={s for c in doc.candidates.values() for s in c.context.get('scenario_ids',[])}
            for identity in affected_topics-used_topics: doc.topics.pop(identity,None)
            for identity in affected_scenarios-used_scenarios: doc.scenarios.pop(identity,None)
            for identity, edge in list(doc.edges.items()):
                if (removing & {edge.source_id,edge.target_id} or edge.target_id in erased-used
                        or edge.target_id in (affected_topics-used_topics)|(affected_scenarios-used_scenarios)):
                    del doc.edges[identity]
            doc.tombstones.extend(sorted(removing))
            doc.pending_erasure_ids.extend(sorted(removing))
            doc.usages = [u for u in doc.usages if u['memory_id'] not in removing]
            for identity in sorted(removing): self._event(doc,'memory_deleted',identity,at)
        # 撤回先持久化；清除失败时留下待完成标记，后续模型请求会被门控而非泄露旧输出。
        with self.sessions.control(context):
            if any(self.sessions.snapshot(c).task is not None and self.sessions.snapshot(c).task.status=='running'
                   for c in self.sessions.contexts(context.owner_id)):
                raise RuntimeError('请先停止运行中的会话再删除Memory')
            result=self._write(context, action, build=True)
            self.reconcile_deletions(context)
            return result

    def reconcile_deletions(self,context):
        pending=self.document(context).pending_erasure_ids
        if not pending: return
        self.sessions.erase_memory_derivatives(context,pending)
        def clear(doc,at):
            doc.pending_erasure_ids=[i for i in doc.pending_erasure_ids if i not in pending]
        self._write(context,clear)

    def records(self, context, *, as_of, scenario_ids=None):
        if as_of.tzinfo is None: raise ValueError('as_of必须包含时区')
        doc = self.document(context)
        result = []
        for versions in doc.memories.values():
            past = [m for m in versions if m.updated_at <= as_of and m.confirmed_at <= as_of]
            if not past: continue
            m = past[-1]
            if (m.user_id != str(context.owner_id) or m.lifecycle not in {'active','disputed'}
                    or m.observed_at > as_of or m.effective_from > as_of
                    or (m.valid_until is not None and m.valid_until <= as_of)
                    or (m.scope == 'session' and m.session_id != str(context.session_id))): continue
            if scenario_ids is not None and m.scenario_ids and not set(m.scenario_ids)&set(scenario_ids): continue
            result.append(m)
        return result

    def register_topic(self, context, name, *, summary='', keywords=()):
        def action(doc, at):
            identity = str(uuid5(context.owner_id, 'topic:'+name))
            item = Topic(id=identity,user_id=str(context.owner_id),name=name,summary=summary,
                         keywords=list(keywords),created_at=at)
            if identity not in doc.topics: doc.topics[identity] = item
            return doc.topics[identity]
        return self._write(context,action)

    def register_scenario(self, context, label, *, conditions=()):
        def action(doc, at):
            identity = str(uuid5(context.owner_id, 'scenario:'+label))
            item = Scenario(id=identity,user_id=str(context.owner_id),label=label,conditions=list(conditions),created_at=at)
            if identity not in doc.scenarios: doc.scenarios[identity]=item
            return doc.scenarios[identity]
        return self._write(context,action)

    def link(self, context, source_id, target_id, kind):
        def action(doc, at):
            known = set(doc.memories)|set(doc.evidence)|set(doc.topics)|set(doc.scenarios)|set(doc.goals)
            if source_id not in known or target_id not in known: raise PermissionError('图端点未授权')
            if kind in {'GENERALIZES','EXCEPTION_OF'}:
                raise PermissionError('一般化/例外关系需要独立语义审核，v0.1不自动建立')
            return self._edge(doc,context,source_id,target_id,kind,[],at)
        return self._write(context,action,build=True)

    def search(self, context, query, **kwargs):
        from min_agent.long_memory.retrieval import search
        return search(self,context,query,**kwargs)

    def set_goal(self, context, content, *, source_index, priority=1, horizon='stage', lifecycle='active', effective_from=None):
        def action(doc, at):
            source=self._source(context,source_index,at,'DIRECT')
            identity=str(uuid5(context.owner_id,'goal:'+content))
            if identity in doc.goals: return doc.goals[identity][-1]
            item=Goal(id=identity,user_id=str(context.owner_id),content=content,priority=priority,
                horizon=horizon,lifecycle=lifecycle,effective_from=effective_from or at,
                observed_at=at,updated_at=at,source_ref=source.id)
            doc.evidence.setdefault(source.id,source)
            doc.goals[identity]=[item]
            self._event(doc,'goal_created',identity,at,source_id=source.id)
            return item
        return self._write(context,action,build=True)

    def update_goal(self, context, goal_id, *, source_index, **changes):
        if not changes or set(changes)-{'priority','lifecycle','horizon','effective_from'}:
            raise ValueError('只能明确修改目标优先级、生命周期、期限或生效时刻')
        def action(doc, at):
            if goal_id not in doc.goals: raise PermissionError('目标不属于当前用户')
            source=self._source(context,source_index,at,'DIRECT')
            previous=doc.goals[goal_id][-1]
            item=Goal.model_validate(previous.model_copy(update=dict(**changes,updated_at=at,
                observed_at=at,source_ref=source.id,version=previous.version+1)).model_dump())
            doc.evidence.setdefault(source.id,source)
            doc.goals[goal_id].append(item)
            self._event(doc,'goal_updated',goal_id,at,changed_fields=sorted(changes),source_id=source.id)
            return item
        return self._write(context,action,build=True)

    def goals(self, context, *, as_of):
        result={}
        for identity,versions in self.document(context).goals.items():
            past=[g for g in versions if g.observed_at<=as_of and g.updated_at<=as_of and g.effective_from<=as_of]
            if past: result[identity]=past[-1]
        return result

    def note_usage(self, context, memory_id, event_id):
        """可信调用方记录实际提供/使用事件，不接受模型自报次数或重复事件加权。"""
        def action(doc, at):
            if memory_id not in doc.memories: raise PermissionError('Memory不可用')
            if any(u['memory_id']==memory_id and u['event_id']==event_id for u in doc.usages): return
            doc.usages.append(dict(memory_id=memory_id,event_id=event_id,at=at.isoformat()))
        return self._write(context,action)

    def rank_for_core(self, context, query, **kwargs):
        from min_agent.long_memory.selection import rank_for_core
        return rank_for_core(self,context,query,**kwargs)

    def assemble_context(self, context, query, **kwargs):
        from min_agent.long_memory.selection import assemble_context
        return assemble_context(self,context,query,**kwargs)

    def remember(self, context, *, source_index, **properties):
        """可信用户入口显式记住的窄语法；不从引用/助手建议中猜测批准。"""
        import re
        messages=self.sessions.snapshot(context).messages
        if type(source_index) is not int or not 0<=source_index<len(messages):
            raise PermissionError('来源不在当前会话')
        message=messages[source_index]
        match=re.fullmatch(r'\s*(?:请)?(?:记住|remember)\s*[:：]\s*(\S[\s\S]*)',message.content,re.I)
        if message.role!='user' or match is None: raise PermissionError('需要用户明确的记住：内容指令')
        candidate=self.propose(context,match[1].strip(),source_indices=[source_index],**properties)
        review=self.review(context,candidate.id)
        return self.confirm(context,candidate.id,review_token=review['review_token'])
