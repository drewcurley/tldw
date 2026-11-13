"""`docs/contracts.md` must list every contract the code has, and nothing it doesn't.

Modelled on `test_isolation.py::test_conftest_covers_every_home_path`: each
category below is pulled out of the code by an extractor, and every item it finds
must appear in `docs/contracts.md` as an exact backticked token. A new route, env
var, storage key or selector that isn't documented fails the build, naming the
category and the item.

Conventions `docs/contracts.md` must follow (drafters, read this):

* **Exact backticked tokens.** An item counts as documented only when it appears
  as a whole code span, spelled exactly as in code: `/summarize/stream`, not
  `POST /summarize/stream`; `progress`, not `"progress"`. Anywhere in the doc
  counts for the forward check. Two categories are looser, and say so below:
  home paths (a token may continue past the path: `~/.cache/youtube-tldw/
  transcripts/<video_id>.json` covers `~/.cache/youtube-tldw/transcripts`) and
  temp prefixes (a trailing `*` is allowed).
* **What each category's token is.**
  routes: the path. http_methods: `GET`, `POST`, `OPTIONS`. ndjson_types and
  port_message_types: the bare type string. partial_kinds: the `kind` value.
  request_keys and response_keys: the key. status_codes: the number. cli_*:
  the subcommand, the flag (`--port`), the exit code (`130`). env_vars and
  config_keys: the name (config keys in both spellings the CLI accepts,
  `output-dir` and `output_dir`). storage_keys: the key. home_paths: the path
  with `~/` for the home directory. temp_prefixes: the prefix. executables: the
  program, or the module run with `python -m`. urlopen_sites and
  model_download_sites: the name of the function holding the call.
  manifest_permissions: the permission or host pattern. page_selectors: the
  selector string. thread_starts: both the thread's target function and the
  function that starts it.
* **Reverse checks** (doc lists it, code no longer has it -> fail) run for routes,
  env vars and storage keys. They read only the *first column of tables* under
  a heading (any level) whose text contains, case-insensitively:
  routes -> "routes"; env vars -> "environment variables"; storage keys ->
  "chrome.storage.local". A section runs to the next heading of the same or a
  higher level. Only first-column tokens shaped like the category are checked
  (a path starting with `/`; an UPPER_CASE name; a camelCase identifier), so a
  method or a note in that column is ignored. A doc with no such heading fails.

`EXCLUDED` holds every item an extractor finds that is deliberately not a
contract, each with its reason. A selector receiver that is neither in
`_PAGE_RECEIVERS` nor excluded fails too, so a new one forces a decision.

Each extractor has a count floor (`FLOORS`, set to what it finds today): an
extractor broken by a refactor finds nothing, and "nothing missing" would pass.

The leak check at the bottom is the deterministic half of the plan's public-repo
rule: it runs over `docs/` and the task files this playbook adds after the
seeded ones.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "youtube_tldw"
EXT = ROOT / "extension"
DOC = ROOT / "docs" / "contracts.md"

# --- exclusions ---------------------------------------------------------------

# item -> reason. Every entry must have a reason, and must still be found by an
# extractor (a stale exclusion fails), so this can't quietly hide a contract.
EXCLUDED: dict[str, str] = {
    "*": "content.js walks every element of a transcript row (`row.querySelectorAll(\"*\")`) "
         "looking for the text; it names no YouTube markup",
    "audio/mpeg": "the MIME type of a Blob in content.js (`{ type: \"audio/mpeg\" }`), "
                  "not a port message type",
    # Selector receivers. content.js builds its panel in a shadow root; these
    # query tldw's own markup, not YouTube's page, so no YouTube change breaks them.
    "root.querySelector*": "shadow root: tldw's own panel markup, not YouTube's page",
    "c.querySelector*": "the panel's `.content` element inside the shadow root",
    "box.querySelector*": "the sponsor box inside the shadow root",
    "pill.querySelector*": "tldw's own injected pill, not YouTube's page",
    "el.querySelector*": "the panel's `.meta` element inside the shadow root",
}

# Receivers whose querySelector* arguments are YouTube page selectors.
# `p` is a page panel (`panels.find((p) => p.querySelector(SEGMENT_ROWS))`);
# `d`/`doc` are pageMeta's and the transcript reader's document.
_PAGE_RECEIVERS = {"document", "d", "doc", "panel", "row", "p", "(doc || document)"}

# Set to what each extractor finds today (2026-11-12). Raise with the code.
FLOORS = {
    "routes": 11,
    "http_methods": 3,
    "ndjson_types": 16,
    "partial_kinds": 2,
    "request_keys": 28,
    "response_keys": 19,
    "port_message_types": 35,
    "port_names": 1,
    "status_codes": 7,
    "cli_subcommands": 8,
    "cli_flags": 26,
    "cli_exit_codes": 4,
    "env_vars": 10,
    "config_keys": 9,
    "storage_keys": 9,
    "home_paths": 11,
    "temp_prefixes": 3,
    "executables": 5,
    "urlopen_sites": 1,
    "model_download_sites": 2,
    "manifest_permissions": 4,
    "page_selectors": 13,
    "thread_starts": 12,
}

# --- helpers ------------------------------------------------------------------


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _tree(name: str) -> ast.Module:
    return ast.parse((SRC / name).read_text(encoding="utf-8"), filename=name)


def _enclosing_functions(tree: ast.AST) -> dict[ast.AST, str]:
    """node -> name of the innermost *named top-level-or-method* function holding it.
    Nested helpers (`def _run():` inside a function) map to their outer function,
    which is the one a reader can find by name."""
    owner: dict[ast.AST, str] = {}

    def visit(node, current):
        for child in ast.iter_child_nodes(node):
            name = current
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and current is None:
                name = child.name
            owner[child] = name
            visit(child, name)

    for top in tree.body:
        if isinstance(top, ast.ClassDef):
            for item in top.body:
                owner[item] = getattr(item, "name", None)
                visit(item, getattr(item, "name", None))
        else:
            start = top.name if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
            owner[top] = start
            visit(top, start)
    return owner


def _str(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _js(name: str) -> str:
    return (EXT / name).read_text(encoding="utf-8")


def _js_files():
    return sorted(EXT.glob("*.js"))


def _line(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _js_literal_keys(text: str, start: int) -> list[str]:
    """Top-level keys of the JS object literal whose `{` is at text[start]:
    `a: 1`, `"a": 1` and shorthand `a` all count; strings, comments and nesting
    are skipped over."""
    assert text[start] == "{", text[start:start + 20]
    depth, i, piece, pieces = 0, start, [], []
    while i < len(text):
        ch = text[i]
        if ch in "\"'`":
            end = i + 1
            while text[end] != ch:
                end += 2 if text[end] == "\\" else 1
            if depth == 1:
                piece.append(text[i:end + 1])
            i = end + 1
            continue
        if text.startswith("//", i):
            i = text.index("\n", i)
            continue
        if text.startswith("/*", i):
            i = text.index("*/", i) + 2
            continue
        if ch in "{[(":
            depth += 1
            if depth == 1:
                i += 1
                continue
        elif ch in "}])":
            depth -= 1
            if depth == 0:
                pieces.append("".join(piece))
                break
        elif ch == "," and depth == 1:
            pieces.append("".join(piece))
            piece = []
            i += 1
            continue
        if depth >= 1:
            piece.append(ch)
        i += 1
    keys = []
    for p in (p.strip() for p in pieces):
        m = (re.match(r"^([A-Za-z_$][\w$]*)\s*:", p) or re.match(r"^[\"']([^\"']+)[\"']\s*:", p)
             or re.match(r"^([A-Za-z_$][\w$]*)$", p))
        if m:
            keys.append(m.group(1))
        elif p:
            raise AssertionError(f"can't read a key from {p!r}: teach _js_literal_keys")
    return keys


# --- extractors: each returns {item: "path:line"} ------------------------------

def routes() -> dict[str, str]:
    """Every path literal in the five route-definition sites of server.py: do_GET,
    the do_POST chain (including the "not one of ours" tuple and the in-slot
    dispatch), and _body_limit. do_OPTIONS has no path of its own; its methods are
    in http_methods."""
    tree, found = _tree("server.py"), {}
    sites = {"do_GET", "do_POST", "_body_limit"}
    seen_sites = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in sites:
            seen_sites.add(node.name)
            for sub in ast.walk(node):
                s = _str(sub)
                if s and re.fullmatch(r"/[a-z/]+", s):
                    found.setdefault(s, f"src/youtube_tldw/server.py:{sub.lineno}")
    assert seen_sites == sites, f"route-definition sites moved: found {seen_sites}"
    return found


def http_methods() -> dict[str, str]:
    """The `do_X` handlers and the CORS Allow-Methods list; the two must agree."""
    tree, handlers, allowed = _tree("server.py"), {}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("do_"):
            handlers[node.name[3:]] = f"src/youtube_tldw/server.py:{node.lineno}"
        if (isinstance(node, ast.Call) and len(node.args) == 2
                and _str(node.args[0]) == "Access-Control-Allow-Methods"):
            for m in _str(node.args[1]).split(","):
                allowed[m] = f"src/youtube_tldw/server.py:{node.lineno}"
    assert set(handlers) == set(allowed), (
        f"do_* handlers {sorted(handlers)} and Allow-Methods {sorted(allowed)} disagree")
    return handlers


def ndjson_types() -> dict[str, str]:
    """Every `{"type": "x", ...}` server.py builds: the NDJSON events, and the
    plain-JSON `/summarize/cached` answer (`result` / `miss`)."""
    found = {}
    for node in ast.walk(_tree("server.py")):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if _str(k) == "type" and _str(v):
                    found.setdefault(_str(v), f"src/youtube_tldw/server.py:{node.lineno}")
    return found


def partial_kinds() -> dict[str, str]:
    """`kind` values summarize.py hands on_partial; server.py forwards each as a
    `partial` event. (core.py's own `meta` kind becomes a `meta` event instead.)"""
    found = {}
    for node in ast.walk(_tree("summarize.py")):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "on_partial"
                and node.args and isinstance(node.args[0], ast.Dict)):
            for k, v in zip(node.args[0].keys, node.args[0].values):
                if _str(k) == "kind" and _str(v):
                    found.setdefault(_str(v), f"src/youtube_tldw/summarize.py:{node.lineno}")
    return found


