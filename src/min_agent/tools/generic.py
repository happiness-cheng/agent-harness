"""通用工具：read / write / edit / bash。

这是 pi 式的最小工具面：模型用四个通用入口自己编排，而不是为每类操作
增加一个专用工具。文件三件套仍复用 ``FileSandbox`` 的工作区约束、敏感
路径拒绝、原子写与写锁；``bash`` 是唯一能越出工作区的入口，默认按宿主
用户权限运行，**不是沙箱**。本模块不检查 Plan/Build 或目标确认——那是
调用方（``HarnessApplication`` 的 generic 模式）显式选择的取舍。
"""

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from min_agent.session.task_state import AuthorizationRequired
from min_agent.tools.filesystem import (
    MAX_PATH_LENGTH,
    MAX_WRITE_BYTES,
    FileSandbox,
)
from min_agent.tools.registry import ToolDefinition

MAX_READ_LINES = 2000
MAX_COMMAND_CHARS = 20_000
MAX_BASH_OUTPUT_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_TIMEOUT_SECONDS = 600.0


class ReadArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str = Field(min_length=1, max_length=MAX_PATH_LENGTH,
                      description="相对工作区根的文本文件路径；不接受绝对路径或 ..。")
    offset: int = Field(default=1, ge=1, le=10_000_000,
                        description="起始行号，从 1 开始。")
    limit: int = Field(default=MAX_READ_LINES, ge=1, le=MAX_READ_LINES,
                       description="最多读取的行数，上限 2000。")


class WriteArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str = Field(min_length=1, max_length=MAX_PATH_LENGTH,
                      description="相对工作区根的目标路径；父目录必须已存在。")
    content: str = Field(max_length=MAX_WRITE_BYTES,
                         description="要写入的完整文本；已存在文件会被原子覆盖。")
    expected_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$",
        description="可选：覆盖前期望的文件 sha256（来自 read）。不匹配则拒绝（stale_version），"
                    "防止盖掉别人刚改的内容；不传则直接覆盖。")


class EditArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str = Field(min_length=1, max_length=MAX_PATH_LENGTH,
                      description="要修改的文本文件，相对工作区根。")
    old_text: str = Field(min_length=1, max_length=MAX_WRITE_BYTES,
                          description="要被替换的原文，必须在文件中唯一出现。")
    new_text: str = Field(max_length=MAX_WRITE_BYTES,
                          description="替换后的文本，可为空字符串。")
    expected_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$",
        description="可选：修改前期望的文件 sha256（来自 read）。不匹配则拒绝（stale_version）；"
                    "不传则直接修改。")


class BashArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    command: str = Field(min_length=1, max_length=MAX_COMMAND_CHARS,
                         description="交给 bash -c 的命令；工作目录是工作区根。")
    timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, ge=1,
                                   le=MAX_TIMEOUT_SECONDS,
                                   description="超时秒数，1–600，默认 120。")
    verify: str | None = Field(
        default=None, max_length=MAX_COMMAND_CHARS,
        description="可选：执行前写下的只读验证命令（如 test -f out.txt、grep ...）。"
                    "主命令退出码 0 后才跑；必须是只读（分类为 low），否则拒绝。"
                    "结果里 verification.passed 如实报告，不推翻主命令结果。")


MAX_VERIFY_OUTPUT_BYTES = 4096


def _ok(**payload: object) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False)


