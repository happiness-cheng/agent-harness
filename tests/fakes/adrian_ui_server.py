"""隔离浏览器验收夹具：真实Harness/HTTP，模型是脚本控制，不读凭据。"""
import argparse

from min_agent.application import HarnessApplication
from min_agent.models import ModelResponse
from min_agent.web.__main__ import run
from min_agent.web.launcher import DatabaseLease, identity_for
from tests.integration.test_adrian_memory_wiring import RecallModel
from tests.integration.test_long_memory_runtime import seed


class WorkbenchModel(RecallModel):
    def generate(self,messages,*,tools=None):
        if 'search_long_memory' not in {t['name'] for t in tools or []}:
            self.requests.append(messages)
            return ModelResponse(finish_reason='stop',content='普通任务离线答复')
        return super().generate(messages,tools=tools)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--database',required=True);parser.add_argument('--port',type=int,required=True)
    args=parser.parse_args()
    identity=identity_for(args.database,args.port,demo=False,memory_mode='active')
    with DatabaseLease(args.database,identity):
        harness=HarnessApplication(args.database,model=WorkbenchModel(),memory_mode='active')
        source=harness.create_session('合成来源')['session_id'];context=harness.context(source);seed(harness,context)
        harness.memory.remember(context,source_index=0)
        harness.create_session('跨会话回忆验收')
        run(harness,identity,open_browser=False)