def request_keys() -> dict[str, str]:
    """Every key read from a request body: `body.get("k")`, `body["k"]`, the
    `for k in ("a", "b")` form, the `page` payload's `page.get`, a panel row's
    `row.get` (transcript.parse_panel_rows) and a history turn's `msg.get`
    (ask.trim_history)."""
    scopes = [("server.py", {"body", "page"}, None),
              ("transcript.py", {"row"}, "parse_panel_rows"),
              ("ask.py", {"msg"}, "trim_history")]
    found = {}
    for module, names, only_in in scopes:
        tree = _tree(module)
        owner = _enclosing_functions(tree)
        where = f"src/youtube_tldw/{module}"
        for node in ast.walk(tree):
            if only_in and owner.get(node) != only_in:
                continue
            target = key = None
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get" and node.args):
                target, key = node.func.value, node.args[0]
            elif isinstance(node, ast.Subscript):
                target, key = node.value, node.slice
            if not (isinstance(target, ast.Name) and target.id in names):
                continue
            if _str(key):
                found.setdefault(_str(key), f"{where}:{node.lineno}")
        for node in ast.walk(tree):  # all(isinstance(body.get(k), str) for k in (...))
            if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
                gen = node.generators[0]
                uses_body = any(isinstance(n, ast.Attribute) and n.attr == "get"
                                and isinstance(n.value, ast.Name) and n.value.id in names
                                for n in ast.walk(node.elt))
                if uses_body and isinstance(gen.iter, ast.Tuple):
                    for e in gen.iter.elts:
                        if _str(e):
                            found.setdefault(_str(e), f"{where}:{node.lineno}")
    return found


