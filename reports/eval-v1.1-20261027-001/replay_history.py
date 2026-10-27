"""逐字重放历史最终响应，经过现有完整应用；不声称重测模型决策。"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from min_agent.evaluation import worker
from min_agent.evaluation.contracts import TaskSpec
from min_agent.evaluation.runner import save_json
from min_agent.evaluation.verifier import verify
from min_agent.model_context import ModelContextLimits
from min_agent.models import ModelResponse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--history', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    spec = TaskSpec.model_validate_json((args.history/'task.json').read_text(encoding='utf-8'))
    original = json.loads((args.history/'observation.json').read_text(encoding='utf-8'))
    assert not original['tools'] and original['model_requests'] == 1

    class Replay:
        def __init__(self, spec):
            self.request_count = 0

        def context_limits(self):
            return ModelContextLimits(model='historical-response-replay', context_window=65536,
                max_output_tokens=4096, source='local-replay-fixture-not-provider')

        def generate(self, messages, *, tools=None):
            self.request_count += 1
            assert self.request_count == 1
            save_json(out/'visible-request.json', {'messages':[
                m.model_dump(mode='json', exclude={'provider_state'}) for m in messages], 'tools':tools})
            return ModelResponse(finish_reason='stop', content=original['answer'])

    worker.TaskModel = Replay
    observation = worker.execute(spec, out, owner_id=uuid4())
    observation = observation.model_copy(update={'extra':dict(observation.extra,
        backend='HISTORICAL_FINAL_RESPONSE_REPLAY', external_generation_requests=0,
        history_sha256=sha256((args.history/'observation.json').read_bytes()).hexdigest())})
    result = verify(spec, observation)
    save_json(out/'task.json', spec)
    save_json(out/'observation.json', observation)
    save_json(out/'result.json', result)
    assert observation.answer == original['answer'] and not observation.tools
    assert result.verdict == 'FAIL'
    print(json.dumps({'verdict':result.verdict, 'model_decision_retested':False,
        'recorded_answer_identical':True, 'tools':0, 'external_generation_requests':0}))


if __name__ == '__main__':
    main()
