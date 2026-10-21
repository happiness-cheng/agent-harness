import subprocess
import sys


def test_installed_package_imports_outside_project(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", "import min_agent"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
