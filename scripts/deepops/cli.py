"""The one way a deepops script reads its command line.

    parser = cli.parser(__doc__)
    parser.add_argument("--check", action="store_true", help="...")
    args = parser.parse_args(argv)

The script's docstring is its --help, printed as written (its usage lines and
examples), and an option the script does not know is an error (exit 2) instead of
being ignored (`upstream.py --help` once ran the whole upstream check, and a
mistyped `--chek` runs a script's default action, which may rewrite files).
Standard library only.
"""

import argparse


def parser(doc):
    """An ArgumentParser whose help is DOC verbatim."""
    return argparse.ArgumentParser(description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
