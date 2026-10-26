# -*- coding: utf-8 -*-
"""interceptors: a Form.xml that exists but does not parse is reported, not dropped as if the form were absent."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from cfe_rebase import interceptors

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(os.path.dirname(HERE), "corpus", "synthetic")
FORM = "Documents/ЗаказПокупателя/Forms/ФормаДокумента/Ext/Form.xml"


class UnreadableForm(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_unreadable_form_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_tool(self, ext_root):
        out = os.path.join(self.tmp, "out-" + os.path.basename(ext_root))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            interceptors.main(["--ext-root", ext_root, "--config-old", os.path.join(CORPUS, "old"),
                               "--config-new", os.path.join(CORPUS, "new"), "--out", out])
        with open(os.path.join(out, "interceptors.json"), encoding="utf-8") as fh:
            return json.load(fh), buf.getvalue()

    def test_clean_corpus_has_no_unreadable_forms(self):
        data, console = self.run_tool(os.path.join(CORPUS, "ext"))
        self.assertEqual(data["unreadable_forms"], [])
        self.assertNotIn("UNREADABLE", console)

    def test_merge_markers_in_form_xml(self):
        ext = os.path.join(self.tmp, "ext")
        shutil.copytree(os.path.join(CORPUS, "ext"), ext)
        path = os.path.join(ext, "Синт_Основное", *FORM.split("/"))
        with open(path, "rb") as fh:
            data = fh.read()
        head, sep, tail = data.partition(b"\n")
        with open(path, "wb") as fh:
            fh.write(head + sep + b"<<<<<<< new\r\n" + tail)
        clean, _ = self.run_tool(os.path.join(CORPUS, "ext"))
        broken, console = self.run_tool(ext)
        self.assertEqual([(u["dump"], u["form"]) for u in broken["unreadable_forms"]], [("Синт_Основное", FORM)])
        self.assertIn("ExpatError", broken["unreadable_forms"][0]["error"])
        self.assertIn("UNREADABLE Form.xml: 1", console)
        # the bindings of that form are what is missing from the registry
        lost = [r for r in clean["registry"] if r.get("form") == FORM]
        self.assertTrue(lost)
        self.assertEqual(len(broken["registry"]), len(clean["registry"]) - len(lost))


if __name__ == "__main__":
    unittest.main()
