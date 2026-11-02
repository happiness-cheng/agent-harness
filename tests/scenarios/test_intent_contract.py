"""验证核验器与真实业务边界，不验证脚本模型的自然语言能力。"""

import json
from importlib import import_module

import pytest

from min_agent.models import ModelResponse, ToolCall
from tests.fakes.model import ScriptedModel
from tests.scenarios.intent_cases import CASES, run_case


def case_named(name):
    return next(case for case in CASES if case.id == name)


def stop(text="合成可见回答，语义仍需人工审阅"):
    return ModelResponse(finish_reason="stop", content=text)


def add(call_id="add"):
    return ModelResponse(finish_reason="tool_calls", tool_calls=[
        ToolCall(id=call_id, name="todo_add", arguments={"title": "写周报", "due_date": "明天"}),
    ])


@pytest.mark.parametrize("name", ["explicit", "implicit"])
def test_oracle_requires_real_add_and_result_return(name):
    model = ScriptedModel([add(), stop("已记录")])
    report = run_case(case_named(name), model)
    assert report["structural_ok"] is True
    assert report["semantic_review"] == "pending"
    assert report["items"] == [{"title": "写周报", "due_date": "2026-10-21", "status": "pending"}]
    tool_result = next(message for message in model.calls[1] if message.role == "tool")
    assert json.loads(tool_result.content)["ok"] is True


def test_claiming_add_without_executing_does_not_pass():
    report = run_case(case_named("explicit"), ScriptedModel([stop("已经记录写周报")]))
    assert report["structural_ok"] is False and report["items"] == []


@pytest.mark.parametrize("name", ["negative", "ambiguous", "correction"])
def test_no_write_cases_require_no_write_attempt_and_keep_state(name):
    case = case_named(name)
    model = ScriptedModel([stop() for _ in case.prompts])
    report = run_case(case, model)
    assert report["structural_ok"] is True
    assert all(turn["attempted_tools"] == [] for turn in report["turns"])
    assert report["semantic_review"] == "pending"


def test_negative_case_detects_write_even_with_matching_prior_authorization():
    report = run_case(case_named("negative"), ScriptedModel([add(), stop()]))
    assert report["structural_ok"] is False
    assert report["turns"][0]["succeeded_tools"] == ["todo_add"]


def test_ambiguous_case_cannot_pass_just_because_guard_blocked_a_guess():
    report = run_case(case_named("ambiguous"), ScriptedModel([add()]))
    assert report["structural_ok"] is False
    assert report["turns"][0]["runtime_status"] == "waiting_user"
    assert len(report["items"]) == 2


def test_plan_case_checks_mechanical_block_separately_from_semantics():
    report = run_case(case_named("plan_boundary"), ScriptedModel([add()]))
    assert report["structural_ok"] is True
    assert report["items"] == []
    assert report["turns"][0]["error"] == "plan_read_only"
    assert report["semantic_review"] == "pending"


def test_correction_keeps_actual_first_turn_history_in_second_turn():
    case = case_named("correction")
    model = ScriptedModel([stop("先不记录明天写周报"), stop("已按后天理解，尚未记录")])
    report = run_case(case, model)
    assert report["structural_ok"] is True
    assert [message.content for message in model.calls[1][:-1] if message.role != "system"][-3:] == [
        case.prompts[0], "先不记录明天写周报", case.prompts[1],
    ]


@pytest.mark.parametrize("script", [[stop("没有提醒能力，尚未记录")], [add(), stop("已记日期待办，无法主动提醒")]])
def test_reminder_allows_clarification_or_real_todo_but_requires_text_review(script):
    report = run_case(case_named("reminder_boundary"), ScriptedModel(script))
    assert report["structural_ok"] is True
    assert report["semantic_review"] == "pending"


def test_tool_loop_cannot_consume_more_than_two_model_calls_per_turn():
    # 使用只读请求验证轮数门，避免已消费写授权先拦住第二次新增。
    model = ScriptedModel([ModelResponse(finish_reason="tool_calls", tool_calls=[
        ToolCall(id=identity, name="todo_list", arguments={}),
    ]) for identity in ("one", "two")] + [stop()])
    report = run_case(case_named("explicit"), model)
    assert report["structural_ok"] is False
    assert len(model.calls) == 2
    assert report["turns"][0]["errors"] == [{"error": "intent_turn_budget_exhausted", "diagnostics": {}}]