# bash 最小安全门（GATE-MIN-001，2026-11-23；v4，2026-11-23，EVAL-DIAG-004）。
# 只做字符串层面的检查：防误操作和直白注入，不防有意绕过
#（比如 python -c "import os; os.remove(...)" 就绕过去了）。真隔离要沙箱，另议。
# 拦住就返回错误，不改写、不猜原意。
# v2（EVAL-DIAG-002）：按解析后的真实路径判（工作区内绝对路径放行，~ 展开）；
# 引号感知切段；凭据名检查跳过引号里的串。
# v3（EVAL-DIAG-003，Claude 第三轮审批）：切段按 shell 语义 fail-closed——
# 反斜杠转义、单引号里什么都不执行、双引号里 $(...) 和反引号仍执行；
# 引号没闭合就整条拦掉。另：& 也切段；sudo/env/xargs 这类包壳命令看包壳后
# 的第一个实命令；路径检查先去掉 token 外层的引号和重定向符；/dev/null 放行；
# Windows 上 Git Bash 风格的 /d/... 先转成 D:\... 再判；凭据名改显式列表。
# v4（EVAL-DIAG-004，Claude 第四轮审批，修完冻结）：切段改栈式解析——
# 单引号、双引号、$(...)、圆括号各占一层，$( 之后进入命令上下文（里面的
# ; && | 会切段）；heredoc（<<定界符）正文整段跳过，不当命令解析；首词前
# 跳过 do/then 等 shell 关键字；/dev/null 在解析路径前先判（Windows 上
# 也生效）；find 的 -exec/-delete 当 rm 族；--opt=路径 拆出值再判；
# $HOME 展开；重定向符先去再去引号（顺序反了会漏）；grep 族的模式不查
# 凭据名；command -v 是查询不拦；.env.example/.env.sample 豁免。
# 措辞改成“近似 shell 语义”——手写解析不是真解析器，见已知缺口。
# v5（EVAL-DIAG-005，Claude 第五轮审批）：冻结分两类——新增 idiom 规则
# 冻结，解析器和 shell 语法不一致的 bug 照修（fail-closed 会把它们变成
# 整条被拦）。本轮 3 个照修：<<<（herestring）不再误认成 heredoc；
# 词首的 # 到行尾是注释（注释里的撇号不当引号）；未加引号的 heredoc
# （<<EOF）正文里的 $(...) 和反引号会被 shell 执行，只查这些展开
# （加引号的 <<'EOF' 是纯文字，仍整段跳过）。
# v6（EVAL-DIAG-006，Claude 第六轮审批，维持冻结）：2 个照修——未加引号
# heredoc 的正文整段攒起来再扫展开（多行 $(...) 也能跨行配对），展开没
# 闭合按解析失败处理（整条拦）；<<\EOF 反斜杠引用定界符，去掉反斜杠、
# 标成“已引用”。预言机扩语料后又找到 1 个照修：引号包着的命令名
# （"rm"）——shell 执行前会去掉命令词上的引号，命令词比较前先归一化
# （_cmd_word），包壳后和 find -exec 后的命令词同样归一化。
# v7（EVAL-DIAG-007，Windows 本机复核跟进，维持冻结）：3 个照修——
# _resolve 用 realpath 展开 8.3 短名（B11；同时跟符号链接，更安全）；
# 路径检查跳过 <<* heredoc 操作符 token（G2：<<\EOF 去掉 << 剩 \EOF，
# Windows 上是盘根绝对路径）；D10 移出脚本化 D 集改手跑（脚本层在两平台
# 都过不了，改由 windows-check 手跑点 2 验证）。
# 已知缺口（v5 仍在，写死在测试里）：cat ".env" 这种引号包着的凭据名会漏过
# （read 工具那层还会拦 .env）；符号链接解析后的路径不跟；git clean /
# reset --hard / `> file` 截断不算门要拦的（见 DECISION_LOG → EVAL-DIAG-003，
# 该执行前审批那一档拦——那一档目前还不存在，这三类现在完全无门）；
# sudo -u root rm / xargs sh -c 这类包壳后带参数的仍会漏过；find -name 的
# 值（模式）不查凭据名；反引号里嵌套引号仍按旧法切段。
# 只写进局限、不改代码（EVAL-DIAG-005）：只展开 $HOME，其它 $VAR/...
# （如 $TMPDIR/..）会被当成工作区内的相对路径；路径检查没有跳过引号区间，
# 引号里的文字如果含绝对路径或 ..，会被当路径拦（F6，在全量运行里量）；
# eval、exec、timeout、bash -c、FOO=1 rm x 都不拦；bash <<'EOF' 把命令
# 喂给 bash（和 bash -c 同类，F5）不拦。
_BLOCKED_FIRST_WORDS = frozenset({"rm", "rmdir", "del", "rd", "format", "mkfs", "dd", "shred"})
_WRAPPER_WORDS = frozenset({"sudo", "env", "command", "xargs", "nice", "nohup", "time"})
_SHELL_KEYWORDS = frozenset({"do", "then", "else", "elif", "if", "while", "until",
                               "done", "fi", "esac"})
_GREP_WORDS = frozenset({"grep", "rg", "ack", "egrep", "fgrep"})


def _parse_heredoc_delim(command: str, start: int) -> tuple[str, bool, bool, int]:
    """解析 << 后的定界符，返回（定界符，去前导 Tab，定界符加了引号，定界符后下标）。
    定界符可以是 EOF、'EOF'、"EOF"；解析不到返回 ("", False, False, start)。"""
    i, n = start, len(command)
    strip_tabs = False
    if i < n and command[i] == "-":
        strip_tabs = True
        i += 1
    while i < n and command[i] in (" ", "\t"):
        i += 1
    if i < n and command[i] in ("'", '"'):
        quote = command[i]
        j = command.find(quote, i + 1)
        if j == -1:
            return "", False, False, start
        return command[i + 1:j], strip_tabs, True, j + 1
    # \EOF：反斜杠引用定界符，定界符是 EOF，且算已引用（正文不展开）。
    if i < n and command[i] == "\\" and i + 1 < n and command[i + 1] not in ("'", '"'):
        j = i + 1
        while j < n and command[j] not in (" ", "\t", ";", "|", "&", "<", ">", "(", ")", "\n"):
            j += 1
        return command[i + 1:j], strip_tabs, True, j
    j = i
    while j < n and command[j] not in (" ", "\t", ";", "|", "&", "<", ">", "(", ")", "\n"):
        j += 1
    return command[i:j], strip_tabs, False, j


