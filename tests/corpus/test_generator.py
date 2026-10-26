# -*- coding: utf-8 -*-
"""The generator tests/corpus/gen reproduces the committed synthetic corpus byte for byte.

The committed corpus is the source of truth. A case is changed in the generator first, its output is reviewed and
then copied over (tests/corpus/synthetic/README.md, "Как перегенерировать"); this test fails as soon as the generator
and the corpus drift apart. The comparison is done here, not by the generator's own --check code.

    python -m unittest discover -s tests -t .
"""
import contextlib
import io
import os
import shutil
import tempfile
import unittest

from .gen import build_corpus

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, "synthetic")
NOT_GENERATED = {"expected", "README.md"}


def files_under(root):
    """{relative '/'-path: bytes} of every file under root, except the top-level hand-written expected/ and README."""
    res = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        if rel_dir == ".":
            dirnames[:] = [d for d in dirnames if d not in NOT_GENERATED]
            filenames = [f for f in filenames if f not in NOT_GENERATED]
        for name in filenames:
            with open(os.path.join(dirpath, name), "rb") as fh:
                res[name if rel_dir == "." else rel_dir + "/" + name] = fh.read()
    return res


class GeneratorReproducesCorpus(unittest.TestCase):

    def test_generated_files_equal_the_committed_corpus(self):
        tmp = tempfile.mkdtemp(prefix="cfe_corpus_gen_test_")
        self.addCleanup(shutil.rmtree, tmp, True)
        root = os.path.join(tmp, "corpus")
        with contextlib.redirect_stdout(io.StringIO()):
            build_corpus.build(root, os.path.join(tmp, "rebase"))
        want, got = files_under(CORPUS), files_under(root)
        self.assertTrue(want, "no committed corpus files under %s" % CORPUS)
        self.assertEqual(sorted(want), sorted(got), "file lists differ")
        differ = [rel for rel in sorted(want) if want[rel] != got[rel]]
        self.assertEqual([], differ, "files whose bytes differ (details: build_corpus.py --check)")


if __name__ == "__main__":
    unittest.main()
