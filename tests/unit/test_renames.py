# -*- coding: utf-8 -*-
"""A vendor rename keeps the uuid: borrowed, interceptors, plan and lint match the objects by it, not by name.

11.5.17 -> 11.5.27 renamed Документ.СверкаВзаиморасчетов to СверкаВзаиморасчетов2_4 (and two more documents, an enum,
an attribute) under the same uuid: the tools reported them as removed (OBJECT_REMOVED, MISSING_BASE, then NEW_BASE
once the adoption was renamed), and lint skipped BOM / line endings of the renamed modules.
"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from cfe_rebase import borrowed, interceptors, lint, plan

HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" '
        'xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config" version="2.20">\n')
ADOPTED = "<ObjectBelonging>Adopted</ObjectBelonging>"
VENDOR_DOC, VENDOR_ATTR, VENDOR_TS = "4d4f485d-0000-0000-0000-000000000001", "4d4f485d-0000-0000-0000-000000000002", \
    "4d4f485d-0000-0000-0000-000000000003"
OWN_DOC = "a194f8cd-0000-0000-0000-000000000001"


def write(path, text, newline="\r\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\xef\xbb\xbf" + text.replace("\n", newline).encode("utf-8"))


def attribute(name, uuid, extends=None):
    return '<Attribute uuid="%s"><Properties>%s<Name>%s</Name>%s</Properties></Attribute>' % (
        uuid, ADOPTED if extends else "", name,
        "<ExtendedConfigurationObject>%s</ExtendedConfigurationObject>" % extends if extends else "")


def tabular(name, uuid, extends=None):
    return '<TabularSection uuid="%s"><Properties>%s<Name>%s</Name>%s</Properties><ChildObjects/></TabularSection>' % (
        uuid, ADOPTED if extends else "", name,
        "<ExtendedConfigurationObject>%s</ExtendedConfigurationObject>" % extends if extends else "")


def document(name, uuid, children, extends=None):
    return HEAD + '<Document uuid="%s"><Properties>%s<Name>%s</Name>%s</Properties><ChildObjects>%s</ChildObjects>' \
                  '</Document></MetaDataObject>' % (
                      uuid, ADOPTED if extends else "", name,
                      "<ExtendedConfigurationObject>%s</ExtendedConfigurationObject>" % extends if extends else "",
                      "".join(children))


def configuration(name, extension=False):
    return HEAD + '<Configuration uuid="c-%s"><Properties><Name>%s</Name>%s</Properties></Configuration>' \
                  '</MetaDataObject>' % (name, name, "<ConfigurationExtensionPurpose>Customization"
                                                     "</ConfigurationExtensionPurpose>" if extension else "")


VENDOR_MODULE = "Процедура Печать(Параметр) Экспорт\nКонецПроцедуры\n"
EXT_MODULE = '&Вместо("Печать")\nПроцедура Расш_Печать(Параметр)\nКонецПроцедуры\n'


class _Renamed(unittest.TestCase):
    """old: Документ.Сверка with attribute Контрагент and tabular section Товары; new: the same uuids named Сверка2_4,
    Партнер, Позиции. The extension adopts the object, the attribute, the tabular section, and intercepts Печать of
    the manager module."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_renames_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        t = self.tmp
        for root, doc, attr, ts in (("old", "Сверка", "Контрагент", "Товары"), ("new", "Сверка2_4", "Партнер",
                                                                                "Позиции")):
            write(os.path.join(t, root, "Configuration.xml"), configuration("Типовая"))
            write(os.path.join(t, root, "Documents", doc + ".xml"), document(doc, VENDOR_DOC, [
                attribute(attr, VENDOR_ATTR), tabular(ts, VENDOR_TS)]))
            write(os.path.join(t, root, "Documents", doc, "Ext", "ManagerModule.bsl"), VENDOR_MODULE)
        self.ext = os.path.join(t, "ext", "Расш")
        self.write_extension(self.ext, "Сверка")

    def write_extension(self, root, name, newline="\r\n"):
        write(os.path.join(root, "Configuration.xml"), configuration("Расш", True))
        write(os.path.join(root, "Documents", name + ".xml"), document(name, OWN_DOC, [
            attribute("Контрагент", "e-attr", VENDOR_ATTR), tabular("Товары", "e-ts", VENDOR_TS)], VENDOR_DOC))
        write(os.path.join(root, "Documents", name, "Ext", "ManagerModule.bsl"), EXT_MODULE, newline)

    def args(self, ext_root=None):
        return ["--ext-root", ext_root or os.path.join(self.tmp, "ext"), "--config-old", os.path.join(self.tmp, "old"),
                "--config-new", os.path.join(self.tmp, "new"), "--out", os.path.join(self.tmp, "out")]

    def report(self, module, name, ext_root=None):
        with contextlib.redirect_stdout(io.StringIO()):
            module.main(self.args(ext_root))
        with open(os.path.join(self.tmp, "out", name + ".json"), encoding="utf-8") as fh:
            return json.load(fh)


