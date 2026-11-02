"""有界、持久、失败关闭的实验进化循环；champion不是生产发布。"""
import json
from pathlib import Path
import time

from pydantic import Field

from min_agent.evaluation.contracts import Contract, EvaluationResult, TaskSpec, digest
from min_agent.evaluation.evolution_backend import (
    CalculatorBackend, TARGET, apply_bindings, external_usage, manifest, snapshot,
)
from min_agent.evaluation.finance import micros
from min_agent.evaluation.journal import JsonDocument
from min_agent.evaluation.runner import compare_results, save_json
from min_agent.evaluation.verification_rules import protocol_fingerprint
from min_agent.web.launcher import DatabaseLease


class EvolutionPolicy(Contract):
    live: bool = False
    max_candidates: int = Field(default=3, ge=1, le=10)
    max_stalled: int = Field(default=2, ge=1, le=5)
    max_seconds: int = Field(default=900, ge=1, le=3600)
    max_requests: int = Field(default=0, ge=0, le=50)
    max_usd: float = Field(default=0, ge=0, le=1)


class EvolutionStop(Exception):
    pass


def controller_fingerprint():
    files = [Path(__file__), Path(__file__).with_name('evolution_backend.py')]
    return digest({p.name: p.read_text(encoding='utf-8') for p in files})


def validate_tasks(development, holdout):
    tasks = [TaskSpec.model_validate(t) for t in [*development, *holdout]]
    if not development or not holdout or len(tasks) > 12 or len({t.id for t in tasks}) != len(tasks):
        raise ValueError('需要互不重复、有限的开发任务与保留任务')
    for t in tasks:
        if (t.category != 'runtime' or t.profile != 'calculator' or t.fixture != 'empty'
                or t.tools != ['calculator'] or t.mode != 'plan' or t.actions or t.recovery
                or t.evaluation != 'deterministic' or t.data_classification != 'synthetic'
                or t.expected_verdict != 'PASS' or not t.live_eligible
                or not any(c.kind == 'calculator_value' for c in t.checks)):
            raise ValueError('V1只支持无副作用合成计算任务，不使用诊断FAIL/UNKNOWN作为优化目标')
    return tasks