def _find_expansions(text: str) -> tuple[list[str], bool]:
    """找一段文字里的命令替换 $(...) 和反引号，返回（里面的内容列表，
    是否遇到没闭合的展开）。只在未加引号的 heredoc 正文里用——那里的
    展开会被 shell 执行。没闭合的展开按解析失败处理（调用方整条拦掉），
    不能悄悄放行（fail-closed）。"""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i:i + 2] == "$(":
            # 找配对的 )，引号里的 ) 不算；嵌套的 ( 也算深度。
            depth, quote = 1, None
            j = i + 2
            while j < n and depth > 0:
                ch = text[j]
                if quote == "'":
                    if ch == "'":
                        quote = None
                elif quote == '"':
                    if ch == "\\":
                        j += 1
                    elif ch == '"':
                        quote = None
                elif ch in ("'", '"'):
                    quote = ch
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                j += 1
            if depth == 0:
                out.append(text[i + 2:j - 1])
                i = j
                continue
            return out, True  # 没闭合，按解析失败处理
        if text[i] == "`":
            j = text.find("`", i + 1)
            if j == -1:
                return out, True  # 没闭合，按解析失败处理
            out.append(text[i + 1:j])
            i = j + 1
            continue
        i += 1
    return out, False


def _skip_heredoc_body(command: str, start: int, heredocs: list) -> tuple[int | None, list[str]]:
    """从 start 起按行跳过 heredoc 正文；每读到定界符那行就消掉一个。
    加引号的定界符（<<'EOF'、<<\\EOF）正文是纯文字，整段跳过；没加引号的
    （<<EOF）正文里的 $(...) 和反引号会被 shell 执行——正文整段攒起来再扫
    展开（多行 $(...) 也能跨行配对），交给调用方再查一遍；展开没闭合按
    解析失败处理，整条拦掉。全部消完返回（定界符行之后第一个字符的下标，
    展开内容列表）；到结尾还没消完返回 (None, [])。"""
    expansions: list[str] = []
    body_lines: list[str] = []  # 当前这份 heredoc 的正文行（只在未加引号时攒）
    i, n = start, len(command)
    while heredocs and i < n:
        j = command.find("\n", i)
        if j == -1:
            line, nxt = command[i:], n
        else:
            line, nxt = command[i:j], j + 1
        check = line.rstrip("\r")
        delim, strip_tabs, quoted = heredocs[0]
        if strip_tabs:
            check = check.lstrip("\t")
        if check == delim:
            heredocs.pop(0)
            if not quoted and body_lines:
                found, unclosed = _find_expansions("\n".join(body_lines))
                if unclosed:
                    return None, []
                expansions.extend(found)
            body_lines = []
        elif not quoted:
            body_lines.append(line)
        i = nxt
    if heredocs:
        return None, []
    return i, expansions


