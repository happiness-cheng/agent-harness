"""命令效果分类：把一次工具调用判成 低 / 中 / 高 三档。

这是执行前分级的「确定性」一层（规则之后、小模型之前）。它只判效果档位，
不做放行/拒绝决定——那是 ``gate.ToolGate`` 的事。分类复用 gate-v7 的近似
shell 解析器（``generic._split_segments`` 等），不另写解析器。

三档语义：
- low（低）：只读、无副作用。read 工具、新建文件 write、带约束的只读 bash。
- medium（中）：灰区，有写入或执行未知代码。edit、覆盖 write、脚本执行、
  未知命令、向工作区文件重定向。交给小模型审批（fail-closed）。
- high（高）：破坏性或外发。rm 族、git push/reset --hard/clean、curl/wget/ssh、
  pip install、sudo、chmod/kill 等。交给用户确认。

明确不保证：手写解析不是真 shell 解析器，分类是「尽力而为的减速带」，
不是安全边界；真边界是沙箱（S7）与人工确认。解析失败由规则层 fail-closed。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from min_agent.tools.generic import (
    _cmd_word,
    _real_first_index,
    _split_segments,
)

# 破坏性命令（与 gate-v7 _BLOCKED_FIRST_WORDS 同源）：高→人工确认。
DESTRUCTIVE_WORDS = frozenset({"rm", "rmdir", "del", "rd", "format", "mkfs", "dd", "shred"})

# 只读白名单：首词在此且满足参数约束才判 low。
READ_ONLY_WORDS = frozenset({
    "ls", "cat", "head", "tail", "wc", "stat", "file", "du", "df", "pwd",
    "echo", "printf", "which", "type", "test", "true", "false", "sort", "uniq",
    "cut", "tr", "diff", "cmp", "md5sum", "sha256sum", "basename", "dirname",
    "realpath", "readlink", "date", "printenv", "grep", "rg", "ack", "egrep",
    "fgrep", "find", "tree", "nl", "tac", "comm", "paste", "column", "tee",
})

# 网络外发 / 系统修改：高。
_EGRESS_WORDS = frozenset({"curl", "wget", "ssh", "scp", "sftp", "nc", "ncat",
                           "telnet", "ftp", "rsync"})
_SYSTEM_WORDS = frozenset({"chmod", "chown", "chgrp", "kill", "pkill", "killall",
                           "shutdown", "reboot", "halt", "poweroff", "mount", "umount"})
# 脚本/代码执行：中（灰区）。
_EXEC_WORDS = frozenset({"python", "python3", "pytest", "node", "npm", "npx",
                         "bash", "sh", "awk", "sed", "perl", "ruby", "php",
                         "eval", "exec", "source", "xargs", "env"})
_GIT_READ_ONLY = frozenset({"status", "log", "diff", "show", "branch", "remote",
                            "rev-parse", "ls-files", "tag", "describe", "shortlog",
                            "blame", "config"})

_LEVEL_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class Effect:
    """一次调用的效果档位与归因。"""

    level: str  # low / medium / high
    reason: str
    destructive: bool = False
    segments: tuple[str, ...] = field(default_factory=tuple)


def _max_level(levels: list[str]) -> str:
    if not levels:
        return "low"
    return max(levels, key=lambda item: _LEVEL_RANK[item])


def _has_file_redirect(segment: str) -> bool:
    """段里是否有写文件重定向（> / >>），/dev/null 不算。

    近似判：找不在引号里的 > 或 >>，后面跟的不是 /dev/null。
    2>&1 这类 fd 复制不算写文件。
    """
    import re
    # 去掉 2>&1 / >&2 这类 fd 复制，再找 > 或 >>。
    cleaned = re.sub(r"\d*>&\d+", " ", segment)
    for match in re.finditer(r">>?", cleaned):
        rest = cleaned[match.end():].lstrip()
        target = rest.split()[0] if rest.split() else ""
        target = target.strip("'\"")
        if target and target != "/dev/null":
            return True
    return False


def _git_level(tokens: list[str], first_idx: int) -> str:
    sub = ""
    for token in tokens[first_idx + 1:]:
        word = _cmd_word(token)
        if word and not word.startswith("-"):
            sub = word
            break
    if sub in {"push", "clean"}:
        return "high"
    if sub == "reset":
        return "high" if any("--hard" in t for t in tokens) else "medium"
    if sub in {"checkout", "rebase", "merge", "commit", "pull", "fetch", "apply",
               "stash", "restore", "switch"}:
        return "medium"
    if sub in _GIT_READ_ONLY:
        return "low"
    return "medium"


def _pip_level(tokens: list[str], first_idx: int) -> str:
    rest = [_cmd_word(t) for t in tokens[first_idx + 1:]]
    if "install" in rest or "uninstall" in rest:
        return "high"
    if any(t in {"list", "show", "freeze", "check"} for t in rest):
        return "low"
    return "medium"


def _read_only_ok(first: str, tokens: list[str], segment: str) -> bool:
    """只读白名单的参数约束：无写文件重定向、find 无 -exec/-delete、sed 无 -i。"""
    if _has_file_redirect(segment):
        return False
    if first == "find":
        if any(t in {"-exec", "-execdir", "-delete", "-ok", "-okdir"} for t in tokens):
            return False
    if first == "tee":
        # tee 写文件：只有 tee /dev/null 才算只读，否则中。
        targets = [t for t in tokens[1:] if not t.startswith("-")]
        if any(t.strip("'\"") != "/dev/null" for t in targets):
            return False
    return True


def classify_bash(command: str, root: Path) -> Effect:
    """判一条 bash 命令的效果档位。解析失败返回 high（由规则层 fail-closed 拒绝）。"""
    segments, parsed_ok, _expansions = _split_segments(command)
    if not parsed_ok:
        return Effect("high", "命令解析失败（引号/heredoc 未闭合）", destructive=False)
    levels: list[str] = []
    destructive = False
    reasons: list[str] = []
    for segment in segments:
        tokens = segment.split()
        if not tokens:
            continue
        first_idx, first = _real_first_index(tokens)
        if first is None:
            continue
        if first in DESTRUCTIVE_WORDS:
            levels.append("high")
            destructive = True
            reasons.append(f"破坏性命令 {first}")
            continue
        if first in _EGRESS_WORDS:
            levels.append("high")
            reasons.append(f"网络外发 {first}")
            continue
        if first in _SYSTEM_WORDS:
            levels.append("high")
            reasons.append(f"系统修改 {first}")
            continue
        if first == "sudo":
            levels.append("high")
            reasons.append("sudo 提权")
            continue
        if first == "git":
            level = _git_level(tokens, first_idx)
            levels.append(level)
            reasons.append(f"git {level}")
            continue
        if first in {"pip", "pip3"}:
            level = _pip_level(tokens, first_idx)
            levels.append(level)
            reasons.append(f"pip {level}")
            continue
        if first in READ_ONLY_WORDS and _read_only_ok(first, tokens, segment):
            levels.append("low")
            continue
        # 其余（脚本执行、未知命令、带写重定向的只读词）都是灰区。
        levels.append("medium")
        reasons.append(f"灰区命令 {first}")
    level = _max_level(levels)
    if not reasons:
        reason = "只读命令" if level == "low" else "灰区"
    else:
        reason = "；".join(dict.fromkeys(reasons))
    return Effect(level, reason, destructive=destructive, segments=tuple(segments))


def classify_call(name: str, arguments: dict, root: Path) -> Effect:
    """判一次工具调用的效果档位。read/write/edit/bash 四工具。"""
    if name == "read":
        return Effect("low", "只读文件")
    if name == "write":
        path = arguments.get("path", "")
        target = root / path if path else root
        try:
            exists = target.exists()
        except OSError:
            exists = True  # 探测失败按覆盖处理（保守）
        if exists:
            return Effect("medium", "覆盖已有文件")
        return Effect("low", "新建文件")
    if name == "edit":
        return Effect("medium", "修改已有文件")
    if name == "bash":
        return classify_bash(arguments.get("command", ""), root)
    # 未知工具（如 ask_user）默认低；调用方可另行处理。
    return Effect("low", f"工具 {name}")