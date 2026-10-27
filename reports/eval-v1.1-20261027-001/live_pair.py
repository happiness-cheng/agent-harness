"""待授权的固定来源/真实模型成对实验；默认仅打印计划，不读取凭据。"""
import argparse
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT/'candidate-01/workspace/tests/integration/test_research_last_read.py'
SOURCES = {'baseline':ROOT/'baseline-source', 'candidate':ROOT/'candidate-01/source'}
PLAN = {'provider':'DeepSeek', 'model':'deepseek-flash', 'reasoning_effort':'high',
    'runs_per_version':3, 'versions':2, 'model_requests_per_run':7, 'total_generation_ceiling':42,
    'tools_per_run':3, 'experiment_output_tokens':8192, 'experiment_input_window_cap':65536,
    'input_byte_envelope_with_protocol_reserve':67584,
    'peak_input_usd_per_million':0.3, 'peak_output_usd_per_million':1.2,
    'conservative_42_request_envelope_usd':1.2644352,
    'cost_ledger_limit_per_run_usd':0.25, 'six_run_cost_ledger_limit_usd':1.50,
    'external_search_fetch_calls':0, 'codex_cli_calls':0,
    'data':'frozen public excerpt, synthetic policies, application prompts/schemas; new isolated owner/DB',
    'pricing_url':'https://api-docs.deepseek.com/quick_start/pricing/', 'pricing_checked':'2026-10-27',
    'authorization':'PENDING_USER_APPROVAL; --plan makes no Provider calls',
    'note':'固定来源I/O，检验真实模型工具决策；不是实时互联网搜索或统计泛化评测。'}


