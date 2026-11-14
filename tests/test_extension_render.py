"""The extension's HTML-building functions, exercised in node.

There is no JS test runner in this repo, but renderSummary/renderAnswer turn model
output into page HTML — an escaping or regex slip there is an injection bug — so
they get real coverage rather than none.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONTENT_JS = ROOT / "extension" / "content.js"
BACKGROUND_JS = ROOT / "extension" / "background.js"
OPTIONS_JS = ROOT / "extension" / "options.js"
CHECKS_JS = Path(__file__).parent / "js" / "render_checks.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_answer_rendering_escaping_and_citations():
    proc = subprocess.run(
        ["node", str(CHECKS_JS), str(CONTENT_JS), str(BACKGROUND_JS),
         str(OPTIONS_JS)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


PAGE_CHECKS_JS = Path(__file__).parent / "js" / "page_transcript_checks.js"
PAGE_CHECKS_FLOOR = 68


def _checks_run(stdout: str) -> int:
    """The count a node check script reports on its last line: "ok <n> checks"."""
    m = re.search(r"^ok (\d+) checks$", stdout.strip().splitlines()[-1] if stdout.strip() else "")
    return int(m.group(1)) if m else -1


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_reading_the_transcript_from_the_page():
    """The path that lets a summarize happen without the server contacting
    YouTube — track selection, and degrading instead of throwing."""
    proc = subprocess.run(
        ["node", str(PAGE_CHECKS_JS), str(CONTENT_JS)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # One pytest test stands for many node checks; deleting checks must still fail.
    assert _checks_run(proc.stdout) >= PAGE_CHECKS_FLOOR, proc.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("script", ["content.js", "background.js", "options.js",
                                    "channels.js"])
def test_extension_scripts_parse(script):
    proc = subprocess.run(["node", "--check", str(ROOT / "extension" / script)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


PROBE_CHECKS_JS = Path(__file__).parent / "js" / "probe_checks.js"
PROBE_CHECKS_FLOOR = 41


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_cache_check_before_reading_the_page():
    """Runs the real startSummarize and handleProbe with only their collaborators
    faked. It replaced regexes over the source, which four real regressions --
    an old server's 404 treated as an error among them -- all passed."""
    proc = subprocess.run(
        ["node", str(PROBE_CHECKS_JS), str(CONTENT_JS), str(BACKGROUND_JS)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _checks_run(proc.stdout) >= PROBE_CHECKS_FLOOR, proc.stdout


# --- automatic summaries ------------------------------------------------------------

EXT = ROOT / "extension"
CHANNELS_JS = EXT / "channels.js"
JS_DIR = Path(__file__).parent / "js"
YOUTUBE = "https://www.youtube.com/*"


def _node(script, *args):
    return subprocess.run(["node", str(JS_DIR / script), *map(str, args)],
                          capture_output=True, text=True, timeout=120)


# Each node script stands for many checks; the floor makes deleting checks fail.
@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("script,args,floor", [
    ("channels_checks.js", [CHANNELS_JS], 81),
    ("worker_checks.js", [BACKGROUND_JS, CHANNELS_JS], 162),
    ("options_checks.js", [OPTIONS_JS, CHANNELS_JS], 64),
    ("auto_run_checks.js", [CONTENT_JS, CHANNELS_JS], 240),
])
def test_automatic_summaries(script, args, floor):
    proc = _node(script, *args)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _checks_run(proc.stdout) >= floor, proc.stdout


@pytest.mark.parametrize("name", ["manifest.json", "manifest.firefox.json"])
def test_youtube_access_is_optional_and_nothing_else_changed(name):
    """Off by default: installing or updating asks for nothing new. The YouTube
    origin is only requested when the option is ticked."""
    manifest = json.loads((EXT / name).read_text())
    assert manifest["host_permissions"] == ["http://127.0.0.1:8765/*"]
    assert manifest["optional_host_permissions"] == [YOUTUBE]
    assert "content_scripts" not in manifest        # registered only when turned on
    assert manifest["permissions"] == ["activeTab", "scripting", "storage"]


def test_firefox_loads_the_shared_helpers_before_the_worker():
    manifest = json.loads((EXT / "manifest.firefox.json").read_text())
    assert manifest["background"]["scripts"] == ["channels.js", "background.js"]


def test_the_firefox_build_copies_the_shared_helpers():
    script = (EXT / "build-firefox.sh").read_text()
    copy = next(line for line in script.splitlines() if line.startswith("cp ") and "content.js" in line)
    assert "channels.js" in copy.split()


def test_the_options_page_loads_the_helpers_first():
    html = (EXT / "options.html").read_text()
    scripts = re.findall(r'<script src="([^"]+)"', html)
    assert scripts == ["channels.js", "options.js"]


def test_the_worker_imports_the_helpers_first_and_guarded():
    """Chrome's service worker; Firefox's event page has no importScripts, so the
    call is guarded, and a broken file must not take the worker down."""
    src = BACKGROUND_JS.read_text()
    code = [line for line in src.splitlines() if line.strip() and not line.strip().startswith("//")]
    assert code[0] == 'if (typeof importScripts === "function") {'
    assert code[1].strip().startswith('try { importScripts("channels.js"); } catch')


def test_both_injections_load_the_helpers_before_content_js():
    src = BACKGROUND_JS.read_text()
    lists = [json.loads(x) for x in re.findall(r'(?:files|js): (\[[^\]]*\])', src)]
    assert lists, "no script lists found"
    # Every injection loads the helpers first -- except the one fallback, which the
    # toolbar uses only when injecting channels.js itself failed.
    assert lists.count(["content.js"]) == 1, lists
    assert all(x in (["channels.js", "content.js"], ["content.js"]) for x in lists), lists
    fallback = src.index('files: ["content.js"]')
    assert src.rfind("} catch (e) {", 0, fallback) > src.rfind('files: ["channels.js", "content.js"]', 0, fallback)
    assert re.search(r'files: \["channels\.js", "content\.js"\]', src)   # the toolbar
    assert re.search(r'js: \["channels\.js", "content\.js"\]', src)      # the registration


def _options_page():
    """options.html as {id: (tag, attrs, ancestor ids)} -- what the hand-built page
    in options_checks.js assumes about it, so the two can't drift apart."""
    from html.parser import HTMLParser

    class Parser(HTMLParser):
        VOID = {"meta", "input", "br", "img", "link", "hr"}

        def __init__(self):
            super().__init__()
            self.stack, self.ids = [], {}

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if a.get("id"):
                self.ids[a["id"]] = (tag, a, [x for x in self.stack if x])
            if tag not in self.VOID:
                self.stack.append(a.get("id"))

        def handle_endtag(self, tag):
            if tag not in self.VOID and self.stack:
                self.stack.pop()

    parser = Parser()
    parser.feed((ROOT / "extension" / "options.html").read_text(encoding="utf-8"))
    return parser.ids


def test_the_options_test_page_matches_options_html():
    ids = _options_page()
    for name in ("serverUrl", "token", "voice", "preview", "sample", "previewStatus",
                 "model", "modelNote", "segmentRatio", "whisper", "closeAction", "save",
                 "status", "auto", "autoHeading", "autoUnsupported", "autoControls",
                 "autoToggle", "autoRun", "autoStatus", "autoChannels", "autoEmpty"):
        assert name in ids, name
    tag, attrs, _ = ids["autoHeading"]
    assert tag == "h3" and attrs.get("tabindex") == "-1"     # focus target after a remove
    tag, attrs, _ = ids["autoStatus"]
    assert attrs.get("role") == "status" and attrs.get("aria-live") == "polite"
    assert ids["autoRun"][1].get("type") == "checkbox"
    assert "hidden" in ids["autoUnsupported"][1] and "hidden" in ids["autoEmpty"][1]
    # The switch can be hidden on its own while the list stays usable.
    assert ids["autoRun"][2][-1] == "autoToggle"
    for inside in ("autoToggle", "autoStatus", "autoChannels", "autoEmpty"):
        assert "autoControls" in ids[inside][2], inside
    assert "autoControls" not in ids["autoUnsupported"][2]


def test_both_manifests_share_the_title_the_badge_restores():
    """Clearing the badge puts back the manifest's default_title; the two builds
    must agree on it, and on the version."""
    chrome = json.loads((ROOT / "extension" / "manifest.json").read_text())
    firefox = json.loads((ROOT / "extension" / "manifest.firefox.json").read_text())
    assert chrome["action"]["default_title"] == firefox["action"]["default_title"]
    assert chrome["action"]["default_title"]
    assert chrome["version"] == firefox["version"]