def _split_segments(command: str) -> tuple[list[str], bool, list[str]]:
    """近似 shell 语义切段，返回（段列表，是否解析成功，未加引号 heredoc 正文里的展开）。

    栈式解析：单引号、双引号、$(...)、圆括号各占一层。单引号里什么都不
    执行；双引号里 $(...) 和反引号仍执行，$( 之后进入命令上下文（里面的
    ; && | 会切段）；反斜杠在引号外和双引号里转义下一个字符；heredoc
    （<<定界符）的正文：加引号的整段跳过，没加引号的只收集里面的 $(...)
    和反引号展开，交给调用方再查。词首的 # 到行尾是注释。引号没闭合、
    $( 没闭合、heredoc 没找到结束行，返回 ok=False，调用方整条拦掉
    （fail-closed）。
    """
    segments: list[str] = []
    current: list[str] = []
    # 栈元素是帧类型：' 或 " 是引号帧，"$(" 是命令替换帧，"(" 是圆括号帧。
    stack: list[str] = []
    # 待读的 heredoc（定界符，是否去前导 Tab，定界符加了引号）。
    heredocs: list[tuple[str, bool, bool]] = []
    expansions: list[str] = []
    i, n = 0, len(command)

    def _split_here() -> None:
        segments.append("".join(current))
        current.clear()

    while i < n:
        ch = command[i]
        top = stack[-1] if stack else None
        # ---- 单引号帧：里面什么都不执行 ----
        if top == "'":
            current.append(ch)
            if ch == "'":
                stack.pop()
            i += 1
            continue
        # ---- 双引号帧：转义、闭合、$( 和反引号仍执行 ----
        if top == '"':
            if ch == "\\" and i + 1 < n:
                current.append(command[i:i + 2])
                i += 2
                continue
            if ch == '"':
                stack.pop()
                current.append(ch)
                i += 1
                continue
            if command[i:i + 2] == "$(":
                stack.append("$(")
                _split_here()
                i += 2
                continue
            if ch == "`":
                _split_here()
                i += 1
                continue
            current.append(ch)
            i += 1
            continue
        # ---- 命令上下文（没进引号，或在 $( / 圆括号里）----
        if ch == "\\" and i + 1 < n:
            current.append(command[i:i + 2])
            i += 2
            continue
        # 词首的 # 到行尾是注释，整段跳过（注释里的撇号不当引号）。
        if ch == "#" and (i == 0 or command[i - 1] in (" ", "\t", ";", "&", "|", "(", "\n")):
            j = command.find("\n", i)
            i = n if j == -1 else j
            continue
        if ch in ("'", '"'):
            stack.append(ch)
            current.append(ch)
            i += 1
            continue
        if command[i:i + 2] == "$(":
            stack.append("$(")
            _split_here()
            i += 2
            continue
        if ch == "(":
            # <(...) / >(...) 是进程替换，里面的内容是命令，和 $(...) 一样切段；
            # 裸的 ( 是子 shell 或算术，不切（_cmd_word 会剥掉前导 ( 再比）。
            if i > 0 and command[i - 1] in ("<", ">"):
                stack.append("(")
                _split_here()
                i += 1
                continue
            stack.append("(")
            current.append(ch)
            i += 1
            continue
        if ch == ")":
            if stack and stack[-1] in ("$(", "("):
                stack.pop()
            current.append(ch)
            i += 1
            continue
        if ch == "`":
            _split_here()
            i += 1
            continue
        # heredoc：只在命令上下文里认 <<；<<<（herestring）不算——
        # 只排除了第一个 <，走到第二个 < 时又会匹配到 <<，所以前面
        # 不是 < 也要查。
        if (command[i:i + 2] == "<<" and command[i:i + 3] != "<<<"
                and (i == 0 or command[i - 1] != "<")):
            delim, strip_tabs, quoted, end = _parse_heredoc_delim(command, i + 2)
            if delim:
                heredocs.append((delim, strip_tabs, quoted))
                current.append(command[i:end])
                i = end
                continue
        if command[i:i + 2] in ("&&", "||"):
            _split_here()
            i += 2
            continue
        # & 只有在不是重定向的一部分时才是后台符：>&、<&、&>、&>> 都是重定向
        # （比如 2>&1），不能在这里切段（预言机扩语料后找到的漏网）。
        if ch == "&" and ((i > 0 and command[i - 1] in (">", "<"))
                           or (i + 1 < n and command[i + 1] == ">")):
            current.append(ch)
            i += 1
            continue
        if ch in (";", "|", "&", "\n"):
            _split_here()
            if ch == "\n" and heredocs:
                # 换行之后是 heredoc 正文：按行跳过，直到定界符那行。
                i, found = _skip_heredoc_body(command, i + 1, heredocs)
                if i is None:
                    return segments, False, expansions
                expansions.extend(found)
                continue
            i += 1
            continue
        current.append(ch)
        i += 1
    _split_here()
    return segments, not stack, expansions


def _quoted_spans(segment: str) -> list[tuple[int, int]]:
    """返回 segment 里单双引号包着的字符区间（起止下标）。
    双引号里反斜杠转义，单引号里反斜杠是普通字符；$(...) 里嵌套的
    引号按简单规则处理（嵌套引号可能让区间提前闭合，宁可多查、不漏查）。"""
    spans: list[tuple[int, int]] = []
    quote: str | None = None
    start = 0
    i = 0
    while i < len(segment):
        ch = segment[i]
        if quote == "'":
            if ch == "'":
                spans.append((start, i + 1))
                quote = None
            i += 1
            continue
        if quote == '"':
            if ch == "\\" and i + 1 < len(segment):
                i += 2
                continue
            if ch == '"':
                spans.append((start, i + 1))
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            start = i
        i += 1
    return spans


def _gitbash_drive_path(token: str) -> str | None:
    """Git Bash 风格的 /d/... 路径只在 Windows 上出现；转成 D:\\... 再判。
    只在 Windows 上调用，Linux 上 /d/... 就是根目录下的真路径。
    纯函数，可在任何平台上单测。"""
    import re
    match = re.match(r"^/([A-Za-z])/(.*)$", token)
    if match is None:
        return None
    return f"{match.group(1).upper()}:\\{match.group(2).replace('/', '\\')}"


