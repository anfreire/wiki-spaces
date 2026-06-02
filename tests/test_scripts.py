"""Guards for the dev-from-source shims under `scripts/`."""

from __future__ import annotations

import re
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def test_script_shims_declare_pyyaml_dependency():
    """C6: the shims run via `uv run --script`, which builds an ISOLATED env
    from the PEP-723 `dependencies` list — the project venv is NOT on the path.
    Each shim imports the `wiki_spaces` package, which imports `pyyaml`
    (`_md.py`), so every shim must declare it; otherwise `uv run scripts/<x>.py`
    dies with `ModuleNotFoundError: No module named 'yaml'` on any real code
    path. A bare `dependencies = []` is the bug this guards against."""
    shims = sorted(SCRIPTS_DIR.glob("*.py"))
    assert shims, "expected dev shims under scripts/"
    for shim in shims:
        text = shim.read_text(encoding="utf-8")
        m = re.search(r"^# dependencies = (\[.*\])\s*$", text, re.MULTILINE)
        assert m, f"{shim.name}: missing PEP-723 `# dependencies = [...]` line"
        assert "pyyaml" in m.group(1), (
            f"{shim.name}: PEP-723 dependencies must include pyyaml "
            f"(the package imports it); got {m.group(1)}"
        )
