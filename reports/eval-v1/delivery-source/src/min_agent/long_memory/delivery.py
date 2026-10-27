"""生产只读取证：基于已核验v08，绑定真实Registry及最终Provider边界。"""

from collections import defaultdict

from copy import deepcopy

from hashlib import sha256

import json

from min_agent.long_memory.delivery_support import (ReadEvidence, track_dependencies, encoded, local_bindings, requested_attributes, body, keywords, decorate_entry)

from min_agent.long_memory.integration import SearchParameters, TopicParameters

from min_agent.long_memory.models import Query

from min_agent.long_memory.retrieval import eligible_for_query

from min_agent.tools.registry import ToolDefinition

def digest(value):
    return sha256(encoded(value)).hexdigest()

class EvidenceSession:
    def __init__(self, memory, context, query, budget):
        if type(budget) is not int or budget < 0:
            raise ValueError('预算必须为非负整数')
        self.memory, self.context, self.query, self.budget = memory, context, query.model_copy(deep=True), budget
        self.discovered_ids, self.search_ids = set(), set()
        self.requested_ids, self.loaded_ids = set(), set()
        self.tool_events, self.receipts = [], []
        self.view, self.selected_ids, self.omitted = {}, [], []
        self.feedback_fits = True
        self.stop_reason = None

    def legal(self):
        self.memory.reconcile_deletions(self.context)
        return {m.id: m for m in eligible_for_query(self.memory, self.context, self.query)[0]}

    def entry(self, item):
        text = body(item)
        return decorate_entry(self.memory, self.context, self.query, item, dict(
            source=item.id, time=item.observed_at.isoformat(), scene=item.session_id,
            scenario_ids=item.scenario_ids, version=item.version,
            evidence_type=item.evidence_type, evidence=item.evidence_refs,
            valid_from=item.effective_from.isoformat(), valid_until=item.valid_until.isoformat() if item.valid_until else None,
            quotes=[dict(start=0, end=len(text), text=text)]))

    def read(self, memory_ids):
        ids = set(memory_ids)
        self.requested_ids.update(ids)
        pool = self.legal()
        if not ids <= self.discovered_ids or not ids <= pool.keys():
            self.tool_events.append(dict(name='read_long_memory_evidence', requested=sorted(ids),
                                         loaded=[], status='unavailable'))
            raise PermissionError('ID未披露或当前不可读；不返回正文')
        # 已从合法存储读取并校验，仍不代表模型已收到。
        for identity in ids:
            self.entry(pool[identity])
        self.loaded_ids.update(ids)
        self.tool_events.append(dict(name='read_long_memory_evidence', requested=sorted(ids),
                                     loaded=sorted(ids), status='loaded_not_delivered'))
        return json.dumps(dict(requested_ids=sorted(ids), loaded_ids=sorted(ids),
            delivery='loaded_not_delivered', next='consult_next_memory_delivery_for_budget_omissions'))

    def priority(self, item, entry):
        text = body(item)
        wanted = set(requested_attributes(self.query.text))
        bound = {b['attribute'] for b in local_bindings(text)
                 if b['status'] in {'asserted', 'conditional', 'negated'}} & wanted
        overlap = len(keywords(self.query.text) & keywords(text))
        # 固定分层，非新权重实验：属性/词项相关性，明确search后读取，来源，成本。
        # 来源优先级只决定同等相关证据的呈现，不把用户/助手话语升级为现实核实。
        source = 1 if entry['role'] == 'user' else 0
        return (-len(bound), -overlap, -int(item.id in self.search_ids), -source, len(encoded(entry)))

    def projection(self, entries, pool, index):
        selected = {e['source'] for e in entries}
        omitted = [dict(id=i, reason='budget' if i in self.loaded_ids and i in pool else 'unavailable_or_not_loaded')
                   for i in sorted(self.requested_ids - selected)]
        base = dict(reference_only=True, current_request_takes_precedence=True,
                    coverage='partial_view_not_exhaustive_history', evidence=entries)
        if not self.requested_ids:
            base['root_index'] = index
        feedback = dict(state='prepared_not_delivered', requested=len(self.requested_ids),
                        loaded=len(self.loaded_ids & pool.keys()), selected=len(selected),
                        omitted=len(omitted), history_absence_not_established=True)
        # 全量遗漏在本地收据；上下文只展示最多4个ID，且元数据也计入原预算。
        for n in range(min(4, len(omitted)), -1, -1):
            proposal = dict(base, memory_delivery=dict(feedback, omitted_items=omitted[:n],
                                                     omitted_details_not_shown=len(omitted)-n))
            if len(encoded(proposal)) <= self.budget:
                return proposal, omitted
        return None, omitted

    def build(self, messages):
        pool = self.legal()
        topics = {t for m in pool.values() for t in m.topic_ids}
        doc = self.memory.document(self.context)
        index = []
        if not self.requested_ids:
            for topic in sorted(doc.topics.values(), key=lambda t: (t.name, t.created_at)):
                if topic.id not in topics or topic.created_at > self.query.as_of:
                    continue
                addition = dict(id=topic.id, name=topic.name)
                if self.projection([], pool, index+[addition])[0] is not None:
                    index.append(addition)
        grouped = defaultdict(list)
        for identity in self.loaded_ids & pool.keys():
            item = pool[identity]
            entry = self.entry(item)
            # 不同情景不按文本合并；不可区分的同优先级证据成组保留/遗漏，避免用ID打破平局。
            semantic = {k:v for k,v in entry.items() if k not in {'source', 'scene', 'evidence', 'scenario_ids'}}
            key = (self.priority(item, entry), digest(semantic))
            grouped[key].append(entry)
        entries = []
        for key in sorted(grouped):
            group = grouped[key]
            candidate, _ = self.projection(entries+group, pool, index)
            if candidate is not None:
                entries.extend(group)
        # ID排序只规范化已选集合的序列化，不参与集合选择。
        entries.sort(key=lambda e: e['source'])
        view, omitted = self.projection(entries, pool, index)
        self.feedback_fits = view is not None
        self.view = view or {}
        self.selected_ids = [e['source'] for e in entries] if view else []
        self.omitted = omitted
        return self.view or None

    def before_send(self, messages):
        if not self.feedback_fits:
            self.stop_reason = 'evidence_feedback_limit'
            raise PermissionError('预算连最小遗漏反馈都无法容纳；不发送空白误导上下文')
        actual = json.loads(messages[-1].content)['untrusted_user_memory']
        if encoded(actual) != encoded(self.view):
            self.stop_reason = 'evidence_projection_mismatch'
            raise PermissionError('最终请求与已准备视图不一致')
        pool = self.legal()
        for entry in actual.get('evidence', []):
            item = pool.get(entry['source'])
            if item is None or digest(self.entry(item)) != digest(entry):
                self.stop_reason = 'evidence_changed_before_send'
                raise PermissionError('证据在装配后已变更或撤回；不发送旧正文')
        doc = self.memory.document(self.context)
        allowed_topics = {t for m in pool.values() for t in m.topic_ids}
        shown_topics = set()
        for index in actual.get('root_index', []):
            topic = doc.topics.get(index['id'])
            if topic is None or topic.id not in allowed_topics or topic.created_at > self.query.as_of or topic.name != index['name']:
                self.stop_reason = 'evidence_changed_before_send'
                raise PermissionError('索引来源在装配后已变更或撤回')
            shown_topics.add(topic.id)
        metadata_support = {m.id for m in pool.values() if shown_topics & set(m.topic_ids)}
        # 保守追踪可能暴露的依赖；这不将attempt改称delivered。
        track_dependencies(self.memory, self.context, set(self.selected_ids) | metadata_support)
        receipt = dict(number=len(self.receipts)+1, status='attempted',
                       requested=sorted(self.requested_ids), loaded=sorted(self.loaded_ids & pool.keys()),
                       selected=list(self.selected_ids), delivered=[], omitted=deepcopy(self.omitted),
                       reference_sha256=digest(actual), bytes=len(encoded(actual)), budget=self.budget,
                       metadata_support_ids=sorted(metadata_support))
        self.receipts.append(receipt)
        return receipt

    def after_send(self, receipt):
        receipt.update(status='delivered', delivered=list(receipt['selected']))

    def failed_send(self, receipt):
        receipt.update(status='delivery_unknown', delivered=[])

