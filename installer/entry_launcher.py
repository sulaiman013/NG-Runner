"""PyInstaller's entry script for NGRunnerLauncher.exe, the small build the Start Menu item,
the scheduled tasks and the Run key start (ngrunner.launcher.main). It sits outside the
package for the same reason as entry_app.py.
"""
from __future__ import annotations

from ngrunner.launcher import main

raise SystemExit(main())
