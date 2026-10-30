# -*- coding: utf-8 -*-
"""ROLE_NO_RIGHTS (lint / check_work) and OBJECT_NAME_CLASH (borrowed / check_borrowed): two findings of stage 5 of
a real migration (УТ 11.5.17.172 -> 11.5.27.79). XML fragments follow the real extensions: МоёРасширение v43
(Расш1_ОсновнаяРоль - 130 rights in the source base, 0 after the load from XML into the new base) and ЗаменаСтавок_Расш («Уже
существует объект с именем Обработка.ЗаменаСтавокНДС»)."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from cfe_rebase import borrowed, lint, mcp_server, plan, selection

HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" '
        'xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:xr="http://v8.1c.ru/8.3/xcf/readable" version="2.20">\n')
ADOPTED = "\t\t\t<ObjectBelonging>Adopted</ObjectBelonging>\n"
EXT = "МоёРасширение_v_43"
ROLE = "Расш1_ОсновнаяРоль"
# Rights.xml of the hand fix that gave the role its 130 rights back (proven by a load)
FIXED_RIGHTS = ('<?xml version="1.0" encoding="UTF-8"?>\n<Rights xmlns="http://v8.1c.ru/8.2/roles" '
                'xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                'xsi:type="Rights" version="2.20">\n\t<setForNewObjects>true</setForNewObjects>\n'
                '\t<setForAttributesByDefault>true</setForAttributesByDefault>\n'
                '\t<independentRightsOfChildObjects>false</independentRightsOfChildObjects>\n</Rights>')


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))


def ext_config(name):
    return (HEAD + '\t<Configuration uuid="27facfad-ffe4-4a56-8468-9ebf05d8c14b">\n\t\t<Properties>\n' + ADOPTED +
            "\t\t\t<Name>%s</Name>\n\t\t\t<ConfigurationExtensionPurpose>Customization"
            "</ConfigurationExtensionPurpose>\n\t\t</Properties>\n\t</Configuration>\n</MetaDataObject>" % name)


def vendor_config():
    return (HEAD + '\t<Configuration uuid="c-vendor">\n\t\t<Properties>\n\t\t\t<Name>УправлениеТорговлей</Name>\n'
            "\t\t</Properties>\n\t</Configuration>\n</MetaDataObject>")


def role(name, adopted=False):
    """Roles/Расш1_ОсновнаяРоль.xml of МоёРасширение v43 as it is."""
    return (HEAD + '\t<Role uuid="57384a97-9e59-485e-85f1-b29e1f938935">\n\t\t<Properties>\n%s\t\t\t<Name>%s</Name>\n'
            "\t\t\t<Synonym/>\n\t\t\t<Comment/>\n\t\t</Properties>\n\t</Role>\n</MetaDataObject>" % (
                ADOPTED if adopted else "", name))


def obj(tag, name, adopted=False, children=""):
    return (HEAD + '\t<%s uuid="u-%s">\n\t\t<Properties>\n%s\t\t\t<Name>%s</Name>\n\t\t\t<Comment/>\n'
            "\t\t</Properties>\n\t\t<ChildObjects>%s</ChildObjects>\n\t</%s>\n</MetaDataObject>" % (
                tag, name, ADOPTED if adopted else "", name, children, tag))


def data_processor(name="ЗаменаСтавокНДС", adopted=False):
    """DataProcessors/ЗаменаСтавокНДС.xml of ЗаменаСтавок_Расш (its Properties as they are)."""
    return (HEAD + '\t<DataProcessor uuid="5ec09e53-8302-4952-8903-74168553c507">\n\t\t<Properties>\n%s'
            "\t\t\t<Name>%s</Name>\n\t\t\t<Synonym>\n\t\t\t\t<v8:item>\n\t\t\t\t\t<v8:lang>ru</v8:lang>\n"
            "\t\t\t\t\t<v8:content>Замена ставок НДС</v8:content>\n\t\t\t\t</v8:item>\n\t\t\t</Synonym>\n"
            "\t\t\t<Comment/>\n\t\t\t<UseStandardCommands>true</UseStandardCommands>\n"
            "\t\t\t<DefaultForm>DataProcessor.%s.Form.ФормаОбработки</DefaultForm>\n\t\t</Properties>\n"
            "\t\t<ChildObjects>\n\t\t\t<Form>ФормаОбработки</Form>\n\t\t</ChildObjects>\n\t</DataProcessor>\n"
            "</MetaDataObject>" % (ADOPTED if adopted else "", name, name))


class RoleNoRights(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_role_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = os.path.join(self.tmp, "work")
        self.root = os.path.join(self.work, EXT)
        write(os.path.join(self.root, "Configuration.xml"), ext_config(EXT))
        write(os.path.join(self.root, "Roles", ROLE + ".xml"), role(ROLE))
        # own objects of МоёРасширение the role gave rights to in the source base, and an adopted one it did not
        write(os.path.join(self.root, "Catalogs", "мр_УсловияЗаполненияПлатежныхДокументов.xml"),
              obj("Catalog", "мр_УсловияЗаполненияПлатежныхДокументов"))
        write(os.path.join(self.root, "Documents", "мр_ОстаткиТоваровИзБухгалтерии.xml"),
              obj("Document", "мр_ОстаткиТоваровИзБухгалтерии"))
        write(os.path.join(self.root, "Catalogs", "Номенклатура.xml"), obj("Catalog", "Номенклатура", True))
        write(os.path.join(self.root, "CommonModules", "мр_Сервер.xml"), obj("CommonModule", "мр_Сервер"))

    def issues(self, ext=None):
        issues, _ = lint.lint(self.work, ext)
        return issues

    def roles(self, ext=None):
        return [x for x in self.issues(ext) if x["check"] == "ROLE_NO_RIGHTS"]

    def test_real_v43_role_without_rights_xml_is_a_risk(self):
        got = self.roles()
        self.assertEqual([(x["file"], x["severity"], x["role"], x["own_objects"], x["line"]) for x in got],
                         [("Roles/%s.xml" % ROLE, "risk", ROLE, 2, 0)])
        detail = got[0]["detail"]
        self.assertIn("роль загрузится пустой", detail)
        self.assertIn("Справочник.мр_УсловияЗаполненияПлатежныхДокументов", detail)
        self.assertNotIn("Номенклатура", detail)  # adopted: not the extension's own
        self.assertIn("setForNewObjects=true", detail)
        self.assertIn("Roles/%s/Ext/Rights.xml" % ROLE, detail)
        self.assertEqual(got[0]["path"], os.path.join(self.root, "Roles", ROLE + ".xml"))
        self.assertEqual(selection.level("lint", got[0]), "risk")

    def test_real_fixed_fixed_role_is_clean(self):
        write(os.path.join(self.root, "Roles", ROLE, "Ext", "Rights.xml"), FIXED_RIGHTS)
        self.assertEqual(self.roles(), [])

    def test_adopted_role_without_rights_xml_is_not_a_finding(self):
        # real extensions: adopted БазовыеПраваУТ / Администрирование without Rights.xml keep the vendor's rights
        os.remove(os.path.join(self.root, "Roles", ROLE + ".xml"))
        write(os.path.join(self.root, "Roles", "БазовыеПраваУТ.xml"), role("БазовыеПраваУТ", adopted=True))
        self.assertEqual(self.roles(), [])

    def test_extension_without_own_objects_with_rights_is_not_a_finding(self):
        # real extensions: only adoptions and common modules - the role gives nothing
        for rel in ("Catalogs/мр_УсловияЗаполненияПлатежныхДокументов.xml",
                    "Documents/мр_ОстаткиТоваровИзБухгалтерии.xml"):
            os.remove(os.path.join(self.root, *rel.split("/")))
        self.assertEqual(self.roles(), [])

    def test_rights_xml_lost_against_the_source_is_also_file_missing(self):
        src = os.path.join(self.tmp, "ext", EXT)
        shutil.copytree(self.root, src)
        write(os.path.join(src, "Roles", ROLE, "Ext", "Rights.xml"), FIXED_RIGHTS)
        got = [(x["check"], x["severity"]) for x in self.issues(os.path.join(self.tmp, "ext"))]
        self.assertIn(("FILE_MISSING", "error"), got)
        self.assertIn(("ROLE_NO_RIGHTS", "risk"), got)
        self.assertEqual(got[0], ("FILE_MISSING", "error"))  # errors before risks

    def test_risk_sorts_between_errors_and_info(self):
        write(os.path.join(self.root, "CommonModules", "мр_Сервер", "Ext", "Module.bsl"), "Процедура А()\n")
        os.makedirs(os.path.join(self.tmp, "ext"))
        src = os.path.join(self.tmp, "ext", EXT)
        shutil.copytree(self.root, src)
        write(os.path.join(src, "Reports", "Старый.xml"), obj("Report", "Старый"))  # OBJECT_REMOVED: info
        sev = [x["severity"] for x in self.issues(os.path.join(self.tmp, "ext"))]
        self.assertEqual(sev, sorted(sev, key=lint.SEVERITIES.index))
        self.assertEqual(set(sev), {"error", "risk", "info"})

    def test_check_work_counts_risks_and_selects_them(self):
        res = mcp_server.check_work(self.work, out=os.path.join(self.tmp, "out"))
        self.assertEqual(res["status"], "ok", res)
        s = res["summary"]
        self.assertEqual((s["errors"], s["risk"], s["info"], s["checks"]), (0, 1, 0, {"ROLE_NO_RIGHTS": 1}))
        self.assertEqual([(x["check"], x["severity"], x["role"], x["own_objects"]) for x in res["items"]],
                         [("ROLE_NO_RIGHTS", "risk", ROLE, 2)])
        only_errors = mcp_server.check_work(self.work, out=os.path.join(self.tmp, "out"), severity="error")
        self.assertEqual(only_errors["summary"]["select"]["matched"], 0)
        self.assertEqual(only_errors.get("items", []), [])
        by_flag = mcp_server.check_work(self.work, out=os.path.join(self.tmp, "out"), flag=["risk"])
        self.assertEqual(len(by_flag["items"]), 1)

    def test_console_and_markdown_name_the_risks(self):
        out = os.path.join(self.tmp, "out")
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            lint.main(["--work", self.work, "--out", out])
        self.assertIn("errors: 0, risks: 1, to confirm: 0", buf.getvalue())
        with open(os.path.join(out, "lint.md"), encoding="utf-8") as fh:
            self.assertIn("Ошибок: 0, рисков: 1, к подтверждению: 0.", fh.read())


class ObjectNameClash(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_objclash_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for side in ("old", "new"):
            write(os.path.join(self.tmp, side, "Configuration.xml"), vendor_config())
            write(os.path.join(self.tmp, side, "Catalogs", "СтавкиНДС.xml"), obj("Catalog", "СтавкиНДС"))
            write(os.path.join(self.tmp, side, "Ext", "HomePageWorkArea.xml"), HEAD + "</MetaDataObject>")
        # УТ 11.5.27.79 has the data processor, 11.5.17.172 does not
        write(os.path.join(self.tmp, "new", "DataProcessors", "ЗаменаСтавокНДС.xml"), data_processor())
        self.ext = os.path.join(self.tmp, "ext", "ЗаменаСтавок_Расш")
        write(os.path.join(self.ext, "Configuration.xml"), ext_config("ЗаменаСтавок_Расш"))
        write(os.path.join(self.ext, "DataProcessors", "ЗаменаСтавокНДС.xml"), data_processor())
        write(os.path.join(self.ext, "Catalogs", "СтавкиНДС.xml"), obj("Catalog", "СтавкиНДС", True))
        write(os.path.join(self.ext, "Ext", "HomePageWorkArea.xml"), HEAD + "</MetaDataObject>")
        self.out = os.path.join(self.tmp, "out")

    def run_tool(self):
        shutil.rmtree(self.out, True)
        with contextlib.redirect_stdout(io.StringIO()):
            borrowed.main(["--ext-root", os.path.join(self.tmp, "ext"), "--config-old", os.path.join(self.tmp, "old"),
                           "--config-new", os.path.join(self.tmp, "new"), "--out", self.out])
        with open(os.path.join(self.out, "borrowed.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def clashes(self):
        return [f for f in self.run_tool()["findings"] if f["check"] == "OBJECT_NAME_CLASH"]

    def test_real_new_vendor_data_processor_is_an_error_in_the_plan(self):
        data = self.run_tool()
        self.assertEqual([f["check"] for f in data["findings"]], ["OBJECT_NAME_CLASH"])  # the adopted catalog: clean
        f = data["findings"][0]
        self.assertEqual((f["ext"], f["object"], f["file"], f["part"], f["name"], f["vendor_name"], f["in_old"]),
                         ("ЗаменаСтавок_Расш", "Обработка.ЗаменаСтавокНДС", "DataProcessors/ЗаменаСтавокНДС.xml",
                          "объект", "ЗаменаСтавокНДС", "ЗаменаСтавокНДС", False))
        self.assertIn("Уже существует объект с именем Обработка.ЗаменаСтавокНДС", f["detail"])
        self.assertIn("появился", f["detail"])
        self.assertEqual(selection.level("borrowed", f), "error")
        p = plan.build({}, {}, {}, {}, {}, data)
        item = next(x for x in p["items"] if x["module"] == "DataProcessors/ЗаменаСтавокНДС.xml")
        self.assertEqual(item["group"], "MUST_FIX")
        self.assertTrue(item["what"][0].startswith("собственный объект Обработка.ЗаменаСтавокНДС: "))
        self.assertIn("borrowed:OBJECT_NAME_CLASH", item["sources"])

    def test_names_are_compared_case_insensitively(self):
        os.remove(os.path.join(self.tmp, "new", "DataProcessors", "ЗаменаСтавокНДС.xml"))
        write(os.path.join(self.tmp, "new", "DataProcessors", "ЗАМЕНАСТАВОКНДС.xml"), data_processor("ЗАМЕНАСТАВОКНДС"))
        got = self.clashes()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["vendor_name"].casefold(), "заменаставокндс")

    def test_vendor_object_already_in_old_goes_to_review(self):
        write(os.path.join(self.tmp, "old", "DataProcessors", "ЗаменаСтавокНДС.xml"), data_processor())
        data = self.run_tool()
        f = next(f for f in data["findings"] if f["check"] == "OBJECT_NAME_CLASH")
        self.assertTrue(f["in_old"])
        self.assertIn("не от обновления", f["detail"])
        item = next(x for x in plan.build({}, {}, {}, {}, {}, data)["items"]
                    if x["module"] == "DataProcessors/ЗаменаСтавокНДС.xml")
        self.assertEqual(item["group"], "REVIEW")

    def test_another_kind_of_the_same_name_is_not_a_clash(self):
        # the vendor configuration itself has ЖурналРегистрации as a common module and as a data processor
        os.remove(os.path.join(self.tmp, "new", "DataProcessors", "ЗаменаСтавокНДС.xml"))
        write(os.path.join(self.tmp, "new", "CommonModules", "ЗаменаСтавокНДС.xml"),
              obj("CommonModule", "ЗаменаСтавокНДС"))
        self.assertEqual(self.clashes(), [])

    def test_adopted_object_of_the_vendor_name_is_not_a_clash(self):
        write(os.path.join(self.ext, "DataProcessors", "ЗаменаСтавокНДС.xml"), data_processor(adopted=True))
        self.assertEqual(self.clashes(), [])

    def test_check_borrowed_answers_with_the_vendor_name(self):
        res = mcp_server.check_borrowed([os.path.join(self.tmp, "ext")], os.path.join(self.tmp, "old"),
                                        os.path.join(self.tmp, "new"), self.out, flag=["OBJECT_NAME_CLASH"])
        self.assertEqual(res["status"], "ok", res)
        self.assertEqual([(x["check"], x["object"], x["vendor_name"], x["path"]) for x in res["items"]],
                         [("OBJECT_NAME_CLASH", "Обработка.ЗаменаСтавокНДС", "ЗаменаСтавокНДС",
                           os.path.join(self.ext, "DataProcessors", "ЗаменаСтавокНДС.xml"))])
        self.assertIs(res["items"][0]["in_old"], False)


if __name__ == "__main__":
    unittest.main()
