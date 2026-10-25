"""实验预算审计：量度实际协议输入，预留下一次，已完成请求按usage结算。"""
from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace

import httpx

from min_agent.context import ContextLimit
from min_agent.llm.deepseek import DeepSeekResponsesModel
from min_agent.long_memory.answer_eval import encoded

SINGLE_LIMIT=32768
TOTAL_LIMIT=65536
OUTPUT_RESERVE=4096
FRAMING_RESERVE=4096


class BudgetBlocked(ContextLimit):
    def __init__(self,code):
        super().__init__(code);self.code=code


def request_breakdown(messages,tools):
    # 调用适配器本来的纯构建方法，不创建客户端、不读凭据、不访问网络。
    builder=SimpleNamespace(_provider='deepseek.responses',_settings=SimpleNamespace(model='deepseek-flash',reasoning_effort='high',max_output_tokens=OUTPUT_RESERVE))
    body=DeepSeekResponsesModel._build_body(builder,messages,tools)
    wire=httpx.Request('POST','https://api.deepseek.com/responses',json=body).content
    raw=[m.model_dump(mode='json') for m in messages]
    projections=[];memory_bytes=0;authority_bytes=0
    for m in messages:
        if m.role!='user':continue
        try:value=json.loads(m.content)
        except (ValueError,TypeError):continue
        if isinstance(value,dict) and 'application_state' in value:
            projections.append(m)
            memory_bytes+=len(encoded(value.get('untrusted_user_memory',{})))
            authority_bytes+=len(encoded(value['application_state']))
    systems=[m.content for m in messages if m.role=='system']
    system_hashes=[sha256(s.encode()).hexdigest() for s in systems]
    input_upper=len(wire)+FRAMING_RESERVE
    return dict(wire_body_bytes=len(wire),wire_input_bytes=len(encoded(body['input'])),
        wire_tool_schema_bytes=len(encoded(body['tools'])),wire_tool_count=len(body['tools']),
        internal_message_bytes=len(encoded(raw)),internal_tool_schema_bytes=len(encoded(tools or [])),
        system_text_bytes=sum(len(s.encode()) for s in systems),system_count=len(systems),
        duplicate_system_count=len(systems)-len(set(system_hashes)),system_hashes=system_hashes,
        history_tool_message_bytes=len(encoded([m for m in raw if m['role']=='tool'])),
        history_assistant_message_bytes=len(encoded([m for m in raw if m['role']=='assistant'])),
        dynamic_projection_count=len(projections),
        current_projection_message_bytes=len(projections[-1].content.encode()) if projections else 0,
        old_projection_message_bytes=sum(len(m.content.encode()) for m in projections[:-1]),
        current_memory_projection_bytes=memory_bytes,authority_state_bytes=authority_bytes,
        native_state_bytes=sum(len(encoded(m.provider_state.model_dump(mode='json'))) for m in messages if m.provider_state),
        logical_calls_also_in_native_bytes=sum(len(encoded(m.tool_calls and [c.model_dump() for c in m.tool_calls] or [])) for m in messages if m.provider_state),
        wire_function_calls=sum(i.get('type')=='function_call' for i in body['input']),
        input_upper_units=input_upper,output_reserve=OUTPUT_RESERVE,framing_reserve=FRAMING_RESERVE,
        request_upper_units=input_upper+OUTPUT_RESERVE,
        legacy_request_upper_units=len(encoded(raw))+len(encoded(tools or []))+8192,
        wire_body_sha256=sha256(wire).hexdigest(),
        component_note='component sizes overlap; only wire_body_bytes is the serialized request total')


class BudgetAccount:
    def __init__(self):
        self.single_limit=SINGLE_LIMIT;self.total_limit=TOTAL_LIMIT
        self.actual_tokens=0;self.pending_units=0;self.stop_code=None;self.events=[]
        self._input_bound=0
    def block(self,code):
        self.stop_code=code
        if self.events:self.events[-1]['gate']=code
        raise BudgetBlocked(code)
    def begin(self,report):
        if self.stop_code:raise BudgetBlocked(self.stop_code)
        if self.pending_units:self.block('pending_request_not_settled')
        row=deepcopy(report)
        row.update(attempt=len(self.events)+1,actual_spent_before=self.actual_tokens,
            single_limit=self.single_limit,cumulative_limit=self.total_limit,
            cumulative_if_reserved=self.actual_tokens+report['request_upper_units'],gate='allowed')
        self.events.append(row)
        if report['request_upper_units']>self.single_limit:self.block('single_request_limit')
        if self.actual_tokens+report['request_upper_units']>self.total_limit:self.block('cumulative_token_limit')
        self.pending_units=report['request_upper_units'];self._input_bound=report['input_upper_units']
    def settle(self,usage):
        if not self.pending_units:self.block('no_request_reservation')
        if not isinstance(usage,dict) or any(type(usage.get(k)) is not int or usage[k]<0 for k in ('input_tokens','output_tokens')):
            self.block('usage_unknown')
        actual=usage['input_tokens']+usage['output_tokens']
        if usage['input_tokens']>self._input_bound or usage['output_tokens']>OUTPUT_RESERVE or actual>self.pending_units:
            self.block('usage_exceeds_reservation')
        self.actual_tokens+=actual
        self.events[-1].update(actual_input_tokens=usage['input_tokens'],actual_output_tokens=usage['output_tokens'],
            actual_spent_after=self.actual_tokens,released_upper_units=self.pending_units-actual,
            unused_output_reserve=OUTPUT_RESERVE-usage['output_tokens'])
        self.pending_units=0
