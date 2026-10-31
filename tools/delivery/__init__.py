r"""
The delivery into Jim's toolkit: NG Runner's converter and workbook as one script of his,
scripts\ng_convert.py, that his chat runs on a summary instead of typing the job's parts (P2, design
"One toolkit, two users"; Jim's approval of 2026-10-31: NG Runner's tools may go into his toolkit
through his own regression, as reviewed deliveries).

    front.py     the script's own code: its command line, and the gates it runs every time against
                 Jim's own takeoff.py, parse_pulled.py and classify.py
    package.py   makes the one file from NG Runner's modules, unchanged, and offers it only when
                 Jim's regression.py is clean on a staged copy of his toolkit with it added; writes
                 the delivery into .local-fixtures\delivery, never into his toolkit or on Drive

    python -m tools.delivery.package [--toolkit <his toolkit>] [--out <folder>] [--python <tools' Python>]
"""
