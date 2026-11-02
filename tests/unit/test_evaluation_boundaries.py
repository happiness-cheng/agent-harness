"""验证器的新反例：中断、未知副作用及不可比较报告不得放行。"""
import pytest

from min_agent.evaluation.contracts import Observation
from min_agent.evaluation.runner import compare_results
from min_agent.evaluation.suites import task_by_id
from min_agent.evaluation.verifier import verify


def calc(**changes):
    task = task_by_id('runtime.calc')
    data = dict(run_id='probe', task_id=task.id, version_id='fixture',
                execution_status='completed', answer='5',
                budget={'usage': {'model_calls': 1, 'tool_calls': 1}},
                tools=[{'name': 'calculator', 'result': {'ok': True, 'result': '5'}}])
    data.update(changes)
    return verify(task, Observation(**data))


def test_unknown_side_effect_is_not_hidden_by_correct_answer():
    assert calc(trace=[{'execution_state': 'unknown'}]).verdict == 'UNKNOWN'


def test_failed_execution_cannot_pass_using_stale_correct_answer():
    assert calc(execution_status='failed').verdict == 'UNKNOWN'


def test_negative_usage_cannot_pass_budget_check():
    assert calc(budget={'usage': {'model_calls': -1, 'tool_calls': 1}}).verdict == 'UNKNOWN'


def test_malformed_tool_observation_returns_unknown_instead_of_crashing():
    assert calc(tools=[{'name': 'calculator', 'result': None}]).verdict == 'UNKNOWN'


def test_comparison_rejects_missing_checks():
    before = calc()
    after = before.model_copy(update={'checks': before.checks[:-1]})
    with pytest.raises(ValueError):
        compare_results(before, after)


def test_comparison_matches_checks_by_identity_not_position():
    before = calc()
    checks = [c.model_copy(update={'verdict': 'FAIL'}) if c.id == 'answer' else c
              for c in reversed(before.checks)]
    after = before.model_copy(update={'checks': checks, 'verdict': 'FAIL'})
    result = compare_results(before, after)
    assert result['changed_checks'] == [{'id': 'answer', 'before': 'PASS', 'after': 'FAIL'}]


@pytest.mark.parametrize('answer,expected', [
    ('计算结果已得出：**2 + 3 = 5**。','PASS'),
    ('计算结果已得出：**2 + 3 = 999**。','FAIL'),
    ('计算结果已得出：**9 + 4 = 5**。','UNKNOWN'),
    ('虽然出现5，但最后的答案是999','UNKNOWN'),
])
def test_local_model_explicit_markdown_equation(answer,expected):
    assert calc(answer=answer).verdict == expected