def bind_active_memory(registry, memory, context, query, *, budget=4096):
    state = EvidenceSession(memory, context, query, budget)
    query = state.query
    for name in ('search_long_memory', 'read_long_memory_topic'):
        def handler(_name=name, **kwargs):
            # 场景只能收窄当前任务的可信查询范围，不允许覆盖为其他情景。
            requested = set(kwargs.get('scenario_ids', []))
            allowed = set(query.scenario_ids)
            if allowed and requested and not requested <= allowed:
                raise PermissionError('不能扩大任务场景范围')
            kwargs['scenario_ids'] = list(requested or allowed)
            # 复用原search/records，用任务冻结时点查询，不让后来的全局clock污染候选。
            current = Query.model_validate(query.model_copy(update={'scenario_ids':kwargs['scenario_ids']}).model_dump())
            if _name == 'search_long_memory':
                current = Query.model_validate(current.model_copy(update=dict(text=kwargs['question'], top_k=kwargs.get('top_k',8))).model_dump())
                found = memory.search(context, current)
                memory.sessions.set_extension(context, 'long_memory_last_search', found['trace'])
                result = dict(memory_ids=[hit['memory_id'] for hit in found['hits']])
            else:
                topic = memory.document(context).topics.get(kwargs['topic_id'])
                if topic is None or topic.created_at > query.as_of:
                    raise PermissionError('主题当前不可读')
                records = [m for m in eligible_for_query(memory,context,current)[0] if topic.id in m.topic_ids]
                records.sort(key=lambda m:(-m.observed_at.timestamp(),m.id))
                result = dict(topic_id=topic.id, memory_ids=[m.id for m in records[:kwargs.get('limit',8)]])
            legal = state.legal()
            ids = [i for i in result['memory_ids'] if i in legal]
            result['memory_ids'] = ids
            state.discovered_ids.update(ids)
            if _name == 'search_long_memory':
                state.search_ids.update(ids)
            state.tool_events.append(dict(name=_name, arguments=kwargs, returned_ids=ids))
            return json.dumps(result, ensure_ascii=False)
        schema = SearchParameters if name == 'search_long_memory' else TopicParameters
        registry.register(ToolDefinition(name, '只读检索任务时点与场景内已确认的记忆索引；返回ID不代表正文已读取或送达。', schema, handler))
    registry.register(ToolDefinition('read_long_memory_evidence',
        '只读已披露的合法Memory ID。返回requested/loaded，绝不表示正文已送达。'
        '正文及有界预算遗漏反馈在下一轮动态上下文；局部遗漏不能证明完整历史不存在。'
        '实际使用记忆时用[memory:正文source字段的ID]引用，不引用搜索、目录或omitted列表中的ID。', ReadEvidence, state.read))
    registry._long_memory_builder = state.build
    registry._memory_delivery = state
    return state


