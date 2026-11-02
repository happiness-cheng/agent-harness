"""Memory评测：纯离线、来源级召回基线；不调用模型，不修改生产Memory。"""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from uuid import NAMESPACE_URL, uuid5

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


REFERENCE_COMMIT = 'dbc046eb896ac4e39aa19c7774c8bf49583b89a6'


def instant(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('时间必须包含时区')
    return parsed


def evidence(case):
    return {f"{h['session']}:{m['id']}": (h, m)
            for h in case['histories'] for m in h['messages']}


def permitted(history, query):
    return (history['owner'] == query['owner']
            and instant(history['available_at']) <= instant(query['at'])
            and (history['scope'] == 'user' or history['session'] == query['session']))


def validate_suite(data):
    """固定来源必须存在且不越权；不因坏样本偷偷减少分母。"""
    try:
        ids = set()
        if data['version'] != 'memory-suite-v2' or data['synthetic'] is not True:
            raise ValueError('当前入口只接受修正判据后的v2合成评测，旧材料只供审计')
        if not isinstance(data['cases'], list) or not data['cases']:
            raise ValueError('评测集不能为空')
        for case in data['cases']:
            if case['id'] in ids: raise ValueError('重复案例ID')
            ids.add(case['id'])
            query, gold = case['query'], case['gold']
            instant(query['at'])
            for field in ('owner', 'session', 'text', 'retrieval_query'):
                if not isinstance(query[field], str) or not query[field].strip():
                    raise ValueError('问题字段为空')
            sessions = set()
            for h in case['histories']:
                instant(h['available_at'])
                if h['session'] in sessions or h['scope'] not in {'user', 'session'}:
                    raise ValueError('会话ID重复或范围无效')
                sessions.add(h['session'])
                message_ids = set()
                for m in h['messages']:
                    if (m['id'] in message_ids or m['role'] not in {'user', 'assistant'}
                            or not isinstance(m['content'], str) or not m['content']):
                        raise ValueError('消息ID、角色或内容无效')
                    message_ids.add(m['id'])
            sources = evidence(case)
            sets = []
            for field in ('required_evidence', 'allowed_evidence', 'forbidden_evidence'):
                refs = gold[field]
                if not isinstance(refs, list) or len(refs) != len(set(refs)) or not set(refs) <= sources.keys():
                    raise ValueError('未知或重复来源')
                sets.append(set(refs))
            required, allowed, forbidden = sets
            hard_forbidden = gold['hard_forbidden_evidence']
            if (not isinstance(hard_forbidden, list) or len(hard_forbidden) != len(set(hard_forbidden))
                    or not set(hard_forbidden) <= forbidden):
                raise ValueError('硬边界来源必须属于禁止来源')
            if not required <= allowed or allowed & forbidden:
                raise ValueError('gold允许/禁止来源冲突')
            if type(gold['need_memory']) is not bool or gold['need_memory'] != bool(required):
                raise ValueError('需要记忆标识与必要来源不一致')
            if any(not permitted(sources[r][0], query) for r in allowed):
                raise ValueError('gold不能使用别人的、未来的或私有会话的资料')
            if case['split'] not in {'dev', 'acceptance'} or not gold['expected_facts']:
                raise ValueError('缺少划分或语义期望')
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f'评测集格式错误: {exc}') from exc


def component_input(case):
    """gold不进入被测组件；不同用户分别入库，留给组件接受隔离挑战。"""
    query = {k: case['query'][k] for k in ('owner', 'session', 'at', 'text', 'retrieval_query')}
    histories = [deepcopy(h) for h in case['histories']
                 if instant(h['available_at']) <= instant(query['at'])]
    return {'histories': histories, 'query': query}


def score_retrieval(case, returned):
    sources = evidence(case)
    refs = set(returned)
    if not refs <= sources.keys(): raise ValueError('被测组件返回未知来源')
    gold = case['gold']
    required, allowed = set(gold['required_evidence']), set(gold['allowed_evidence'])
    forbidden = set(gold['forbidden_evidence'])
    # 权限及时点属于硬边界，即使某份gold漏标，也不能把越界材料计为合法命中。
    boundaries = {r for r, (h, _) in sources.items() if not permitted(h, case['query'])}
    forbidden |= boundaries
    leaked = sorted(refs & (boundaries | set(gold['hard_forbidden_evidence'])))
    return dict(required_recall=len(refs & required) / len(required) if required else None,
                evidence_precision=len(refs & allowed) / len(refs) if refs else None,
                forbidden_returned=sorted(refs & forbidden), hard_boundary_returned=leaked,
                hard_failure=bool(leaked),
                unnecessary_retrieval=not gold['need_memory'] and bool(refs),
                safety_claim='no_leak_observed_not_proof' if not leaked else 'boundary_violation',
                returned=sorted(refs),
                returned_utf8_bytes=sum(len(sources[r][1]['content'].encode('utf-8')) for r in refs))


