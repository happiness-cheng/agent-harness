"""持续进化控制器的冻结机制场景；假后端不代表真实模型收益。"""
from pathlib import Path
import json

import pytest

from min_agent.evaluation.contracts import CheckSpec, Observation, TaskSpec
from min_agent.evaluation.verifier import verify
from min_agent.evaluation.evolution import EvolutionPolicy, EvolutionSession


BINDINGS = {'bindings': {'Add': 'add', 'Sub': 'subtract', 'Mult': 'multiply', 'Div': 'divide'}}
TARGET = 'src/min_agent/tools/calculator.py'


def task(identity, expression='2+3', expected='5'):
    return TaskSpec(id=identity, category='runtime', profile='calculator', tools=['calculator'],
                    prompt='调用calculator，最后只返回数字。', parameters={'expression': expression},
                    checks=[CheckSpec(id='answer', kind='calculator_value', expected=expected)],
                    max_model_calls=3, segment_model_calls=3, max_rounds=3,
                    live_eligible=True)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'source'
    file = root / TARGET
    file.parent.mkdir(parents=True)
    file.write_text('import ast\nfrom decimal import Context\n'
                    '_BINARY_OPERATIONS={ast.Add:Context.subtract, ast.Sub:Context.subtract, '
                    'ast.Mult:Context.multiply, ast.Div:Context.divide}\n', encoding='utf-8')
    guard = root / 'src/min_agent/evaluation/verifier.py'
    guard.parent.mkdir(parents=True)
    guard.write_text('protected = True\n', encoding='utf-8')
    return root


class FakeBackend:
    live = False
    kind = 'INJECTED_MECHANISM_TEST'

    def __init__(self, *, regression=False, unknown=False, tamper=False, crash=False):
        self.proposals = 0
        self.evaluations = 0
        self.regression, self.unknown, self.tamper, self.crash = regression, unknown, tamper, crash
        self.received = []

    def evaluate(self, spec, source, out, **limits):
        self.evaluations += 1
        fixed = 'ast.Add: Context.add' in (source / TARGET).read_text(encoding='utf-8')
        value = '5' if fixed or spec.id.startswith('holdout') else '-1'
        if fixed and self.regression and spec.id.startswith('holdout'):
            value = '999'
        o = Observation(run_id='fixture', task_id=spec.id, version_id='fixture',
                        execution_status='unknown' if self.unknown else 'completed', answer=value,
                        budget={'usage': {'model_calls': 1, 'tool_calls': 1}},
                        tools=[{'name': 'calculator', 'result': {'ok': True, 'result': value}}])
        return verify(spec, o)

    def propose(self, source, failures, out, **limits):
        self.proposals += 1
        self.received = failures
        if self.tamper:
            (source / 'src/min_agent/evaluation/verifier.py').write_text('tampered=True', encoding='utf-8')
        if self.crash:
            raise KeyboardInterrupt()
        return BINDINGS


def session(tmp_path, source, **policy):
    return EvolutionSession.create(tmp_path / 'experiment', source, [task('dev')],
                                   [task('holdout')], EvolutionPolicy(**policy))


def test_verified_improvement_is_promoted_only_inside_experiment(tmp_path, source):
    run = session(tmp_path, source)
    backend = FakeBackend()
    result = run.run(backend)
    assert result['reason'] == 'NO_VERIFIED_FAILURE'
    assert result['accepted_versions'] == 1
    assert backend.proposals == 1
    assert 'Context.subtract' in (source / TARGET).read_text(encoding='utf-8')
    assert all(item['task_id'] != 'holdout' for item in backend.received)
    assert run.run(backend)['accepted_versions'] == 1
    assert backend.proposals == 1


def test_healthy_baseline_does_not_manufacture_a_failure(tmp_path, source):
    from min_agent.evaluation.improvement import render_candidate
    file = source / TARGET
    file.write_text(render_candidate(file.read_text(encoding='utf-8'), BINDINGS), encoding='utf-8')
    backend = FakeBackend()
    result = session(tmp_path, source).run(backend)
    assert result['reason'] == 'NO_VERIFIED_FAILURE' and backend.proposals == 0


def test_holdout_regression_rejects_candidate(tmp_path, source):
    backend = FakeBackend(regression=True)
    result = session(tmp_path, source, max_candidates=1).run(backend)
    assert result['accepted_versions'] == 0
    assert result['attempts'][0]['decision'] == 'REJECT_REGRESSION'


def test_unknown_baseline_does_not_trigger_blind_repair(tmp_path, source):
    backend = FakeBackend(unknown=True)
    result = session(tmp_path, source).run(backend)
    assert result['reason'] == 'EVIDENCE_UNKNOWN' and backend.proposals == 0


def test_proposer_cannot_modify_protected_source(tmp_path, source):
    result = session(tmp_path, source).run(FakeBackend(tamper=True))
    assert result['reason'] == 'SOURCE_CHANGED'
    assert result['accepted_versions'] == 0


def test_interrupted_proposal_is_not_replayed_on_restart(tmp_path, source):
    run = session(tmp_path, source)
    with pytest.raises(KeyboardInterrupt):
        run.run(FakeBackend(crash=True))
    backend = FakeBackend()
    result = EvolutionSession(run.path).run(backend)
    assert result['reason'] == 'INTERRUPTED_UNKNOWN' and backend.proposals == 0


def test_no_external_budget_prevents_backend_entry(tmp_path, source):
    run = session(tmp_path, source, live=True, max_requests=0, max_usd=0)
    backend = FakeBackend()
    backend.live = True
    result = run.run(backend)
    assert result['reason'] == 'BUDGET_EXHAUSTED'
    assert backend.evaluations == 0 and backend.proposals == 0


