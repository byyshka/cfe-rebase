# -*- coding: utf-8 -*-
"""borrowed PROPERTY_CHANGED: controlled properties of adopted objects against the new release.

The fixtures are fragments of the XML of the corpora: a real extension (УТ 11.5.17.172 -> 11.5.27.79),
whose load into 11.5.27 the platform refused on 30.10.2026 with «Значение контролируемого свойства Хранение у объекта
ФункциональнаяОпция.ВестиСведенияДляДекларацийАлкоВРознице не совпадает со значением в расширяемой конфигурации» and
«… РежимСовместимости у объекта  не совпадает …», and real extensions that load (a synonym, a main DCS,
register records, subsystem content of their own). The corpus test at the end runs the real dumps when they are on
this machine."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from cfe_rebase import borrowed, plan, selection

HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" '
        'xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:xr="http://v8.1c.ru/8.3/xcf/readable" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config" version="2.20">\n')
ADOPTED = "<ObjectBelonging>Adopted</ObjectBelonging>"
FO = "ВестиСведенияДляДекларацийАлкоВРознице"
OLD_CONST, NEW_CONST = "ВестиСведенияДляДекларацийАлкоВРознице", "ВестиСведенияДляДекларацийПоАлкогольнойПродукции"


def md(tag, name, props, adopted=False, children=""):
    return HEAD + '<%s uuid="o-%s"><Properties>%s<Name>%s</Name><Comment/>%s</Properties>%s</%s></MetaDataObject>' % (
        tag, name, ADOPTED if adopted else "", name, props,
        "<ChildObjects>%s</ChildObjects>" % children if children is not None else "", tag)


def configuration(name, props, extension=False):
    purpose = "<ConfigurationExtensionPurpose>Customization</ConfigurationExtensionPurpose>" if extension else ""
    return HEAD + '<Configuration uuid="c-%s"><Properties>%s<Name>%s</Name>%s%s</Properties><ChildObjects/>' \
                  '</Configuration></MetaDataObject>' % (name, ADOPTED if extension else "", name, purpose, props)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\xef\xbb\xbf" + text.encode("utf-8"))


def items(*refs):
    return "".join('<xr:Item xsi:type="xr:MDObjectRef">%s</xr:Item>' % r for r in refs)


def string_type(length):
    return ("<Type><v8:Type>xs:string</v8:Type><v8:StringQualifiers><v8:Length>%d</v8:Length><v8:AllowedLength>"
            "Variable</v8:AllowedLength></v8:StringQualifiers></Type>" % length)


class PropertyChanged(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_borrowed_props_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ext = os.path.join(self.tmp, "ext", "МоёРасширение")
        # vendor: УТ 11.5.17.172 (old) and 11.5.27.79 (new) - FunctionalOptions/ВестиСведенияДляДекларацийАлкоВРознице,
        # Configuration.xml CompatibilityMode
        for root, location, compat in (("old", OLD_CONST, "Version8_3_24"), ("new", NEW_CONST, "Version8_3_27")):
            r = os.path.join(self.tmp, root)
            write(os.path.join(r, "Configuration.xml"), configuration(
                "УправлениеТорговлей", "<CompatibilityMode>%s</CompatibilityMode>" % compat))
            write(os.path.join(r, "FunctionalOptions", FO + ".xml"), md("FunctionalOption", FO, (
                "<Location>Constant.%s</Location><PrivilegedGetMode>true</PrivilegedGetMode><Content>%s</Content>"
                % (location, items("Report.ЖурналУчетаРозничнойПродажиАлкогольнойПродукции"))), children=None))
            for const in (OLD_CONST, NEW_CONST):
                write(os.path.join(r, "Constants", const + ".xml"), md("Constant", const, "<Type><v8:Type>xs:boolean"
                                                                       "</v8:Type></Type>", children=None))
        self.compat("Version8_3_24")
        self.location(OLD_CONST)
        self.out = os.path.join(self.tmp, "out")

    # -- the extension -------------------------------------------------------------------------------------------
    def compat(self, value):
        write(os.path.join(self.ext, "Configuration.xml"), configuration(
            "МоёРасширение", "<CompatibilityMode>%s</CompatibilityMode>" % value if value else "", extension=True))

    def location(self, const):
        # the adopted functional option as the platform dumps it: Location only, with the vendor's value at borrowing
        write(os.path.join(self.ext, "FunctionalOptions", FO + ".xml"), md(
            "FunctionalOption", FO, "<ExtendedConfigurationObject>5d8523c3-5678-4662-b822-c9bb4f9ad369"
                                    "</ExtendedConfigurationObject><Location>Constant.%s</Location>" % const,
            adopted=True, children=None))

    def vendor(self, rel, old, new):
        write(os.path.join(self.tmp, "old", *rel.split("/")), old)
        write(os.path.join(self.tmp, "new", *rel.split("/")), new)

    def mine(self, rel, text):
        write(os.path.join(self.ext, *rel.split("/")), text)

    def run_tool(self, old=True):
        args = ["--ext-root", os.path.join(self.tmp, "ext"), "--config-old", os.path.join(self.tmp, "old"),
                "--config-new", os.path.join(self.tmp, "new"), "--out", self.out]
        with contextlib.redirect_stdout(io.StringIO()):
            borrowed.main(args)
        with open(os.path.join(self.out, "borrowed.json"), encoding="utf-8") as fh:
            # CompatibilityMode of the extension's Configuration.xml is COMPAT_MODE, the rest PROPERTY_CHANGED
            return [f for f in json.load(fh)["findings"] if f["check"] in ("PROPERTY_CHANGED", "COMPAT_MODE")]

    def got(self):
        return sorted((f["object"], f["part"], f["name"]) for f in self.run_tool())

    # -- the real refusal ----------------------------------------------------------------------------------------
    def test_real_refusal_both_properties(self):
        found = self.run_tool()
        self.assertEqual(sorted((f["object"], f["name"]) for f in found),
                         [("Конфигурация.МоёРасширение", "CompatibilityMode"), ("ФункциональнаяОпция." + FO, "Location")])
        fo = next(f for f in found if f["name"] == "Location")
        self.assertEqual((fo["file"], fo["part"]), ("FunctionalOptions/%s.xml" % FO, "свойство"))
        self.assertEqual((fo["value"], fo["new_value"]), ("Constant." + OLD_CONST, "Constant." + NEW_CONST))
        self.assertIn("Хранение (Location)", fo["detail"])
        # the new constant is not in the extension: borrow it
        self.assertIn('cfe-borrow -Object "Constant.%s"' % NEW_CONST, fo["detail"])
        cm = next(f for f in found if f["name"] == "CompatibilityMode")
        self.assertIn("РежимСовместимости", cm["detail"])
        self.assertEqual((cm["check"], cm["ext_value"], cm["new_value"]), ("COMPAT_MODE", "Version8_3_24", "Version8_3_27"))

    def test_hand_fix_clears_it(self):
        # the hand fix: Location of new, the new constant borrowed, CompatibilityMode of new
        self.location(NEW_CONST)
        self.mine("Constants/%s.xml" % NEW_CONST, md("Constant", NEW_CONST, "<ExtendedConfigurationObject>44bd39ff"
                                                     "</ExtendedConfigurationObject>", adopted=True, children=None))
        self.compat("Version8_3_27")
        self.assertEqual(self.run_tool(), [])

    def test_no_borrow_hint_when_the_constant_is_adopted(self):
        self.mine("Constants/%s.xml" % NEW_CONST, md("Constant", NEW_CONST, "", adopted=True, children=None))
        fo = next(f for f in self.run_tool() if f["name"] == "Location")
        self.assertNotIn("cfe-borrow", fo["detail"])

    def test_compatibility_mode_absent_or_equal(self):
        self.compat(None)  # most extensions do not hold it (most real ones)
        self.assertEqual([f["name"] for f in self.run_tool()], ["Location"])
        self.compat("Version8_3_27")  # some real extensions
        self.assertEqual([f["name"] for f in self.run_tool()], ["Location"])

    def test_proven_property_reported_without_old(self):
        # Location is named by the platform's refusal: reported against new whatever the extension had against old
        self.location("ДругаяКонстанта")
        found = self.run_tool()
        fo = next(f for f in found if f["name"] == "Location")
        self.assertIn("и с old", fo["detail"])

    # -- controlled by the corpora: the extension holds the vendor's old value -------------------------------------
    def test_catalog_code_length(self):
        # Catalogs/СтатьиДвиженияДенежныхСредств of a real extension: CodeLength / DescriptionLength / Hierarchical, as in old
        props = "<Hierarchical>true</Hierarchical><CodeLength>%d</CodeLength><DescriptionLength>100" \
                "</DescriptionLength><CodeType>String</CodeType><CodeAllowedLength>Variable</CodeAllowedLength>"
        self.vendor("Catalogs/Статьи.xml", md("Catalog", "Статьи", props % 9), md("Catalog", "Статьи", props % 11))
        self.mine("Catalogs/Статьи.xml", md("Catalog", "Статьи", props % 9, adopted=True))
        self.assertIn(("Справочник.Статьи", "свойство", "CodeLength"), self.got())

    def test_value_other_than_old_is_the_extensions_own(self):
        props = "<CodeLength>%d</CodeLength>"
        self.vendor("Catalogs/Статьи.xml", md("Catalog", "Статьи", props % 9), md("Catalog", "Статьи", props % 11))
        self.mine("Catalogs/Статьи.xml", md("Catalog", "Статьи", props % 15, adopted=True))
        self.assertNotIn("Справочник.Статьи", [o for o, _, _ in self.got()])

    def test_common_module_flags_and_style_item(self):
        flags = "<Global>false</Global><Server>true</Server><ServerCall>%s</ServerCall>"
        self.vendor("CommonModules/ОбщийМодульСервер.xml", md("CommonModule", "ОбщийМодульСервер", flags % "false",
                                                              children=None),
                    md("CommonModule", "ОбщийМодульСервер", flags % "true", children=None))
        self.mine("CommonModules/ОбщийМодульСервер.xml", md("CommonModule", "ОбщийМодульСервер", flags % "false",
                                                            adopted=True, children=None))
        # StyleItems/ГиперссылкаЦвет of a real extension: a kind outside metadata.KINDS
        self.vendor("StyleItems/ГиперссылкаЦвет.xml", md("StyleItem", "ГиперссылкаЦвет", "<Type>Color</Type>",
                                                         children=None),
                    md("StyleItem", "ГиперссылкаЦвет", "<Type>Font</Type>", children=None))
        self.mine("StyleItems/ГиперссылкаЦвет.xml", md("StyleItem", "ГиперссылкаЦвет", "<Type>Color</Type>",
                                                       adopted=True, children=None))
        got = self.got()
        self.assertIn(("ОбщийМодуль.ОбщийМодульСервер", "свойство", "ServerCall"), got)
        self.assertIn(("ЭлементСтиля.ГиперссылкаЦвет", "свойство", "Type"), got)

    def test_form_type_and_command_group(self):
        form = lambda t, a=False: md("Form", "ФормаСписка", "<FormType>%s</FormType>" % t, adopted=a,  # noqa: E731
                                     children=None)
        command = lambda g, a=False: ('<Command uuid="k"><Properties>%s<Name>Печать</Name><Group>%s</Group>'  # noqa
                                      '</Properties></Command>' % (ADOPTED if a else "", g))
        obj = lambda g, a=False: md("Document", "Заказ", "", adopted=a,  # noqa: E731
                                    children="<Form>ФормаСписка</Form>" + command(g, a))
        self.vendor("Documents/Заказ.xml", obj("FormCommandBarImportant"), obj("CommandGroup.Печать"))
        self.vendor("Documents/Заказ/Forms/ФормаСписка.xml", form("Managed"), form("Ordinary"))
        self.mine("Documents/Заказ.xml", obj("FormCommandBarImportant", True))
        self.mine("Documents/Заказ/Forms/ФормаСписка.xml", form("Managed", True))
        got = self.got()
        self.assertIn(("Документ.Заказ", "свойство формы ФормаСписка", "FormType"), got)
        self.assertIn(("Документ.Заказ", "свойство команды Печать", "Group"), got)

    # -- not controlled: the extension changes them or adds to them -----------------------------------------------
    def test_extended_and_modified_properties(self):
        # Documents/Взаимодействия: RegisterRecords of the extension's own registers (a real extension); Subsystems/Продажи: the
        # extension's content (a real extension); Reports/ОстаткиИДоступностьТоваров: its synonym and main DCS (real extensions)
        reg = lambda r: md("Document", "Взаимодействие", "<RegisterRecords>%s</RegisterRecords>" % items(*r))  # noqa
        self.vendor("Documents/Взаимодействие.xml", reg([]), reg(["InformationRegister.Новый"]))
        self.mine("Documents/Взаимодействие.xml", md("Document", "Взаимодействие", "<RegisterRecords/>", True))
        sub = lambda c: md("Subsystem", "Продажи", "<Content>%s</Content>" % items(*c), children=None)  # noqa: E731
        self.vendor("Subsystems/Продажи.xml", sub(["Document.Заказ"]), sub(["Document.Заказ", "Report.Новый"]))
        self.mine("Subsystems/Продажи.xml", md("Subsystem", "Продажи", "<Content>%s</Content>" % items(
            "Document.мр_ЖурналСпецификаций"), True, children=None))
        rep = lambda syn, dcs: md("Report", "Остатки", (  # noqa: E731
            "<Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>%s</v8:content></v8:item></Synonym>"
            "<MainDataCompositionSchema>Report.Остатки.Template.%s</MainDataCompositionSchema>" % (syn, dcs)))
        self.vendor("Reports/Остатки.xml", rep("Остатки", "ОсновнаяСхема"), rep("Остатки товаров", "ОсновнаяСхема2"))
        self.mine("Reports/Остатки.xml", rep("Остатки", "ОсновнаяСхема").replace("<Name>", ADOPTED + "<Name>", 1))
        self.assertEqual([g for g in self.got() if g[0] != "ФункциональнаяОпция." + FO and not g[0].startswith("Конфигурация")],
                         [])

    def test_type_placeholder_and_widened_type(self):
        # DefinedTypes/ДокументПродажиИСМП of a real extension: a composite type is a placeholder <Type/> in the extension;
        # DefinedTypes/ОснованияЗапросаКоммерческихПредложенийПоставщиков: ЗаказКлиента in 11.5.17, three documents
        # in 11.5.27 - only widened, and the hand fix with it loaded
        dt = lambda *t: md("DefinedType", "Основания", "<Type>%s</Type>" % "".join(  # noqa: E731
            "<v8:Type>cfg:DocumentRef.%s</v8:Type>" % x for x in t), children=None)
        self.vendor("DefinedTypes/Основания.xml", dt("ЗаказКлиента"), dt("ЗаявкаНаЗакупку", "ЗаказКлиента"))
        self.mine("DefinedTypes/Основания.xml", dt("ЗаказКлиента").replace("<Name>", ADOPTED + "<Name>", 1))
        self.vendor("DefinedTypes/ДокументПродажи.xml", dt("Реализация", "Чек"), dt("Реализация"))
        self.mine("DefinedTypes/ДокументПродажи.xml", md("DefinedType", "ДокументПродажи", "<Type/>", True,
                                                         children=None))
        # a constant whose simple type the vendor changed: controlled
        const = lambda n: md("Constant", "Префикс", string_type(n), children=None)  # noqa: E731
        self.vendor("Constants/Префикс.xml", const(2), const(4))
        self.mine("Constants/Префикс.xml", const(2).replace("<Name>", ADOPTED + "<Name>", 1))
        got = [g for g in self.got() if g[0] != "ФункциональнаяОпция." + FO and not g[0].startswith("Конфигурация")]
        self.assertEqual(got, [("Константа.Префикс", "свойство", "Type")])

    # -- registration ----------------------------------------------------------------------------------------------
    def test_selection_and_plan(self):
        row = {"check": "PROPERTY_CHANGED"}
        self.assertEqual(selection.level("borrowed", row), "error")
        self.assertLess(selection.rank("borrowed", {"check": "TYPE_CHANGED"}), selection.rank("borrowed", row))
        self.assertLess(selection.rank("borrowed", row), selection.rank("borrowed", {"check": "LIST_FIELD_LOST"}))
        p = plan.build({}, {}, {}, {}, {}, {"findings": self.run_tool()})
        self.assertEqual(p["counts"], {"MUST_FIX": 2, "REVIEW": 0, "CLARIFY": 0})
        self.assertEqual({x["module"] for x in p["items"]}, {"Configuration.xml", "FunctionalOptions/%s.xml" % FO})
        self.assertEqual(sorted(x["sources"] for x in p["items"]), [["borrowed:COMPAT_MODE"], ["borrowed:PROPERTY_CHANGED"]])


if __name__ == "__main__":
    unittest.main()
