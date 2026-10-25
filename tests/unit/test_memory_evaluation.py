"""评测器的判据独立于待评Memory实现。"""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('eval_memory', ROOT / 'scripts/eval_memory.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def suite():
    return json.loads((ROOT / 'docs/acceptance/m02-memory-suite-v2.json').read_text(encoding='utf-8'))


def test_frozen_suite_has_balanced_public_splits_and_valid_evidence():
    data = suite()
    evaluation.validate_suite(data)
    assert len(data['cases']) == 32
    assert sum(c['split'] == 'acceptance' for c in data['cases']) == 16
    for category in {c['category'] for c in data['cases']}:
        group = [c for c in data['cases'] if c['category'] == category]
        assert sorted(c['split'] for c in group) == ['acceptance', 'acceptance', 'dev', 'dev']


@pytest.mark.parametrize('change', ['missing_ref', 'foreign_gold', 'future_gold', 'overlap', 'duplicate', 'bad_time',
                                    'empty', 'hard_not_forbidden', 'private_gold'])
def test_invalid_suite_fails_explicitly_instead_of_silently_skipping(change):
    data = suite()
    case = data['cases'][0]
    if change == 'missing_ref': case['gold']['required_evidence'] = ['absent:m1']
    if change == 'foreign_gold': case['histories'][0]['owner'] = 'beta'
    if change == 'future_gold': case['histories'][0]['available_at'] = '2027-01-01T00:00:00+08:00'
    if change == 'overlap': case['gold']['forbidden_evidence'] = ['s1:m1']
    if change == 'duplicate': data['cases'].append(copy.deepcopy(case))
    if change == 'bad_time': case['query']['at'] = 'yesterday'
    if change == 'empty': data['cases'] = []
    if change == 'hard_not_forbidden': case['gold']['hard_forbidden_evidence'] = ['s1:m1']
    if change == 'private_gold': case['histories'][0]['scope'] = 'session'
    with pytest.raises(ValueError): evaluation.validate_suite(data)


def test_component_input_contains_no_gold_but_keeps_foreign_owner_challenge():
    case = suite()['cases'][24]
    request = evaluation.component_input(case)
    assert set(request) == {'histories', 'query'}
    assert {h['owner'] for h in request['histories']} == {'alpha', 'beta'}
    request['histories'][0]['messages'][0]['content'] = 'mutated'
    assert case['histories'][0]['messages'][0]['content'] != 'mutated'


def test_future_information_is_not_ingested():
    request = evaluation.component_input(suite()['cases'][7])
    assert [h['session'] for h in request['histories']] == ['s1']


def test_empty_retrieval_does_not_get_fake_precision_or_prove_isolation():
    result = evaluation.score_retrieval(suite()['cases'][24], [])
    assert result['required_recall'] == 0
    assert result['evidence_precision'] is None
    assert result['safety_claim'] == 'no_leak_observed_not_proof'


def test_stored_inventory_is_not_counted_as_recalled_and_unknown_ids_fail():
    case = suite()['cases'][0]
    assert evaluation.score_retrieval(case, [])['required_recall'] == 0
    with pytest.raises(ValueError): evaluation.score_retrieval(case, ['invented:m1'])


def test_foreign_leak_is_a_hard_failure_even_with_all_required_evidence():
    result = evaluation.score_retrieval(suite()['cases'][24], ['s1:m1', 's2:m1'])
    assert result['required_recall'] == 1
    assert result['hard_failure'] is True
    assert result['forbidden_returned'] == ['s2:m1']


def test_no_memory_case_penalizes_unnecessary_retrieval_without_fake_recall():
    result = evaluation.score_retrieval(suite()['cases'][16], ['s1:m1'])
    assert result['required_recall'] is None
    assert result['unnecessary_retrieval'] is True
    assert result['evidence_precision'] == 0


def test_old_version_may_be_returned_as_historical_evidence():
    result = evaluation.score_retrieval(suite()['cases'][4], ['s1:m1', 's2:m1'])
    assert result['hard_failure'] is False
    assert result['required_recall'] == result['evidence_precision'] == 1


@pytest.mark.parametrize('index, returned', [(18, ['s1:m1', 's2:m1']), (26, ['s1:m1', 's1:m2'])])
def test_irrelevant_or_unverified_source_is_not_itself_a_privacy_leak(index, returned):
    result = evaluation.score_retrieval(suite()['cases'][index], returned)
    assert result['hard_failure'] is False
    assert result['forbidden_returned']


def test_real_legacy_adapter_exposes_cross_session_gap_and_same_session_positive():
    case = suite()['cases'][0]
    request = evaluation.component_input(case)
    result = evaluation.legacy_retrieve(request)
    assert result['returned'] == []
    assert result['archived_histories'] == 1
    request['query']['session'] = 's1'
    positive = evaluation.legacy_retrieve(request)
    assert positive['returned'] == ['s1:m1']
    assert positive['material'][0]['content'] == case['histories'][0]['messages'][0]['content']


def test_full_history_diagnostic_does_not_use_gold_to_hide_forgetting_failure():
    case = suite()['cases'][20]
    result = evaluation.full_history_retrieve(evaluation.component_input(case))
    assert evaluation.score_retrieval(case, result['returned'])['hard_failure'] is True
    private = evaluation.full_history_retrieve(evaluation.component_input(suite()['cases'][22]))
    assert private['returned'] == []


def test_report_keeps_unrun_semantic_stages_and_real_request_count_separate():
    data = suite()
    data['cases'] = data['cases'][:1]
    report = evaluation.run_baselines(data)
    assert report['live_model_requests'] == 0
    assert report['stages']['answer'] == 'not_run'
    assert report['stages']['delivery'] == 'not_run'
    assert len(report['results']) == 3
    assert report['token_usage'] is None


def test_positive_restart_and_denied_source_controls_use_real_runtime(tmp_path):
    result = evaluation.run_controls(tmp_path)
    assert result['same_session_recall'] is True
    assert result['sqlite_reopen_source'] is True
    assert result['same_owner_other_session_denied'] is True
    assert result['foreign_owner_source_denied'] is True
