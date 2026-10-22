"""离线探针：实际应用链路中的Skill披露，不验证模型自主选择。"""

import argparse
from collections import deque
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from min_agent.application import HarnessApplication
from min_agent.models import ModelResponse, ToolCall
from min_agent.skills import SkillCatalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    # 提前排除覆盖已有证据；本脚本不读取凭据、不创建网络客户端。
    with args.report.open("x", encoding="utf-8") as output:
        output.write("{}")
    fixtures = {
        "public-evidence": (Path(__file__).resolve().parents[1]
                            / "src/min_agent/skills/public-evidence.md").read_text(encoding="utf-8"),
        "writing-polish": "# 中文文本润色：保留原意和事实\n\nS01_POLISH_BODY：先保留事实，再改善表达；不新增指标。\n",
        "debug-feedback": "# 故障分析：用复现和反馈定位原因\n\nS01_DEBUG_BODY：先复现，再提出可证伪假设；未知结果不能算成功。\n",
    }
    markers = {"public-evidence": "先明确需要名单广度还是证据深度",
               "writing-polish": "S01_POLISH_BODY", "debug-feedback": "S01_DEBUG_BODY"}

    def request(name, identity):
        return ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id=identity, name="read_skill", arguments={"name": name})])

    snapshots = []
    responses = deque([
        request("writing-polish", "polish-1"), ModelResponse(finish_reason="stop", content="离线润色答复"),
        request("debug-feedback", "debug-1"), ModelResponse(finish_reason="stop", content="离线故障答复"),
        request("writing-polish", "polish-2"), ModelResponse(finish_reason="stop", content="离线重复读取答复"),
        ModelResponse(finish_reason="stop", content="2"),
    ])

    class ProbeModel:
        def generate(self, messages, *, tools=None):
            snapshots.append({"messages": [m.model_dump(mode="json") for m in messages],
                              "tools": deepcopy(tools)})
            return responses.popleft()

    disk_reads = []
    original_read = Path.read_text
    with TemporaryDirectory(prefix="agent-skill-audit-") as temp:
        directory = Path(temp)
        root = directory / "skills"
        root.mkdir()
        for name, body in fixtures.items():
            (root / (name + ".md")).write_text(body, encoding="utf-8")
        catalog = SkillCatalog(root)

        def tracked_read(path, *positional, **options):
            if path.parent == root:
                disk_reads.append(path.name)
            return original_read(path, *positional, **options)

        with patch("min_agent.application.SkillCatalog", return_value=catalog), patch.object(Path, "read_text", tracked_read):
            harness = HarnessApplication(directory / "audit.sqlite3", model=ProbeModel())
            try:
                first = harness.create_session()["session_id"]
                for question in ("请润色：今天修好了登录错误。", "帮我分析一个可复现的接口异常。",
                                 "再按刚才的润色方法改写：今天完成了测试。"):
                    harness.submit(first, question)
                    harness.wait(first)
                    assert harness.snapshot(first)["task"]["status"] == "completed"
                other = harness.create_session()["session_id"]
                harness.submit(other, "1加1是多少？")
                harness.wait(other)
                assert harness.snapshot(other)["task"]["status"] == "completed"
            finally:
                harness.close()
        for invalid in ("../secret", "unknown"):
            try:
                catalog.read(invalid)
            except PermissionError:
                pass
            else:
                raise AssertionError("非法名称未拒绝")

    assert not responses and len(snapshots) == 7
    counts = []
    for snapshot in snapshots:
        wire = json.dumps(snapshot, ensure_ascii=False)
        counts.append({name: wire.count(marker) for name, marker in markers.items()})
        schema = next(tool for tool in snapshot["tools"] if tool["name"] == "read_skill")
        assert all(name in schema["description"] for name in fixtures)
        assert not any(marker in schema["description"] for marker in markers.values())
    assert [item["writing-polish"] for item in counts] == [0, 1, 1, 1, 1, 2, 0]
    assert [item["debug-feedback"] for item in counts] == [0, 0, 0, 1, 1, 1, 0]
    assert all(item["public-evidence"] == 0 for item in counts)
    assert all(counts[0][name] == 0 for name in fixtures)
    assert set(disk_reads[:3]) == {name + ".md" for name in fixtures}
    report = {
        "experiment": "S01-skill-disclosure-audit", "live_requests": 0,
        "scope": "真实HarnessApplication/Runtime/ManagedModel；脚本模型预设调用；临时目录替换SkillCatalog来源",
        "fixtures": fixtures, "body_markers": markers, "body_marker_counts": counts,
        "skill_disk_reads": disk_reads, "model_inputs": snapshots,
        "checks": {"directory_only_before_invocation": True, "only_selected_body_added": True,
                   "body_persists_in_same_session": True, "other_session_has_no_body": True,
                   "unrelated_skill_body_absent": True, "invalid_names_rejected": True},
        "gaps": ["summaries读取所有正文取首行", "重复read_skill会重复追加正文", "未支持SKILL.md目录和附属资料按需读取",
                 "未验证真实模型自主选Skill", "只测短正文，未验证长正文压缩与重注入"],
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"checks": report["checks"], "model_inputs": len(snapshots),
                      "body_marker_counts": counts, "disk_read_count": len(disk_reads)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