class MemoryDeliveryModel:
    """只包装实际任务Provider；上下文压缩/研究子调用不冒充证据送达。"""
    def __init__(self, model, state):
        self.model, self.state = model, state

    def _save(self, receipt):
        snapshot = self.state.memory.sessions.snapshot(self.state.context)
        value = dict(deepcopy(receipt), task_id=str(snapshot.task.task_id) if snapshot.task else None)
        self.state.memory.sessions.set_extension(self.state.context, 'long_memory_delivery', value)

    def generate(self, messages, *, tools=None):
        from min_agent.context import ContextLimit
        from min_agent.models import ModelResponse
        try:
            receipt = self.state.before_send(messages)
        except (PermissionError, ValueError, KeyError) as error:
            raise ContextLimit('Memory投影已变更或反馈预算不足，不发送') from error
        self._save(receipt)
        try:
            response = self.model.generate(messages, tools=tools)
        except Exception:
            self.state.failed_send(receipt)
            self._save(receipt)
            raise
        self.state.after_send(receipt)
        self._save(receipt)
        if isinstance(response, ModelResponse) and response.finish_reason == 'stop':
            check = citation_check(response.content or '', self.state.view)
            self.state.memory.sessions.set_extension(self.state.context, 'long_memory_citation', check)
            if check['status'] == 'invalid':
                # 不发布伪引用，不为修引用额外调用模型；走原Runtime失败/事实检查点路径。
                return ModelResponse(finish_reason='error')
        return response


def citation_check(content, view):
    import re
    visible = {i for e in view.get('evidence', []) for i in [e['source'], *e['evidence']]}
    ids = re.findall(r'\[memory:([^\]\s]+)\]', content)
    try:
        data = json.loads(content)
        if isinstance(data, dict) and 'used_evidence_ids' in data:
            value = data['used_evidence_ids']
            if not isinstance(value, list) or any(not isinstance(i, str) for i in value):
                return dict(status='invalid', unsupported_ids=[], reason='invalid_citation_type')
            ids += value
    except (ValueError, TypeError):
        pass
    unknown = sorted(set(ids) - visible)
    return dict(status='invalid' if unknown else 'valid' if ids else 'not_provided',
                cited_ids=sorted(set(ids)), unsupported_ids=unknown,
                semantic_support='not_proven_by_id_check')


def read_delivered_evidence(memory, context, identity, scenario_ids=()):
    """仅供本机用户核对最近送达的来源，读取时再次检查有效性与撤回。"""
    receipt = memory.sessions.extension(context,'long_memory_delivery',{}) or {}
    if receipt.get('status') != 'delivered': raise PermissionError('没有确认送达的证据')
    state = EvidenceSession(memory,context,Query(text='核对已送达证据',as_of=memory.clock(),scenario_ids=list(scenario_ids)),4096)
    for candidate in receipt.get('delivered',[]):
        item = state.legal().get(candidate)
        if item is not None and identity in {item.id,*item.evidence_refs}:
            return state.entry(item)
    raise PermissionError('来源未送达或当前已不可用')