def seed_history(store, context, history):
    from min_agent.models import Message
    task = store.begin(context)
    store.append(context, task.task_id, [Message(role=m['role'], content=m['content'])
                                          for m in history['messages']])
    store.finish(context, task.task_id, status='completed')


def legacy_retrieve(request):
    """走当前真实归档/召回路径。不会把其他会话资料偷偷并入当前会话。"""
    from min_agent.memory import MemoryJournal
    from min_agent.sessions import SessionStore
    store = SessionStore()
    contexts = {}
    for h in request['histories']:
        context = store.create(uuid5(NAMESPACE_URL, 'memory-eval-owner:' + h['owner']))
        contexts[h['session']] = (context, h)
        seed_history(store, context, h)
    query = request['query']
    owner = uuid5(NAMESPACE_URL, 'memory-eval-owner:' + query['owner'])
    if query['session'] in contexts:
        context, history = contexts[query['session']]
        if context.owner_id != owner: raise PermissionError('查询会话不属于当前用户')
    else:
        context, history = store.create(owner), None
    records = MemoryJournal(store, context).select(query['retrieval_query'])
    material = []
    for record in records:
        for i, message in enumerate(record['messages'], record['start']):
            if history is None: raise ValueError('新会话不应返回历史归档')
            source = history['messages'][i]
            if source['content'] != message['content']: raise ValueError('来源映射不匹配')
            material.append(dict(source=f"{history['session']}:{source['id']}",
                                 role=message['role'], content=message['content']))
    return dict(returned=[m['source'] for m in material], material=material,
                archived_histories=len(request['histories']))


def full_history_retrieve(request):
    """诊断上界：不做相关性或遗忘筛选，故意保留全历史的缺点。"""
    material = [dict(source=f"{h['session']}:{m['id']}", role=m['role'], content=m['content'])
                for h in request['histories'] if permitted(h, request['query']) for m in h['messages']]
    return dict(returned=[m['source'] for m in material], material=material)


def run_baselines(data):
    import min_agent.memory
    validate_suite(data)
    results = []
    adapters = dict(no_memory=lambda request: dict(returned=[], material=[]),
                    legacy_current_session=legacy_retrieve, full_history_diagnostic=full_history_retrieve)
    for case in data['cases']:
        for name, adapter in adapters.items():
            start = perf_counter()
            output = adapter(component_input(case))
            elapsed = (perf_counter() - start) * 1000
            results.append(dict(case_id=case['id'], split=case['split'], category=case['category'],
                                baseline=name, output=output, score=score_retrieval(case, output['returned']),
                                setup_and_retrieval_ms=round(elapsed, 3)))
    summaries = {}
    for name in adapters:
        rows = [r['score'] for r in results if r['baseline'] == name]
        positives = [r for r in rows if r['required_recall'] is not None]
        negatives = [r for r in rows if r['required_recall'] is None]
        summaries[name] = dict(cases=len(rows), memory_needed=len(positives),
            all_required_retrieved=sum(r['required_recall'] == 1 for r in positives),
            mean_required_recall=sum(r['required_recall'] for r in positives) / len(positives) if positives else None,
            no_memory_needed=len(negatives), unnecessary_retrieval=sum(r['unnecessary_retrieval'] for r in rows),
            hard_failures=sum(r['hard_failure'] for r in rows),
            retrieved_utf8_bytes=sum(r['returned_utf8_bytes'] for r in rows))
    return dict(suite_version=data['version'], split_note=data['split_note'],
        live_model_requests=0, token_usage=None, runtime_source=str(Path(min_agent.memory.__file__).resolve()),
        runtime_source_sha256=hashlib.sha256(Path(min_agent.memory.__file__).read_bytes()).hexdigest(),
        stages=dict(write='raw_archive_only_not_semantic_memory', retrieval='executed',
                    delivery='not_run', answer='not_run', judge='not_run'),
        measurement_note='UTF-8正文大小不等于token；耗时包含建库/归档，不是纯检索延迟。安全硬失败按来源暴露计，不代表已生成泄漏回答。',
        summaries=summaries, results=results)


