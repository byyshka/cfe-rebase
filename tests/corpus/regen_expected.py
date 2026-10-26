# -*- coding: utf-8 -*-
"""Regenerate tests/corpus/synthetic/expected/<run>.json from the current tools.

An expected file is the reviewed truth, not a snapshot of whatever the code does today. Overwrite it only after
reading the report.md of that run and agreeing with every verdict and flag in it: a regenerated file without that
review turns a regression into the new norm. Hence two steps:

    python -m tests.corpus.regen_expected            # run every tool, keep the outputs, show what would change
    python -m tests.corpus.regen_expected --write    # the same, then overwrite the expected files

Outputs stay in --out (default: a new temporary folder, printed) so that each report.md can be read. Runs and their
arguments are those of test_synthetic.RUNS; CFE_REBASE_LEGACY_TOOLS works here as in the tests.
"""
import argparse
import difflib
import json
import os
import sys
import tempfile

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from tests.corpus import test_synthetic as ts  # noqa: E402
    from tests.corpus.normalize import dumps  # noqa: E402
else:
    from . import test_synthetic as ts
    from .normalize import dumps


def main(argv=None):
    ap = argparse.ArgumentParser(description="Regenerate the expected JSON of the synthetic corpus")
    ap.add_argument("--write", action="store_true", help="overwrite expected/<run>.json (after reading report.md)")
    ap.add_argument("--out", help="folder for the tool outputs (default: a new temporary folder)")
    args = ap.parse_args(argv)
    base = args.out or tempfile.mkdtemp(prefix="cfe_corpus_regen_")
    os.makedirs(ts.EXPECTED, exist_ok=True)
    changed = 0
    for name, (out, data) in ts.run_all(base).items():
        path = os.path.join(ts.EXPECTED, name + ".json")
        old = None
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                old = json.load(fh)
        md = sorted(f for f in os.listdir(out) if f.endswith(".md"))
        state = "same" if old == data else ("new" if old is None else "CHANGED")
        print("%-20s %-8s review: %s" % (name, state, ", ".join(os.path.join(out, f) for f in md)))
        if state == "CHANGED":
            diff = difflib.unified_diff(dumps(old).splitlines(), dumps(data).splitlines(), "expected", "actual",
                                        lineterm="", n=1)
            print("\n".join("    " + s for s in list(diff)[:60]))
        if state != "same":
            changed += 1
            if args.write:
                with open(path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(dumps(data))
    print("%d run(s) differ from expected; %s" % (
        changed, "expected files written" if args.write else "nothing written (pass --write after the review)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
