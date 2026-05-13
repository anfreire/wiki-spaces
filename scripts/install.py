#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Shim: forwards to wiki_spaces.install:main().

Kept for the documented dev-from-source flow (`./scripts/install.py`).
Once installed via pip/uvx, prefer the `wiki-spaces install` console
script — same code path.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from wiki_spaces.install import main  # noqa: E402

sys.exit(main())
