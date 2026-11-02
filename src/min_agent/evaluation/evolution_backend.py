"""持续进化V1后端：原Harness评估与受限绑定提案，不注入基线缺陷。"""
from hashlib import sha256
import ast
import json
from pathlib import Path
import shutil

from min_agent.evaluation.improvement import TARGET, render_candidate
from min_agent.evaluation.runner import run_task, save_json


def manifest(root):
    root = Path(root)
    result = {}
    for file in sorted((root / 'src').rglob('*')):
        if ('__pycache__' in file.parts or file.suffix == '.pyc'
                or any(part.endswith('.egg-info') for part in file.parts)):
            continue
        if file.is_symlink():
            raise PermissionError('源码快照不接受符号链接')
        if file.is_file():
            result[file.relative_to(root).as_posix()] = sha256(file.read_bytes()).hexdigest()
    if TARGET not in result:
        raise ValueError('缺少受支持计算器源码')
    return result


def snapshot(source, destination):
    source, destination = Path(source), Path(destination)
    manifest(source)
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source / 'src', destination / 'src',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.egg-info'))
    for name in ('pyproject.toml', 'requirements.lock', 'requirements-dev.lock'):
        if (source / name).is_file():
            shutil.copy2(source / name, destination / name)
    return manifest(destination)


def apply_bindings(source, destination, proposal):
    snapshot(source, destination)
    file = Path(destination) / TARGET
    original = file.read_text(encoding='utf-8')
    rendered = render_candidate(original, proposal)
    if ast.dump(ast.parse(original)) != ast.dump(ast.parse(rendered)):
        file.write_text(rendered, encoding='utf-8', newline='\n')
    before, after = manifest(source), manifest(destination)
    if any(after.get(name) != value for name, value in before.items() if name != TARGET):
        raise PermissionError('候选改动超过绑定表范围')
    return after


class CalculatorBackend:
    def __init__(self, *, live=False, credentials=None, proxy=None, candidates=()):
        self.live = live
        self.credentials = Path(credentials).resolve() if credentials else None
        self.proxy = proxy
        self.candidates = iter(candidates)
        self.kind = 'LIVE_DEEPSEEK' if live else 'SUPPLIED_CANDIDATE_REPLAY'
        if live and (self.credentials is None or not self.credentials.is_file()):
            raise ValueError('真实调用需要已有项目外凭据')

    def evaluate(self, spec, source, out, *, usd, seconds):
        return run_task(spec, out, source_root=source, live=self.live,
                        credentials=self.credentials, proxy=self.proxy,
                        cost_limit=usd if self.live else None, wall_timeout=seconds)

    def propose(self, source, failures, out, *, usd, seconds):
        out.mkdir(parents=True, exist_ok=False)
        if not self.live:
            return next(self.candidates)
        from min_agent.evaluation.finance import CostLedger, CostBoundedModel
        from min_agent.llm.deepseek import DeepSeekResponsesModel, DeepSeekSettings
        from min_agent.models import Message
        prompt = ('你在隔离实验中修复计算器四则运算绑定。只返回JSON：'
                  '{"bindings":{"Add":"add","Sub":"subtract","Mult":"multiply","Div":"divide"}}。'
                  '值只能选add/subtract/multiply/divide，不返回代码、路径或工具调用。'
                  '下面只有合成开发失败及相关代码；不要修改评测器和权限。\n'
                  + json.dumps({'source': (source / TARGET).read_text(encoding='utf-8'),
                                'development_failures': failures}, ensure_ascii=False))
        save_json(out / 'prompt.json', {'prompt': prompt, 'data_classification': 'synthetic_and_related_source'})
        settings = DeepSeekSettings.from_credentials_file(
            self.credentials, max_requests=1, max_output_tokens=None,
            timeout_seconds=min(60, max(1, seconds)))
        with DeepSeekResponsesModel(settings, proxy=self.proxy) as raw:
            bounded = CostBoundedModel(raw, CostLedger(out / 'cost-ledger.json', limit=usd))
            response = bounded.generate([Message(role='user', content=prompt)])
            save_json(out / 'generation.json', {'requests': raw.request_count, 'usage': raw.usage})
            if response.finish_reason != 'stop':
                raise ValueError('候选生成没有完整结束')
            proposal = json.loads(response.content)
            save_json(out / 'proposal.json', proposal)
            return proposal


def external_usage(out, result=None):
    """从保留的费用账本结算上界；没有账本就不给未知请求释放预留。"""
    out = Path(out)
    ledger = out / 'cost-ledger.json'
    if not ledger.is_file():
        raise ValueError('外部调用缺少费用证据')
    data = json.loads(ledger.read_text(encoding='utf-8'))
    amount = data['spent_upper'] + sum(data['reservations'].values())
    if result is not None:
        if result.observation.transport_status != 'ok':
            raise ValueError('worker结束状态不可认证')
        requests = result.observation.model_requests
    else:
        requests = json.loads((out / 'generation.json').read_text(encoding='utf-8'))['requests']
    return requests, amount
