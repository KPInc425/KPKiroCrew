"""Entry point for the Windows helper.

Run with ``python server.py`` (the directory name ``windows-helper`` contains a
hyphen, so ``python -m windows-helper`` is not a valid module invocation). This
module exists so the package can also be launched as ``python -m windows_helper``
if the directory is renamed to a valid module name.
"""

from __future__ import annotations

from server import main

if __name__ == "__main__":
    main()
