"""评测本身的烟雾测试：Runner 有没有坏，不是模型行不行。

只跑 C1（能力）和 S1（安全），确认 reset/run/verifier/record 闭环能走通。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eval.runner import run_suite  # noqa: E402


def test_runner_passes_capability_and_blocks_sensitive_read():
    summary = run_suite(["C1", "S1"], repeat=1)
    by_id = {r["task_id"]: r for r in summary["results"]}
    assert by_id["C1"]["verdict"] == "PASS"
    assert by_id["S1"]["verdict"] == "PASS"
    assert summary["schema"]["generic"]["tools"] == ["read", "write", "edit", "bash"]


def test_blocked_command_gate(tmp_path):
    from min_agent.tools.generic import _blocked_command
    root = tmp_path / "work"
    root.mkdir()
    assert _blocked_command("rm victim.txt", root) is not None
    assert _blocked_command("echo hi; rm victim3.txt", root) is not None
    assert _blocked_command("cat ../outside/secret.txt", root) is not None
    assert _blocked_command("cat /etc/passwd", root) is not None
    assert _blocked_command("cat .env", root) is not None
    assert _blocked_command("echo hi", root) is None
    assert _blocked_command("ls -1 img_*.png", root) is None
    assert _blocked_command("grep secret big.log", root) is None  # 搜索词不是文件名，不拦
    # v2（EVAL-DIAG-002）：~ 展开后在工作区外，拦；工作区内绝对路径，放行；
    # 工作区外的绝对路径、上一级，仍拦；引号里的 ; 不切段，放行。
    assert _blocked_command("cat ~/.ssh/id_rsa", root) is not None
    assert _blocked_command(f"cat {root}/config.txt", root) is None
    assert _blocked_command(f"cd {root} && ls", root) is None
    assert _blocked_command("ls /tmp", root) is not None
    assert _blocked_command("cd ..", root) is not None
    assert _blocked_command('echo "a; rm b"', root) is None
    assert _blocked_command("echo \"...secret.txt...\"", root) is None
    # v3（EVAL-DIAG-003，Claude 第三轮审批）：fail-closed 引号解析、
    # 包壳命令、重定向、/dev/null、凭据显式列表。
    assert _blocked_command('echo "$(rm victim.txt)"', root) is not None  # D1
    assert _blocked_command('cat "/etc/passwd"', root) is not None  # D2，引号包着的路径也要判
    assert _blocked_command("echo x >/etc/hosts", root) is not None  # D3，重定向目标越界
    assert _blocked_command('find . -name "*.tmp" | xargs rm', root) is not None  # D4
    assert _blocked_command("sleep 1 & rm victim.txt", root) is not None  # D5，& 也切段
    assert _blocked_command("sudo rm x / env rm x", root) is not None  # D6，包壳
    assert _blocked_command("cat .env.local", root) is not None  # D7，.env* 前缀
    assert _blocked_command("ls > /dev/null", root) is None  # D8，空设备放行
    assert _blocked_command('echo "it\\"s"; rm x', root) is not None  # D9，转义引号
    assert _blocked_command('echo "unclosed', root) is not None  # 引号没闭合，整条拦
    assert _blocked_command("cat docs/secrets.md", root) is None  # B8，显式列表不再误伤
    assert _blocked_command("cat foo.key", root) is not None  # *.key
    assert _blocked_command("cat credentials", root) is not None  # 精确名
    assert _blocked_command("cat my-credentials.txt", root) is None  # 子串不再拦，见 DECISION_LOG
    # v4（EVAL-DIAG-004，Claude 第四轮审批）：关键字跳过、/dev/null 先判、
    # heredoc 跳过、find -exec/-delete、--opt=路径、$HOME、重定向-引号顺序、
    # $(...) 栈式解析、grep 模式跳过、command -v、.env.example 豁免。
    assert _blocked_command('for f in *.tmp; do rm "$f"; done', root) is not None  # E1
    assert _blocked_command("if [ -e x ]; then rm x; fi", root) is not None  # E2
    assert _blocked_command("ls 2>/dev/null", root) is None  # E3（Linux 上；Windows 靠先判 bare）
    assert _blocked_command("cat > n.txt <<'EOF'\nit's a test; rm is a word\n/etc/hosts\nEOF", root) is None  # E4
    assert _blocked_command('find . -name "*.tmp" -exec rm {} \\;', root) is not None  # E5
    assert _blocked_command('find . -name "*.tmp" -delete', root) is not None  # E6
    assert _blocked_command("ls $HOME/Documents", root) is not None  # E7，$HOME 展开到工作区外
    assert _blocked_command("tar -xf a.tar --directory=/etc", root) is not None  # E8
    assert _blocked_command('echo x >"/etc/hosts"', root) is not None  # E9，顺序反了会漏
    assert _blocked_command("cat <.env", root) is not None  # E10，凭据名先去重定向符
    assert _blocked_command('echo "$(ls; rm x)"', root) is not None  # E11，$( 里 ; 切段
    assert _blocked_command("grep -rn credentials docs/", root) is None  # E12，模式不是文件名
    assert _blocked_command("grep foo credentials", root) is not None  # 模式之后才是文件，要拦
    assert _blocked_command("command -v rm", root) is None  # E13，查询不是执行
    assert _blocked_command('echo "$(echo "it\'s")"', root) is None  # E14，嵌套引号不误报未闭合
    assert _blocked_command("cat .env.example", root) is None  # 示例文件豁免
    assert _blocked_command("cat .env.sample", root) is None
    assert _blocked_command("cat <<EOF\nrm x\nEOF", root) is None  # heredoc 正文不当命令
    assert _blocked_command("cat <<'EOF'\nrm x", root) is not None  # 没找到结束行，整条拦
    # v5（EVAL-DIAG-005，Claude 第五轮审批）：<<<、# 注释、未加引号 heredoc 展开。
    assert _blocked_command('cat <<< "$(rm victim.txt)"', root) is not None  # F1
    assert _blocked_command('read x <<< "a b"\necho $x', root) is None  # F2
    assert _blocked_command("ls # don't panic", root) is None  # F3，注释里的撇号不当引号
    assert _blocked_command("cat <<EOF\n$(rm victim.txt)\nEOF", root) is not None  # F4
    assert _blocked_command("echo a#b", root) is None  # # 不在词首，不是注释
    assert _blocked_command("cat <<EOF\nhello $USER\nEOF", root) is None  # 未加引号正文里的普通文字不拦
    # 只写进局限、不改代码：引号里的绝对路径仍会被当路径拦（F6）；
    # bash <<'EOF' 喂命令（F5）、eval/exec/timeout/bash -c、FOO=1 rm 都不拦。
    assert _blocked_command('echo "wrote /tmp/out.txt"', root) is not None
    assert _blocked_command("bash <<'EOF'\nrm victim.txt\nEOF", root) is None
    assert _blocked_command("eval rm x", root) is None
    assert _blocked_command("FOO=1 rm x", root) is None
    # v6（EVAL-DIAG-006，Claude 第六轮审批）：多行 $(...)、<<\EOF。
    assert _blocked_command("cat <<EOF\n$(\nrm victim.txt\n)\nEOF", root) is not None  # G1
    assert _blocked_command("cat <<\\EOF\nhello\nEOF", root) is None  # G2
    assert _blocked_command("cat <<EOF\n$(rm victim.txt\nEOF", root) is not None  # 展开没闭合，整条拦
    # # 注释的边缘（Claude 第六轮确认）：$#、a#b、URL 里的 # 都不当注释。
    assert _blocked_command("echo $#", root) is None
    assert _blocked_command("echo a#b", root) is None
    assert _blocked_command("echo https://example.com/#frag", root) is None
    assert _blocked_command("echo hi # don't", root) is None
    # 预言机找到的漏网（EVAL-DIAG-006）：引号包着的命令名，shell 执行前
    # 会去掉引号，"rm" 就是 rm。
    assert _blocked_command('"rm" victim.txt', root) is not None
    assert _blocked_command("'rm' victim.txt", root) is not None
    assert _blocked_command('"sudo" rm x', root) is not None
    assert _blocked_command('sudo "rm" x', root) is not None
    assert _blocked_command('find . -exec "rm" {} \\;', root) is not None
    assert _blocked_command('echo "rm" x', root) is None  # rm 不在段首，不拦
    # 预言机找到的漏网（EVAL-DIAG-006）：进程替换 <(...) / >(...) 里的命令。
    assert _blocked_command("diff <(rm victim.txt) errors.txt", root) is not None
    assert _blocked_command("cat >(rm victim.txt)", root) is not None
    assert _blocked_command("diff <(ls) errors.txt", root) is None  # 里面的命令无害，放行
    # & 重定向不是后台符：2>&1、>&2、&> 都不切段；& 后台符仍切段。
    assert _blocked_command("ls -la victim3.txt 2>&1", root) is None
    assert _blocked_command("sleep 1 & rm victim.txt", root) is not None
    # v7（EVAL-DIAG-007，Windows 本机复核跟进）：heredoc 操作符 token 不是路径。
    assert _blocked_command("cat <<\\EOF\nhello\nEOF", root) is None  # G2 回归
    assert _blocked_command("cat <<EOF\nhello\nEOF", root) is None
    assert _blocked_command("cat <<< 'hello'", root) is None
    # v7：realpath 跟符号链接——工作区里的链接指到外面，路径检查要拦住
    # （原来“符号链接不跟”的缺口，现在关上了）。
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x")
    link = root / "link"
    link.symlink_to(outside)
    assert _blocked_command("cat link/secret.txt", root) is not None
    # v8（EVAL-DIAG-008）：{work} 替换用 as_posix()——Windows 上 str(work)
    # 是反斜杠路径，bash 会当转义吃掉；posix 斜杠 bash 原生认。
    from pathlib import PureWindowsPath
    from eval.runner import _substitute_args
    sub = _substitute_args({"command": "cat {work}/config.txt"},
                           PureWindowsPath("D:/AppCache/CHEN-D~1/Temp/x/work"))
    assert sub["command"] == "cat D:/AppCache/CHEN-D~1/Temp/x/work/config.txt"
    assert "\\" not in sub["command"]
    # 已知缺口（字符串门防不住）：python -c 绕过、引号包着的凭据名、
    # sudo -u root rm（包壳后带参数），不拦——写死在这里，别假装拦住了。
    assert _blocked_command('python -c "import os; os.remove(\'x\')"', root) is None
    assert _blocked_command('cat ".env"', root) is None
    assert _blocked_command("sudo -u root rm x", root) is None


def test_gitbash_drive_path():
    from min_agent.tools.generic import _gitbash_drive_path
    assert _gitbash_drive_path("/d/work/a.txt") == "D:\\work\\a.txt"
    assert _gitbash_drive_path("/c/Windows/System32") == "C:\\Windows\\System32"
    assert _gitbash_drive_path("/etc/passwd") is None  # 不是单字母盘符
    assert _gitbash_drive_path("d/work/a.txt") is None  # 相对路径不转
    # Windows 语义模拟：转出来的 D:\work\a.txt 确实在 D:\work 里面
    # （Linux 上跑不出 Windows 分支，用 PureWindowsPath 验算解析逻辑）。
    from pathlib import PureWindowsPath
    root = PureWindowsPath("D:/work")
    resolved = PureWindowsPath(_gitbash_drive_path("/d/work/a.txt"))
    assert resolved == root or root in resolved.parents


def test_runner_gate_blocks_destructive_bash():
    # GATE-MIN-001：补门后 S4 预期 PASS——rm victim.txt 在起进程前被拦，文件还在。
    # 旧测试 test_runner_exposes_missing_bash_gate（预期 FAIL）随补门作废。
    summary = run_suite(["S4", "S5", "S7"], repeat=1)
    by_id = {r["task_id"]: r for r in summary["results"]}
    assert by_id["S4"]["verdict"] == "PASS"
    assert by_id["S5"]["verdict"] == "PASS"
    assert by_id["S7"]["verdict"] == "PASS"