def response_keys() -> dict[str, str]:
    """Keys of `_to_payload`'s dict, of `/health`'s dict (do_GET) and of
    claude_client.backend_info(), which /health returns as `backend`."""
    found = {}
    for module, fn in (("server.py", "_to_payload"), ("server.py", "do_GET"),
                       ("claude_client.py", "backend_info")):
        hit = False
        for node in ast.walk(_tree(module)):
            if isinstance(node, ast.FunctionDef) and node.name == fn:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Dict):
                        for k in sub.keys:
                            if _str(k):
                                hit = True
                                found.setdefault(_str(k), f"src/youtube_tldw/{module}:{sub.lineno}")
        assert hit, f"{module}:{fn} has no dict literal any more"
    found.pop("error", None)  # do_GET's 404 body: an error shape, not a /health key
    return found


def _js_types():
    """(port message types, NDJSON types the background reads), each {type: where}."""
    port, ndjson = {}, {}
    for path in _js_files():
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"\btype:\s*\"([^\"]+)\"", text):
            port.setdefault(m.group(1), f"{_rel(path)}:{_line(text, m.start())}")
        for m in re.finditer(r"\b(\w+)\.type\s*===\s*\"([^\"]+)\"", text):
            bucket = ndjson if m.group(1) == "ev" else port
            bucket.setdefault(m.group(2), f"{_rel(path)}:{_line(text, m.start())}")
    return port, ndjson


