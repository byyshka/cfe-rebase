# -*- coding: utf-8 -*-
"""borrow-edit: remove an adoption or rename it to the vendor's name in a work copy, byte for byte.

In a real run (11.5.17 -> 11.5.27) this was done by hand: six adoptions of objects the vendor deleted (files, the
ChildObjects line of Configuration.xml, the entries of ConfigDumpInfo.xml with their nested blocks) and three documents
whose files kept the old name.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from cfe_rebase import borrow_edit
from cfe_rebase import mcp_server as srv

HAVE_GIT = shutil.which("git") is not None
BOM = b"\xef\xbb\xbf"
HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" '
        'xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:xr="http://v8.1c.ru/8.3/xcf/readable" '
        'xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config" version="2.20">\n')
ADOPTED = "<ObjectBelonging>Adopted</ObjectBelonging>"

CONFIGURATION = HEAD + """\t<Configuration uuid="c-ext">
\t\t<Properties>
\t\t\t<Name>Расш</Name>
\t\t\t<ConfigurationExtensionPurpose>Customization</ConfigurationExtensionPurpose>
\t\t</Properties>
\t\t<ChildObjects>
\t\t\t<CommonModule>Старый</CommonModule>
\t\t\t<CommonModule>ОбщегоНазначения</CommonModule>
\t\t\t<Document>Сверка</Document>
\t\t\t<Document>Заказ</Document>
\t\t\t<Enum>Состояния</Enum>
\t\t</ChildObjects>
\t</Configuration>
</MetaDataObject>
"""
DUMP_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<ConfigDumpInfo xmlns="http://v8.1c.ru/8.3/xcf/dumpinfo" format="Hierarchical" version="2.20">
\t<ConfigVersions>
\t\t<Metadata name="CommonModule.ОбщегоНазначения" id="m-1" configVersion="a"/>
\t\t<Metadata name="CommonModule.Старый" id="m-2" configVersion="b"/>
\t\t<Metadata name="CommonModule.Старый.Module" id="m-2.0" configVersion="c"/>
\t\t<Metadata name="CommonModule.СтарыйКлиент" id="m-3" configVersion="d"/>
\t\t<Metadata name="Document.Заказ" id="d-2" configVersion="e">
\t\t\t<Metadata name="Document.Заказ.Attribute.Сверка" id="d-2a"/>
\t\t</Metadata>
\t\t<Metadata name="Document.Сверка" id="d-1" configVersion="f"/>
\t\t<Metadata name="Document.Сверка.ManagerModule" id="d-1.2" configVersion="g"/>
\t\t<Metadata name="Enum.Состояния" id="e-1" configVersion="h">
\t\t\t<Metadata name="Enum.Состояния.EnumValue.Новый" id="e-1a"/>
\t\t\t<Metadata name="Enum.Состояния.EnumValue.Закрыт" id="e-1b"/>
\t\t</Metadata>
\t</ConfigVersions>
</ConfigDumpInfo>
"""


def obj(tag, name, uuid, extends, children=""):
    return HEAD + ('\t<%s uuid="%s">\n\t\t<InternalInfo>\n\t\t\t<xr:GeneratedType name="%sRef.%s" category="Ref"/>\n'
                   '\t\t</InternalInfo>\n\t\t<Properties>\n\t\t\t%s\n\t\t\t<Name>%s</Name>\n'
                   '\t\t\t<ExtendedConfigurationObject>%s</ExtendedConfigurationObject>\n\t\t</Properties>\n'
                   '\t\t<ChildObjects>%s</ChildObjects>\n\t</%s>\n</MetaDataObject>\n') % (
        tag, uuid, tag, name, ADOPTED, name, extends, children, tag)


def attribute(name, typ):
    return ('\n\t\t\t<Attribute uuid="a-%s">\n\t\t\t\t<Properties>\n\t\t\t\t\t<Name>%s</Name>\n\t\t\t\t\t<Type>\n'
            '\t\t\t\t\t\t<v8:Type>%s</v8:Type>\n\t\t\t\t\t</Type>\n\t\t\t\t</Properties>\n\t\t\t</Attribute>\n\t\t' % (
                name, name, typ))