def _resolve(token: str, root: Path) -> Path:
    """把一个路径 token 拼到工作区根再归一化。
    用 realpath 展开 Windows 8.3 短名（CHEN-D~1 → chen-duxiu；root 经
    FileSandbox.resolve() 是长名，token 可能是短名，不展开就比不上）。
    realpath 会跟符号链接——这比不跟更安全（指到工作区外的链接会被正确
    拦住），已知局限里的“符号链接不跟”相应作废。"""
    if os.name == "nt":
        converted = _gitbash_drive_path(token)
        if converted is not None:
            token = converted
    if token == "$HOME" or token.startswith("$HOME/"):
        token = os.path.expanduser("~") + token[len("$HOME"):]
    elif token == "${HOME}" or token.startswith("${HOME}/"):
        token = os.path.expanduser("~") + token[len("${HOME}"):]
    if token.startswith("~"):
        path = Path(os.path.expanduser(token))
    else:
        path = Path(token)
    if not path.is_absolute():
        path = root / path
    return Path(os.path.realpath(path))


def _is_path_like(token: str) -> bool:
    return ("/" in token or "\\" in token or token in ("..", ".") or token.startswith("~")
            or token.startswith("$HOME") or token.startswith("${HOME}"))


def _strip_redirect(token: str) -> str:
    """去掉 token 开头的重定向符（> >> < 2> 2>> >&），剩下的才是路径。
    只认“开头是数字+<>”的形式，123abc 这种文件名不会被误伤。"""
    import re
    stripped = re.sub(r"^\d*[<>]+", "", token)
    if stripped != token and stripped.startswith("&"):
        stripped = stripped[1:]
    return stripped


def _cmd_word(token: str) -> str:
    """命令词归一化：去掉前导 ( { ! 和两端引号、括号，再小写。
    shell 执行前会去掉命令词上的引号，"rm" 就是 rm（预言机扩语料后找到的漏网）；
    段尾粘着的 )（比如 done)"）也要剥掉，否则 done 这类关键字跳不过。"""
    return token.lstrip("({!").strip("'\"()").lower()


def _real_first_index(tokens: list[str]) -> tuple[int, str | None]:
    """每段的第一个实词：跳过 do/then/else/elif/if/while/until 这类
    shell 关键字和 ( { ! 这类标点。for f in ...; do rm "$f" 的第二段
    是 do rm "$f"，实词是 rm。返回（下标，实词小写），没有返回 (-1, None)。"""
    for idx, token in enumerate(tokens):
        word = _cmd_word(token)
        if not word or word in _SHELL_KEYWORDS:
            continue
        return idx, word
    return -1, None


def _is_credential_name(name: str) -> bool:
    """凭据文件名：显式列表，不再用 credential/secret 子串匹配
    （子串会误伤工作区内正常文件，比如 docs/secrets.md，B8）。
    .env.example / .env.sample 是常见的非机密示例文件，豁免。"""
    if name in (".env.example", ".env.sample"):
        return False
    return (name.startswith(".env") or name.endswith(".pem") or name.endswith(".key")
            or name.startswith("id_rsa") or name in (".netrc", ".npmrc", "credentials"))


