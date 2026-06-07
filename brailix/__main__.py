"""``python -m brailix`` — run the command-line interface.

The implementation lives in :mod:`brailix.cli` so it can also be wired as
the ``brailix`` console script (see ``pyproject.toml`` ``[project.scripts]``);
this module is just the ``python -m`` shim.
"""

from __future__ import annotations

from brailix.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
