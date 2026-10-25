"""实验调用门禁：发送前预留费用和Token字节上界；用量未知后停止。"""
from decimal import Decimal
from hashlib import sha256
from time import perf_counter
import math

from min_agent.context import ContextLimit
from min_agent.long_memory.answer_eval import encoded

INPUT_RATE=Decimal('.30')
OUTPUT_RATE=Decimal('1.20')


class CallLedger:
    def __init__(self,*,max_requests,max_cost_usd,journal=None):
        if type(max_requests) is not int or max_requests<1:raise ValueError('请求上限无效')
        if type(max_cost_usd) not in (int,float) or not math.isfinite(max_cost_usd) or max_cost_usd<=0:
            raise ValueError('费用上限无效')
        self.max_requests=max_requests;self.cap=Decimal(str(max_cost_usd))
        self.calls=0;self.reserved=Decimal(0);self.cost=Decimal(0);self.rows=[]
        self.stop_reason=None;self.journal=journal
    def record(self,event):
        if self.journal:self.journal(event)
    def reserve(self,input_units):
        fee=(Decimal(input_units)*INPUT_RATE+Decimal(4096)*OUTPUT_RATE)/Decimal(1_000_000)
        if self.stop_reason or self.calls>=self.max_requests or self.reserved+fee>self.cap:
            raise ContextLimit('请求或费用硬限；不发送')
        self.calls+=1;self.reserved+=fee
        self.record(dict(event='request_reserved',number=self.calls,reserved_usd=float(fee)))
        return fee


class MeteredModel:
    def __init__(self,provider,ledger,*,total_units=65536,label=None):
        self.provider=provider;self.ledger=ledger;self.total_units=total_units
        self.reserved_units=0;self.label=label or {}
    def generate(self,messages,*,tools=None):
        payload=[m.model_dump(mode='json') for m in messages]
        input_units=len(encoded(payload))+len(encoded(tools or []))+4096
        units=input_units+4096
        if units>32768 or self.reserved_units+units>self.total_units:
            raise ContextLimit('单次或累计上下文上限；不发送')
        fee=self.ledger.reserve(input_units);self.reserved_units+=units
        start=perf_counter();before=len(self.provider.usage)
        request_number=self.ledger.calls
        self.ledger.record(dict(event='request',number=request_number,**self.label,
            request_sha256=sha256(encoded(dict(messages=payload,tools=tools or []))).hexdigest(),
            messages=[m.model_dump(mode='json',exclude={'provider_state'}) for m in messages],tools=tools or [],
            input_units_upper=input_units))
        try:response=self.provider.generate(messages,tools=tools or [])
        except Exception:
            self.ledger.stop_reason='provider_error_no_retry'
            self.ledger.record(dict(event='request_failed',number=request_number,**self.label))
            raise ContextLimit('提供商请求失败；本批停止，无重试') from None
        row=dict(number=request_number,**self.label,raw_answer=response.content,
                 finish_reason=response.finish_reason,tool_calls=[c.model_dump(mode='json') for c in response.tool_calls],
                 latency_ms=(perf_counter()-start)*1000,usage=None,cost_peak_usd=None)
        usage=self.provider.usage[-1] if len(self.provider.usage)>before else None
        if usage is None or any(type(usage.get(k)) is not int or usage[k]<0 for k in ('input_tokens','output_tokens')):
            self.ledger.stop_reason='usage_unknown'
        else:
            cost=(Decimal(usage['input_tokens'])*INPUT_RATE+Decimal(usage['output_tokens'])*OUTPUT_RATE)/Decimal(1_000_000)
            row.update(usage=usage,cost_peak_usd=float(cost));self.ledger.cost+=cost
            if cost>fee or usage['input_tokens']>input_units or usage['output_tokens']>4096:
                self.ledger.stop_reason='usage_exceeds_reservation'
        self.ledger.rows.append(row);self.ledger.record(dict(event='response',**row))
        if self.ledger.stop_reason:raise ContextLimit('实际用量未知或超预留；本批停止')
        return response
