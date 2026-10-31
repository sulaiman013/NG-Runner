r"""
The IC Conversion: the JSON in Jim's current shape, and the workbook that renders it (P2's "shared
converter and workbook", design section 7; docs/p2/build-spec.md).

Why it is a tool and not part of the app: the workbook is an .xlsx written with openpyxl, which NG
Runner's own build doesn't carry and mustn't (pyproject.toml; tools/tools-venv.txt). So nothing here
runs in NG Runner's process. It runs where openpyxl is: inside the delivered scripts\ng_convert.py, in
Jim's chat and, from P3, from the adopted copy of his toolkit through toolkit.run_tool in the tools'
Python (tools/delivery packages it). Kept out of the ngrunner package so the PyInstaller build, which
takes every ngrunner module, never meets openpyxl.

    conversion.py   NG Runner's converter on a summary's bytes -> the v2 IC Conversion JSON (a dict)
    sheets.py       the JSON -> the workbook's cells, his four sheets, and its formulas evaluated in
                    Python: no LibreOffice, as Jim's PC has none
    xlsx.py         saving it (openpyxl, imported only there) and reading an .xlsx back with the
                    standard library, so a saved file is checked cell by cell in Python
    score.py        dev only: the workbook scored against the chat's real ones, by named cells

Only conversion.py imports NG Runner (and so pypdf). sheets.py and xlsx.py import the standard library
alone, so the checks run in NG Runner's venv, in the tools' Python and in Jim's container alike.
"""
