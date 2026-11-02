"""夜间CLI；不设开机启动，未指定live不调用真实模型。"""
import argparse
import json
from pathlib import Path
from min_agent.evaluation.night import NightQueue,NightPolicy,NightJob,NightOutcome,run_night,write_morning
from min_agent.evaluation.suites import task_by_id


class SimulatedClock:
    def __init__(self):self.now=0.0
    def time(self):return self.now
    def sleep(self,seconds):self.now+=seconds


def demo(out):
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    queue=NightQueue(out/'queue.json');queue.initialize(NightPolicy(deepseek_usd=1,codex_calls=2))
    jobs=[NightJob(id='A-approval',goal='审批阻塞反例',task=task_by_id('runtime.calc'),requires_confirmation=True),
        NightJob(id='B-independent',goal='真实Runtime验证独立计算',task=task_by_id('runtime.calc')),
        NightJob(id='C-deepseek',goal='模拟DeepSeek费用门禁',kind='analysis',task=task_by_id('runtime.calc'),max_deepseek_usd=.7),
        NightJob(id='D-deepseek-blocked',goal='预算不足不得启动',kind='analysis',task=task_by_id('runtime.calc'),max_deepseek_usd=.7),
        NightJob(id='E-codex',goal='模拟Codex调用槽位',kind='code',task=task_by_id('runtime.calc')),
        NightJob(id='F-codex-blocked',goal='Codex槽位不足不得启动',kind='code',task=task_by_id('runtime.calc')),
        NightJob(id='G-failure',goal='实际虚报失败应有限重试',task=task_by_id('tools.false_success'),max_retries=1),
        NightJob(id='H-doubao',goal='豆包人工接力',kind='manual',task=task_by_id('runtime.calc'))]
    for job in jobs:queue.add(job)
    clock=SimulatedClock()
    def simulated(job,route,directory):
        from min_agent.evaluation.runner import run_task
        result=run_task(job.task,directory)
        clock.sleep(3600)
        return NightOutcome(verdict=result.verdict,evidence_refs=[str(directory/'result.json')],reason=','.join(result.failures),
            deepseek_usd_upper=.7 if route=='deepseek' else 0,codex_invocations=1 if route=='codex' else 0)
    result=run_night(queue,executor=simulated,clock=clock,wait=True)
    result['demo_notice']='SIMULATED_10_HOURS_AND_ROUTING; actual Harness/SQLite/verifier, zero real model calls; simulated costs are not bills'
    (out/'simulation-summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(simulated_elapsed_seconds=clock.time(),states={k:v['status'] for k,v in result['jobs'].items()},real_model_calls=0),ensure_ascii=False))


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init');init.add_argument('--queue',type=Path,required=True);init.add_argument('--jobs',type=Path,required=True)
    init.add_argument('--hours',type=float,default=10);init.add_argument('--deepseek-budget',type=float,default=0);init.add_argument('--codex-calls',type=int,default=0);init.add_argument('--reserve',type=float,default=.2)
    add=sub.add_parser('add');add.add_argument('--queue',type=Path,required=True);add.add_argument('--job',type=Path,required=True)
    run=sub.add_parser('run');run.add_argument('--queue',type=Path,required=True);run.add_argument('--wait',action='store_true')
    run.add_argument('--live',action='store_true');run.add_argument('--credentials',type=Path);run.add_argument('--proxy')
    report=sub.add_parser('report');report.add_argument('--queue',type=Path,required=True)
    demo_parser=sub.add_parser('demo');demo_parser.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.command=='demo':demo(a.out);return
    queue=NightQueue(a.queue)
    if a.command=='init':
        jobs=[NightJob.model_validate(v) for v in json.loads(a.jobs.read_text(encoding='utf-8'))]
        queue.initialize(NightPolicy(hours=a.hours,deepseek_usd=a.deepseek_budget,codex_calls=a.codex_calls,reserve_ratio=a.reserve))
        for job in jobs:queue.add(job)
    elif a.command=='add':queue.add(NightJob.model_validate_json(a.job.read_text(encoding='utf-8')))
    elif a.command=='report':write_morning(queue,queue.read())
    else:
        if a.live and a.credentials is None:p.error('live需要显式项目外凭据；不会暗中寻找或换账户')
        run_night(queue,wait=a.wait,live=a.live,credentials=a.credentials,proxy=a.proxy)


if __name__=='__main__':main()
