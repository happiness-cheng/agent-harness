"""每次生成前持久预留；费用不确定时不释放预留，非账单/余额接口。"""
import json
from math import ceil,isfinite
from uuid import uuid4
from min_agent.evaluation.journal import JsonDocument


def micros(value):
    if type(value) not in {int,float} or not isfinite(value) or value<0:raise ValueError('费用必须为有限非负数')
    return ceil(value*1_000_000)


class CostLedger(JsonDocument):
    def __init__(self,path,*,limit,input_rate=.3,output_rate=1.2):
        super().__init__(path)
        if not self.path.exists():
            with self.transaction():
                if not self.path.exists():self.write(dict(limit=micros(limit)/1e6,spent_upper=0,reservations={},events=[],
                    input_rate=input_rate,output_rate=output_rate,currency='USD',rate_source='configured_peak_rates_2026-10-26'))
        if micros(self.read()['limit'])!=micros(limit):raise ValueError('不能重启时重置/扩大费用账本')
    def reserve(self,amount):
        amount=micros(amount)
        with self.transaction():
            data=self.read();used=micros(data['spent_upper'])+sum(micros(v) for v in data['reservations'].values())
            if used+amount>micros(data['limit']):raise PermissionError('费用预算不足，未启动生成')
            ticket=uuid4().hex;data['reservations'][ticket]=amount/1e6
            data['events'].append(dict(event='reserved_before_generation',ticket=ticket,usd_upper=amount/1e6));self.write(data)
            return ticket
    def settle(self,ticket,usage):
        with self.transaction():
            data=self.read();reserved=data['reservations'].pop(ticket)
            if isinstance(usage,dict) and type(usage.get('input_tokens')) is int and type(usage.get('output_tokens')) is int:
                charged=micros((usage['input_tokens']*data['input_rate']+usage['output_tokens']*data['output_rate'])/1e6)/1e6
                known=True
            else:charged=reserved;known=False
            data['spent_upper']=(micros(data['spent_upper'])+micros(charged))/1e6
            data['events'].append(dict(event='settled_usage_upper' if known else 'unknown_kept_full_reserve',ticket=ticket,usd_upper=charged))
            data['breach']=micros(data['spent_upper'])>micros(data['limit']);self.write(data)
            if data['breach']:raise PermissionError('费用观测超出配置上界，必须停止并人工核对费率')


class CostBoundedModel:
    def __init__(self,model,ledger):self.model,self.ledger=model,ledger
    def __getattr__(self,name):return getattr(self.model,name)
    def generate(self,messages,*,tools=None):
        body=self.model._build_body(messages,tools)
        rates=self.ledger.read();inputs=len(json.dumps(body,ensure_ascii=False).encode())+2048
        amount=(inputs*rates['input_rate']+body['max_output_tokens']*rates['output_rate'])/1e6
        ticket=self.ledger.reserve(amount);before=len(self.model.usage)
        try:response=self.model.generate(messages,tools=tools)
        except Exception:
            self.ledger.settle(ticket,usage=None);raise
        usage=self.model.usage
        self.ledger.settle(ticket,usage=usage[-1] if len(usage)>before else None)
        return response
