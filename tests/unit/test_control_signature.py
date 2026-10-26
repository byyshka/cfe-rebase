# -*- coding: utf-8 -*-
"""control_rebase: an &ИзменениеИКонтроль whose body has nothing to port but whose header does not fit the vendor
method is UNCHANGED with a note and problems - not ready (check_control fails it); the analysis commands refuse an
--out inside a dump.

    python -m unittest tests.unit.test_control_signature
"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from cfe_rebase import control_rebase
from tests.unit.test_registry import run_cli

BOM = b"\xef\xbb\xbf"
HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\r\n<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" '
        'version="2.20">\r\n')
EXT_CFG = HEAD + ('<Configuration uuid="e"><Properties><Name>E</Name><ConfigurationExtensionPurpose>Customization'
                  '</ConfigurationExtensionPurpose></Properties></Configuration></MetaDataObject>')
VENDOR_CFG = HEAD + '<Configuration uuid="c"><Properties><Name>T</Name></Properties></Configuration></MetaDataObject>'
MODULE_XML = HEAD + ('<CommonModule uuid="m"><Properties><Name>М</Name></Properties><ChildObjects></ChildObjects>'
                     '</CommonModule></MetaDataObject>')
MODULE = "CommonModules/М/Ext/Module.bsl"
CORPUS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "corpus", "synthetic")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(BOM + text.encode("utf-8"))


def make_dumps(base, ext_header="Процедура ЕГ_П(А)", vendor_header="Процедура П(А, Б) Экспорт"):
    """(ext root, old, new): one common module М; the vendor body of П is the same in old and new, the extension
    intercepts it by &ИзменениеИКонтроль with the same body and the given header."""
    body = "\r\n\tА = Б;\r\nКонецПроцедуры\r\n"
    ext = os.path.join(base, "ext")
    write(os.path.join(ext, "E", "Configuration.xml"), EXT_CFG)
    write(os.path.join(ext, "E", *MODULE.split("/")), '&ИзменениеИКонтроль("П")\r\n' + ext_header + body)
    dumps = []
    for name in ("old", "new"):
        root = os.path.join(base, name)
        write(os.path.join(root, "Configuration.xml"), VENDOR_CFG)
        write(os.path.join(root, "CommonModules", "М.xml"), MODULE_XML)
        write(os.path.join(root, *MODULE.split("/")), vendor_header + body)
        dumps.append(root)
    return ext, dumps[0], dumps[1]


class SignatureNotReady(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_ctl_sig_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_control(self, ext, old, new):
        out = os.path.join(self.tmp, "out")
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            control_rebase.main(["--ext", ext, "--old", old, "--new", new, "--out", out])
        with open(os.path.join(out, "report.json"), encoding="utf-8") as fh:
            rows = json.load(fh)["methods"]
        with open(os.path.join(out, "report.md"), encoding="utf-8") as fh:
            md = fh.read()
        self.assertEqual(len(rows), 1)
        return rows[0], buf.getvalue(), md

    def test_unchanged_body_with_a_header_that_does_not_fit_new(self):
        row, console, md = self.run_control(*make_dumps(self.tmp))
        self.assertEqual(row["verdict"], "UNCHANGED")  # the verdict name stays: the body needs no port
        self.assertEqual(row["sig_ext_vs_new"], ["параметров 1 ≠ 2"])
        self.assertEqual(row["problems"], ["сигнатура против new: параметров 1 ≠ 2"])
        self.assertIn("не подходит к new", row["note"])
        self.assertFalse(control_rebase.ready(row))
        self.assertNotIn("file", row)  # no candidate: nothing was merged
        self.assertIn("not ready (signature does not fit new): 1", console)
        self.assertIn("метод не готов", md)

    def test_a_fitting_header_is_ready(self):
        row, console, _ = self.run_control(*make_dumps(self.tmp, ext_header="Процедура ЕГ_П(А, Б)"))
        self.assertEqual(row["verdict"], "UNCHANGED")
        self.assertEqual(row["sig_ext_vs_new"], [])
        self.assertNotIn("problems", row)
        self.assertTrue(control_rebase.ready(row))
        self.assertNotIn("not ready", console)


class OutNeverInsideADump(unittest.TestCase):
    """calls, queries, interceptors, bodies and method-diff refuse --out inside any dump they read."""
    EXT = os.path.join(CORPUS, "ext")
    OLD = os.path.join(CORPUS, "old")
    NEW = os.path.join(CORPUS, "new")

    def commands(self):
        dumps = ["--ext-root", self.EXT, "--config-old", self.OLD, "--config-new", self.NEW]
        for name in ("calls", "queries", "interceptors", "bodies"):
            yield name, dumps
        yield "method-diff", ["--ext", self.EXT, "--old", self.OLD, "--new", self.NEW]

    def test_out_inside_each_dump_is_refused(self):
        for name, argv in self.commands():
            for root in (self.EXT, os.path.join(self.EXT, "Синт_Основное"), self.OLD, self.NEW):
                out = os.path.join(root, "_out_guard")
                with self.subTest(name, root=root):
                    try:
                        code, _, stderr = run_cli([name] + argv + ["--out", out])
                        self.assertNotEqual(code, 0)
                        self.assertIn("never writes into dumps", stderr)
                        self.assertFalse(os.path.exists(out))
                    finally:
                        shutil.rmtree(out, True)


if __name__ == "__main__":
    unittest.main()