def _blocked_command(command: str, root: Path, *, allow_destructive: bool = False) -> str | None:
    """起进程前检查 bash 命令；被拦返回原因，否则返回 None。

    allow_destructive=True 时跳过破坏性词（rm 族 / find -delete/-exec）检查，
    供执行前分级门（gate.ToolGate）在用户确认高危调用后放行；解析失败、凭据名、
    工作区外路径仍照常拦。默认 False，gate-v7 行为逐字节不变。
    """
    import re
    segments, parsed_ok, expansions = _split_segments(command)
    if not parsed_ok:
        return "命令解析失败（引号未闭合或 heredoc 没找到结束行），被直接拒绝。"
    # 未加引号的 heredoc 正文里的展开会被 shell 执行，再查一遍。
    for inner in expansions:
        reason = _blocked_command(inner, root)
        if reason is not None:
            return reason
    for segment in segments:
        tokens = segment.split()
        if not tokens:
            continue
        first_idx, first = _real_first_index(tokens)
        if first is None:
            continue
        if not allow_destructive and first in _BLOCKED_FIRST_WORDS:
            return f"破坏性命令 {first} 被直接拒绝。"
        # 包壳命令（sudo/env/xargs……）：看包壳后的第一个实命令。
        # 跳过 -x 这类开关和 VAR=1 这类赋值；sudo -u root rm 这种仍会漏过；
        # command -v 是查询（问 rm 在哪），不是执行，不拦。
        if not allow_destructive and first in _WRAPPER_WORDS:
            for token in tokens[first_idx + 1:]:
                if first == "command" and token in ("-v", "-V"):
                    break
                if token.startswith("-") or "=" in token:
                    continue
                if _cmd_word(token) in _BLOCKED_FIRST_WORDS:
                    return f"破坏性命令 {token} 被直接拒绝。"
                break
        # find 的 -exec / -execdir 后跟 rm 族、-delete：效果等同 rm。
        # -exec sh -c 'rm ...' 这种仍会漏过。
        if not allow_destructive:
            for pos, token in enumerate(tokens):
                if token == "-delete":
                    return "find -delete 等同删除，被直接拒绝。"
                if token in ("-exec", "-execdir") and pos + 1 < len(tokens):
                    if _cmd_word(tokens[pos + 1]) in _BLOCKED_FIRST_WORDS:
                        return f"find {token} 后跟破坏性命令 {tokens[pos + 1]}，被直接拒绝。"
        # 路径越界：先去重定向符再去引号（顺序不能反，>"/etc/hosts" 这种
        # 先去引号会剩下一个引号）；--opt=路径 拆出值再判；/dev/null 在
        # 解析路径前先判，Windows 上也生效；落在工作区内的放行。
        for token in tokens:
            candidate = token
            if candidate.startswith("-") and "=" in candidate:
                candidate = candidate.split("=", 1)[1]
            # heredoc/herestring 操作符（<<EOF、<<\EOF、<<<、3<<EOF）：定界符
            # 不是路径，不查。否则 <<\EOF 去掉 << 剩 \EOF，Windows 上是盘根
            # 绝对路径，会被误拦（Linux 上只是普通文件名，云机测不出来）。
            if candidate.lstrip("0123456789").startswith("<<"):
                continue
            bare = _strip_redirect(candidate).strip("'\"")
            if bare == "/dev/null":
                continue
            if not _is_path_like(bare):
                continue
            resolved = _resolve(bare, root)
            if not (resolved == root or root in resolved.parents):
                return f"路径 {token} 超出工作区，被直接拒绝。"
        # 凭据文件名：只查没加引号的 token（引号里通常是要输出的文字，
        # 比如 echo "...secret.txt..."，不是文件名）；按显式列表判。
        # 显式列表里有 credentials、id_rsa 这种没点没斜杠的裸名，必须全查；
        # grep 族的第一个非开关 token 是搜索模式（-- 之后也是模式），不是文件名，跳过。
        spans = _quoted_spans(segment)
        matches = list(re.finditer(r"\S+", segment))
        pattern_pos = -1
        if first in _GREP_WORDS:
            seen_dashdash = False
            for pos, match in enumerate(matches):
                if pos <= first_idx:
                    continue
                tok = match.group(0)
                if tok == "--":
                    seen_dashdash = True
                    continue
                if not seen_dashdash and tok.startswith("-"):
                    continue
                pattern_pos = pos
                break
        for pos, match in enumerate(matches):
            if pos == pattern_pos:
                continue
            if any(s <= match.start() and match.end() <= e for s, e in spans):
                continue
            token = _strip_redirect(match.group(0)).strip("'\"")
            name = token.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
            if _is_credential_name(name):
                return f"凭据文件 {match.group(0)} 被直接拒绝。"
    return None





def _error(code: str, message: str, **details: object) -> str:
    return json.dumps({"ok": False, "error": {"code": code, "message": message, **details}},
                      ensure_ascii=False)


def _cap_utf8(text: str, limit: int) -> tuple[str, bool]:
    """按 UTF-8 字节截断，不把多字节字符切成半个。"""
    raw = text.encode("utf-8", "replace")
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode("utf-8", "ignore") + "\n...[truncated]", True


def _git_bash() -> str | None:
    """优先 Git Bash；拒绝 System32 的 WSL 启动器（cwd/路径语义不同）。"""
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if base:
                candidate = Path(base) / "Git" / "bin" / "bash.exe"
                if candidate.is_file():
                    return str(candidate)
        found = shutil.which("bash")
        if found:
            system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
            try:
                if Path(found).resolve() == (system_root / "System32" / "bash.exe").resolve():
                    return None
            except OSError:
                return None
            return found
        return None
    return shutil.which("bash")


def _bash_environment(root: Path, shell: str) -> dict[str, str]:
    """清掉常见凭据环境变量，再把项目 venv 放到 PATH 前面。"""
    markers = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
    environment = {
        key: value for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in markers)
    }
    environment.pop("ADRIAN_CREDENTIALS_FILE", None)
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    parts: list[str] = []
    # 优先工作区自己的 venv；没有则退回应用所在仓库的 venv，避免外部工作区里
    # `python` 落到系统解释器。
    candidates = [
        root / ".venv" / ("Scripts" if os.name == "nt" else "bin"),
        Path(__file__).resolve().parents[3] / ".venv" / ("Scripts" if os.name == "nt" else "bin"),
    ]
    for venv in candidates:
        if venv.is_dir():
            parts.append(str(venv))
            break
    if os.name == "nt":
        git_root = Path(shell).parent.parent
        for relative in ("usr/bin", "mingw64/bin"):
            candidate = git_root / relative
            if candidate.is_dir():
                parts.append(str(candidate))
    existing = environment.get("PATH")
    if existing:
        parts.append(existing)
    environment["PATH"] = os.pathsep.join(parts)
    return environment


