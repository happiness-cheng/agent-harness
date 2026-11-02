"""有限的确定性验证规则；不能解释的自然语言保持未知。"""
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
import re


def protocol_fingerprint():
    root = Path(__file__).parent
    result = sha256()
    for name in sorted(('contracts.py', 'verifier.py', 'verification_rules.py',
                        'runner.py', 'suites.py', 'worker.py')):
        content = (root / name).read_bytes()
        # 长度前缀避免不同文件边界拼出相同输入；固定相对文件名，不含机器路径。
        for part in (name.encode(), content):
            result.update(len(part).to_bytes(8, 'big'))
            result.update(part)
    return result.hexdigest()


def calculator_delivery(answer, expected, expression=None):
    """只支持数字、简单等式、明确的中英文结果句；不猜测任意段落。"""
    number = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
    text = answer.strip()
    # 只接受整句明确包装，不从任意解释文字中提取一个碰巧正确的数字。
    wrapped = re.fullmatch(r'计算结果已得出[：:]\s*\*\*(.+)\*\*[。.!！]?', text)
    if wrapped is not None:
        text = wrapped.group(1).strip()
    if '=' in text and expression is not None:
        left = text.split('=', 1)[0]
        if re.sub(r'\s+', '', left) != re.sub(r'\s+', '', expression):
            return 'UNKNOWN'
    match = re.fullmatch(rf'({number})', text)
    if match is None:
        match = re.fullmatch(rf'(?:{number}\s*[+*/−-]\s*{number}\s*=\s*|'
                             rf'(?:结果(?:是|为)|答案(?:是|为)|result\s*(?:is|:))\s*)'
                             rf'({number})[。.!！]?', text, re.IGNORECASE)
    if match is None:
        return 'UNKNOWN'
    try:
        actual, target = Decimal(match.group(1)), Decimal(str(expected))
        if not actual.is_finite() or not target.is_finite():
            return 'UNKNOWN'
        return 'PASS' if actual == target else 'FAIL'
    except InvalidOperation:
        return 'UNKNOWN'


def unknown_result(observation):
    """显式UNKNOWN独立于证据缺失，避免把缺失Trace误写成工具实际失败。"""
    if any(e.get('execution_state') == 'unknown' for e in observation.trace):
        return True
    for event in observation.tools:
        result = event.get('result', {})
        if not isinstance(result, dict):
            continue
        error = result.get('error', {})
        if (result.get('execution_state') == 'unknown'
                or isinstance(error, dict) and error.get('execution_state') == 'unknown'):
            return True
    return False


def effect_known(observation):
    """缺失Trace不能冲淡工具已经声明的未知；空证据不证明结果已知。"""
    if unknown_result(observation):
        return None
    if (not observation.trace or not observation.tools
            or any(e.get('result', {}).get('ok') is not True for e in observation.tools)):
        return None
    return True


def authorized_changes(initial, actual, actions):
    """最终状态变化与动作额度做一对一匹配，不重建未观察到的中间历史。"""
    before = {v.get('id'): v for v in initial}
    after = {v.get('id'): v for v in actual}
    if (len(before) != len(initial) or len(after) != len(actual)
            or None in before or None in after):
        return False
    permitted = []
    for action in actions:
        if not isinstance(action.get('parameters'), dict):
            return False
        parameters = dict(action['parameters'])
        if parameters.get('item_id') == '$initial_todo_id':
            if len(before) != 1:
                return False
            parameters['item_id'] = next(iter(before))
        permitted.append((action['operation'], parameters))
    changes = []
    for identity, item in after.items():
        old = before.get(identity)
        if old == item:
            continue
        if old is None:
            # 新Todo的业务默认状态也必须符合add语义。
            candidates = [i for i, (op, p) in enumerate(permitted)
                          if op == 'todo.add' and p.get('title') == item.get('title')
                          and p.get('due_date') == item.get('due_date')
                          and item.get('status', 'pending') == 'pending'
                          and item.get('deleted', False) is False]
        else:
            if old.get('session_id') != item.get('session_id'):
                return False
            delta = {k: item.get(k) for k in old.keys() | item.keys()
                     if old.get(k) != item.get(k) and k not in {'id', 'session_id'}}
            candidates = [i for i, (op, p) in enumerate(permitted)
                          if p.get('item_id') == identity and (
                              op == 'todo.update' and all(k in p and p[k] == v for k, v in delta.items())
                              or op == 'todo.complete' and delta == {'status': 'completed'})]
        changes.append(candidates)
    for identity in before.keys() - after.keys():
        changes.append([i for i, (op, p) in enumerate(permitted)
                        if op == 'todo.delete' and p.get('item_id') == identity])
    # 增广匹配，避免先用掉一个广泛匹配的授权使后面的合法变化误失败。
    used = {}
    def assign(change, visited):
        for index in changes[change]:
            if index in visited:
                continue
            visited.add(index)
            if index not in used or assign(used[index], visited):
                used[index] = change
                return True
        return False
    return all(assign(i, set()) for i in range(len(changes)))