def write(path, text, newline="\r\n", bom=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write((BOM if bom else b"") + text.replace("\n", newline).encode("utf-8"))


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


def git(cwd, *args):
    return subprocess.run(["git", "-c", "core.quotepath=off"] + list(args), cwd=cwd, capture_output=True, check=True)


class _Work(unittest.TestCase):
    """A work copy under git: common module Старый (adopted, its module calls nothing), document Сверка (the vendor
    renamed it Сверка2_4), document Заказ with an attribute of type DocumentRef.Сверка, enum Состояния (referenced only
    in comments), common module ОбщегоНазначения whose module calls Старый in code."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_borrow_edit_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = os.path.join(self.tmp, "work")
        e = os.path.join(self.work, "Расш")
        self.ext = e
        write(os.path.join(e, "Configuration.xml"), CONFIGURATION)
        write(os.path.join(e, "ConfigDumpInfo.xml"), DUMP_INFO, bom=False)
        write(os.path.join(e, "CommonModules", "Старый.xml"), obj("CommonModule", "Старый", "o-1", "v-1"))
        write(os.path.join(e, "CommonModules", "Старый", "Ext", "Module.bsl"),
              "Процедура П() Экспорт\nКонецПроцедуры\n")
        write(os.path.join(e, "CommonModules", "ОбщегоНазначения.xml"),
              obj("CommonModule", "ОбщегоНазначения", "o-2", "v-2"))
        write(os.path.join(e, "CommonModules", "ОбщегоНазначения", "Ext", "Module.bsl"),
              "Процедура Расш_Ф()\n\t// Состояния: Перечисления.Состояния.Новый\n\tСтарый.П();\n"
              "\tТекст = \"ВЫБРАТЬ\n\t|\tДокумент.Сверка.Ссылка\";\nКонецПроцедуры\n")
        write(os.path.join(e, "Documents", "Сверка.xml"), obj("Document", "Сверка", "o-3", "v-3"))
        write(os.path.join(e, "Documents", "Сверка", "Ext", "ManagerModule.bsl"),
              "Процедура Печать()\nКонецПроцедуры\n", newline="\n")
        write(os.path.join(e, "Documents", "Заказ.xml"),
              obj("Document", "Заказ", "o-4", "v-4", attribute("Сверка", "cfg:DocumentRef.Сверка")))
        write(os.path.join(e, "Enums", "Состояния.xml"), obj("Enum", "Состояния", "o-5", "v-5"))
        if HAVE_GIT:
            git(self.work, "init", "-q")
            git(self.work, "config", "core.autocrlf", "false")
            git(self.work, "add", "-A")
            git(self.work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "before")

    def path(self, *parts):
        return os.path.join(self.ext, *parts)

    def cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            code = borrow_edit.main(["--work", self.work] + list(args))
        return code, buf.getvalue()


@unittest.skipUnless(HAVE_GIT, "git is not on PATH")
class Remove(_Work):

    def test_remove_keeps_every_other_byte(self):
        cfg, info = read(self.path("Configuration.xml")), read(self.path("ConfigDumpInfo.xml"))
        code, out = self.cli("--remove", "Перечисление.Состояния")
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(self.path("Enums", "Состояния.xml")))
        self.assertEqual(read(self.path("Configuration.xml")),
                         cfg.replace("\t\t\t<Enum>Состояния</Enum>\r\n".encode("utf-8"), b""))
        block = ('\t\t<Metadata name="Enum.Состояния" id="e-1" configVersion="h">\r\n'
                 '\t\t\t<Metadata name="Enum.Состояния.EnumValue.Новый" id="e-1a"/>\r\n'
                 '\t\t\t<Metadata name="Enum.Состояния.EnumValue.Закрыт" id="e-1b"/>\r\n'
                 '\t\t</Metadata>\r\n').encode("utf-8")
        self.assertIn(block, info)
        self.assertEqual(read(self.path("ConfigDumpInfo.xml")), info.replace(block, b""))

    def test_remove_with_its_folder_and_module_entries(self):
        # ОбщегоНазначения calls Старый: removed together, the reference does not count
        code, out = self.cli("--remove", "CommonModule.Старый", "CommonModules/ОбщегоНазначения")
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(self.path("CommonModules", "Старый")))
        self.assertFalse(os.path.exists(self.path("CommonModules", "ОбщегоНазначения")))
        info = read(self.path("ConfigDumpInfo.xml")).decode("utf-8")
        self.assertNotIn('"CommonModule.Старый"', info)
        self.assertNotIn('"CommonModule.Старый.Module"', info)
        self.assertIn('"CommonModule.СтарыйКлиент"', info)  # another object whose name starts the same
        status = git(self.work, "status", "--porcelain").stdout.decode("utf-8")
        self.assertEqual(len(status.splitlines()), 6)  # 4 files deleted, Configuration.xml, ConfigDumpInfo.xml

    def test_refused_while_code_references_it(self):
        before = git(self.work, "status", "--porcelain").stdout
        code, out = self.cli("--remove", "ОбщийМодуль.Старый")
        self.assertEqual(code, 1)
        self.assertIn("ОТКАЗ", out)
        self.assertIn("CommonModules/ОбщегоНазначения/Ext/Module.bsl:3", out)
        self.assertTrue(os.path.exists(self.path("CommonModules", "Старый.xml")))
        self.assertEqual(git(self.work, "status", "--porcelain").stdout, before)

    def test_type_reference_in_xml_refuses(self):
        res = borrow_edit.run(self.work, remove=["Документ.Сверка"], dry_run=True)
        self.assertEqual({(r["file"], r["kind"]) for r in res["refs"]},
                         {("Documents/Заказ.xml", "xml"), ("CommonModules/ОбщегоНазначения/Ext/Module.bsl", "bsl")})
        self.assertTrue(res["refused"])

    def test_own_object_is_refused(self):
        text = read(self.path("Enums", "Состояния.xml")).replace(ADOPTED.encode("utf-8"), b"")
        with open(self.path("Enums", "Состояния.xml"), "wb") as fh:
            fh.write(text)
        res = borrow_edit.run(self.work, remove=["Enum.Состояния"])
        self.assertIn("собственный", res["refused"][0])
        self.assertFalse(res["applied"])

    def test_untracked_file_is_refused(self):
        write(self.path("Enums", "Состояния", "Ext", "Help.xml"), "<Help/>")
        res = borrow_edit.run(self.work, remove=["Перечисление.Состояния"])
        self.assertIn("git не отслеживает", res["refused"][0])
        self.assertTrue(os.path.exists(self.path("Enums", "Состояния.xml")))

    def test_dry_run_writes_nothing(self):
        res = borrow_edit.run(self.work, remove=["Перечисление.Состояния"], dry_run=True)
        self.assertEqual(res["deleted"], ["Enums/Состояния.xml"])
        self.assertEqual(res["changed"], ["ConfigDumpInfo.xml", "Configuration.xml"])
        self.assertFalse(res["applied"])
        self.assertEqual(git(self.work, "status", "--porcelain").stdout, b"")

    def test_bad_input(self):
        for kw in ({"remove": ["Справочник"]}, {"remove": ["Неведомое.Х"]}, {}, {"rename": ["Документ.Сверка"]}):
            with self.assertRaises(borrow_edit.EditError):
                borrow_edit.run(self.work, **kw)


@unittest.skipUnless(HAVE_GIT, "git is not on PATH")
class Rename(_Work):

    def test_rename_moves_files_and_rewrites_xml(self):
        module = read(self.path("Documents", "Сверка", "Ext", "ManagerModule.bsl"))
        code, out = self.cli("--rename", "Документ.Сверка=Сверка2_4")
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(self.path("Documents", "Сверка")))
        self.assertEqual(read(self.path("Documents", "Сверка2_4", "Ext", "ManagerModule.bsl")), module)  # LF kept
        own = read(self.path("Documents", "Сверка2_4.xml")).decode("utf-8")
        self.assertIn("<Name>Сверка2_4</Name>", own)
        self.assertIn('name="DocumentRef.Сверка2_4"', own)
        self.assertIn("<Document>Сверка2_4</Document>", read(self.path("Configuration.xml")).decode("utf-8"))
        info = read(self.path("ConfigDumpInfo.xml")).decode("utf-8")
        self.assertIn('"Document.Сверка2_4.ManagerModule"', info)
        self.assertIn('"Document.Заказ.Attribute.Сверка"', info)  # an attribute named so is not the document
        order = read(self.path("Documents", "Заказ.xml")).decode("utf-8")
        self.assertIn("cfg:DocumentRef.Сверка2_4", order)
        self.assertIn("<Name>Сверка</Name>", order)
        # Russian references are listed, not rewritten
        self.assertIn("ссылка (rename Документ.Сверка) CommonModules/ОбщегоНазначения/Ext/Module.bsl:5", out)
        self.assertIn("Документ.Сверка.Ссылка", read(self.path("CommonModules", "ОбщегоНазначения", "Ext",
                                                                "Module.bsl")).decode("utf-8"))
        git(self.work, "add", "-A")
        renamed = git(self.work, "diff", "--cached", "-M", "--name-status").stdout.decode("utf-8")
        self.assertIn("R100", renamed)  # the module moved unchanged

    def test_name_from_the_vendor_by_uuid(self):
        new = os.path.join(self.tmp, "new")
        write(os.path.join(new, "Documents", "Сверка2_4.xml"), obj("Document", "Сверка2_4", "v-3", "-"))
        res = borrow_edit.run(self.work, rename=["Документ.Сверка"], config_new=new, dry_run=True)
        self.assertEqual(res["operations"][0]["new_name"], "Сверка2_4")
        self.assertIn(["Documents/Сверка.xml", "Documents/Сверка2_4.xml"], res["moved"])

    def test_name_taken_is_refused(self):
        res = borrow_edit.run(self.work, rename=["Документ.Сверка=Заказ"])
        self.assertIn("уже есть", res["refused"][0])
        self.assertTrue(os.path.exists(self.path("Documents", "Сверка.xml")))


@unittest.skipUnless(HAVE_GIT, "git is not on PATH")
class Mcp(_Work):

    def test_dry_run_by_default_then_apply(self):
        res = srv.apply_borrow_edit(self.work, remove=["Перечисление.Состояния"])
        self.assertEqual(res["status"], "ok", res.get("error"))
        self.assertEqual((res["summary"]["dry_run"], res["summary"]["applied"]), (True, False))
        self.assertTrue(os.path.exists(self.path("Enums", "Состояния.xml")))
        out = os.path.join(self.tmp, "out")
        res = srv.apply_borrow_edit(self.work, remove=["Перечисление.Состояния"], dry_run=False, out=out)
        self.assertEqual(res["status"], "ok", res.get("error"))
        self.assertTrue(res["summary"]["applied"])
        self.assertFalse(os.path.exists(self.path("Enums", "Состояния.xml")))
        with open(res["report"]["json"], encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["deleted"], ["Enums/Состояния.xml"])

    def test_refusal_names_the_references(self):
        res = srv.apply_borrow_edit(self.work, remove=["ОбщийМодуль.Старый"], dry_run=False)
        self.assertEqual(res["error"]["code"], "bad_input")
        self.assertIn("CommonModules/ОбщегоНазначения/Ext/Module.bsl:3", res["error"]["message"])
        dry = srv.apply_borrow_edit(self.work, remove=["ОбщийМодуль.Старый"])
        self.assertEqual(dry["items"][0]["action"], "ref")
        self.assertTrue(dry["summary"]["refused"])


if __name__ == "__main__":
    unittest.main()
