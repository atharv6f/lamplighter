`python3 report.py settings.txt` should print the settings as a table, as
the docstrings of `parser.py` and `formatter.py` say. It fails on the
`callback_url` line, and long keys come out cut. Fix it, so that the
table holds every setting with its whole key and its whole value.
