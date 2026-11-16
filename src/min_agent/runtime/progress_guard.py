"""规范化重复调用候选检测；有新结果不视为停滞。"""

import json


def _stable(value):
    if isinstance(value, dict):
        return {key: _stable(item) for key, item in value.items()
                if key not in {"timestamp", "retrieved_at", "duration_ms"}}
    if isinstance(value, list):
        return [_stable(item) for item in value]
    if isinstance(value, str):
        try:
            return _stable(json.loads(value))
        except (ValueError, TypeError):
            return value
    return value


class ProgressGuard:
    def __init__(self, sessions=None, context=None):
        self._sessions, self._context = sessions, context
        self._state = sessions.extension(context, "progress_guard", {}) if sessions else {}

    def observe(self, call, outcome):
        result = json.dumps(_stable(outcome), sort_keys=True, ensure_ascii=False)
        correction = self._state.get("correction_result")
        signature = json.dumps([call.name, call.arguments, _stable(outcome)], sort_keys=True, ensure_ascii=False)
        repeats = self._state.get("repeats", 0) + 1 if signature == self._state.get("signature") else 1
        self._state = {"signature": signature, "repeats": repeats}
        if repeats == 3:
            self._state["correction_result"] = result
        if correction is not None and result == correction:
            self._state.update(repeats=4, correction_result=result)
            repeats = 4
        if self._sessions:
            self._sessions.set_extension(self._context, "progress_guard", self._state)
        return "pause" if repeats > 3 else "correct" if repeats == 3 else "continue"
