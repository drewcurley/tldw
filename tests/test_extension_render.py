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


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_reading_the_transcript_from_the_page():
    """The path that lets a summarize happen without the server contacting
    YouTube — track selection, and degrading instead of throwing."""
    proc = subprocess.run(
        ["node", str(PAGE_CHECKS_JS), str(CONTENT_JS)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("script", ["content.js", "background.js", "options.js",
                                    "mute_words.js"])
def test_extension_scripts_parse(script):
    proc = subprocess.run(["node", "--check", str(ROOT / "extension" / script)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


PROBE_CHECKS_JS = Path(__file__).parent / "js" / "probe_checks.js"


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


MUTE_CHECKS_JS = Path(__file__).parent / "js" / "mute_checks.js"
MUTE_WORDS_JS = ROOT / "extension" / "mute_words.js"
# T-020: each group must run at least this many checks, so a group that silently
# runs nothing can't pass.
MUTE_GROUP_FLOORS = {"list": 12, "pattern": 15, "ranges": 17, "windows": 13,
                     "refine": 15, "refinement": 8, "step": 24, "timing": 9,
                     "sound": 20, "quiet": 12}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("group", sorted(MUTE_GROUP_FLOORS))
def test_cover_listed_words_pure_logic(group):
    """T-020: the real pure functions for "Cover listed words", run in node."""
    proc = subprocess.run(
        ["node", str(MUTE_CHECKS_JS), str(CONTENT_JS), str(MUTE_WORDS_JS), group],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    ran = int(proc.stdout.strip().rsplit("checks=", 1)[1].split()[0])
    assert ran >= MUTE_GROUP_FLOORS[group], f"{group} ran {ran} checks"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_cover_listed_words_unknown_group_fails():
    proc = subprocess.run(
        ["node", str(MUTE_CHECKS_JS), str(CONTENT_JS), str(MUTE_WORDS_JS), "nope"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode != 0


ALIGN_BG_CHECKS_JS = Path(__file__).parent / "js" / "align_bg_checks.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_background_word_timing_handler():
    """T-020: the real handleAlign -- streaming, every failure reason, and the abort
    on port disconnect."""
    proc = subprocess.run(["node", str(ALIGN_BG_CHECKS_JS), str(BACKGROUND_JS)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert int(proc.stdout.strip().rsplit("checks=", 1)[1]) >= 20


SETTINGS_LISTENER_JS = Path(__file__).parent / "js" / "settings_listener_checks.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_settings_listener_passes_only_the_cover_keys():
    """T-020 rev 5.6: the one storage.onChanged listener -- local area only,
    closeAction still live, and only muteSound/muteWords newValues passed on."""
    proc = subprocess.run(["node", str(SETTINGS_LISTENER_JS), str(CONTENT_JS)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert int(proc.stdout.strip().rsplit("checks=", 1)[1]) >= 12


MUTE_WIRING_JS = Path(__file__).parent / "js" / "mute_wiring_checks.js"
# T-020: the wiring groups and the least each must run.
MUTE_WIRING_FLOORS = {"harness": 5, "sound": 17, "session": 27, "ownership": 17,
                      "sounds": 9, "teardown": 20, "align": 18, "reasons": 18,
                      "panel": 11, "pill": 23, "live": 83}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("group", sorted(MUTE_WIRING_FLOORS))
def test_cover_listed_words_wiring(group):
    """T-020: the real session wiring -- listeners, ownership, sound, the pill, word
    timing and every failure status -- driven with a fake page, video and Web Audio."""
    proc = subprocess.run(
        ["node", str(MUTE_WIRING_JS), str(CONTENT_JS), str(MUTE_WORDS_JS), group],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    ran = int(proc.stdout.strip().rsplit("checks=", 1)[1].split()[0])
    assert ran >= MUTE_WIRING_FLOORS[group], f"{group} ran {ran} checks"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_cover_listed_words_wiring_unknown_group_fails():
    proc = subprocess.run(
        ["node", str(MUTE_WIRING_JS), str(CONTENT_JS), str(MUTE_WORDS_JS), "nope"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode != 0


OPTIONS_MUTE_JS = Path(__file__).parent / "js" / "options_mute_checks.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_options_cover_listed_words_fields():
    """T-020: the real options.js -- load, Restore and Save for the word list and
    the sound, with storage recorded."""
    proc = subprocess.run(
        ["node", str(OPTIONS_MUTE_JS), str(OPTIONS_JS), str(MUTE_WORDS_JS), str(CONTENT_JS)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert int(proc.stdout.strip().rsplit("checks=", 1)[1]) >= 25


# --- T-020: the Firefox copy list ----------------------------------------------

def _referenced_files(ext: Path) -> set[str]:
    """Every file the extension loads: both manifests' scripts, pages and icons,
    every executeScript files array, and every <script src> in options.html."""
    import json
    import re
    found = set()

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str) and re.fullmatch(r"[\w./-]+\.(?:js|html|png)", v):
            found.add(v)

    for name in ("manifest.json", "manifest.firefox.json"):
        walk(json.loads((ext / name).read_text(encoding="utf-8")))
    for path in ext.glob("*.js"):
        for m in re.finditer(r"files:\s*\[([^\]]*)\]", path.read_text(encoding="utf-8")):
            found.update(re.findall(r"\"([^\"]+)\"", m.group(1)))
    for page in ext.glob("*.html"):
        found.update(re.findall(r"<script\s+src=\"([^\"]+)\"", page.read_text(encoding="utf-8")))
    return found


def _missing_from_firefox_build(ext: Path) -> list[str]:
    subprocess.run(["bash", str(ext / "build-firefox.sh")], check=True,
                   capture_output=True, text=True, timeout=60)
    dist = ext / "dist-firefox"
    return sorted(f for f in _referenced_files(ext) if not (dist / f).is_file())


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_firefox_build_copies_every_file_the_extension_loads(tmp_path):
    ext = tmp_path / "extension"
    shutil.copytree(ROOT / "extension", ext,
                    ignore=shutil.ignore_patterns("dist-firefox"))
    assert "mute_words.js" in _referenced_files(ext)
    assert _missing_from_firefox_build(ext) == []


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_the_copy_list_check_catches_a_missing_file(tmp_path):
    ext = tmp_path / "extension"
    shutil.copytree(ROOT / "extension", ext,
                    ignore=shutil.ignore_patterns("dist-firefox"))
    script = ext / "build-firefox.sh"
    script.write_text(script.read_text(encoding="utf-8").replace(" mute_words.js", ""),
                      encoding="utf-8")
    assert _missing_from_firefox_build(ext) == ["mute_words.js"]


def test_the_injected_files_are_the_word_list_then_content():
    import re
    text = BACKGROUND_JS.read_text(encoding="utf-8")
    arrays = re.findall(r"executeScript\([^)]*files:\s*\[([^\]]*)\]", text)
    assert [re.findall(r"\"([^\"]+)\"", a) for a in arrays] == [["mute_words.js", "content.js"]]
    page = (ROOT / "extension" / "options.html").read_text(encoding="utf-8")
    assert re.findall(r"<script\s+src=\"([^\"]+)\"", page) == ["mute_words.js", "options.js"]
