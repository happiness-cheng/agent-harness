"""独立重放旧回查响应形状与新投影，使用已有原文完整性回归作为判据。"""
from dataclasses import replace
import json
from pathlib import Path
import runpy
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT))
import min_agent.context as module
from min_agent.memory import MemoryJournal
from min_agent.tools.registry import ToolRegistry


def main():
    old_fit, old_register = module.ContextManager._fits, ToolRegistry.register
    measures = []
    def fit(self, messages, tools):
        measures.append(module.request_bound(messages, tools)+self.output_reserve+self.safety_margin)
        return old_fit(self, messages, tools)
    def register(self, tool):
        if tool.name == 'recall_memory':
            def handler(question):
                return json.dumps([{k:v for k,v in row.items() if k != 'messages'}
                    for row in MemoryJournal(self._sessions,self._context).select(question)],ensure_ascii=False)
            tool = replace(tool, handler=handler)
        return old_register(self, tool)
    test = runpy.run_path(str(ROOT/'tests/integration/test_memory_tools.py'))[
        'test_explicit_source_read_and_structured_recall_are_not_silently_head_truncated']
    results = {}
    try:
        module.ContextManager._fits = fit
        for variant in ('old-recall-shape','new-recall-shape'):
            ToolRegistry.register = register if variant == 'old-recall-shape' else old_register
            measures.clear()
            with tempfile.TemporaryDirectory() as folder:
                try:
                    test(Path(folder))
                    passed = True
                except AssertionError:
                    passed = False
            results[variant] = dict(existing_integration_test_passed=passed,
                request_bounds_including_reserves=list(measures), window=32768)
        assert results['old-recall-shape']['existing_integration_test_passed'] is False
        assert max(results['old-recall-shape']['request_bounds_including_reserves']) > 32768
        assert results['new-recall-shape']['existing_integration_test_passed'] is True
        assert max(results['new-recall-shape']['request_bounds_including_reserves']) <= 32768
        with (ROOT/'reports/memory_v02/legacy-source-read-verified.json').open('x',encoding='utf-8') as stream:
            json.dump(results,stream,ensure_ascii=False,indent=2)
        print(json.dumps(results,ensure_ascii=False,indent=2))
    finally:
        module.ContextManager._fits, ToolRegistry.register = old_fit, old_register


if __name__ == '__main__':
    main()