def test_rollback_restores_experiment_pointer_without_resetting_budget(tmp_path, source):
    run = session(tmp_path, source)
    result = run.run(FakeBackend())
    prior = result['request_slots_used']
    rolled = run.rollback('baseline')
    assert rolled['champion'] == 'baseline'
    assert rolled['request_slots_used'] == prior


def test_finished_evidence_is_rechecked_before_being_reported_again(tmp_path, source):
    run = session(tmp_path, source)
    result = run.run(FakeBackend())
    record = next(iter(result['versions']['baseline']['results'].values()))
    (run.directory / record['path']).write_text('{}', encoding='utf-8')
    assert run.run(FakeBackend())['reason'] == 'EVIDENCE_CHANGED'


def test_live_reservations_survive_process_interruption(tmp_path, source):
    class Crash(FakeBackend):
        live = True
        def evaluate(self, *args, **kwargs):
            raise KeyboardInterrupt()
    run = session(tmp_path, source, live=True, max_requests=3, max_usd=1)
    with pytest.raises(KeyboardInterrupt):
        run.run(Crash())
    before = run.read()
    assert before['request_slots_used'] == 3 and before['reserved_usd'] == 1
    after = EvolutionSession(run.path).run(Crash())
    assert after['reason'] == 'INTERRUPTED_UNKNOWN'
    assert after['request_slots_used'] == 3 and after['reserved_usd'] == 1


def test_protocol_change_stops_before_backend(tmp_path, source, monkeypatch):
    import min_agent.evaluation.evolution as module
    run = session(tmp_path, source)
    monkeypatch.setattr(module, 'protocol_fingerprint', lambda: 'f' * 64)
    backend = FakeBackend()
    assert run.run(backend)['reason'] == 'PROTOCOL_CHANGED'
    assert backend.evaluations == 0


def test_identical_binding_proposal_does_not_count_formatting_as_evolution(tmp_path, source):
    from min_agent.evaluation.evolution_backend import apply_bindings, manifest
    file = source / TARGET
    file.write_text(file.read_text(encoding='utf-8').replace('ast.Add:Context.subtract', 'ast.Add:Context.add'), encoding='utf-8')
    before = manifest(source)
    after = apply_bindings(source, tmp_path / 'same-candidate', BINDINGS)
    assert before == after


def test_rejected_duplicate_candidate_is_not_evaluated_forever(tmp_path, source):
    backend = FakeBackend(regression=True)
    result = session(tmp_path, source).run(backend)
    assert result['reason'] == 'SEARCH_LIMIT'
    assert [a['decision'] for a in result['attempts']] == ['REJECT_REGRESSION', 'REJECT_REPEAT']
    assert backend.proposals == 2 and backend.evaluations == 4


def test_request_cap_and_known_cost_are_persisted_between_tasks(tmp_path, source):
    class Metered(FakeBackend):
        live = True
        def evaluate(self, spec, source, out, **limits):
            out.mkdir(parents=True)
            (out / 'cost-ledger.json').write_text(json.dumps({'spent_upper': .0001, 'reservations': {}}))
            result = super().evaluate(spec, source, out, **limits)
            return result.model_copy(update={'observation': result.observation.model_copy(update={'model_requests': 1})})
    backend = Metered()
    run = session(tmp_path, source, live=True, max_requests=3, max_usd=1)
    result = run.run(backend)
    assert result['reason'] == 'BUDGET_EXHAUSTED' and backend.evaluations == 1
    assert result['request_slots_used'] == 3 and result['observed_requests'] == 1
    assert result['spent_usd_upper'] == .0001 and result['reserved_usd'] == 0
    assert run.run(backend)['request_slots_used'] == 3 and backend.evaluations == 1


def test_expected_failure_diagnostic_is_not_a_learning_target(tmp_path, source):
    invalid = task('diagnostic').model_copy(update={'expected_verdict': 'FAIL'})
    with pytest.raises(ValueError):
        EvolutionSession.create(tmp_path / 'invalid', source, [invalid], [task('holdout')], EvolutionPolicy())


def test_live_proposer_uses_cost_gate_and_only_returns_data(tmp_path, source, monkeypatch):
    from types import SimpleNamespace
    import min_agent.llm.deepseek as module
    from min_agent.evaluation.evolution_backend import CalculatorBackend
    from min_agent.models import ModelResponse
    captured = []
    class Raw:
        def __init__(self, settings, **kwargs):
            self.request_count, self.usage = 0, []
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def _build_body(self, messages, tools):
            return {'max_output_tokens': 100, 'synthetic': True}
        def generate(self, messages, *, tools=None):
            assert tools is None
            self.request_count += 1
            captured.extend(m.content for m in messages)
            self.usage.append({'input_tokens': 100, 'output_tokens': 20})
            return ModelResponse(finish_reason='stop', content=json.dumps(BINDINGS))
    monkeypatch.setattr(module, 'DeepSeekResponsesModel', Raw)
    monkeypatch.setattr(module.DeepSeekSettings, 'from_credentials_file', lambda *a, **k: SimpleNamespace())
    credentials = tmp_path / 'synthetic-credentials.json'
    credentials.write_text('{}')
    backend = CalculatorBackend(live=True, credentials=credentials)
    out = tmp_path / 'proposal'
    assert backend.propose(source, [{'task_id': 'dev', 'answer': '-1'}], out, usd=.01, seconds=10) == BINDINGS
    generation = json.loads((out / 'generation.json').read_text(encoding='utf-8'))
    ledger = json.loads((out / 'cost-ledger.json').read_text(encoding='utf-8'))
    assert generation['requests'] == 1 and ledger['spent_upper'] > 0
    assert 'holdout' not in ''.join(captured)
