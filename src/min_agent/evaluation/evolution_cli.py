"""显式启动有界进化批次；不创建后台定时任务或自动发布。"""
import argparse
import json
from pathlib import Path

from min_agent.evaluation.contracts import CheckSpec, TaskSpec
from min_agent.evaluation.evolution import EvolutionPolicy, EvolutionSession
from min_agent.evaluation.evolution_backend import CalculatorBackend
from min_agent.evaluation.runner import ROOT


def curriculum():
    def task(identity, expression, expected):
        return TaskSpec(id=identity, category='runtime', profile='calculator',
                        prompt=f'请调用calculator计算{expression}。最终只输出计算结果数值，不要解释或Markdown。',
                        parameters={'expression': expression}, tools=['calculator'],
                        live_eligible=True, max_model_calls=3, max_rounds=3,
                        segment_model_calls=3, max_tool_calls=3, timeout_seconds=90,
                        checks=[CheckSpec(id='value', kind='calculator_value', expected=expected)])
    return ([task('evolution.dev.add', '2+3', '5'), task('evolution.dev.subtract', '9-4', '5')],
            [task('evolution.holdout.add', '17+8', '25'),
             task('evolution.holdout.multiply', '3*7', '21'),
             task('evolution.holdout.divide', '81/9', '9')])


def main():
    parser = argparse.ArgumentParser(description='验证器约束的实验进化V1，默认离线')
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    init.add_argument('--out', type=Path, required=True)
    init.add_argument('--source', type=Path, default=ROOT)
    init.add_argument('--live', action='store_true')
    init.add_argument('--request-ceiling', type=int, default=0)
    init.add_argument('--cost-limit', type=float, default=0)
    init.add_argument('--seconds', type=int, default=900)
    init.add_argument('--max-candidates', type=int, default=3)
    run = sub.add_parser('run')
    run.add_argument('--state', type=Path, required=True)
    run.add_argument('--live', action='store_true')
    run.add_argument('--credentials', type=Path)
    run.add_argument('--proxy')
    run.add_argument('--candidates', type=Path, help='离线重放JSON数组，不伪装为模型生成')
    status = sub.add_parser('status')
    status.add_argument('--state', type=Path, required=True)
    rollback = sub.add_parser('rollback')
    rollback.add_argument('--state', type=Path, required=True)
    rollback.add_argument('--version', required=True)
    args = parser.parse_args()
    if args.command == 'init':
        development, holdout = curriculum()
        session = EvolutionSession.create(args.out, args.source, development, holdout,
                                          EvolutionPolicy(live=args.live, max_requests=args.request_ceiling,
                                                          max_usd=args.cost_limit, max_seconds=args.seconds,
                                                          max_candidates=args.max_candidates))
        print(session.path)
        return
    session = EvolutionSession(args.state)
    if args.command == 'status':
        result = session.read()
    elif args.command == 'rollback':
        result = session.rollback(args.version)
    else:
        candidates = json.loads(args.candidates.read_text(encoding='utf-8')) if args.candidates else []
        if not isinstance(candidates, list):
            parser.error('离线候选必须为JSON数组')
        backend = CalculatorBackend(live=args.live, credentials=args.credentials,
                                    proxy=args.proxy, candidates=candidates)
        result = session.run(backend)
    print(json.dumps({k: result[k] for k in ('status', 'reason', 'champion', 'accepted_versions',
                                           'request_slots_used', 'observed_requests',
                                           'spent_usd_upper', 'reserved_usd')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