def fixture():
    frozen = json.loads((ROOT/'frozen-experiment.json').read_text(encoding='utf-8'))
    if sha256(FIXTURE.read_bytes()).hexdigest()!=frozen['test_sha256']:
        raise ValueError('冻结观察器已变化，停止')
    spec = importlib.util.spec_from_file_location('v11_frozen_observer', FIXTURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def worker(args):
    sys.path.insert(0, str(SOURCES[args.variant]/'src'))
    import pytest
    from min_agent.evaluation.finance import CostBoundedModel, CostLedger
    from min_agent.evaluation.runner import save_json
    from min_agent.llm.deepseek import DeepSeekResponsesModel, DeepSeekSettings
    module = fixture()
    case = module.CASES[args.case_index]
    ledger = CostLedger(args.out.parent/(args.out.name+'-cost.json'), limit=.25, input_rate=.3, output_rate=1.2)
    with DeepSeekResponsesModel(DeepSeekSettings.from_credentials_file(args.credentials,
            max_requests=7, max_output_tokens=8192), proxy=args.proxy) as raw:
        bounded = CostBoundedModel(raw, ledger)

        class RecordedProvider:
            def __init__(self):
                self.calls, self.errors = [], []
            def __getattr__(self, name):
                return getattr(bounded, name)
            def context_limits(self):
                value = raw.context_limits()
                window = min(value.context_window, 65536)
                return value.model_copy(update={'context_window':window,
                    'max_output_tokens':min(value.max_output_tokens, window),
                    'source':value.source+'; bounded-experiment-window'})
            def generate(self, messages, *, tools=None):
                self.calls.append({'phase':'research' if messages and messages[0].content.startswith(
                    '你是当前任务中的只读研究助手。') else 'main',
                    'tools':sorted(tool['name'] for tool in tools or []),
                    'messages':[m.model_dump(mode='json', exclude={'provider_state'}) for m in messages]})
                try:
                    before = len(raw.usage)
                    response = bounded.generate(messages, tools=tools)
                    usage = raw.usage
                    if len(usage)<=before or any(type(usage[-1].get(key)) is not int or usage[-1][key]<0
                            for key in ('input_tokens','output_tokens')):
                        # finance 已保留本次完整费用预留；不再发起下一次生成。
                        raise RuntimeError('provider_usage_unknown_stop_batch')
                    return response
                except Exception as error:
                    self.errors.append({'error_type':type(error).__name__})
                    raise

        recorded = RecordedProvider()
        module.ResearchPolicy = lambda case:recorded  # 仅替换模型，原冻结观察器与I/O不变。
        patch = pytest.MonkeyPatch()
        try:
            result = module.run_case(args.out, case, patch)
        finally:
            patch.undo()
        simulated = bool(getattr(raw, '_v11_offline_simulation', False))
        # 复用冻结评分，只校正模型来源/计数元数据；不改 checks、verdict 或验收标准。
        projection = dict(result)
        projection['model_visible_calls'] = projection.pop('fixed_policy_calls')
        projection['backend'] = 'MOCK_TRANSPORT_FROZEN_IO' if simulated else 'LIVE_PROVIDER_FROZEN_IO'
        projection['external_generation_requests'] = 0 if simulated else raw.request_count
        save_json(args.out/'result.json', projection)
        receipt = {'backend':'MOCK_TRANSPORT_FROZEN_IO' if simulated else 'LIVE_PROVIDER_FROZEN_IO',
            'variant':args.variant, 'case':case['id'],
            'verdict':'UNKNOWN' if recorded.errors else result['verdict'],
            'mechanical_observer_verdict':result['verdict'], 'provider_errors':recorded.errors,
            'generation_requests':0 if simulated else raw.request_count,
            'adapter_attempts':raw.request_count, 'usage':raw.usage, 'criteria_changed':False,
            'observer_note':'只校正来源与计数元数据；机械评分来自原冻结观察器。',
            'new_model_decision_tested':not simulated}
        save_json(args.out/'live-receipt.json', receipt)
        print(json.dumps(receipt, ensure_ascii=False))
        return 2 if recorded.errors else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--credentials', type=Path)
    parser.add_argument('--proxy')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--variant', choices=tuple(SOURCES))
    parser.add_argument('--case-index', type=int, choices=range(3))
    args = parser.parse_args()
    if args.plan or not args.live:
        print(json.dumps(PLAN, ensure_ascii=False, indent=2))
        return 0
    if args.credentials is None or args.out is None:
        parser.error('--live需要明确项目外凭据与新的输出目录；本命令不得在获得授权前执行')
    args.credentials = args.credentials.resolve()
    project = ROOT.parents[1]
    if args.credentials.is_relative_to(project):
        parser.error('凭据必须位于项目外')
    args.out = args.out.resolve()
    if args.worker:
        if args.variant is None or args.case_index is None:
            parser.error('worker缺少版本或任务')
        return worker(args)
    sys.path.insert(0, str(SOURCES['baseline']/'src'))
    from min_agent.evaluation.processes import run_owned
    from min_agent.evaluation.runner import save_json
    from min_agent.evaluation.worker import source_version
    candidate = json.loads((ROOT/'candidate-01/candidate.json').read_text(encoding='utf-8'))
    expected = {'baseline':candidate['baseline_source'], 'candidate':candidate['candidate_source']}
    if any(source_version(root)!=expected[name] for name,root in SOURCES.items()):
        raise ValueError('待测源码变化，停止而不发送请求')
    fixture()
    args.out.mkdir(parents=True, exist_ok=False)
    save_json(args.out/'approved-plan.json', dict(PLAN, authorization='EXPLICIT_LIVE_INVOCATION'))
    completed = []
    for variant in SOURCES:
        for index in range(3):
            directory = args.out/(variant+'-'+str(index))
            command = [sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--live', '--worker',
                '--variant', variant, '--case-index', str(index), '--out', str(directory),
                '--credentials', str(args.credentials)]
            if args.proxy:
                command += ['--proxy', args.proxy]
            try:
                process = run_owned(command, capture_output=True, timeout=120, env=dict(os.environ))
                (args.out/(directory.name+'-worker.log')).write_bytes(process.stdout+process.stderr)
                receipt_file = directory/'live-receipt.json'
                receipt = json.loads(receipt_file.read_text(encoding='utf-8')) if receipt_file.exists() else {}
                completed.append({'run':directory.name, 'exit':process.returncode, 'receipt':receipt})
                failed = process.returncode!=0 or bool(receipt.get('provider_errors')) or not receipt
            except Exception as error:
                completed.append({'run':directory.name, 'transport':'UNKNOWN', 'error_type':type(error).__name__})
                failed = True
            save_json(args.out/'batch.json', {'completed':completed, 'stopped_on_error':failed,
                'request_ceiling':42, 'auto_retry':False})
            if failed:
                return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
