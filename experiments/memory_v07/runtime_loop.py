"""v07实验闭环；只更换预算会计，保留原Runtime和只读取证契约。"""
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
from time import perf_counter

from min_agent.budget import BudgetLedger,BudgetLimits
from min_agent.context import ContextLimit
from experiments.memory_v07.budget import BudgetAccount,BudgetBlocked,request_breakdown
from min_agent.long_memory.active_evidence import active_registry,ProjectedRuntime,validate_answer,visible_ids
from min_agent.long_memory.evidence_budget import INPUT_RATE,OUTPUT_RATE


class AccountedModel:
    def __init__(self,provider,ledger,*,label=None):
        self.provider=provider;self.ledger=ledger;self.label=label or {}
        self.account=BudgetAccount();self.delivered=[];self.calls=0;self.stop_reason=None
    def generate(self,messages,*,tools=None):
        report=request_breakdown(messages,tools or [])
        try:self.account.begin(report)
        except BudgetBlocked as error:
            self.stop_reason=error.code
            self.ledger.record(dict(event='budget_blocked',**self.label,diagnostic=self.account.events[-1]))
            raise
        self.ledger.record(dict(event='budget_preflight',**self.label,diagnostic=self.account.events[-1]))
        try:fee=self.ledger.reserve(report['input_upper_units'])
        except ContextLimit:
            code=self.ledger.stop_reason or ('external_call_limit' if self.ledger.calls>=self.ledger.max_requests else 'fee_limit')
            self.stop_reason=code;self.account.block(code)
        before=len(self.provider.usage);started=perf_counter();self.calls+=1
        safe=[m.model_dump(mode='json',exclude={'provider_state'}) for m in messages]
        import json
        reference=json.loads(messages[-1].content).get('untrusted_user_memory',{})
        request=dict(number=self.ledger.calls,**self.label,messages=safe,tools=tools or [],
            reference=deepcopy(reference),budget=deepcopy(self.account.events[-1]),delivery_state='provider_call_started')
        self.delivered.append(request);self.ledger.record(dict(event='request',**request))
        try:response=self.provider.generate(messages,tools=tools or [])
        except Exception:
            self.stop_reason='provider_error_no_retry';self.ledger.stop_reason=self.stop_reason
            self.ledger.record(dict(event='request_failed',number=self.ledger.calls,**self.label))
            self.account.block(self.stop_reason)
        request['delivery_state']='response_received'
        usage=self.provider.usage[-1] if len(self.provider.usage)>before else None
        row=dict(number=self.ledger.calls,**self.label,raw_answer=response.content,finish_reason=response.finish_reason,
            tool_calls=[c.model_dump(mode='json') for c in response.tool_calls],usage=usage,
            latency_ms=(perf_counter()-started)*1000,cost_peak_usd=None)
        if isinstance(usage,dict) and all(type(usage.get(k)) is int and usage[k]>=0 for k in ('input_tokens','output_tokens')):
            cost=(Decimal(usage['input_tokens'])*INPUT_RATE+Decimal(usage['output_tokens'])*OUTPUT_RATE)/Decimal(1_000_000)
            row['cost_peak_usd']=float(cost);self.ledger.cost+=cost
        self.ledger.rows.append(row);self.ledger.record(dict(event='response',**row))
        try:self.account.settle(usage)
        except BudgetBlocked as error:
            self.stop_reason=error.code;self.ledger.stop_reason=error.code
            self.ledger.record(dict(event='budget_blocked',**self.label,diagnostic=self.account.events[-1]))
            raise
        if Decimal(str(row['cost_peak_usd']))>fee:
            self.stop_reason='fee_exceeds_reservation';self.ledger.stop_reason=self.stop_reason
            self.account.block(self.stop_reason)
        self.ledger.record(dict(event='usage_settled',**self.label,diagnostic=self.account.events[-1]))
        return response


def run_active_v07(memory,context,query,*,provider,ledger,max_model_calls=3,max_tool_calls=4,label=None):
    registry,state=active_registry(memory,context,query,budget=4096)
    limits=BudgetLimits(model_calls=max_model_calls,tool_calls=max_tool_calls)
    runtime_ledger=BudgetLedger(limits);segment=runtime_ledger.start_segment(limits)
    model=AccountedModel(provider,ledger,label=label)
    runtime=ProjectedRuntime(model,registry,budget=segment,query=query,view_builder=registry.memory_view)
    started=perf_counter();result=runtime.run_session(query.text,store=memory.sessions,context=context)
    raw=result.response.content if result.status=='completed' and result.response else None
    reason=model.stop_reason
    if reason is None and result.error_code=='budget_exhausted':
        failed=[e for e in runtime.trace.events if e.error_code=='budget_exhausted']
        reason='tool_call_limit' if any(e.operation=='tool' for e in failed) else 'model_call_limit'
    if reason is None and result.error_code:reason=result.error_code
    actual=model.delivered[-1]['reference'] if model.delivered else {}
    return dict(runtime_status=result.status,error_code=result.error_code,stop_reason=reason,
        model_budget_attempts=runtime_ledger.usage.model_calls,tool_calls=runtime_ledger.usage.tool_calls,
        provider_calls=model.calls,actual_tokens=model.account.actual_tokens,
        pending_upper_units=model.account.pending_units,budget_events=model.account.events,
        raw_answer=raw,answer_sha256=sha256(raw.encode()).hexdigest() if raw else None,
        validation=validate_answer(raw,visible_ids(actual)),delivered_requests=model.delivered,
        last_delivered_reference=actual,prepared_reference=state['view'],tool_events=state['tool_events'],
        runtime_latency_ms=(perf_counter()-started)*1000,
        trace=[e.model_dump(mode='json') for e in runtime.trace.events])
