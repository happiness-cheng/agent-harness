import json
from pathlib import Path
from types import SimpleNamespace
import pytest


def test_codex_adapter_has_strict_schema_sandbox_and_observable_call_limit(tmp_path,monkeypatch):
    from min_agent.evaluation.codex_adapter import CodexCandidateModel
    from min_agent.models import Message
    import min_agent.evaluation.codex_adapter as module
    monkeypatch.setenv('OPENAI_API_KEY','SYNTHETIC_SHOULD_NOT_INHERIT')
    captured=[]
    def fake(command,**kwargs):
        captured.append((command,kwargs))
        assert 'OPENAI_API_KEY' not in kwargs['env'] and '--ignore-rules' not in command
        Path(command[command.index('--output-last-message')+1]).write_text(json.dumps({'bindings':{
            'Add':'add','Sub':'subtract','Mult':'multiply','Div':'divide'}}),encoding='utf-8')
        return SimpleNamespace(returncode=0,stdout=json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':5}}),stderr='')
    monkeypatch.setattr(module.subprocess,'run',fake)
    model=CodexCandidateModel(tmp_path/'receipts',tmp_path,executable='synthetic-codex',max_calls=1)
    response=model.generate([Message(role='user',content='公开合成任务')])
    assert response.finish_reason=='stop' and model.request_count==1
    with pytest.raises(PermissionError):model.generate([Message(role='user',content='不能再调用')])
    command=captured[0][0]
    assert command[command.index('--sandbox')+1]=='read-only'
    assert command[command.index('--ask-for-approval')+1]=='never'
    assert '--output-schema' in command and not any('danger' in a or 'yolo' in a for a in command)


def test_cost_gate_refuses_before_invoking_provider(tmp_path):
    from min_agent.evaluation.finance import CostLedger,CostBoundedModel
    class Fake:
        usage=[]
        def _build_body(self,*a):return {'max_output_tokens':10000}
        def generate(self,*a,**k):pytest.fail('预算不足不得生成')
    model=CostBoundedModel(Fake(),CostLedger(tmp_path/'cost.json',limit=.00001))
    with pytest.raises(PermissionError):model.generate([])