def run_controls(directory):
    from min_agent.memory import MemoryJournal
    from min_agent.sessions import SessionStore
    from min_agent.storage import SQLiteStorage
    path = Path(directory) / 'synthetic-memory-control.sqlite3'
    with SQLiteStorage.create_new(path) as storage:
        store = SessionStore(storage=storage)
        context = store.create(uuid5(NAMESPACE_URL, 'control-alpha'))
        seed_history(store, context, dict(messages=[dict(role='user', content='合成阳性标记PINE-42。')]))
        journal = MemoryJournal(store, context)
        record = journal.select('PINE-42')[0]
        source = record['events'][0]['source']
        result = dict(same_session_recall=journal.read_source(source)['text'] == '合成阳性标记PINE-42。')
        for key, owner in [('same_owner_other_session_denied', context.owner_id),
                           ('foreign_owner_source_denied', uuid5(NAMESPACE_URL, 'control-beta'))]:
            other = store.create(owner)
            try:
                MemoryJournal(store, other).read_source(source)
            except PermissionError:
                result[key] = True
            else:
                result[key] = False
    with SQLiteStorage.open(path) as storage:
        journal = MemoryJournal(SessionStore(storage=storage), context)
        result['sqlite_reopen_source'] = (journal.read_source(source)['text'] == '合成阳性标记PINE-42。'
                                          and bool(journal.select('PINE-42')))
    result['scope_note'] = '现有当前会话引用与重开控制；不证明未来跨会话检索器的隔离、遗忘或语义质量。'
    return result


def audit_book(root):
    """只读YAML/JSON，不导入或运行参考项目的API/裁判客户端。"""
    import yaml
    root = Path(root).resolve()
    commit = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                            check=True, capture_output=True, text=True).stdout.strip()
    if commit != REFERENCE_COMMIT: raise ValueError('参考源码提交与固定版本不一致')
    dirty = subprocess.run(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=all',
                            '--', 'chapter3/user-memory-evaluation'],
                           check=True, capture_output=True, text=True).stdout.strip()
    if dirty: raise ValueError('参考目录存在本地修改，不能冒充固定提交的清单')
    base = root / 'chapter3/user-memory-evaluation'
    rows, ids = [], set()
    for path in sorted((base / 'test_cases').rglob('*.yaml')):
        raw = path.read_bytes()
        case = yaml.safe_load(raw)
        if case['test_id'] in ids: raise ValueError('参考集案例ID重复')
        ids.add(case['test_id'])
        counts = [sum(m['role'] == 'user' for m in h['messages']) for h in case['conversation_histories']]
        rows.append(dict(path=path.relative_to(root).as_posix(), sha256=hashlib.sha256(raw).hexdigest(),
                         test_id=case['test_id'], category=case['category'], histories=len(counts),
                         user_rounds_per_history=counts))
    if len(rows) != 60 or Counter(r['category'] for r in rows) != dict(layer1=20, layer2=20, layer3=20):
        raise ValueError('固定参考集应为三层各20例')
    gold = json.loads((base / 'fixtures/gold_facts.json').read_text(encoding='utf-8'))
    responses = json.loads((base / 'fixtures/system_responses.example.json').read_text(encoding='utf-8'))
    fixture_hashes = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in [base / 'metrics.py', base / 'evaluator.py', base / 'models.py',
                                base / 'comparison.py', base / 'fixtures/gold_facts.json',
                                base / 'fixtures/system_responses.example.json']}
    return dict(repository='https://github.com/bojieli/ai-agent-book', commit=commit, yaml_version=yaml.__version__,
                license='Apache-2.0; 原始集不复制进本项目', cases=len(rows),
                layers=dict(Counter(r['category'] for r in rows)),
                gold_cases=len([k for k in gold if not k.startswith('_')]),
                supplied_response_counts={k: len(v) for k, v in responses.items() if not k.startswith('_')},
                min_user_rounds_per_history=min(n for r in rows for n in r['user_rounds_per_history']),
                max_total_user_rounds=max(sum(r['user_rounds_per_history']) for r in rows),
                source_hashes=fixture_hashes, manifest=rows,
                status='source_inventory_only; no upstream online or canned comparison executed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, default=Path(__file__).resolve().parents[1] / 'docs/acceptance/m02-memory-suite-v2.json')
    parser.add_argument('--report', type=Path, required=True, help='新建报告，不覆盖已有证据')
    parser.add_argument('--control-dir', type=Path, required=True, help='已有空目录，存放新建的合成控制数据库')
    parser.add_argument('--book-root', type=Path)
    args = parser.parse_args()
    # 在所有评测动作前拒绝覆盖；报告仍用排他创建避免竞争覆盖。
    if args.report.exists(): parser.error('报告已存在，请选择新路径')
    data = json.loads(args.suite.read_text(encoding='utf-8'))
    report = run_baselines(data)
    report['suite_sha256'] = hashlib.sha256(args.suite.read_bytes()).hexdigest()
    report['controls'] = run_controls(args.control_dir)
    if args.book_root: report['reference_audit'] = audit_book(args.book_root)
    with args.report.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(dict(summaries=report['summaries'], controls=report['controls'],
                          live_model_requests=0), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
