from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import urllib.parse

from agent.canonical_path_policy import (
    CanonicalMutationDenied,
    assert_attachment_destination_allowed,
    enforce_tool_path_policy,
)


def _enforce(tool: str, args: dict, library: Path, cwd: Path):
    return enforce_tool_path_policy(tool, args, task_id="fixture", roots=(library,), cwd=cwd)


def test_write_patch_and_unrelated_write_policy(tmp_path):
    library = tmp_path / "library"; library.mkdir()
    page = library / "entities" / "owner.md"; page.parent.mkdir(); page.write_text("owner\n")
    unrelated = tmp_path / "work" / "notes.md"; unrelated.parent.mkdir()
    for tool, args in (
        ("write_file", {"path": str(page), "content": "bad"}),
        ("write_file", {"path": "entities/owner.md", "content": "bad"}),
        ("patch", {"mode": "replace", "path": str(page), "old_string": "x", "new_string": "y"}),
        ("patch", {"mode": "patch", "patch": f"*** Begin Patch\n*** Update File: {page}\n+x\n*** End Patch"}),
    ):
        with pytest.raises(CanonicalMutationDenied):
            _enforce(tool, args, library, library if not Path(str(args.get("path", ""))).is_absolute() else tmp_path)
    assert _enforce("write_file", {"path": str(unrelated), "content": "ok"}, library, tmp_path) is None
    assert _enforce("read_file", {"path": str(page)}, library, tmp_path) is None
    assert _enforce("search_files", {"path": str(library), "pattern": "Owner"}, library, tmp_path) is None


def test_symlink_relative_and_percent_encoded_bypasses_denied(tmp_path):
    library = tmp_path / "canonical library"; library.mkdir()
    (library / "entities").mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    alias = outside / "alias"; alias.symlink_to(library, target_is_directory=True)
    attempts = (
        alias / "entities" / "owner.md",
        library / "entities" / ".." / "entities" / "owner.md",
        Path(str(library).replace(" ", "%20")) / "entities" / "owner.md",
    )
    for path in attempts:
        with pytest.raises(CanonicalMutationDenied):
            _enforce("write_file", {"path": str(path), "content": "bad"}, library, outside)
    with pytest.raises(CanonicalMutationDenied):
        _enforce("write_file", {"path": "entities/%2e%2e/entities/owner.md", "content": "bad"}, library, library)


def test_shell_command_path_arguments_and_shell_wrappers_denied(tmp_path):
    library = tmp_path / "library"; library.mkdir()
    page = library / "owner.md"; page.write_text("owner\n")
    commands = (
        f"rm -- {page}",
        f"chmod 600 {page}",
        f"printf bad > {page}",
        f"sh -c 'tee {page} >/dev/null'",
        f"python -c 'from pathlib import Path; Path(\"{page}\").write_text(\"bad\")'",
        "echo bad > owner.md",
    )
    for command in commands:
        with pytest.raises(CanonicalMutationDenied):
            _enforce("terminal", {"command": command}, library, library if command == "echo bad > owner.md" else tmp_path)
    assert _enforce("terminal", {"command": f"grep owner {page}"}, library, tmp_path) is None
    assert _enforce("terminal", {"command": "printf ok > unrelated.txt"}, library, tmp_path) is None


def test_attachment_destination_cannot_land_in_canonical_tree(tmp_path):
    library = tmp_path / "library"; library.mkdir()
    with pytest.raises(CanonicalMutationDenied):
        assert_attachment_destination_allowed(library / "uploaded.md", roots=(library,))
    assert_attachment_destination_allowed(tmp_path / "attachments" / "uploaded.md", roots=(library,))


