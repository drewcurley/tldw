"""The extension's HTML-building functions, exercised in node.

There is no JS test runner in this repo, but renderSummary/renderAnswer turn model
output into page HTML — an escaping or regex slip there is an injection bug — so
they get real coverage rather than none.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONTENT_JS = ROOT / "extension" / "content.js"
CHECKS_JS = Path(__file__).parent / "js" / "render_checks.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_answer_rendering_escaping_and_citations():
    proc = subprocess.run(
        ["node", str(CHECKS_JS), str(CONTENT_JS)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("script", ["content.js", "background.js", "options.js"])
def test_extension_scripts_parse(script):
    proc = subprocess.run(["node", "--check", str(ROOT / "extension" / script)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
