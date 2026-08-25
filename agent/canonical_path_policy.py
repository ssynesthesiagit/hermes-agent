"""Fail-closed boundary for canonical Librarian paths.

The dedicated Librarian identity is the primary ownership boundary.  This
module is the process-level boundary: before dispatch, every file-producing
surface is checked against configured canonical roots.  Shell and arbitrary
code are not treated as parseable languages.  If they mention or resolve into
a canonical root they are denied unless the command is a small, provably
read-only invocation.
"""
from __future__ import annotations

import ast
import os
import re
import shlex
import urllib.parse
from pathlib import Path
from typing import Any, Iterable, Iterator


class CanonicalMutationDenied(PermissionError):
    pass


_READ_TOOLS = frozenset({"read_file", "search_files", "read_window", "read_preview"})
_DIRECT_PATH_MUTATORS = frozenset({"write_file", "edit_file", "remove_file", "patch", "apply_patch"})
_SHELL_TOOLS = frozenset({"terminal", "terminal_tool"})
_TRUSTED_LIBRARIAN_MCP_TOOLS = frozenset(
    {
        "mcp__yatima_librarian__memory_seed",
        "mcp__yatima_librarian__memory_query",
        "mcp__yatima_librarian__memory_capture",
        "mcp__yatima_librarian__memory_update_request",
        "mcp__yatima_librarian__memory_status",
    }
)
_PATCH_PATH = re.compile(r"(?m)^\*\*\* (?:Update|Add|Delete|Move to) File:\s*(.+?)\s*$")
_PATH_KEY_PARTS = frozenset(
    {
        "path", "file", "filename", "destination", "dest", "destination_root",
        "output", "output_path", "target_path", "save_to", "directory", "folder",
        "root", "download_dir", "upload_path",
    }
)
_FILE_PRODUCER_NAME = re.compile(
    r"(?:write|edit|patch|remove|delete|upload|download|save|export|render|attachment|"
    r"file|video|image|browser|result|terminal|execute)", re.IGNORECASE
)
_PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")
_BAD_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_MAX_DECODE_DEPTH = 16
_MAX_VALUE_LENGTH = 131_072
_READ_ONLY_PROGRAMS = frozenset(
    {"cat", "grep", "egrep", "fgrep", "head", "tail", "wc", "stat", "sha256sum", "md5sum"}
)
_PURE_CODE_MODULES = frozenset(
    {
        "builtins", "collections", "datetime", "decimal", "fractions", "functools",
        "hashlib", "heapq", "itertools", "json", "math", "operator", "pathlib",
        "re", "statistics", "string",
    }
)
_HERMES_READ_CALLS = frozenset({"read_file", "search_files", "read_window", "read_preview"})
_SAFE_BUILTIN_CALLS = frozenset(
    {
        "abs", "all", "any", "bool", "bytes", "complex", "dict", "divmod",
        "enumerate", "filter", "float", "format", "frozenset", "hash", "hex",
        "int", "isinstance", "issubclass", "iter", "len", "list", "map", "max",
        "min", "next", "oct", "ord", "pow", "print", "range", "repr", "reversed",
        "round", "set", "slice", "sorted", "str", "sum", "tuple", "type", "zip",
    }
)
_DYNAMIC_CODE_CALLS = frozenset({"eval", "exec", "compile", "__import__", "breakpoint", "input"})
_PATH_MUTATION_ATTRIBUTES = frozenset(
    {
        "chmod", "chown", "copy", "copy2", "copyfile", "copytree", "link", "lchmod",
        "lchown", "makedirs", "mkdir", "move", "openpty", "remove", "removedirs",
        "rename", "renames", "replace", "rmdir", "rmtree", "symlink", "symlink_to",
        "touch", "truncate", "unlink", "write", "writelines", "write_bytes", "write_text",
    }
)
_READ_ONLY_ATTRIBUTES = frozenset(
    {
        "absolute", "as_posix", "as_uri", "casefold", "center", "count", "exists",
        "find", "format", "format_map", "get", "glob", "group", "groups", "hexdigest",
        "index", "is_absolute", "is_dir", "is_file", "is_relative_to", "is_symlink",
        "items", "iterdir", "join", "joinpath", "keys", "lower", "lstat", "match",
        "name", "parent", "parents", "parts", "read", "read_bytes", "read_text",
        "relative_to", "resolve", "rfind", "rglob", "rindex", "split", "splitlines",
        "startswith", "stat", "stem", "strip", "suffix", "suffixes", "upper", "values",
        "with_name", "with_stem", "with_suffix",
    }
)