def port_message_types() -> dict[str, str]:
    """Every `type: "x"` posted and every `m.type === "x"` / `msg.type === "x"`
    handled in extension/ -- the content<->background port protocol and the
    tabs.sendMessage `TLDW_*` messages. `ev.type` (the server's NDJSON, read by
    background.js) is kept out; see ndjson_types_read_by_background."""
    return _js_types()[0]


def port_names() -> dict[str, str]:
    found = {}
    for path in _js_files():
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"\.connect\(\s*\{\s*name:\s*\"([^\"]+)\"", text):
            found.setdefault(m.group(1), f"{_rel(path)}:{_line(text, m.start())}")
    return found


def status_codes() -> dict[str, str]:
    found = {}
    for path in _js_files():
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"\bstatus\s*===\s*(\d{3})\b", text):
            found.setdefault(m.group(1), f"{_rel(path)}:{_line(text, m.start())}")
    return found


def _cli_functions():
    tree = _tree("cli.py")
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def cli_subcommands() -> dict[str, str]:
    """`main`'s `argv[0] == "x"` dispatch, `tldw config`'s own `args[0] == "x"`
    actions, and the `choices` of positional arguments (`tldw cache clean`)."""
    fns, found = _cli_functions(), {}
    for fn, var in (("main", "argv"), ("_run_config", "args")):
        for node in ast.walk(fns[fn]):
            if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Subscript)
                    and getattr(node.left.value, "id", None) == var
                    and _str(node.comparators[0])):
                found.setdefault(_str(node.comparators[0]), f"src/youtube_tldw/cli.py:{node.lineno}")
    for node in ast.walk(_tree("cli.py")):
        if (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "add_argument"
                and node.args and _str(node.args[0]) and not _str(node.args[0]).startswith("-")):
            for kw in node.keywords:
                if kw.arg == "choices" and isinstance(kw.value, ast.List):
                    for e in kw.value.elts:
                        found.setdefault(_str(e), f"src/youtube_tldw/cli.py:{node.lineno}")
    return found


def cli_flags() -> dict[str, str]:
    """Every flag of every parser in cli.py: the default `<url>` mode's, `serve`'s,
    `cache`'s and `usage`'s."""
    found = {}
    for node in ast.walk(_tree("cli.py")):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "add_argument":
            for a in node.args:
                if _str(a) and _str(a).startswith("-"):
                    found.setdefault(_str(a), f"src/youtube_tldw/cli.py:{node.lineno}")
    return found


def cli_exit_codes() -> dict[str, str]:
    """Integer `return`s of `main`, `run` and every `_run_*` subcommand handler."""
    found = {}
    for name, fn in _cli_functions().items():
        if name in ("main", "run") or name.startswith("_run_"):
            for node in ast.walk(fn):
                if (isinstance(node, ast.Return) and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, int)):
                    found.setdefault(str(node.value.value), f"src/youtube_tldw/cli.py:{node.lineno}")
    return found


def env_vars() -> dict[str, str]:
    """`os.environ.get/[]/setdefault/pop`, `os.getenv`, `"K" in os.environ`, and
    the same on a copied `env` dict, in every module."""
    found = {}
    for path in sorted(SRC.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            target = key = None
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("get", "setdefault", "pop") and node.args):
                target, key = node.func.value, node.args[0]
            elif isinstance(node, ast.Subscript):
                target, key = node.value, node.slice
            elif (isinstance(node, ast.Compare) and len(node.ops) == 1
                    and isinstance(node.ops[0], (ast.In, ast.NotIn))):
                target, key = node.comparators[0], node.left
            is_env = ((isinstance(target, ast.Attribute) and target.attr == "environ")
                      or (isinstance(target, ast.Name) and target.id in ("env", "environ")))
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "getenv" and node.args):
                is_env, key = True, node.args[0]
            if is_env and _str(key) and re.fullmatch(r"[A-Z][A-Z0-9_]+", _str(key)):
                found.setdefault(_str(key), f"{_rel(path)}:{node.lineno}")
    return found


def config_keys() -> dict[str, str]:
    """Every `config.get("k")`, plus both spellings in `tldw config`'s keymap
    (`output-dir` on the command line, `output_dir` in config.json)."""
    found = {}
    for path in sorted(SRC.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get" and getattr(node.func.value, "id", None) == "config"
                    and node.args and _str(node.args[0])):
                found.setdefault(_str(node.args[0]), f"{_rel(path)}:{node.lineno}")
    keymap = None
    for node in ast.walk(_cli_functions()["_run_config"]):
        if (isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "keymap"):
            keymap = node.value
    assert isinstance(keymap, ast.Dict), "cli._run_config's keymap moved"
    for k, v in zip(keymap.keys, keymap.values):
        for s in (_str(k), _str(v)):
            found.setdefault(s, f"src/youtube_tldw/cli.py:{keymap.lineno}")
    return found