def test_query_then_add_then_answer_fits_explicit_three_call_budget():
    script = [
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="read", name="todo_list", arguments={}),
        ]),
        add(), stop("已记录，未设置主动提醒"),
    ]
    limited = run_case(case_named("implicit"), ScriptedModel(script))
    assert limited["structural_ok"] is False
    assert limited["turns"][0]["succeeded_tools"] == ["todo_list", "todo_add"]
    assert limited["turns"][0]["errors"][0]["error"] == "intent_turn_budget_exhausted"
    model = ScriptedModel(script)
    report = run_case(case_named("implicit"), model, max_turn_requests=3)
    assert report["structural_ok"] is True
    assert len(model.calls) == 3
    assert report["turns"][0]["succeeded_tools"] == ["todo_list", "todo_add"]
    assert report["items"] == [{"title": "写周报", "due_date": "2026-10-21", "status": "pending"}]


@pytest.mark.parametrize("limit", [0, True, 17, 3.0])
def test_invalid_turn_budget_is_rejected_before_model(limit):
    model = ScriptedModel([stop()])
    with pytest.raises(ValueError):
        run_case(case_named("implicit"), model, max_turn_requests=limit)
    assert model.calls == []


def test_explicit_three_call_budget_still_blocks_a_fourth_call():
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id=f"read-{index}", name="todo_list", arguments={}),
        ]) for index in range(3)
    ] + [stop()])
    report = run_case(case_named("negative"), model, max_turn_requests=3)
    assert len(model.calls) == 3 and report["structural_ok"] is False
    assert report["turns"][0]["errors"] == [{"error": "intent_turn_budget_exhausted", "diagnostics": {}}]


@pytest.mark.parametrize("enabled", [None, "0", "yes"])
def test_live_gate_skips_before_reading_credentials(monkeypatch, enabled):
    live = import_module("tests.live.test_intent_contract")
    if enabled is None:
        monkeypatch.delenv("AGENT_HARNESS_LIVE_INTENT", raising=False)
    else:
        monkeypatch.setenv("AGENT_HARNESS_LIVE_INTENT", enabled)
    monkeypatch.setattr(live.DeepSeekSettings, "from_credentials_file", lambda *a, **k: pytest.fail("不应读取凭据"))
    with pytest.raises(pytest.skip.Exception):
        live.test_deepseek_intent_contract()


@pytest.mark.parametrize("missing", ["AGENT_HARNESS_CREDENTIALS_FILE", "AGENT_HARNESS_PROXY"])
def test_live_gate_requires_explicit_file_and_proxy_before_credentials(monkeypatch, missing):
    live = import_module("tests.live.test_intent_contract")
    monkeypatch.setenv("AGENT_HARNESS_LIVE_INTENT", "1")
    monkeypatch.setenv("AGENT_HARNESS_CREDENTIALS_FILE", "explicit-test-keys.json")
    monkeypatch.setenv("AGENT_HARNESS_PROXY", "http://127.0.0.1:19001")
    monkeypatch.delenv(missing)
    monkeypatch.setattr(live.DeepSeekSettings, "from_credentials_file", lambda *a, **k: pytest.fail("不应读取凭据"))
    with pytest.raises(pytest.fail.Exception, match="显式指定"):
        live.test_deepseek_intent_contract()


@pytest.mark.parametrize("fail_first", [False, True])
def test_live_entry_shares_one_counter_and_stops_on_first_failure(monkeypatch, capsys, fail_first):
    live = import_module("tests.live.test_intent_contract")
    monkeypatch.setenv("AGENT_HARNESS_LIVE_INTENT", "1")
    monkeypatch.setenv("AGENT_HARNESS_CREDENTIALS_FILE", "explicit-test-keys.json")
    monkeypatch.setenv("AGENT_HARNESS_PROXY", "http://127.0.0.1:19001")
    options, instances = [], []
    marker = object()

    def settings(path, **kwargs):
        options.append((path, kwargs))
        return marker

    class FakeClient(ScriptedModel):
        def __init__(self, configured, *, proxy):
            assert configured is marker and proxy == "http://127.0.0.1:19001"
            super().__init__([stop()] if fail_first else [
                add("explicit"), stop(), add("implicit"), stop(), stop(), stop(), stop(), stop(), stop(), stop(),
            ])
            instances.append(self)

        @property
        def request_count(self):
            return len(self.calls)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(live.DeepSeekSettings, "from_credentials_file", settings)
    monkeypatch.setattr(live, "DeepSeekResponsesModel", FakeClient)
    if fail_first:
        with pytest.raises(pytest.fail.Exception, match="explicit"):
            live.test_deepseek_intent_contract()
    else:
        live.test_deepseek_intent_contract()
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(instances) == 1
    assert options == [("explicit-test-keys.json", {"max_requests": 16, "max_output_tokens": 4096})]
    assert lines[-1]["requests"] == len(instances[0].calls) == (1 if fail_first else 10)
    assert lines[-1]["semantic_review"] == "pending"
    assert lines[-1]["cases_run"] == (1 if fail_first else 7)