def _decode_value(value: str, *, strict_percent: bool = True) -> str:
    """Decode percent escapes to a stable value or reject ambiguous input."""
    decoded = str(value)
    if len(decoded) > _MAX_VALUE_LENGTH or "\x00" in decoded:
        raise CanonicalMutationDenied("canonical Librarian mutation denied: invalid or oversized path payload")
    for _ in range(_MAX_DECODE_DEPTH):
        if strict_percent and _BAD_PERCENT.search(decoded):
            raise CanonicalMutationDenied("canonical Librarian mutation denied: ambiguous percent encoding")
        if not _PERCENT_ESCAPE.search(decoded):
            break
        next_value = urllib.parse.unquote(decoded, errors="strict")
        if len(next_value) > _MAX_VALUE_LENGTH:
            raise CanonicalMutationDenied("canonical Librarian mutation denied: decoded payload is oversized")
        if next_value == decoded:
            break
        decoded = next_value
    else:
        if _PERCENT_ESCAPE.search(decoded):
            raise CanonicalMutationDenied("canonical Librarian mutation denied: percent decoding depth exceeded")
    if strict_percent and _BAD_PERCENT.search(decoded):
        raise CanonicalMutationDenied("canonical Librarian mutation denied: ambiguous percent encoding")
    if decoded.startswith("file://"):
        parsed = urllib.parse.urlsplit(decoded)
        if parsed.netloc not in ("", "localhost"):
            raise CanonicalMutationDenied("canonical Librarian mutation denied: remote file URI")
        decoded = parsed.path
    return os.path.expanduser(os.path.expandvars(decoded))


def _has_symlink_component(path: Path) -> bool:
    absolute = Path(os.path.abspath(path))
    for candidate in reversed((absolute, *absolute.parents)):
        try:
            if candidate.is_symlink():
                return True
        except OSError:
            return True
    return False


def _normalized_root(root: str | os.PathLike[str]) -> Path:
    raw = Path(_decode_value(os.fspath(root)))
    if not raw.is_absolute() or _has_symlink_component(raw) or not raw.is_dir():
        raise CanonicalMutationDenied("canonical Librarian policy root is unavailable or unsafe")
    return raw.resolve(strict=True)


def configured_canonical_roots() -> tuple[Path, ...]:
    try:
        from hermes_cli.config import load_config
        configured = load_config().get("security", {}).get("canonical_library_roots", [])
    except Exception as exc:
        raise CanonicalMutationDenied("canonical Librarian policy configuration unavailable") from exc
    if configured in (None, []):
        return ()
    if not isinstance(configured, list) or any(not isinstance(item, str) or not item.strip() for item in configured):
        raise CanonicalMutationDenied("canonical Librarian policy roots are invalid")
    return tuple(_normalized_root(item) for item in configured)


def _cwd_for_task(task_id: str | None) -> Path:
    try:
        from tools.file_tools import _authoritative_workspace_root
        raw = _authoritative_workspace_root(task_id or "default")
    except Exception:
        raw = None
    return Path(raw or os.getcwd()).resolve(strict=False)


def _contained(candidate: Path, roots: tuple[Path, ...]) -> bool:
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        resolved = Path(os.path.abspath(candidate))
    return any(resolved == root or root in resolved.parents for root in roots)


def _assert_path_allowed(value: str | os.PathLike[str], roots: tuple[Path, ...], cwd: Path) -> None:
    decoded = _decode_value(os.fspath(value)).strip().strip("'\"")
    if not decoded:
        return
    path = Path(decoded)
    candidate = path if path.is_absolute() else cwd / path
    if _contained(candidate, roots):
        raise CanonicalMutationDenied(
            "direct canonical Librarian mutation denied; use MCP capture/update-request or the authorized broker"
        )


def _iter_scalars(value: Any) -> Iterator[tuple[tuple[str, ...], str]]:
    def walk(item: Any, keys: tuple[str, ...]) -> Iterator[tuple[tuple[str, ...], str]]:
        if isinstance(item, dict):
            for key, child in item.items():
                yield from walk(child, keys + (str(key).casefold(),))
        elif isinstance(item, (list, tuple, set)):
            for child in item:
                yield from walk(child, keys)
        elif isinstance(item, (str, os.PathLike)):
            yield keys, os.fspath(item)
    yield from walk(value, ())


def _is_path_key(keys: tuple[str, ...]) -> bool:
    for key in keys:
        normalized = key.replace("-", "_").casefold()
        if normalized in _PATH_KEY_PARTS or any(
            part in normalized for part in ("path", "destination", "output", "save_to", "download", "upload")
        ):
            return True
    return False


def _text_mentions_root(text: str, roots: tuple[Path, ...]) -> bool:
    decoded = _decode_value(text, strict_percent=False)
    return any(str(root) in decoded for root in roots)


