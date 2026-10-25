"""只读分析冻结的v07真实收据；不调用模型，不以Gold改变检索或排序。"""
import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from experiments.memory_v07.trial import load, make_fixture
from min_agent.long_memory.active_evidence import active_registry
from min_agent.long_memory.answer_eval import encoded


def save(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    base = ROOT / 'reports/memory_v07'
    live = json.loads((base / 'live.json').read_text(encoding='utf-8'))
    events = [json.loads(line) for line in (base / 'live.jsonl').read_text(encoding='utf-8').splitlines()]
    pack = load(base / 'prepared.json.gz')
    cases = {case['case_id']: case for case in pack['cases']}
    cells = []
    request_rows = []
    for cell in live['cells']:
        identity, group = cell['case_id'], cell['group']
        requests = [e for e in events if e['event'] == 'request' and e['case_id'] == identity and e['group'] == group]
        responses = [e for e in events if e['event'] == 'response' and e['case_id'] == identity and e['group'] == group]
        parsed = cell['validation'].get('parsed', {})
        answer = parsed.get('answer', '')
        gold = cases[identity]['gold']['required_facts']
        ref = encoded(cell['last_delivered_reference']).decode()
        # 字面计数仅辅助人工来源审查，不声称能证明一般语义正确性。
        hits = [fact for fact in gold if fact.casefold() in answer.casefold()]
        visible = [fact for fact in gold if fact.casefold() in ref.casefold()]
        valid_answer = cell['validation']['schema_valid']
        cells.append(dict(case_id=identity, group=group, attempted_requests=len(requests),
                          usable_responses=len(responses), tool_calls=cell['tool_calls'],
                          schema_valid=valid_answer, citation_valid=cell['validation']['citation_valid'],
                          gold_facts=gold, answer_literal_hits=hits if valid_answer else 'NOT_RUN',
                          visible_literal_hits=visible if requests else 'NOT_RUN',
                          input_tokens=cell['input_tokens'] if responses else 'UNKNOWN',
                          output_tokens=cell['output_tokens'] if responses else 'UNKNOWN',
                          peak_cost_estimate=cell['estimated_cost_usd'] if responses else 'UNKNOWN',
                          provider_latency_ms=cell['provider_latency_ms'] if responses else 'UNKNOWN',
                          stop_reason=cell['stop_reason'], semantic_review='see source-review.md'))
        for request in requests:
            budget = request['budget']
            response = next((e for e in responses if e['number'] == request['number']), None)
            request_rows.append(dict(case_id=identity, group=group, number=request['number'],
                wire_body_bytes=budget['wire_body_bytes'], history_tool_bytes=budget['history_tool_message_bytes'],
                tools_bytes=budget['wire_tool_schema_bytes'], memory_bytes=budget['current_memory_projection_bytes'],
                native_state_bytes=budget['native_state_bytes'], output_reserve=budget['output_reserve'],
                single_upper=budget['request_upper_units'], cumulative_upper=budget['cumulative_if_reserved'],
                single_limit=budget['single_limit'], cumulative_limit=budget['cumulative_limit'],
                preflight_gate=budget['gate'], response_received=response is not None,
                input_tokens=response['usage']['input_tokens'] if response else 'UNKNOWN',
                output_tokens=response['usage']['output_tokens'] if response else 'UNKNOWN'))
    completed_pairs = [identity for identity in cases if all(any(c['case_id'] == identity and c['group'] == g
                       and c['schema_valid'] for c in cells) for g in ('B0', 'B2'))]
    summary = dict(status=live['status'], stop_reason=live['stop_reason'],
                   attempted_requests=sum(e['event'] == 'request' for e in events),
                   usable_responses=sum(e['event'] == 'response' for e in events),
                   complete_pairs=completed_pairs, automatic_retries=0,
                   known_peak_cost_estimate=live['known_usage_peak_estimate_usd'],
                   all_requests_peak_fee_reserve=live['reserved_peak_usd'],
                   invoice='NOT_AVAILABLE', cells=cells, paired_groups={})
    for group in ('B0', 'B2'):
        chosen = [c for c in cells if c['case_id'] in completed_pairs and c['group'] == group]
        summary['paired_groups'][group] = dict(
            required_answer_hits=sum(len(c['answer_literal_hits']) for c in chosen),
            required_visible_hits=sum(len(c['visible_literal_hits']) for c in chosen),
            required_total=sum(len(c['gold_facts']) for c in chosen),
            schema_valid=sum(c['schema_valid'] for c in chosen),
            citation_valid=sum(c['citation_valid'] for c in chosen),
            input_tokens=sum(c['input_tokens'] for c in chosen),
            output_tokens=sum(c['output_tokens'] for c in chosen),
            peak_cost_estimate=sum(c['peak_cost_estimate'] for c in chosen),
            provider_latency_ms=sum(c['provider_latency_ms'] for c in chosen),
            model_attempts=sum(c['attempted_requests'] for c in chosen),
            tool_calls=sum(c['tool_calls'] for c in chosen))
    save(args.output_dir / 'metrics.json', summary)
    with (args.output_dir / 'requests.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(request_rows[0]))
        writer.writeheader()
        writer.writerows(request_rows)
    # 使用真实工具轨迹复现投影顺序，Gold只用于这个离线诊断的输出审查。
    bank = next(c for c in live['cells'] if c['case_id'] == 'layer1_01_bank_account' and c['group'] == 'B2')
    fixture = cases[bank['case_id']]
    memory, ctx, query = make_fixture(fixture)
    registry, state = active_registry(memory, ctx, query)
    registry.memory_view([])
    for event in bank['tool_events']:
        registry.get(event['name']).handler(**event['arguments'])
    view = registry.memory_view([])
    assert encoded(view) == encoded(bank['last_delivered_reference'])
    target = '99960778-2ed0-54d9-ba39-abf8cf18019e'
    assert target in state['discovered_ids'] and target in state['loaded_ids']
    assert target not in {e['source'] for e in view['evidence']}
    # 只保留实际请求过的第二组ID。它是对照诊断，不修改真实结果或生产策略。
    old_loaded = list(state['loaded_ids'])
    second_read = bank['tool_events'][-1]['arguments']['memory_ids']
    state['loaded_ids'] = second_read
    alternative = registry.memory_view([])
    diagnosis = dict(mode='OFFLINE_COUNTERFACTUAL_NOT_MODEL_RESULT',
        exact_actual_projection_reproduced=True, target_memory_id=target,
        search_position=1 + next(e for e in bank['tool_events'] if e['name']=='search_long_memory')['returned_ids'].index(target),
        requested_ids=old_loaded, actual_visible_ids=[e['source'] for e in view['evidence']],
        actual_reference_bytes=len(encoded(view)), fixed_reference_budget=4096,
        target_discovered=True, target_read_requested=True, target_actually_visible=False,
        alternative_second_read_only_ids=[e['source'] for e in alternative['evidence']],
        alternative_reference_bytes=len(encoded(alternative)),
        alternative_target_visible=target in {e['source'] for e in alternative['evidence']},
        gold_used_only_for_offline_diagnosis=True, external_requests=0)
    assert diagnosis['alternative_target_visible']
    save(args.output_dir / 'packing-replay.json', diagnosis)
    lines = ['# v0.7真实逐题完整回答', '', '问题每题只出现一次；原始JSON、实际消息和证据正文另存live.json/live.jsonl。', '']
    for identity, case in cases.items():
        lines += ['## ' + identity, '', '问题：' + case['question'], '']
        for group in ('B0', 'B2'):
            cell = next((c for c in live['cells'] if c['case_id']==identity and c['group']==group), None)
            lines += ['### ' + group, '']
            if cell is None:
                lines += ['NOT_RUN：前一请求失败后整批停止。', '']
            elif cell['raw_answer'] is None:
                lines += ['NO_USABLE_RESPONSE：已发起请求，未取得可用响应；usage/扣费未知，不是0。', '']
            else:
                lines += ['```json', cell['raw_answer'], '```', '', '实际送达的最终参考材料：', '```json',
                          json.dumps(cell['last_delivered_reference'],ensure_ascii=False,indent=2),'```','']
    (args.output_dir / 'answers.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k not in {'cells'}},ensure_ascii=False))


if __name__ == '__main__':
    main()