def storage_keys() -> dict[str, str]:
    """Keys of both `DEFAULTS` objects, of every object literal passed to
    `storage.local.get/set` (shorthand properties included), and every
    `changes.<key>` the content script's storage.onChanged listener reads."""
    found = {}
    for path in _js_files():
        text = path.read_text(encoding="utf-8")
        where = lambda i: f"{_rel(path)}:{_line(text, i)}"  # noqa: E731
        for m in re.finditer(r"\bconst DEFAULTS\s*=\s*", text):
            for k in _js_literal_keys(text, m.end()):
                found.setdefault(k, where(m.start()))
        for m in re.finditer(r"storage\.local\.(?:get|set)\(\s*", text):
            if text[m.end()] == "{":
                for k in _js_literal_keys(text, m.end()):
                    found.setdefault(k, where(m.start()))
            else:
                arg = re.match(r"[\w$]+", text[m.end():]).group(0)
                assert arg == "DEFAULTS", f"{where(m.start())}: storage call with {arg!r}"
        for m in re.finditer(r"\bchanges\.(\w+)", text):
            found.setdefault(m.group(1), where(m.start()))
    return found


def _home_path(node) -> str | None:
    """`Path.home() / "a" / "b"` -> "~/a/b"; None for anything else."""
    parts = []
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        if _str(node.right) is None:
            return None
        parts.insert(0, _str(node.right))
        node = node.left
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "home" and getattr(node.func.value, "id", None) == "Path"):
        return "/".join(["~", *parts])
    return None


def home_paths() -> dict[str, str]:
    """Every `Path.home() / ...` chain in src/, constants and inline ones alike
    (hygiene's Hugging Face default), written with `~`."""
    found = {}
    for path in sorted(SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # `a / "b" / "c"` nests: only the outermost chain is the path, not `~/a`.
        inner = {id(n.left) for n in ast.walk(tree)
                 if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)}
        for node in ast.walk(tree):
            p = None if id(node) in inner else _home_path(node)
            if p and p != "~":
                found.setdefault(p, f"{_rel(path)}:{node.lineno}")
    return found