def _shell_candidates(command: str) -> Iterable[str]:
    decoded = _decode_value(command, strict_percent=False)
    try:
        tokens = shlex.split(decoded, posix=True)
    except ValueError as exc:
        raise CanonicalMutationDenied("canonical Librarian mutation denied: ambiguous shell quoting") from exc
    for token in tokens:
        cleaned = token.strip("(){}[];,<>=")
        if cleaned:
            yield cleaned
        if "=" in token:
            yield token.split("=", 1)[1].strip("'\"")
    for quoted in re.findall(r"['\"]([^'\"]+)['\"]", decoded):
        yield quoted
    for absolute in re.findall(r"/(?:[^\s'\";&|<>])+", decoded):
        yield absolute.rstrip("),]")


def _provably_read_only_shell(command: str) -> bool:
    if re.search(r"[;&|<>`]|\$\(|\n", command):
        return False
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return False
    if not tokens:
        return True
    program = Path(tokens[0]).name
    if program not in _READ_ONLY_PROGRAMS:
        return False
    dangerous_options = ("--output", "--files-with-matches", "--include", "--exclude")
    return not any(token.startswith(dangerous_options) for token in tokens[1:])


def _enforce_shell(command: str, roots: tuple[Path, ...], cwd: Path) -> None:
    decoded = _decode_value(command, strict_percent=False)
    if _provably_read_only_shell(decoded):
        return
    if _contained(cwd, roots):
        raise CanonicalMutationDenied(
            "direct canonical Librarian mutation denied: ambiguous code or command runs inside canonical root"
        )
    if _text_mentions_root(decoded, roots):
        raise CanonicalMutationDenied(
            "direct canonical Librarian mutation denied: ambiguous code or command contains canonical root"
        )

    # Follow literal `cd` anchors across command-list segments.  We do not try
    # to execute a shell grammar; unresolved/dynamic anchors fail closed when
    # a candidate can land in a canonical root.
    anchor = cwd
    for segment in re.split(r"(?:&&|\|\||;|\n)", decoded):
        stripped = segment.strip()
        cd_match = re.match(r"^cd\s+(?:--\s+)?([^\s]+)(?:\s|$)", stripped)
        if cd_match:
            destination = cd_match.group(1).strip("'\"")
            candidate = Path(destination)
            anchor = (candidate if candidate.is_absolute() else anchor / candidate).resolve(strict=False)
            if _contained(anchor, roots):
                raise CanonicalMutationDenied(
                    "direct canonical Librarian mutation denied: command changes directory into canonical root"
                )
        for candidate in _shell_candidates(stripped):
            try:
                _assert_path_allowed(candidate, roots, anchor)
            except CanonicalMutationDenied:
                raise
            except (OSError, ValueError):
                raise CanonicalMutationDenied("canonical Librarian mutation denied: ambiguous shell path")


def _attribute_path(node: ast.AST) -> tuple[str, ...] | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return tuple(reversed(parts))
    return None


def _literal_read_mode(call: ast.Call) -> bool:
    mode_node: ast.AST | None = call.args[1] if len(call.args) > 1 else None
    for keyword in call.keywords:
        if keyword.arg == "mode":
            mode_node = keyword.value
    if mode_node is None:
        return True
    return isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str) and mode_node.value in {
        "r", "rb", "rt",
    }


