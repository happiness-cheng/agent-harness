"""原Harness/SQLite/Verifier实际集成；只用脚本模型，无真实外部调用。"""
from pathlib import Path

from min_agent.evaluation.evolution import EvolutionPolicy, EvolutionSession
from min_agent.evaluation.evolution_backend import CalculatorBackend, TARGET, snapshot
from min_agent.evaluation.evolution_cli import curriculum
from min_agent.evaluation.improvement import render_candidate


ROOT = Path(__file__).resolve().parents[2]
BINDINGS = {'bindings': {'Add': 'add', 'Sub': 'subtract', 'Mult': 'multiply', 'Div': 'divide'}}


def test_current_real_source_stops_without_candidate_or_artificial_fault(tmp_path):
    development, holdout = curriculum()
    run = EvolutionSession.create(tmp_path / 'healthy', ROOT, development[:1], holdout[:1], EvolutionPolicy())
    result = run.run(CalculatorBackend())
    assert result['reason'] == 'NO_VERIFIED_FAILURE', result
    assert not result['attempts'] and result['observed_requests'] == 0


def test_controlled_fault_repair_changes_later_harness_runs_and_preserves_holdout(tmp_path):
    source = tmp_path / 'synthetic-source'
    snapshot(ROOT, source)
    file = source / TARGET
    broken = {'bindings': dict(BINDINGS['bindings'], Add='subtract')}
    file.write_text(render_candidate(file.read_text(encoding='utf-8'), broken), encoding='utf-8')
    development, holdout = curriculum()
    run = EvolutionSession.create(tmp_path / 'repair', source, development[:1], holdout[:1], EvolutionPolicy())
    result = run.run(CalculatorBackend(candidates=[BINDINGS]))
    assert result['reason'] == 'NO_VERIFIED_FAILURE', result
    assert result['accepted_versions'] == 1
    assert result['attempts'][0]['decision'] == 'ACCEPT_EXPERIMENT'
    assert result['auto_published'] is False
    assert result['champion'] != 'baseline'


def test_successive_candidates_reuse_the_verified_champion(tmp_path):
    source = tmp_path / 'two-fault-source'
    snapshot(ROOT, source)
    file = source / TARGET
    both_wrong = {'bindings': dict(BINDINGS['bindings'], Add='subtract', Sub='add')}
    file.write_text(render_candidate(file.read_text(encoding='utf-8'), both_wrong), encoding='utf-8')
    development, holdout = curriculum()
    run = EvolutionSession.create(tmp_path / 'successive', source, development, holdout[1:2], EvolutionPolicy())
    partial = {'bindings': dict(BINDINGS['bindings'], Sub='add')}
    result = run.run(CalculatorBackend(candidates=[partial, BINDINGS]))
    assert result['reason'] == 'NO_VERIFIED_FAILURE', result
    assert result['accepted_versions'] == 2
    assert result['attempts'][1]['parent'] == 'candidate-1'
    assert result['champion'] == 'candidate-2'
