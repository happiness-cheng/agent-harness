"""通过真实模型适配器+MockTransport检验待授权接线，外部请求0。"""
import argparse
from argparse import Namespace
import importlib.util
import json
from pathlib import Path
import sys
import threading

import httpx

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--variant', choices=['baseline','candidate'], required=True)
parser.add_argument('--case-index', type=int, choices=range(3), required=True)
parser.add_argument('--out-name')
parser.add_argument('--diagnose', action='store_true')
parser.add_argument('--omit-usage', action='store_true')
args = parser.parse_args()
sys.path.insert(0, str(ROOT/('baseline-source' if args.variant=='baseline' else 'candidate-01/source')/'src'))
spec = importlib.util.spec_from_file_location('v11_live_driver', ROOT/'live_pair.py')
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)
module = live.fixture()
policy = module.ResearchPolicy(module.CASES[args.case_index])

from min_agent.llm import deepseek
from min_agent.models import Message, ToolCall

OriginalModel = deepseek.DeepSeekResponsesModel


def handle(request):
    if request.url.path=='/models':
        return httpx.Response(200, json={'data':[{'id':'deepseek-flash',
            'context_window':1048576, 'max_output_tokens':393216}]})
    assert request.url.path=='/responses' and request.method=='POST'
    body = json.loads(request.content)
    messages = []
    for item in body['input']:
        kind = item.get('type')
        if 'role' in item:
            content = item['content']
            if isinstance(content, list):
                content = ''.join(part.get('text','') for part in content)
            messages.append(Message(role=item['role'], content=content))
        elif kind=='function_call':
            messages.append(Message(role='assistant', content='', tool_calls=[ToolCall(
                id=item['call_id'], name=item['name'], arguments=json.loads(item['arguments']))]))
        elif kind=='function_call_output':
            messages.append(Message(role='tool', tool_call_id=item['call_id'], content=item['output']))
        else:
            raise AssertionError('离线协议未声明的输入类型')
    response = policy.generate(messages, tools=body.get('tools'))
    if response.finish_reason=='tool_calls':
        output = [{'type':'function_call','status':'completed','call_id':call.id,
            'name':call.name,'arguments':json.dumps(call.arguments,ensure_ascii=False)} for call in response.tool_calls]
    else:
        output = [{'type':'message','role':'assistant','status':'completed',
            'content':[{'type':'output_text','text':response.content}]}]
    result = {'status':'completed','model':'deepseek-flash','output':output}
    if not args.omit_usage:
        result['usage'] = {'input_tokens':100,'output_tokens':100,'total_tokens':200}
    return httpx.Response(200, json=result)


def mock_model(settings, **kwargs):
    model = OriginalModel(settings, transport=httpx.MockTransport(handle))
    model._v11_offline_simulation = True
    return model


deepseek.DeepSeekResponsesModel = mock_model
deepseek.DeepSeekSettings.from_credentials_file = classmethod(
    lambda cls, path, **kwargs:cls(api_key='synthetic-v11-not-a-secret', **kwargs))
out = ROOT/'offline-live-adapter-smoke'/(args.out_name or args.variant+'-'+str(args.case_index))
out.parent.mkdir(exist_ok=True)
diagnostics = []
def trace(frame, event, value):
    if event=='exception' and frame.f_code.co_filename.endswith(('application.py','model_context.py')):
        diagnostics.append({'function':frame.f_code.co_name,'line':frame.f_lineno,
                            'error_type':value[0].__name__,'message':str(value[1])[:160]})
    return trace
if args.diagnose:
    threading.settrace(trace)
try:
    status = live.worker(Namespace(variant=args.variant, case_index=args.case_index, out=out,
        credentials=ROOT.parents[2]/'synthetic-path-never-read.json', proxy=None))
finally:
    threading.settrace(None)
    (out.parent/(out.name+'-diagnostics.json')).write_text(json.dumps(diagnostics,indent=2),encoding='utf-8')
receipt = json.loads((out/'live-receipt.json').read_text(encoding='utf-8'))
assert status==(2 if args.omit_usage else 0) and receipt['backend']=='MOCK_TRANSPORT_FROZEN_IO'
assert receipt['generation_requests']==0 and not receipt['new_model_decision_tested']
if args.omit_usage:
    assert receipt['verdict']=='UNKNOWN' and receipt['adapter_attempts']==1
    assert receipt['provider_errors'] and not receipt['usage']
else:
    assert receipt['verdict']==('FAIL' if args.variant=='baseline' else 'PASS'), receipt