class Borrowed(_Renamed):

    def test_renamed_object_and_children(self):
        data = self.report(borrowed, "borrowed")
        got = {(f["name"], f["check"], f.get("new_name")) for f in data["findings"]}
        self.assertEqual(got, {("Сверка", "OBJECT_RENAMED", "Сверка2_4"), ("Контрагент", "RENAMED", "Партнер"),
                               ("Товары", "RENAMED", "Позиции")})
        detail = next(f["detail"] for f in data["findings"] if f["check"] == "OBJECT_RENAMED")
        self.assertIn("borrow-edit --rename Документ.Сверка=Сверка2_4", detail)
        p = plan.build({}, {}, {}, {}, {}, data)
        self.assertEqual([x["group"] for x in p["items"]], ["MUST_FIX"])

    def test_renamed_for_deletion(self):
        os.rename(os.path.join(self.tmp, "new", "Documents", "Сверка2_4.xml"),
                  os.path.join(self.tmp, "new", "Documents", "УдалитьСверка.xml"))
        data = self.report(borrowed, "borrowed")
        detail = next(f["detail"] for f in data["findings"] if f["check"] == "OBJECT_RENAMED")
        self.assertIn("пометил к удалению", detail)
        self.assertIn("borrow-edit --remove Документ.Сверка", detail)

    def test_renamed_adoption_is_clean(self):
        # the work copy after borrow-edit --rename: files and names are the new ones, the uuids stay
        shutil.rmtree(self.ext)
        self.write_extension(self.ext, "Сверка2_4")
        data = self.report(borrowed, "borrowed")
        self.assertEqual({f["check"] for f in data["findings"]}, {"RENAMED"})  # the children are not renamed yet


class Interceptors(_Renamed):

    def test_target_renamed_instead_of_missing_base(self):
        data = self.report(interceptors, "interceptors")
        row = data["registry"][0]
        self.assertEqual(row["flags"], ["TARGET_RENAMED"])
        self.assertIn("Сверка2_4", row["notes"][0])
        self.assertEqual(row["base_line_new"], 1)
        p = plan.build({}, {}, data, {}, {})
        self.assertEqual([(x["group"], x["method"]) for x in p["items"]], [("MUST_FIX", "Расш_Печать")])

    def test_renamed_adoption_is_not_new_base(self):
        shutil.rmtree(self.ext)
        self.write_extension(self.ext, "Сверка2_4")
        data = self.report(interceptors, "interceptors")
        self.assertEqual(data["registry"][0]["flags"], [])
        self.assertEqual(data["registry"][0]["base_line_old"], 1)


class Lint(_Renamed):

    def test_renamed_files_are_paired_by_uuid(self):
        work = os.path.join(self.tmp, "work", "Расш")
        self.write_extension(work, "Сверка2_4", newline="\n")
        issues, _ = lint.lint(os.path.join(self.tmp, "work"), os.path.join(self.tmp, "ext"))
        got = {(i["check"], i["file"]) for i in issues}
        self.assertEqual(got, {("OBJECT_RENAMED", "Documents/Сверка2_4"),
                               ("EOL_LF", "Documents/Сверка2_4/Ext/ManagerModule.bsl")})
        renamed = next(i for i in issues if i["check"] == "OBJECT_RENAMED")
        self.assertEqual(renamed["severity"], "info")
        self.assertIn("Documents/Сверка → Documents/Сверка2_4", renamed["detail"])


if __name__ == "__main__":
    unittest.main()
