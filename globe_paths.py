"""Where the globe keeps its writable files.

From source: the project folder. Built with PyInstaller: the folder of
NWP_Globe.exe (not the read-only bundle inside it), so ``data``,
``cache``, ``.env`` and ``docs`` sit next to the exe.
"""

import os
import sys

FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = (os.path.dirname(os.path.abspath(sys.executable)) if FROZEN
           else os.path.dirname(os.path.abspath(__file__)))