class _ExecuteCodeAllowlist(ast.NodeVisitor):
    """Reject code unless every reachable capability is statically read-only."""

    def __init__(self, tree: ast.AST):
        self.modules: dict[str, str] = {}
        self.imported: dict[str, tuple[str, str]] = {}
        self.functions = {
            node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    @staticmethod
    def deny(reason: str) -> None:
        raise CanonicalMutationDenied(f"canonical Librarian mutation denied: execute_code {reason}")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".", 1)[0]
            if top not in _PURE_CODE_MODULES and top != "hermes_tools":
                self.deny(f"import {alias.name!r} is not allowlisted")
            self.modules[alias.asname or top] = alias.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        top = module.split(".", 1)[0]
        if node.level or (top not in _PURE_CODE_MODULES and top != "hermes_tools"):
            self.deny(f"import from {module!r} is not allowlisted")
        for alias in node.names:
            if alias.name == "*":
                self.deny("wildcard imports are ambiguous")
            if top == "hermes_tools" and alias.name not in _HERMES_READ_CALLS:
                self.deny(f"Hermes capability {alias.name!r} is not read-only")
            self.imported[alias.asname or alias.name] = (module, alias.name)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__"):
            self.deny("dunder attribute access is dynamic")
        if node.attr in _PATH_MUTATION_ATTRIBUTES:
            # visit_Call classifies direct calls without visiting their func.
            # Reaching this visitor means the capability is being extracted or
            # passed around, so its eventual use cannot be proven read-only.
            self.deny(f"attribute {node.attr!r} can mutate files")
        self.generic_visit(node)

    def _check_module_call(self, module: str, attribute: str, call: ast.Call) -> None:
        top = module.split(".", 1)[0]
        if top == "hermes_tools":
            if attribute not in _HERMES_READ_CALLS:
                self.deny(f"Hermes capability {attribute!r} is not read-only")
            return
        if top == "builtins":
            if attribute == "open":
                if not _literal_read_mode(call):
                    self.deny("open mode is writable or dynamic")
                return
            if attribute not in _SAFE_BUILTIN_CALLS:
                self.deny(f"builtin call {attribute!r} is not allowlisted")
            return
        if top == "pathlib":
            if attribute not in {"Path", "PurePath", "PosixPath", "PurePosixPath"}:
                self.deny(f"pathlib call {attribute!r} is not allowlisted")
            return
        if top not in _PURE_CODE_MODULES:
            self.deny(f"module call {module}.{attribute} is not allowlisted")

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
            if name in _DYNAMIC_CODE_CALLS or name == "getattr" or name == "setattr" or name == "delattr":
                self.deny(f"dynamic call {name!r} is forbidden")
            imported = self.imported.get(name)
            if imported:
                self._check_module_call(imported[0], imported[1], node)
            elif name == "open":
                if not _literal_read_mode(node):
                    self.deny("open mode is writable or dynamic")
            elif name not in _SAFE_BUILTIN_CALLS and name not in self.functions:
                self.deny(f"call target {name!r} cannot be proven read-only")
        elif isinstance(func, ast.Attribute):
            path = _attribute_path(func)
            attribute = func.attr
            if attribute == "open":
                if not _literal_read_mode(node):
                    self.deny("open mode is writable or dynamic")
            elif attribute in _PATH_MUTATION_ATTRIBUTES:
                self.deny(f"attribute call {attribute!r} can mutate files")
            elif path and path[0] in self.modules:
                self._check_module_call(self.modules[path[0]], attribute, node)
            elif attribute not in _READ_ONLY_ATTRIBUTES:
                self.deny(f"attribute call {attribute!r} cannot be proven read-only")
        else:
            self.deny("dynamic call target cannot be proven read-only")
        # Visit arguments and receivers, but not func through visit_Attribute:
        # the call-specific checks above already classified that attribute.
        if isinstance(func, ast.Attribute):
            self.visit(func.value)
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)


def _enforce_execute_code(code: str) -> None:
    decoded = _decode_value(code, strict_percent=False)
    try:
        tree = ast.parse(decoded, mode="exec")
    except (SyntaxError, ValueError, TypeError) as exc:
        raise CanonicalMutationDenied(
            "canonical Librarian mutation denied: execute_code is not statically parseable"
        ) from exc
    _ExecuteCodeAllowlist(tree).visit(tree)


def assert_attachment_destination_allowed(
    destination: str | os.PathLike[str], *, roots: tuple[Path, ...] | None = None
) -> None:
    normalized = tuple(_normalized_root(root) for root in roots) if roots is not None else configured_canonical_roots()
    if normalized:
        _assert_path_allowed(destination, normalized, Path.cwd())


def enforce_tool_path_policy(
    tool_name: str,
    arguments: dict[str, Any] | None,
    *,
    task_id: str | None = None,
    roots: tuple[Path, ...] | None = None,
    cwd: Path | None = None,
) -> None:
    """Raise before execution when a non-broker tool could mutate canonical data."""
    name = str(tool_name or "")
    lowered = name.casefold()
    if lowered in _READ_TOOLS:
        return
    normalized = tuple(_normalized_root(root) for root in roots) if roots is not None else configured_canonical_roots()
    if not normalized:
        return
    args = arguments if isinstance(arguments, dict) else {}
    anchor = Path(cwd).resolve(strict=False) if cwd is not None else _cwd_for_task(task_id)

    if lowered in _TRUSTED_LIBRARIAN_MCP_TOOLS:
        for keys, value in _iter_scalars(args):
            if _is_path_key(keys):
                raise CanonicalMutationDenied("canonical Librarian MCP tools accept non-path payloads only")
        return

    if lowered in _SHELL_TOOLS:
        _enforce_shell(str(args.get("command", args.get("cmd", ""))), normalized, anchor)
        return
    if lowered == "execute_code":
        _enforce_execute_code(str(args.get("code", args.get("script", ""))))
        return

    scalars = tuple(_iter_scalars(args))
    if lowered in {"patch", "apply_patch"}:
        for _keys, value in scalars:
            for patch_path in _PATCH_PATH.findall(value):
                _assert_path_allowed(patch_path, normalized, anchor)

    is_file_producer = lowered in _DIRECT_PATH_MUTATORS or bool(_FILE_PRODUCER_NAME.search(lowered))
    for keys, value in scalars:
        if _is_path_key(keys):
            _assert_path_allowed(value, normalized, anchor)
        elif is_file_producer and _text_mentions_root(value, normalized):
            raise CanonicalMutationDenied(
                "direct canonical Librarian mutation denied: file-producing payload contains canonical root"
            )