def temp_prefixes() -> dict[str, str]:
    consts = {}
    for path in sorted(SRC.glob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and _str(node.value):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        consts[t.id] = _str(node.value)
    found = {}
    for path in sorted(SRC.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "mkdtemp":
                for kw in node.keywords:
                    if kw.arg == "prefix":
                        v = _str(kw.value)
                        if v is None and isinstance(kw.value, (ast.Attribute, ast.Name)):
                            v = consts.get(getattr(kw.value, "attr", None) or kw.value.id)
                        assert v, f"{_rel(path)}:{node.lineno}: mkdtemp prefix not resolvable"
                        found.setdefault(v, f"{_rel(path)}:{node.lineno}")
    return found


def executables() -> dict[str, str]:
    """argv lists: a literal program name followed by a literal flag
    (`["ffmpeg", "-y", ...]`), and the module of `[sys.executable, "-m", mod]`."""
    found = {}
    for path in sorted(SRC.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.List) or len(node.elts) < 2:
                continue
            first, second = node.elts[0], node.elts[1]
            if _str(first) and (_str(second) or "").startswith("-"):
                found.setdefault(_str(first), f"{_rel(path)}:{node.lineno}")
            if (isinstance(first, ast.Attribute) and first.attr == "executable"
                    and _str(second) == "-m" and len(node.elts) > 2 and _str(node.elts[2])):
                found.setdefault(_str(node.elts[2]), f"{_rel(path)}:{node.lineno}")
    return found


def _sites(predicate) -> dict[str, str]:
    found = {}
    for path in sorted(SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owner = _enclosing_functions(tree)
        for node in ast.walk(tree):
            if predicate(node):
                name = owner.get(node)
                assert name, f"{_rel(path)}:{node.lineno}: a call site outside any function"
                found.setdefault(name, f"{_rel(path)}:{node.lineno}")
    return found


def urlopen_sites() -> dict[str, str]:
    """Functions that make a direct urllib request (not through a subprocess)."""
    return _sites(lambda n: isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "urlopen")


def model_download_sites() -> dict[str, str]:
    """Functions that download a model: `python -m piper.download_voices`, and a
    faster-whisper `WhisperModel(..., download_root=...)`."""
    def is_download(n):
        if isinstance(n, ast.Call) and any(kw.arg == "download_root" for kw in n.keywords):
            return True
        return (isinstance(n, ast.List) and any("download" in (_str(e) or "") and "." in (_str(e) or "")
                                                for e in n.elts))
    return _sites(is_download)


def manifest_permissions() -> dict[str, str]:
    found = {}
    for name in ("manifest.json", "manifest.firefox.json"):
        data = json.loads((EXT / name).read_text(encoding="utf-8"))
        for key in ("permissions", "host_permissions"):
            for p in data.get(key, []):
                found.setdefault(p, f"extension/{name} {key}")
    return found


def _selector_constants(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2)
            for m in re.finditer(r"\bconst ([A-Z][A-Z0-9_]*)\s*=\s*\"([^\"]+)\"", text)}


_QS = re.compile(r"(\([^()]*\)|[A-Za-z_$][\w$]*)\s*\.\s*querySelector(?:All)?\(\s*")
_STRING = re.compile(r"\"([^\"]*)\"|'([^']*)'|`([^`$]*)`")


def _selector_calls():
    """(receiver, argument, where) for every querySelector* call in extension/."""
    for path in _js_files():
        text = path.read_text(encoding="utf-8")
        consts = _selector_constants(text)
        for m in _QS.finditer(text):
            receiver = m.group(1)
            rest = text[m.end():]
            s = _STRING.match(rest)
            if s:
                arg = next(g for g in s.groups() if g is not None)
            else:
                ident = re.match(r"[A-Za-z_$][\w$]*", rest).group(0)
                arg = consts.get(ident, ident)
            yield receiver, arg, f"{_rel(path)}:{_line(text, m.start())}"


def page_selectors() -> dict[str, str]:
    """YouTube page selectors in content.js: string arguments of querySelector* on
    a page receiver (`_PAGE_RECEIVERS`), selector constants they use (`SEGMENT_ROWS`,
    `PANEL`, `TRANSCRIPT_BUTTON`), and the first argument of pageMeta's `attr(` /
    `text(` helpers. `getElementById` is not read: content.js only looks up its own
    injected ids that way."""
    found = {}
    for receiver, arg, where in _selector_calls():
        if receiver in _PAGE_RECEIVERS and arg not in ("sel",):  # sel: attr/text's own
            found.setdefault(arg, where)
    text = _js("content.js")
    consts = _selector_constants(text)
    for name in ("SEGMENT_ROWS", "PANEL", "TRANSCRIPT_BUTTON"):
        assert name in consts, f"content.js no longer defines {name}"
        found.setdefault(consts[name], f"extension/content.js:{_line(text, text.index('const ' + name))}")
    for m in re.finditer(r"(?<![\w.$])(?:attr|text)\(\s*(['\"])(.+?)\1", text):
        found.setdefault(m.group(2), f"extension/content.js:{_line(text, m.start())}")
    return found


def thread_starts() -> dict[str, str]:
    """Every `threading.Thread(target=f)` and `threading.Timer(t, f)` (aliases such
    as claude_client's `_Timer` included): the target `f` and the function that
    starts it."""
    found = {}
    for path in sorted(SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owner = _enclosing_functions(tree)
        aliases = {t.id for n in tree.body if isinstance(n, ast.Assign)
                   and isinstance(n.value, ast.Attribute) and n.value.attr in ("Thread", "Timer")
                   for t in n.targets if isinstance(t, ast.Name)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            kind = (f.attr if isinstance(f, ast.Attribute) and getattr(f.value, "id", None) == "threading"
                    else ("Timer" if isinstance(f, ast.Name) and f.id in aliases else None))
            if kind not in ("Thread", "Timer"):
                continue
            target = next((kw.value for kw in node.keywords if kw.arg == "target"), None)
            if target is None and kind == "Timer" and len(node.args) >= 2:
                target = node.args[1]
            assert isinstance(target, ast.Name), f"{_rel(path)}:{node.lineno}: unnamed target"
            where = f"{_rel(path)}:{node.lineno}"
            found.setdefault(target.id, where)
            found.setdefault(owner[node], where)
    return found


EXTRACTORS = {f.__name__: f for f in (
    routes, http_methods, ndjson_types, partial_kinds, request_keys, response_keys,
    port_message_types, port_names, status_codes, cli_subcommands, cli_flags,
    cli_exit_codes, env_vars, config_keys, storage_keys, home_paths, temp_prefixes,
    executables, urlopen_sites, model_download_sites, manifest_permissions,
    page_selectors, thread_starts)}

# Categories whose doc token may run past the item (see the module docstring).
_PREFIX_OK = {"home_paths": "/", "temp_prefixes": "*"}


def _contract_items(category: str) -> dict[str, str]:
    return {k: v for k, v in EXTRACTORS[category]().items() if k not in EXCLUDED}


# --- reading the doc ----------------------------------------------------------

def _doc() -> str:
    if not DOC.exists():
        pytest.fail("docs/contracts.md missing: it is the contract inventory this test "
                    "checks the code against (T-001.17)")
    return DOC.read_text(encoding="utf-8")


def backticked(text: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"(?<!`)`([^`\n]+)`(?!`)", text)}


def missing_from(category: str, items, doc: str) -> list[str]:
    tokens = backticked(doc)
    sep = _PREFIX_OK.get(category)

    def documented(item):
        if item in tokens:
            return True
        if sep == "/":
            return any(t.startswith(item + "/") for t in tokens)
        if sep == "*":
            return item + "*" in tokens
        return False
    return sorted(i for i in items if not documented(i))


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
SECTION_KEYWORDS = {"routes": "routes", "env_vars": "environment variables",
                    "storage_keys": "chrome.storage.local"}
_SHAPES = {"routes": r"/[a-z/]+", "env_vars": r"[A-Z][A-Z0-9_]+",
           "storage_keys": r"[a-z][A-Za-z0-9]*"}


def section_first_column(doc: str, keyword: str) -> set[str] | None:
    """Backticked tokens in the first cell of table rows under every heading whose
    text contains `keyword`; None when no heading does."""
    tokens, level, matched = set(), None, False
    for line in doc.splitlines():
        h = _HEADING.match(line)
        if h:
            depth = len(h.group(1))
            if level is not None and depth <= level:
                level = None
            if level is None and keyword in h.group(2).lower():
                level, matched = depth, True
            continue
        if level is not None and line.lstrip().startswith("|"):
            cells = line.strip().strip("|").split("|")
            if cells and not re.fullmatch(r"\s*:?-{3,}:?\s*", cells[0]):
                tokens |= backticked(cells[0])
    return tokens if matched else None


def stale_in(category: str, code_items, doc: str) -> list[str]:
    listed = section_first_column(doc, SECTION_KEYWORDS[category])
    assert listed is not None, (
        f"docs/contracts.md has no heading containing {SECTION_KEYWORDS[category]!r}: "
        f"the reverse check for {category} reads that section's tables")
    shaped = {t for t in listed if re.fullmatch(_SHAPES[category], t)}
    return sorted(shaped - set(code_items))


# --- the checks ---------------------------------------------------------------

def test_every_exclusion_has_a_reason():
    bad = [k for k, why in EXCLUDED.items() if not (isinstance(why, str) and why.strip())]
    assert not bad, f"EXCLUDED entries need a reason: {bad}"


def test_no_exclusion_is_stale():
    found = set().union(*(f() for f in EXTRACTORS.values()))
    found |= {f"{r}.querySelector*" for r, _, _ in _selector_calls()}
    stale = sorted(set(EXCLUDED) - found)
    assert not stale, f"EXCLUDED entries no extractor finds any more (remove them): {stale}"


def test_every_selector_receiver_is_classified():
    """A new `x.querySelector(...)` must be decided: page selector or not."""
    unknown = sorted({f"{r} ({w})" for r, _, w in _selector_calls()
                      if r not in _PAGE_RECEIVERS and f"{r}.querySelector*" not in EXCLUDED})
    assert not unknown, ("querySelector receivers neither in _PAGE_RECEIVERS nor EXCLUDED: "
                         f"{unknown}")


def test_floors_cover_every_extractor():
    assert set(FLOORS) == set(EXTRACTORS)


@pytest.mark.parametrize("category", sorted(EXTRACTORS))
def test_extractor_still_finds_its_floor(category):
    items = _contract_items(category)
    assert len(items) >= FLOORS[category], (
        f"{category}: the extractor found {len(items)}, floor is {FLOORS[category]}. "
        f"A refactor probably broke it; fix the extractor, don't lower the floor "
        f"unless the code really lost them. Found: {sorted(items)}")


def test_background_reads_only_ndjson_types_the_server_emits():
    read = _js_types()[1]
    emitted = ndjson_types()
    assert read, "found no `ev.type === \"x\"` in background.js: the extractor broke"
    unknown = {t: w for t, w in read.items() if t not in emitted}
    assert not unknown, f"background.js reads NDJSON types server.py never emits: {unknown}"


@pytest.mark.parametrize("category", sorted(EXTRACTORS))
def test_contracts_md_lists_every(category):
    items = _contract_items(category)
    missing = missing_from(category, items, _doc())
    assert not missing, (
        f"docs/contracts.md is missing {len(missing)} {category} item(s), each needed as "
        f"an exact backticked token: " + ", ".join(f"`{i}` ({items[i]})" for i in missing))


@pytest.mark.parametrize("category", sorted(SECTION_KEYWORDS))
def test_contracts_md_lists_nothing_the_code_lost(category):
    stale = stale_in(category, _contract_items(category), _doc())
    assert not stale, (
        f"docs/contracts.md's {category} tables list item(s) the code no longer has: "
        f"{stale}. Remove them, or the code lost a contract it shouldn't have.")


def test_the_checks_catch_a_missing_and_a_stale_route():
    """The checks themselves, against a copy of the doc (or, until the doc exists, a
    minimal one built from the code): drop a real route, add a gone one."""
    code = _contract_items("routes")
    assert "/ask/stream" in code, "pick another known route for this test"
    if DOC.exists():
        doc = DOC.read_text(encoding="utf-8")
    else:
        doc = "## Routes\n\n| Route | Method |\n|---|---|\n" + "".join(
            f"| `{r}` | POST |\n" for r in sorted(code))
    assert missing_from("routes", code, doc) == []
    broken = doc.replace("`/ask/stream`", "/ask/stream")
    assert missing_from("routes", code, broken) == ["/ask/stream"]
    first_routes_heading = next(line for line in doc.splitlines()
                                if _HEADING.match(line) and "routes" in line.lower())
    padded = doc.replace(first_routes_heading,
                         first_routes_heading + "\n\n| Route |\n|---|\n| `/gone/route` |\n", 1)
    assert stale_in("routes", code, padded) == ["/gone/route"]


# --- leak check: docs/ and the task files added after the seeded ones ----------

# Seeded by the starter or by T-002/T-003, and allowlisted for template text;
# lines in them are filled in as their boxes are ticked (plan rev 4). INDEX.md is
# generated from the others' frontmatter.
_SEEDED_TASKS = re.compile(r"T-00[123](\.\d+)?\.md|_TEMPLATE\.md|README\.md|INDEX\.md")


def _secret_patterns() -> re.Pattern:
    """Read from scripts/ci-checks.sh so the two can't drift."""
    text = (ROOT / "scripts" / "ci-checks.sh").read_text(encoding="utf-8")
    m = re.search(r"^secret_patterns='([^']+)'", text, re.M)
    assert m, "scripts/ci-checks.sh no longer defines secret_patterns='...'"
    return re.compile(m.group(1))


# Built in pieces so this file never holds the literal marker itself.
_PLACEHOLDER = re.compile(r"\[" + "ins" + r"ert [^]]+ " + "he" + r"re\]")
_HOME_LEAKS = re.compile("|".join(re.escape(s) for s in (
    "/" + "Users/", "/" + "home/", "C:" + "\\" + "Users", "-" + "Users-")))


def _leak_targets() -> list[Path]:
    docs = [p for p in sorted((ROOT / "docs").rglob("*"))
            if p.is_file() and not p.name.startswith(".")]
    tasks = [p for p in sorted((ROOT / "tasks").glob("*.md"))
             if not _SEEDED_TASKS.fullmatch(p.name)]
    return docs + tasks


def leaks_in(text: str) -> list[tuple[int, str]]:
    secrets, hits = _secret_patterns(), []
    for n, line in enumerate(text.splitlines(), 1):
        for kind, rx in (("home path", _HOME_LEAKS), ("secret-shaped", secrets),
                         ("template placeholder", _PLACEHOLDER)):
            if rx.search(line):
                hits.append((n, kind))
    return hits


def test_leak_check_flags_what_it_should():
    samples = ["see /" + "Users/someone/x", "C:" + "\\" + "Users\\x",
               "~/.claude/projects/-" + "Users-x/", "gh" + "p_" + "A" * 36,
               "[" + "insert name " + "here]"]
    for s in samples:
        assert leaks_in(s), f"the leak check missed {s!r}"
    assert leaks_in("~/.cache/youtube-tldw and `<your-token>` and AAAAAAAAAAA") == []


def test_docs_and_new_task_files_carry_no_leaks():
    targets = _leak_targets()
    assert any(p.relative_to(ROOT).parts[0] == "docs" for p in targets), "found nothing in docs/"
    found = []
    for path in targets:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # binary (an image): nothing to read as text
        found += [f"{_rel(path)}:{n} ({kind})" for n, kind in leaks_in(text)]
    assert not found, "public-repo leak(s), see the plan's rule 8: " + ", ".join(found)
