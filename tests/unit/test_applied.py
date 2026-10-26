# -*- coding: utf-8 -*-
"""applied: #Удаление blocks and marker lines become empty lines; line count, BOM and line endings kept byte for byte."""
import contextlib
import io
import os
import shutil
import tempfile
import unittest

from cfe_rebase import applied

BOM = b"\xef\xbb\xbf"

MODULE = [
    '&ИзменениеИКонтроль("Ф")',
    "Процедура ЕГ_Ф()",
    "\tА = 1;",
    "#Удаление",
    "\tБ = 2;",
    "#КонецУдаления",
    "#Вставка",
    "\tВ = 3;",
    "#КонецВставки",
    "КонецПроцедуры",
]
APPLIED = ['&ИзменениеИКонтроль("Ф")', "Процедура ЕГ_Ф()", "\tА = 1;", "", "", "", "", "\tВ = 3;", "", "КонецПроцедуры"]


def encode(lines, eol, bom=False, last_eol=True):
    data = (eol.join(lines) + (eol if last_eol else "")).encode("utf-8")
    return BOM + data if bom else data


class Applied(unittest.TestCase):

    def check(self, data, expected, blanked):
        out, n = applied.applied(data)
        self.assertEqual(out, expected)
        self.assertEqual(n, blanked)
        self.assertEqual(applied.applied_bytes(data), expected)
        self.assertEqual(out.count(b"\n"), data.count(b"\n"))

    def test_bom_crlf(self):
        self.check(encode(MODULE, "\r\n", bom=True), encode(APPLIED, "\r\n", bom=True), 5)

    def test_lf_without_bom(self):
        self.check(encode(MODULE, "\n"), encode(APPLIED, "\n"), 5)

    def test_last_line_without_newline(self):
        self.check(encode(MODULE[:-1], "\r\n", bom=True, last_eol=False),
                   encode(APPLIED[:-1], "\r\n", bom=True, last_eol=False), 5)

    def test_mixed_line_endings_kept_per_line(self):
        data = BOM + "А = 1;\r\n#Удаление\n\tБ = 2;\r\n#КонецУдаления\r\nВ = 3;\n".encode("utf-8")
        self.check(data, BOM + "А = 1;\r\n\n\r\n\r\nВ = 3;\n".encode("utf-8"), 3)

    def test_marker_with_trailing_comment(self):
        src = ["Процедура Ф()", "\t#Вставка // ЕГ 13.04.2026 - причина", "\tА = 1;", "\t#КонецВставки // ЕГ",
               "  #Удаление //", "\tБ = 2;", "#КонецУдаления// конец", "#Область Ф", "КонецПроцедуры"]
        dst = ["Процедура Ф()", "", "\tА = 1;", "", "", "", "", "#Область Ф", "КонецПроцедуры"]
        self.check(encode(src, "\r\n", bom=True), encode(dst, "\r\n", bom=True), 5)

    def test_marker_inside_query_literal(self):
        # The platform writes a marker inside a string literal as its own line between «|» continuation lines.
        src = ["\tЗапрос.Текст =", '\t"ВЫБРАТЬ', "\t|\tТ.Ссылка КАК Ссылка,", "\t#Вставка", "\t|\tТ.Код КАК Код,",
               "\t#КонецВставки", "\t#Удаление", "\t|\tТ.Наименование КАК Наименование,", "\t#КонецУдаления",
               "\t|\tТ.Дата КАК Дата", "\t|ИЗ", '\t|\tСправочник.Т КАК Т";']
        dst = ["\tЗапрос.Текст =", '\t"ВЫБРАТЬ', "\t|\tТ.Ссылка КАК Ссылка,", "", "\t|\tТ.Код КАК Код,", "", "", "",
               "", "\t|\tТ.Дата КАК Дата", "\t|ИЗ", '\t|\tСправочник.Т КАК Т";']
        self.check(encode(src, "\r\n", bom=True), encode(dst, "\r\n", bom=True), 5)


class Cli(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = os.path.join(self.tmp, "src")
        self.files = {
            os.path.join("Ext", "ObjectModule.bsl"): encode(MODULE, "\r\n", bom=True),
            os.path.join("Forms", "Ф", "Ext", "Form", "Module.bsl"): encode(MODULE, "\n"),
            "Configuration.xml": b"<MetaDataObject/>",
        }
        for rel, data in self.files.items():
            path = os.path.join(self.src, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(data)

    def read(self, root, rel):
        with open(os.path.join(root, rel), "rb") as fh:
            return fh.read()

    def test_folder_keeps_relative_paths_and_sources(self):
        dst = os.path.join(self.tmp, "dst")
        with contextlib.redirect_stdout(io.StringIO()):
            applied.main(["--src", self.src, "--dst", dst])
        for rel, data in self.files.items():
            self.assertEqual(self.read(self.src, rel), data)
            if rel.endswith(".bsl"):
                self.assertEqual(self.read(dst, rel), applied.applied_bytes(data))
        self.assertFalse(os.path.exists(os.path.join(dst, "Configuration.xml")))

    def test_dst_inside_src_is_refused(self):
        with self.assertRaises(SystemExit):
            applied.main(["--src", self.src, "--dst", os.path.join(self.src, "applied")])
        self.assertFalse(os.path.exists(os.path.join(self.src, "applied")))

    def test_dst_inside_a_dump_is_refused(self):
        dump = os.path.join(self.tmp, "другая выгрузка")
        os.makedirs(dump)
        with open(os.path.join(dump, "Configuration.xml"), "wb") as fh:
            fh.write(b"<MetaDataObject/>")
        for dst in (dump, os.path.join(dump, "applied", "copy")):
            with self.subTest(dst=dst):
                with self.assertRaises(SystemExit) as cm:
                    applied.main(["--src", self.src, "--dst", dst])
                self.assertIn("Configuration.xml", str(cm.exception.code))
        self.assertEqual(os.listdir(dump), ["Configuration.xml"])

    def test_dst_holding_a_dump_laid_out_like_src_is_refused(self):
        # --src a folder of extensions, --dst the work copy (a folder of extensions too): each module lands in a dump
        exts, work = os.path.join(self.tmp, "exts"), os.path.join(self.tmp, "work")
        shutil.copytree(self.src, os.path.join(exts, "Расш"))
        os.makedirs(os.path.join(work, "Расш"))
        shutil.copyfile(os.path.join(self.src, "Configuration.xml"), os.path.join(work, "Расш", "Configuration.xml"))
        with self.assertRaises(SystemExit):
            applied.main(["--src", exts, "--dst", work])
        self.assertEqual(os.listdir(os.path.join(work, "Расш")), ["Configuration.xml"])
        with contextlib.redirect_stdout(io.StringIO()):  # outside every dump it works
            applied.main(["--src", exts, "--dst", os.path.join(self.tmp, "applied")])
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "applied", "Расш", "Ext", "ObjectModule.bsl")))


if __name__ == "__main__":
    unittest.main()