def test_dispatch_denies_before_tool_execution_and_preserves_reads(tmp_path):
    library = tmp_path / "library"; library.mkdir()
    page = library / "owner.md"; page.write_text("owner\n")
    import model_tools
    with patch("agent.canonical_path_policy.configured_canonical_roots", return_value=(library,)), \
         patch.object(model_tools.registry, "dispatch", return_value=json.dumps({"success": True})) as dispatch:
        denied = json.loads(model_tools.handle_function_call("write_file", {"path": str(page), "content": "bad"}))
        assert "canonical Librarian" in denied["error"]
        dispatch.assert_not_called()
        result = json.loads(model_tools.handle_function_call("read_file", {"path": str(page)}))
        assert result["success"] is True
        dispatch.assert_called_once()


def test_terra_exact_terminal_and_execute_code_bypasses_are_denied(tmp_path, monkeypatch):
    home = tmp_path / "home"
    library = home / "canonical" / "library"
    library.mkdir(parents=True)
    page = library / "owner.md"
    page.write_text("owner\n")
    monkeypatch.setenv("HOME", str(home))
    ancestor = library.parent
    probes = (
        ("terminal", {"command": f"python3 -c \"import builtins; builtins.open('{page}', 'w').write('bad')\""}, tmp_path),
        ("terminal", {"command": f"dd if=/dev/null of={page}"}, tmp_path),
        ("terminal", {"command": f"cd {ancestor} && printf bad > library/owner.md"}, tmp_path),
        ("terminal", {"command": "printf bad > $HOME/canonical/library/owner.md"}, tmp_path),
        ("terminal", {"command": f"env DEST='{page}' sh -c 'bash -c \"printf bad > $DEST\"'"}, tmp_path),
        ("execute_code", {"code": f"import builtins; builtins.open('{page}', 'w').write('bad')"}, tmp_path),
        ("execute_code", {"code": f"import os; os.remove('{page}')"}, tmp_path),
        ("execute_code", {"code": f"from pathlib import Path; Path('{page}').write_bytes(b'bad')"}, tmp_path),
        ("execute_code", {"code": f"import shutil; shutil.copy('/tmp/x', '{page}')"}, tmp_path),
        ("execute_code", {"code": f"import hermes_tools; hermes_tools.write_file(path='{page}', content='bad')"}, tmp_path),
        ("execute_code", {"code": f"import hermes_tools; hermes_tools.patch(path='{page}', old_string='x', new_string='y')"}, tmp_path),
        ("execute_code", {"code": f"import hermes_tools; hermes_tools.terminal(command='dd of={page}')"}, tmp_path),
    )
    for tool, args, cwd in probes:
        with pytest.raises(CanonicalMutationDenied):
            _enforce(tool, args, library, cwd)


