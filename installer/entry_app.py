"""PyInstaller's entry script for NGRunner.exe: NG Runner's own command line (ngrunner.__main__).

It lives outside the package on purpose: PyInstaller puts an entry script's folder on the
import path, and the package's folder there would let ngrunner\\secrets.py stand in for the
standard library's secrets module.
"""
from __future__ import annotations

from ngrunner.__main__ import main

raise SystemExit(main())