class GenericWorkspace:
    """四个通用工具的实现；文件操作仍限制在 root 内。"""

    def __init__(self, root: str | Path) -> None:
        self.sandbox = FileSandbox(root)
        self.root = self.sandbox.root
        # 执行前分级门（gate.ToolGate）在用户确认高危破坏性调用后，把该命令
        # 加入此集合；bash() 取出即销毁，仅跳过破坏性词快速失败，其余检查保留。
        # 串行执行，无并发；默认空集合时 gate-v7 行为不变。
        self.cleared_commands: set[str] = set()

    def read(self, path: str, offset: int = 1, limit: int = MAX_READ_LINES) -> str:
        target, failure = self.sandbox.resolve(path)
        if failure is not None:
            return failure
        assert target is not None
        if target.is_dir():
            # 默认模式的 fs_read 会提示用 fs_list；本模式没有 fs_list。
            return _error("is_directory", "目标是目录；用 bash ls 查看。")
        payload = json.loads(self.sandbox.read_text(path))
        if not payload.get("ok"):
            return json.dumps(payload, ensure_ascii=False)
        lines = payload["content"].splitlines(keepends=True)
        total = len(lines)
        start = min(offset, total + 1)
        chunk = lines[start - 1:start - 1 + limit]
        end = start - 1 + len(chunk)
        return _ok(
            path=payload["path"],
            content="".join(chunk),
            start_line=start,
            end_line=end,
            total_lines=total,
            truncated=end < total,
            sha256=payload["sha256"],
            size=payload["size"],
        )

    def write(self, path: str, content: str, expected_sha256: str | None = None) -> str:
        return self.sandbox.write_text(path, content, expected_sha256=expected_sha256,
                                       require_version=False)

    def edit(self, path: str, old_text: str, new_text: str,
             expected_sha256: str | None = None) -> str:
        return self.sandbox.edit_text(path, old_text, new_text,
                                      expected_sha256=expected_sha256, require_version=False)

    def bash(self, command: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
             verify: str | None = None) -> str:
        if "\x00" in command:
            return _error("invalid_command", "命令包含 NUL 字符。")
        # 最小安全门：起进程前拦，不改写、不猜原意（见 _blocked_command）。
        # 分级门已用户确认的高危破坏性命令在此一次性放行（仅跳过破坏性词）。
        allow_destructive = command in self.cleared_commands
        self.cleared_commands.discard(command)
        blocked = _blocked_command(command, self.root, allow_destructive=allow_destructive)
        if blocked is not None:
            return _error("blocked_command", blocked)
        # GOV-001-S6：验证命令必须先于主命令校验为只读（low），否则拒绝该参数。
        if verify is not None:
            from min_agent.tools.effects import classify_bash
            if classify_bash(verify, self.root).level != "low":
                return _error("invalid_verify", "verify 必须是只读命令（分类为 low）。")
        shell = _git_bash()
        if shell is None:
            return _error("bash_not_found",
                          "未找到 Git Bash；请安装 Git for Windows 或把 bash 加入 PATH。")
        result = self._execute(command, timeout_seconds, shell)
        # 主命令成功（退出码 0）后才跑验证；验证失败不推翻主命令结果，只如实报告。
        if verify is not None and result.get("ok") and result.get("exit_code") == 0:
            verified = self._execute(verify, timeout_seconds, shell)
            vstdout, _ = _cap_utf8(verified.get("stdout", ""), MAX_VERIFY_OUTPUT_BYTES)
            result["verification"] = {
                "command": verify,
                "exit_code": verified.get("exit_code"),
                "stdout": vstdout,
                "passed": verified.get("ok") and verified.get("exit_code") == 0,
            }
        return json.dumps(result, ensure_ascii=False)

    def _execute(self, command: str, timeout_seconds: float, shell: str) -> dict:
        """起 bash 子进程跑一条命令，返回结果 dict（ok/exit_code/stdout/stderr 或 error）。"""
        started = time.perf_counter()
        options: dict = {
            "cwd": str(self.root),
            "env": _bash_environment(self.root, shell),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        if os.name == "nt":
            options["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        else:
            # 独立进程组，超时才能连同子进程一起终止。
            options["start_new_session"] = True
        try:
            process = subprocess.Popen([shell, "-c", command], **options)
        except OSError as error:
            return {"ok": False, "error": {"code": "exec_failed",
                                           "message": f"无法启动 bash：{error}"}}
        try:
            stdout_bytes, stderr_bytes = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            try:
                stdout_bytes, stderr_bytes = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                stdout_bytes, stderr_bytes = b"", b""
            stdout, _ = _cap_utf8(_decode(stdout_bytes), MAX_BASH_OUTPUT_BYTES)
            stderr, _ = _cap_utf8(_decode(stderr_bytes), MAX_BASH_OUTPUT_BYTES)
            return {"ok": False, "error": {
                "code": "timeout", "message": f"命令超过 {timeout_seconds:g} 秒，已终止。",
                "stdout": stdout, "stderr": stderr,
                "duration_ms": int((time.perf_counter() - started) * 1000)}}
        stdout, stdout_truncated = _cap_utf8(_decode(stdout_bytes), MAX_BASH_OUTPUT_BYTES)
        stderr, stderr_truncated = _cap_utf8(_decode(stderr_bytes), MAX_BASH_OUTPUT_BYTES)
        return {"ok": True, "exit_code": process.returncode, "stdout": stdout, "stderr": stderr,
                "truncated": stdout_truncated or stderr_truncated,
                "duration_ms": int((time.perf_counter() - started) * 1000)}


def _kill_tree(process: subprocess.Popen) -> None:
    """超时后连同子进程一起终止；Windows 用 taskkill /T，POSIX 用进程组。"""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except OSError:
            process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", "replace")


def generic_tools(root: str | Path, *, workspace: "GenericWorkspace | None" = None) -> list[ToolDefinition]:
    """构造四个通用工具；调用方负责决定是否注册以及权限门。

    workspace 可由调用方预先构造并传入，以便分级门（gate.ToolGate）持有同一
    实例、放行已确认的高危破坏性命令；不传则内部新建（行为与历史一致）。
    """
    if workspace is None:
        workspace = GenericWorkspace(root)
    return [
        ToolDefinition(
            name="read",
            description=(
                "读取工作区内的 UTF-8 文本文件，按行切片。offset 从 1 开始，"
                "limit 最多 2000 行；返回原文、起止行、总行数、sha256。"
                "大文件只读请求的片段；二进制、非 UTF-8、敏感路径和超过 256 KiB 的文件直接拒绝，不截断。"
            ),
            parameters=ReadArguments,
            handler=workspace.read,
        ),
        ToolDefinition(
            name="write",
            description=(
                "在工作区内新建或原子覆盖文本文件，返回 sha256/size。"
                "父目录必须已存在；敏感路径、目录和超过 1 MiB 的内容拒绝。"
                "覆盖不需要先读，但写前应确认目标路径。"
            ),
            parameters=WriteArguments,
            handler=workspace.write,
        ),
        ToolDefinition(
            name="edit",
            description=(
                "把工作区文件中唯一出现的 old_text 替换为 new_text。"
                "原文未出现或多处出现都拒绝，不做模糊匹配；原子写入，失败不改原文件。"
                "替换前应先 read 确认上下文。"
            ),
            parameters=EditArguments,
            handler=workspace.edit,
        ),
        ToolDefinition(
            name="bash",
            description=(
                "在工作区根目录执行 bash 命令，返回 exit_code/stdout/stderr"
                "（各截断到 64 KiB）和耗时。默认超时 120 秒，范围 1–600。"
                "命令以当前用户权限运行，能访问工作区外的文件和网络，**不是沙箱**；"
                "rm 类删除命令和工作区外路径会被拒绝。"
                "被拒后请换一种安全方式或询问用户，不要尝试绕过。"
                "不要用它读取或外传凭据。需要过滤大结果时，在命令里只输出必要部分。"
            ),
            parameters=BashArguments,
            handler=workspace.bash,
        ),
    ]


class AskUserArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    question: str = Field(min_length=1, max_length=2000,
                          description="要向用户澄清的问题；一次问清一件事。")
    options: list[str] | None = Field(
        default=None, max_length=8,
        description="可选：给用户的候选答案（最多 8 个）；不传则自由回答。")


def build_ask_user_tool(sessions, context) -> ToolDefinition:
    """GOV-001-S5：向用户提问并暂停等待回答。

    记录 ``pending_question`` 会话扩展（snapshot  surfaced），抛
    ``AuthorizationRequired("waiting_user")`` 停批；用户用既有 /resume 回答，
    答案作为 user 消息续跑。无副作用，不走分级审批。
    """
    from datetime import UTC, datetime

    def handler(question: str, options: list[str] | None = None) -> str:
        sessions.set_extension(context, "pending_question", {
            "question": question, "options": options,
            "asked_at": datetime.now(UTC).isoformat(),
        })
        raise AuthorizationRequired("waiting_user")

    return ToolDefinition(
        name="ask_user",
        description=(
            "需要用户澄清才能继续时调用：提出问题（可附候选选项），暂停等待用户回答。"
            "不要用它汇报进度或 rhetorical 提问；只在缺关键信息、有多个合理方向需用户拍板时用。"
            "用户回答后会作为新消息续跑。"
        ),
        parameters=AskUserArguments,
        handler=handler,
    )