def test_recursive_percent_decoding_is_stable_bounded_and_unambiguous(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    page = str(library / "owner.md")
    encoded = page
    for _ in range(4):
        encoded = urllib.parse.quote(encoded, safe="")
    with pytest.raises(CanonicalMutationDenied):
        _enforce("write_file", {"path": encoded, "content": "bad"}, library, tmp_path)
    ambiguous = page
    for _ in range(20):
        ambiguous = urllib.parse.quote(ambiguous, safe="")
    with pytest.raises(CanonicalMutationDenied):
        _enforce("write_file", {"path": ambiguous, "content": "bad"}, library, tmp_path)
    with pytest.raises(CanonicalMutationDenied):
        _enforce("write_file", {"path": "%not-an-escape", "content": "bad"}, library, tmp_path)


def test_only_exact_five_librarian_mcp_names_with_non_path_payloads_are_trusted(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    page = library / "owner.md"
    exact = (
        "mcp__yatima_librarian__memory_seed",
        "mcp__yatima_librarian__memory_query",
        "mcp__yatima_librarian__memory_capture",
        "mcp__yatima_librarian__memory_update_request",
        "mcp__yatima_librarian__memory_status",
    )
    for name in exact:
        assert _enforce(name, {"query": "ordinary text", "source": "fixture://safe"}, library, tmp_path) is None
    with pytest.raises(CanonicalMutationDenied):
        _enforce("evil_memory_capture", {"path": str(page)}, library, tmp_path)
    with pytest.raises(CanonicalMutationDenied):
        _enforce("mcp__evil__memory_capture", {"path": str(page)}, library, tmp_path)
    with pytest.raises(CanonicalMutationDenied):
        _enforce(exact[2], {"file_path": str(page)}, library, tmp_path)


def test_all_path_payload_shapes_and_unknown_file_producers_fail_closed(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    page = library / "owner.md"
    probes = (
        ("edit_file", {"file_path": str(page), "content": "bad"}),
        ("apply_patch", {"patch": f"*** Begin Patch\n*** Update File: {page}\n+bad\n*** End Patch"}),
        ("browser_download", {"destination_root": str(library), "url": "https://example.invalid/x"}),
        ("video_download", {"output_path": str(page)}),
        ("bfl_flux3_get_result", {"save_to": str(page)}),
        ("unknown_file_producer", {"totally_new_destination": str(page)}),
        ("unknown_writer", {"options": {"output": {"path": str(page)}}}),
    )
    for tool, args in probes:
        with pytest.raises(CanonicalMutationDenied):
            _enforce(tool, args, library, tmp_path)


def test_execute_code_dynamic_mutation_capabilities_fail_closed(tmp_path):
    """Terra's novel config-root/string-parts probes and close variants."""
    library = tmp_path / "canonical" / "library"
    library.mkdir(parents=True)
    page = library / "owner.md"
    page.write_text("owner\n")
    root_parts = [repr(part) for part in str(library).split(os.sep) if part]
    probes = (
        # Exact novel class: obtain the canonical root dynamically from config.
        "from hermes_cli.config import load_config\n"
        "root = load_config()['security']['canonical_library_roots'][0]\n"
        "open(root + '/owner.md', 'w').write('bad')",
        # Exact novel class: hide a canonical destination in string parts.
        f"parts = [{', '.join(root_parts)}]\n"
        "target = '/' + '/'.join(parts) + '/owner.md'\n"
        "open(target, 'w').write('bad')",
        f"import builtins as b\nwriter = b.open\nwriter({str(page)!r}, 'w').write('bad')",
        f"target = {str(library)!r} + '/' + 'owner.md'\nopen(target, 'a').write('bad')",
        f"name = 'owner.md'\ntarget = f'{str(library)}/{{name}}'\nopen(target, 'w').write('bad')",
        f"import builtins\ngetattr(builtins, 'open')({str(page)!r}, 'w').write('bad')",
        f"from pathlib import Path as P\nP({str(page)!r}).write_text('bad')",
        f"from os import remove as unlink\nunlink({str(page)!r})",
        f"def helper(destination):\n    open(destination, 'w').write('bad')\nhelper({str(page)!r})",
        f"import hermes_tools as ht\nmutate = ht.write_file\nmutate(path={str(page)!r}, content='bad')",
        f"from hermes_tools import terminal as run\nrun(command='printf bad > {page}')",
        f"eval(\"open({str(page)!r}, 'w').write('bad')\")",
        f"import builtins as bi\nbi.exec(\"open({str(page)!r}, 'w').write('bad')\")",
        f"from builtins import eval as evaluate\nevaluate(\"open({str(page)!r}, 'w').write('bad')\")",
        f"__import__('pathlib').Path({str(page)!r}).write_bytes(b'bad')",
    )
    for code in probes:
        with pytest.raises(CanonicalMutationDenied):
            _enforce("execute_code", {"code": code}, library, tmp_path)


def test_execute_code_preserves_pure_computation_and_read_only_files(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    page = library / "owner.md"
    page.write_text("owner\n")
    allowed = (
        "values = [1, 2, 3]\nresult = sum(value * value for value in values)",
        "import math as m\nresult = m.sqrt(81)",
        f"with open({str(page)!r}, 'r', encoding='utf-8') as fh:\n    result = fh.read().upper()",
        f"from pathlib import Path\nresult = Path({str(page)!r}).read_text(encoding='utf-8')",
    )
    for code in allowed:
        assert _enforce("execute_code", {"code": code}, library, tmp_path) is None