class EvolutionSession(JsonDocument):
    @classmethod
    def create(cls, directory, source, development, holdout, policy):
        policy = EvolutionPolicy.model_validate(policy)
        tasks = validate_tasks(development, holdout)
        directory = Path(directory).resolve()
        if directory.is_relative_to(Path(source).resolve() / 'src'):
            raise ValueError('实验目录不能放入源代码目录')
        directory.mkdir(parents=True, exist_ok=False)
        run = cls(directory / 'state.json')
        baseline = directory / 'versions/baseline'
        files = snapshot(source, baseline)
        frozen = {'policy': policy.model_dump(mode='json'),
                  'tasks': [t.model_dump(mode='json') for t in tasks],
                  'development_ids': [t.id for t in development],
                  'protocol': protocol_fingerprint(), 'controller': controller_fingerprint(),
                  'baseline_manifest': files}
        run.write(dict(version=1, frozen=frozen, frozen_sha256=digest(frozen),
                       status='READY', reason='', champion='baseline',
                       versions={'baseline': {'manifest': files, 'results': {}}},
                       attempts=[], accepted_versions=0, stalled=0, active=None,
                       started_at=None, request_slots_used=0, observed_requests=0,
                       spent_usd_upper=0, reserved_usd=0, events=[], auto_published=False))
        return run

    @property
    def directory(self):
        return self.path.parent

    def _source(self, name):
        return self.directory / 'versions' / name

    def _check(self, data):
        frozen = data['frozen']
        if (digest(frozen) != data['frozen_sha256'] or frozen['protocol'] != protocol_fingerprint()
                or frozen['controller'] != controller_fingerprint()):
            raise EvolutionStop('PROTOCOL_CHANGED')
        for name, version in data['versions'].items():
            if not name.startswith('candidate-') and name != 'baseline':
                raise EvolutionStop('SOURCE_CHANGED')
            if manifest(self._source(name)) != version['manifest']:
                raise EvolutionStop('SOURCE_CHANGED')
            for record in version['results'].values():
                try:
                    value = json.loads((self.directory / record['path']).read_text(encoding='utf-8'))
                    if digest(value) != record['sha256']:
                        raise ValueError('证据摘要改变')
                except (OSError, ValueError):
                    raise EvolutionStop('EVIDENCE_CHANGED') from None

    def _remaining(self, data):
        policy = EvolutionPolicy.model_validate(data['frozen']['policy'])
        seconds = policy.max_seconds - (time.time() - data['started_at'])
        if seconds <= 0:
            raise EvolutionStop('DEADLINE_EXHAUSTED')
        return seconds

    def _reserve(self, data, kind, out, slots):
        self._check(data)
        seconds = self._remaining(data)
        policy = EvolutionPolicy.model_validate(data['frozen']['policy'])
        usd = max(0, policy.max_usd - data['spent_usd_upper'])
        if policy.live:
            if data['request_slots_used'] + slots > policy.max_requests or micros(usd) == 0:
                raise EvolutionStop('BUDGET_EXHAUSTED')
            # 次数按最坏槽位计费、不退回；实际观测次数另列。钱先预留全部剩余额度。
            data['request_slots_used'] += slots
            data['reserved_usd'] = usd
        data['active'] = {'kind': kind, 'out': out.relative_to(self.directory).as_posix(), 'slots': slots}
        self.write(data)
        return usd, seconds

    def _settle(self, data, out, result=None):
        policy = EvolutionPolicy.model_validate(data['frozen']['policy'])
        if policy.live:
            calls, amount = external_usage(out, result)
            if (type(calls) is not int or not 0 <= calls <= data['active']['slots']
                    or micros(amount) > micros(data['reserved_usd'])):
                raise EvolutionStop('ACCOUNTING_UNKNOWN')
            data['observed_requests'] += calls
            data['spent_usd_upper'] = (micros(data['spent_usd_upper']) + micros(amount)) / 1e6
            data['reserved_usd'] = 0
        data['active'] = None
        self.write(data)

    def _results(self, data, version_name, backend):
        version = data['versions'][version_name]
        results = {}
        for index, raw in enumerate(data['frozen']['tasks']):
            task = TaskSpec.model_validate(raw)
            if task.id in version['results']:
                record = version['results'][task.id]
                file = self.directory / record['path']
                value = json.loads(file.read_text(encoding='utf-8'))
                if digest(value) != record['sha256']:
                    raise EvolutionStop('EVIDENCE_CHANGED')
                result = EvaluationResult.model_validate(value)
            else:
                out = self.directory / 'runs' / version_name / (str(index) + '-' + task.id)
                usd, seconds = self._reserve(data, 'evaluate', out, min(task.max_model_calls, task.max_rounds))
                result = backend.evaluate(task, self._source(version_name), out, usd=usd, seconds=seconds)
                self._settle(data, out, result)
                self._check(data)
                # 结果由可信host保存；注入后端仅用于离线机制测试。
                out.mkdir(parents=True, exist_ok=True)
                save_json(out / 'verified-result.json', result)
                version['results'][task.id] = {'path': (out / 'verified-result.json').relative_to(self.directory).as_posix(),
                                               'sha256': digest(result)}
                self.write(data)
            if (result.task_id != task.id or result.spec_sha256 != digest(task)
                    or result.evaluation_protocol_sha256 != data['frozen']['protocol']):
                raise EvolutionStop('PROTOCOL_CHANGED')
            if result.observation.extra.get('provider_errors'):
                raise EvolutionStop('PROVIDER_OR_COST_STOP')
            results[task.id] = result
        return results

    def _stop(self, data, reason):
        data['status'] = 'STOPPED'
        data['reason'] = reason
        self.write(data)
        self._report(data)
        return data

    def _report(self, data):
        text = ['# Adrian持续进化实验', '', '停止原因：' + data['reason'],
                '当前实验版本：' + data['champion'], '实验接受版本数：' + str(data['accepted_versions']),
                '执行后端：' + data.get('backend_kind', 'UNKNOWN'),
                '预占生成请求槽位：' + str(data['request_slots_used']),
                '已观测生成请求：' + str(data['observed_requests']),
                '费用上界估算：$' + str(data['spent_usd_upper']),
                '尚未释放的费用预留：$' + str(data['reserved_usd']),
                '', '实验版本未自动发布。离线结果只证明机制，费用上界不是Provider账单。',
                '完整冻结任务、证据hash、候选和决策见state.json。']
        (self.directory / 'report.md').write_text('\n\n'.join(text), encoding='utf-8')

    def run(self, backend=None):
        backend = backend or CalculatorBackend()
        with DatabaseLease(str(self.path) + '.runner', {'product': 'adrian-evolution'}):
            data = self.read()
            policy = EvolutionPolicy.model_validate(data['frozen']['policy'])
            if backend.live != policy.live:
                raise PermissionError('运行后端必须匹配已冻结的live授权模式')
            if data['active'] is not None:
                return self._stop(data, 'INTERRUPTED_UNKNOWN')
            try:
                self._check(data)
            except EvolutionStop as error:
                return self._stop(data, str(error))
            if data['status'] == 'STOPPED':
                return data
            data['started_at'] = data['started_at'] or time.time()
            data['status'] = 'RUNNING'
            data['backend_kind'] = backend.kind
            self.write(data)
            try:
                while True:
                    self._check(data)
                    self._remaining(data)
                    before = self._results(data, data['champion'], backend)
                    if any(r.safety == 'FAIL' for r in before.values()):
                        return self._stop(data, 'SAFETY_STOP')
                    if any(r.verdict == 'UNKNOWN' or r.safety != 'PASS' for r in before.values()):
                        return self._stop(data, 'EVIDENCE_UNKNOWN')
                    failed = [r for identity, r in before.items()
                              if identity in data['frozen']['development_ids'] and r.verdict == 'FAIL']
                    if not failed:
                        reason = 'NO_VERIFIED_FAILURE' if all(r.verdict == 'PASS' for r in before.values()) else 'HOLDOUT_FAILURE'
                        return self._stop(data, reason)
                    if len(data['attempts']) >= policy.max_candidates or data['stalled'] >= policy.max_stalled:
                        return self._stop(data, 'SEARCH_LIMIT')
                    number = len(data['attempts']) + 1
                    name = 'candidate-' + str(number)
                    attempt = {'version': name, 'decision': 'PENDING', 'parent': data['champion']}
                    data['attempts'].append(attempt)
                    out = self.directory / 'proposals' / name
                    usd, seconds = self._reserve(data, 'propose', out, 1)
                    # 仅开发失败可送给更新器，保留集输入/预期/结果不进入提案上下文。
                    failures = [{'task_id': r.task_id, 'answer': r.observation.answer,
                                 'tools': r.observation.tools, 'failure_types': r.failures} for r in failed]
                    proposal = backend.propose(self._source(data['champion']), failures, out,
                                               usd=usd, seconds=seconds)
                    self._settle(data, out)
                    self._check(data)
                    out.mkdir(parents=True, exist_ok=True)
                    save_json(out / 'accepted-input.json', proposal)
                    attempt['proposal_sha256'] = digest(proposal)
                    try:
                        files = apply_bindings(self._source(data['champion']), self._source(name), proposal)
                    except (ValueError, TypeError, KeyError):
                        attempt['decision'] = 'REJECT_INVALID'
                        data['stalled'] += 1
                        self.write(data)
                        continue
                    if any(v['manifest'][TARGET] == files[TARGET] for v in data['versions'].values()):
                        attempt['decision'] = 'REJECT_REPEAT'
                        data['stalled'] += 1
                        self.write(data)
                        continue
                    data['versions'][name] = {'manifest': files, 'results': {}}
                    self.write(data)
                    after = self._results(data, name, backend)
                    comparisons = [compare_results(before[i], after[i]) for i in before]
                    save_json(out / 'comparison.json', comparisons)
                    regression = any(r.verdict == 'PASS' and after[i].verdict != 'PASS' for i, r in before.items())
                    holdout_pass = all(after[i].verdict == 'PASS' for i in after if i not in data['frozen']['development_ids'])
                    improved = any(before[r.task_id].verdict == 'FAIL' and after[r.task_id].verdict == 'PASS' for r in failed)
                    safe = all(r.safety == 'PASS' and r.verdict != 'UNKNOWN' for r in after.values())
                    if safe and not regression and holdout_pass and improved:
                        attempt['decision'] = 'ACCEPT_EXPERIMENT'
                        data['champion'] = name
                        data['accepted_versions'] += 1
                        data['stalled'] = 0
                    else:
                        attempt['decision'] = 'REJECT_REGRESSION' if regression else 'REJECT_NO_VERIFIED_GAIN'
                        data['stalled'] += 1
                    self.write(data)
                    if any(r.safety == 'FAIL' for r in after.values()):
                        return self._stop(data, 'SAFETY_STOP')
                    if any(r.verdict == 'UNKNOWN' or r.safety != 'PASS' for r in after.values()):
                        return self._stop(data, 'EVIDENCE_UNKNOWN')
            except EvolutionStop as error:
                return self._stop(data, str(error))
            except Exception as error:
                # 不保存原始异常，避免认证/环境信息进入错误报告；未知活动保留预留。
                data['events'].append({'type': 'STOP_ERROR', 'error_type': type(error).__name__})
                return self._stop(data, 'EXECUTION_UNKNOWN')

    def rollback(self, version):
        with DatabaseLease(str(self.path) + '.runner', {'product': 'adrian-evolution'}):
            data = self.read()
            self._check(data)
            allowed = {'baseline'} | {a['version'] for a in data['attempts'] if a['decision'] == 'ACCEPT_EXPERIMENT'}
            if data['active'] is not None or version not in allowed:
                raise PermissionError('只允许回到已有可信实验版本，不能回退未知活动')
            data['events'].append({'type': 'ROLLBACK', 'from': data['champion'], 'to': version})
            data['champion'] = version
            return self._stop(data, 'ROLLED_BACK')
