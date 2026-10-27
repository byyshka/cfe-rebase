# -*- coding: utf-8 -*-
"""borrowed: adopted objects of an extension against the new release, on small hand-written dumps."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from cfe_rebase import borrowed, plan

HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" '
        'xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config" version="2.20">\n')
ADOPTED = "<ObjectBelonging>Adopted</ObjectBelonging>"


def decimal(digits, fraction):
    return ("<Type><v8:Type>xs:decimal</v8:Type><v8:NumberQualifiers><v8:Digits>%d</v8:Digits><v8:FractionDigits>%d"
            "</v8:FractionDigits></v8:NumberQualifiers></Type>" % (digits, fraction))


def refs(*names):
    return "<Type>%s</Type>" % "".join("<v8:Type>cfg:CatalogRef.%s</v8:Type>" % n for n in names)


def string(length):
    return ("<Type><v8:Type>xs:string</v8:Type><v8:StringQualifiers><v8:Length>%d</v8:Length><v8:AllowedLength>"
            "Variable</v8:AllowedLength></v8:StringQualifiers></Type>" % length)


def tabular(name, columns, adopted=False):
    return '<TabularSection uuid="t-%s"><Properties><Name>%s</Name>%s</Properties><ChildObjects>%s</ChildObjects>' \
           '</TabularSection>' % (name, name, ADOPTED if adopted else "", "".join(columns))


def child(tag, name, typ="", adopted=False):
    return '<%s uuid="u-%s"><Properties><Name>%s</Name>%s%s</Properties></%s>' % (
        tag, name, name, ADOPTED if adopted else "", typ, tag)


def obj(tag, name, children, adopted=False):
    return HEAD + '<%s uuid="o-%s"><Properties><Name>%s</Name>%s</Properties><ChildObjects>%s</ChildObjects></%s>' \
                  '</MetaDataObject>' % (tag, name, name, ADOPTED if adopted else "", "".join(children), tag)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\xef\xbb\xbf" + text.encode("utf-8"))


class Borrowed(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_borrowed_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        t = self.tmp
        reg_old = [child("Resource", "Остаток", decimal(10, 4)), child("Resource", "Резерв", decimal(10, 4)),
                   child("Dimension", "Склад", refs("Склады"))]
        reg_new = [child("Resource", "Остаток", decimal(14, 8)), child("Resource", "УдалитьРезерв", decimal(10, 4)),
                   child("Dimension", "Склад", refs("Склады", "Цеха")), child("Attribute", "Комментарий")]
        for root, rc in (("old", reg_old), ("new", reg_new)):
            write(os.path.join(t, root, "InformationRegisters", "Запасы.xml"), obj("InformationRegister", "Запасы", rc))
        write(os.path.join(t, "old", "Enums", "Состояния.xml"),
              obj("Enum", "Состояния", [child("EnumValue", "Новый"), child("EnumValue", "Закрыт")]))
        write(os.path.join(t, "new", "Enums", "Состояния.xml"), obj("Enum", "Состояния", [child("EnumValue", "Новый")]))
        write(os.path.join(t, "old", "Documents", "Старый.xml"), obj("Document", "Старый", []))
        for root in ("old", "new"):
            write(os.path.join(t, root, "Configuration.xml"), HEAD + '<Configuration uuid="c-%s"><Properties><Name>'
                  'Типовая</Name></Properties></Configuration></MetaDataObject>' % root)
        ext = os.path.join(t, "ext", "Расш")
        write(os.path.join(ext, "Configuration.xml"), HEAD + '<Configuration uuid="c-ext"><Properties><Name>Расш</Name>'
              '<ConfigurationExtensionPurpose>Customization</ConfigurationExtensionPurpose></Properties>'
              '</Configuration></MetaDataObject>')
        write(os.path.join(ext, "InformationRegisters", "Запасы.xml"), obj("InformationRegister", "Запасы", [
            child("Resource", "Остаток", decimal(10, 4), True), child("Resource", "Резерв", decimal(10, 4), True),
            child("Dimension", "Склад", refs("Склады"), True), child("Resource", "Чужой", decimal(5, 0), True),
            child("Attribute", "Комментарий")], True))
        write(os.path.join(ext, "InformationRegisters", "Запасы", "Forms", "ФормаЗаписи", "Ext", "Form.xml"),
              "<Form><DataPath>Запись.Резерв</DataPath></Form>")
        write(os.path.join(ext, "Enums", "Состояния.xml"), obj("Enum", "Состояния", [
            child("EnumValue", "Новый", adopted=True), child("EnumValue", "Закрыт", adopted=True)], True))
        write(os.path.join(ext, "Documents", "Старый.xml"), obj("Document", "Старый", [], True))
        write(os.path.join(ext, "Documents", "Свой.xml"), obj("Document", "Свой", []))  # the extension's own object
        self.out = os.path.join(t, "out")

    def run_tool(self):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            borrowed.main(["--ext-root", os.path.join(self.tmp, "ext"), "--config-old", os.path.join(self.tmp, "old"),
                           "--config-new", os.path.join(self.tmp, "new"), "--out", self.out])
        with open(os.path.join(self.out, "borrowed.json"), encoding="utf-8") as fh:
            return json.load(fh), buf.getvalue()

    def test_checks(self):
        data, console = self.run_tool()
        got = {(f["object"], f["name"], f["check"]) for f in data["findings"]}
        self.assertEqual(got, {
            ("РегистрСведений.Запасы", "Остаток", "TYPE_CHANGED"),       # 10.4 -> 14.8
            ("РегистрСведений.Запасы", "Резерв", "REMOVED"),             # the vendor renamed it УдалитьРезерв
            ("РегистрСведений.Запасы", "Чужой", "NOT_IN_VENDOR"),        # in neither release
            ("РегистрСведений.Запасы", "Комментарий", "NAME_CLASH"),     # own attribute, the vendor added the name
            ("Перечисление.Состояния", "Закрыт", "REMOVED"),
            ("Документ.Старый", "Старый", "OBJECT_REMOVED"),
        })  # Склад: the vendor only widened the composite type - not reported; Документ.Свой is not adopted
        by = {(f["name"], f["check"]): f for f in data["findings"]}
        self.assertIn("Удалить", by[("Резерв", "REMOVED")]["detail"])
        self.assertEqual(by[("Резерв", "REMOVED")]["forms"],
                         ["InformationRegisters/Запасы/Forms/ФормаЗаписи/Ext/Form.xml"])
        self.assertIn("Digits=10", by[("Остаток", "TYPE_CHANGED")]["detail"])
        self.assertEqual(data["stats"]["adopted"], 3)
        self.assertIn("findings: 6", console)

    def test_unparsable_vendor_xml_is_not_removed(self):
        # an XML that exists and does not parse is not «no object»: it was OBJECT_REMOVED (new) / REMOVED (old)
        broken_new = os.path.join(self.tmp, "new", "Documents", "Старый.xml")
        write(broken_new, HEAD + "<Document><Properties>")
        write(os.path.join(self.tmp, "old", "Enums", "Состояния.xml"), HEAD + "<Enum><Properties>")
        data, console = self.run_tool()
        got = {(f["object"], f["check"]) for f in data["findings"] if f["object"] != "РегистрСведений.Запасы"}
        self.assertEqual(got, {("Документ.Старый", "XML_UNREADABLE"), ("Перечисление.Состояния", "XML_UNREADABLE")})
        by = {f["object"]: f["detail"] for f in data["findings"] if f["check"] == "XML_UNREADABLE"}
        self.assertIn(broken_new, by["Документ.Старый"])
        self.assertIn("ParseError", by["Документ.Старый"])
        self.assertIn("в old", by["Перечисление.Состояния"])
        self.assertEqual(data["stats"]["unreadable"], 2)
        self.assertIn("unreadable XML: 2", console)

    def test_unparsable_extension_xml_is_reported(self):
        write(os.path.join(self.tmp, "ext", "Расш", "Documents", "Сломан.xml"), HEAD + "<Document><Properties>")
        data, _ = self.run_tool()
        broken = [f for f in data["findings"] if f["check"] == "XML_UNREADABLE"]
        self.assertEqual([(f["object"], f["file"]) for f in broken], [("Документ.Сломан", "Documents/Сломан.xml")])
        self.assertIn("в расширении", broken[0]["detail"])
        self.assertIn("ParseError", broken[0]["detail"])

    def test_form_binding_is_case_insensitive(self):
        write(os.path.join(self.tmp, "ext", "Расш", "InformationRegisters", "Запасы", "Forms", "ФормаЗаписи", "Ext",
                           "Form.xml"), "<Form><DataPath>Запись.резерв</DataPath></Form>")
        data, _ = self.run_tool()
        removed = next(f for f in data["findings"] if f["name"] == "Резерв")
        self.assertEqual(removed["forms"], ["InformationRegisters/Запасы/Forms/ФормаЗаписи/Ext/Form.xml"])

    def test_config_old_is_required(self):
        # without old a type change is not seen and a field in neither release looks REMOVED, not NOT_IN_VENDOR
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            borrowed.main(["--ext-root", os.path.join(self.tmp, "ext"), "--config-new", os.path.join(self.tmp, "new"),
                           "--out", self.out])
        self.assertFalse(os.path.exists(self.out))

    def test_adopted_object_in_neither_release(self):
        # the object is in neither old nor new (the base configuration is modified): a question, like a field there -
        # it was OBJECT_REMOVED and went to MUST_FIX
        write(os.path.join(self.tmp, "ext", "Расш", "Catalogs", "Доработанный.xml"), obj("Catalog", "Доработанный", [],
                                                                                        True))
        data, _ = self.run_tool()
        found = [(f["object"], f["check"]) for f in data["findings"] if f["object"] == "Справочник.Доработанный"]
        self.assertEqual(found, [("Справочник.Доработанный", "NOT_IN_VENDOR")])
        p = plan.build({}, {}, {}, {}, {}, data)
        self.assertEqual(next(x["group"] for x in p["items"] if x["module"] == "Catalogs/Доработанный.xml"), "CLARIFY")

    def test_out_inside_a_dump_is_refused(self):
        for root in (os.path.join(self.tmp, "ext", "Расш"), os.path.join(self.tmp, "old"),
                     os.path.join(self.tmp, "new")):
            out = os.path.join(root, "отчёт")
            with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()):
                borrowed.main(["--ext-root", os.path.join(self.tmp, "ext"), "--config-old",
                               os.path.join(self.tmp, "old"), "--config-new", os.path.join(self.tmp, "new"),
                               "--out", out])
            self.assertIn("в выгрузки не пишет", str(cm.exception.code))
            self.assertFalse(os.path.exists(out))

    def test_plan_groups(self):
        data, _ = self.run_tool()
        p = plan.build({}, {}, {}, {}, {}, data)
        groups = {x["module"]: x["group"] for x in p["items"]}
        self.assertEqual(groups, {"InformationRegisters/Запасы.xml": "MUST_FIX", "Enums/Состояния.xml": "MUST_FIX",
                                  "Documents/Старый.xml": "MUST_FIX"})
        reg = next(x for x in p["items"] if x["module"] == "InformationRegisters/Запасы.xml")
        self.assertEqual(len(reg["what"]), 4)  # one item per object, every finding of it listed
        only_question = plan.build({}, {}, {}, {}, {}, {"findings": [f for f in data["findings"]
                                                                   if f["check"] == "NOT_IN_VENDOR"]})
        self.assertEqual(only_question["counts"], {"MUST_FIX": 0, "REVIEW": 0, "CLARIFY": 1})


class TabularSections(unittest.TestCase):
    """Attributes of an adopted tabular section and tabular sections of the extension: the rules of object attributes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_borrowed_ts_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        t = self.tmp
        old = [child("Attribute", "Контрагент", refs("Партнеры", "Контрагенты")),
               tabular("Товары", [child("Attribute", "Комментарий", string(10)),
                                  child("Attribute", "Склад", refs("Склады")),
                                  child("Attribute", "Упаковка", refs("Упаковки", "Единицы")),
                                  child("Attribute", "Серия", string(5)),
                                  child("Attribute", "Партия", string(5))])]
        new = [child("Attribute", "Контрагент", refs("Контрагенты", "Партнеры")),       # the same types reordered
               tabular("Товары", [child("Attribute", "Комментарий", string(25)),      # 10 -> 25
                                  child("Attribute", "Склад", refs("Склады", "Цеха")),  # only widened
                                  child("Attribute", "Упаковка", refs("Единицы", "Упаковки")),  # reordered
                                  child("Attribute", "Серия", string(5)),
                                  child("Attribute", "Скидка", decimal(15, 2)),      # added by the vendor
                                  child("Attribute", "УдалитьПартия", string(5))]),  # marked for deletion
               tabular("Услуги", [])]                                                  # added by the vendor
        for root, children in (("old", old), ("new", new)):
            write(os.path.join(t, root, "Documents", "Заказ.xml"), obj("Document", "Заказ", children))
        ext = os.path.join(t, "ext", "Расш")
        write(os.path.join(ext, "Configuration.xml"), HEAD + '<Configuration uuid="c-ext"><Properties><Name>Расш</Name>'
              '<ConfigurationExtensionPurpose>Customization</ConfigurationExtensionPurpose></Properties>'
              '</Configuration></MetaDataObject>')
        write(os.path.join(ext, "Documents", "Заказ.xml"), obj("Document", "Заказ", [
            child("Attribute", "Контрагент", refs("Партнеры", "Контрагенты"), True),
            tabular("Товары", [child("Attribute", "Комментарий", string(10), True),
                               child("Attribute", "Склад", refs("Склады"), True),
                               child("Attribute", "Упаковка", refs("Упаковки", "Единицы"), True),
                               child("Attribute", "Серия", string(5)),                 # own, the name is in old too
                               child("Attribute", "Скидка", decimal(10, 2)),           # own, the vendor added it
                               child("Attribute", "Партия", string(5), True)], True),
            tabular("Чужая", [], True),                                                    # in neither release
            tabular("Услуги", [child("Attribute", "Расш_Работа", string(50))])], True))  # own, the vendor added it
        write(os.path.join(ext, "Documents", "Заказ", "Forms", "ФормаДокумента", "Ext", "Form.xml"),
              "<Form><DataPath>Объект.Товары.Комментарий</DataPath></Form>")

    def test_rules_of_attributes(self):
        _, findings, _ = borrowed.analyze([os.path.join(self.tmp, "ext")], *(
            borrowed.metadata.Dump(os.path.join(self.tmp, r), r) for r in ("old", "new")))
        self.assertEqual({(f["part"], f["name"], f["check"]) for f in findings}, {
            ("реквизит табличной части Товары", "Комментарий", "TYPE_CHANGED"),
            ("реквизит табличной части Товары", "Скидка", "NAME_CLASH"),
            ("табличная часть", "Услуги", "NAME_CLASH"),
            ("реквизит табличной части Товары", "Партия", "REMOVED"),
            ("табличная часть", "Чужая", "NOT_IN_VENDOR"),
        })  # Контрагент and Упаковка: the same types in another order; Склад: only widened; Серия: in old as well
        changed = next(f for f in findings if f["check"] == "TYPE_CHANGED")
        self.assertIn("Length=10", changed["detail"])
        self.assertIn("Length=25", changed["detail"])
        self.assertEqual(changed["forms"], ["Documents/Заказ/Forms/ФормаДокумента/Ext/Form.xml"])
        self.assertIn("«УдалитьПартия»", next(f["detail"] for f in findings if f["name"] == "Партия"))

    def test_case_only_renames(self):
        # 1C names are case-insensitive: a vendor rename that changes only the case removes nothing, and the old
        # type (TYPE_CHANGED) and the old name (REMOVED, not NOT_IN_VENDOR) are still found
        old = [child("Attribute", "Контрагент", refs("Партнеры", "Контрагенты")),
               tabular("ТОВАРЫ", [child("Attribute", "комментарий", string(10)),
                                  child("Attribute", "Склад", refs("Склады")),
                                  child("Attribute", "Упаковка", refs("Упаковки", "Единицы")),
                                  child("Attribute", "Серия", string(5)),
                                  child("Attribute", "партия", string(5))])]
        new = [child("Attribute", "контрагент", refs("Контрагенты", "Партнеры")),
               tabular("товары", [child("Attribute", "Комментарий", string(25)),
                                  child("Attribute", "СКЛАД", refs("Склады", "Цеха")),
                                  child("Attribute", "упаковка", refs("Единицы", "Упаковки")),
                                  child("Attribute", "Серия", string(5)),
                                  child("Attribute", "Скидка", decimal(15, 2)),
                                  child("Attribute", "УдалитьПартия", string(5))]),
               tabular("Услуги", [])]
        for root, children in (("old", old), ("new", new)):
            write(os.path.join(self.tmp, root, "Documents", "Заказ.xml"), obj("Document", "Заказ", children))
        _, findings, _ = borrowed.analyze([os.path.join(self.tmp, "ext")], *(
            borrowed.metadata.Dump(os.path.join(self.tmp, r), r) for r in ("old", "new")))
        self.assertEqual({(f["part"], f["name"], f["check"]) for f in findings}, {
            ("реквизит табличной части Товары", "Комментарий", "TYPE_CHANGED"),
            ("реквизит табличной части Товары", "Скидка", "NAME_CLASH"),
            ("табличная часть", "Услуги", "NAME_CLASH"),
            ("реквизит табличной части Товары", "Партия", "REMOVED"),
            ("табличная часть", "Чужая", "NOT_IN_VENDOR"),
        })  # the same findings as with the same case, under the names the extension spells


def list_attr(name, manual=False, query=""):
    return ('<Attribute name="%s"><Type><v8:Type>cfg:DynamicList</v8:Type></Type><Settings xsi:type="DynamicList">'
            '<ManualQuery>%s</ManualQuery>%s<MainTable>Catalog.Склады</MainTable></Settings></Attribute>' % (
                name, "true" if manual else "false", "<QueryText>%s</QueryText>" % query if query else ""))


def form_xml(attrs, paths, base=None):
    body = "<Attributes>%s</Attributes>%s" % ("".join(attrs), "".join("<DataPath>%s</DataPath>" % p for p in paths))
    return "<Form>%s%s</Form>" % (body, "<BaseForm>%s</BaseForm>" % base if base is not None else "")


class _AdoptedListForm(unittest.TestCase):
    """Dumps with one adopted list form (Склады.ФормаСписка) in old, new and the extension."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_borrowed_forms_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.old_list = list_attr("Список")
        self.new_list = list_attr("Список", True, "ВЫБРАТЬ Склады.Ссылка КАК Ссылка ИЗ Справочник.Склады КАК Склады")
        catalog = HEAD + ('<Catalog uuid="o"><Properties><Name>Склады</Name>%s</Properties><ChildObjects>'
                          '<Form>ФормаСписка</Form></ChildObjects></Catalog></MetaDataObject>')
        for root, lst in (("old", self.old_list), ("new", self.new_list)):
            d = os.path.join(self.tmp, root)
            write(os.path.join(d, "Catalogs", "Склады.xml"), catalog % "")
            write(os.path.join(d, "Catalogs", "Склады", "Forms", "ФормаСписка", "Ext", "Form.xml"),
                  form_xml([lst], ["Список.Ссылка"]))
            write(os.path.join(d, "Configuration.xml"), HEAD + '<Configuration uuid="c-%s"><Properties><Name>Т</Name>'
                  '</Properties></Configuration></MetaDataObject>' % root)
        self.ext = os.path.join(self.tmp, "ext", "Расш")
        write(os.path.join(self.ext, "Configuration.xml"), HEAD + '<Configuration uuid="c-e"><Properties><Name>Расш'
              '</Name></Properties></Configuration></MetaDataObject>')
        write(os.path.join(self.ext, "Catalogs", "Склады.xml"), catalog % ADOPTED)
        write(os.path.join(self.ext, "Catalogs", "Склады", "Forms", "ФормаСписка.xml"),
              HEAD + "<Form><Properties><Name>ФормаСписка</Name>%s</Properties></Form></MetaDataObject>" % ADOPTED)

    def checks(self, ext_list, paths=("Список.Ссылка", "Список.Расш_Поле")):
        write(os.path.join(self.ext, "Catalogs", "Склады", "Forms", "ФормаСписка", "Ext", "Form.xml"),
              form_xml([ext_list], paths, form_xml([self.old_list], ["Список.Ссылка"])))
        exts, findings, _ = borrowed.analyze([os.path.join(self.tmp, "ext")], *(
            borrowed.metadata.Dump(os.path.join(self.tmp, r), r) for r in ("old", "new")))
        return [(f["check"], f["detail"]) for f in findings]


class FormLists(_AdoptedListForm):
    """An adopted list form: the vendor turned its dynamic list into a manual query that does not select the field the
    extension shows (a case met in a hand port of a vendor list form)."""

    def test_list_name_in_another_case(self):
        # the extension's form spells the list «СПИСОК»: names are case-insensitive, the lost field is still found
        found = self.checks(list_attr("СПИСОК"), ("СПИСОК.Ссылка", "СПИСОК.Расш_Поле"))
        self.assertEqual([c for c, _ in found], ["LIST_FIELD_LOST"])
        self.assertIn("Расш_Поле", found[0][1])

    def test_form_renamed_for_deletion(self):
        # the vendor renamed the form УдалитьФормаСписка: the hint is looked for among the forms, not the attributes
        write(os.path.join(self.tmp, "new", "Catalogs", "Склады.xml"), HEAD + (
            '<Catalog uuid="o"><Properties><Name>Склады</Name></Properties><ChildObjects>'
            '<Form>УдалитьФормаСписка</Form></ChildObjects></Catalog></MetaDataObject>'))
        found = self.checks(self.old_list)
        self.assertEqual([c for c, _ in found], ["REMOVED"])
        self.assertIn("«УдалитьФормаСписка»", found[0][1])

    def test_field_of_the_extension_lost_in_the_new_query(self):
        found = self.checks(self.old_list)
        self.assertEqual([c for c, _ in found], ["LIST_FIELD_LOST"])
        self.assertIn("Расш_Поле", found[0][1])

    def test_query_text_is_case_insensitive(self):
        write(os.path.join(self.tmp, "new", "Catalogs", "Склады", "Forms", "ФормаСписка", "Ext", "Form.xml"),
              form_xml([list_attr("Список", True, "ВЫБРАТЬ Склады.Ссылка КАК Ссылка, склады.склад КАК склад "
                                                  "ИЗ Справочник.Склады КАК Склады")], ["Список.Ссылка"]))
        found = self.checks(self.old_list, ["Список.Ссылка", "Список.Склад", "Список.Расш_Поле"])
        self.assertEqual([c for c, _ in found], ["LIST_FIELD_LOST"])  # Склад is selected as «склад»
        self.assertIn("выводит расширение: Расш_Поле;", found[0][1])

    def test_standard_attributes_are_not_compared(self):
        # Список.Ref / Список.Date: the form binds standard attributes by English names, the query has Ссылка / Дата
        write(os.path.join(self.tmp, "new", "Catalogs", "Склады", "Forms", "ФормаСписка", "Ext", "Form.xml"),
              form_xml([list_attr("Список", True, "ВЫБРАТЬ Склады.Ссылка КАК Ссылка, Склады.Дата КАК Дата "
                                                  "ИЗ Справочник.Склады КАК Склады")], ["Список.Ссылка"]))
        found = self.checks(self.old_list, ["Список.Ссылка", "Список.Ref", "Список.Date", "Список.Расш_Поле"])
        self.assertEqual([c for c, _ in found], ["LIST_FIELD_LOST"])
        self.assertIn("выводит расширение: Расш_Поле;", found[0][1])

    def test_both_changed_the_list(self):
        mine = list_attr("Список", True, "ВЫБРАТЬ Склады.Ссылка, Склады.Расш_Поле ИЗ Справочник.Склады КАК Склады")
        self.assertEqual([c for c, _ in self.checks(mine)], ["LIST_CHANGED"])

    def test_both_changed_names_the_lost_fields(self):
        # the extension's own query selects Расш_Поле, the new vendor query does not: the merge must add it back;
        # Ref (a standard attribute bound by its English name) is selected by neither query and is not named
        mine = list_attr("Список", True, "ВЫБРАТЬ Склады.Ссылка, Склады.Расш_Поле ИЗ Справочник.Склады КАК Склады")
        found = self.checks(mine, ("Список.Ссылка", "Список.Расш_Поле", "Список.Ref"))
        self.assertEqual([c for c, _ in found], ["LIST_CHANGED"])
        self.assertTrue(found[0][1].endswith("не выбирает поля, которые выводит расширение: Расш_Поле"), found[0][1])

    def test_without_old(self):
        # analyze(cfg_old=None) - the lists are not compared (old is needed), the objects are still checked against new
        write(os.path.join(self.ext, "Catalogs", "Склады", "Forms", "ФормаСписка", "Ext", "Form.xml"),
              form_xml([self.old_list], ("Список.Ссылка", "Список.Расш_Поле"), form_xml([self.old_list],
                                                                                         ["Список.Ссылка"])))
        exts, findings, stats = borrowed.analyze([os.path.join(self.tmp, "ext")], None,
                                                 borrowed.metadata.Dump(os.path.join(self.tmp, "new"), "new"))
        self.assertEqual((len(exts), findings, stats["adopted"]), (1, [], 1))

    def test_both_changed_names_what_the_extension_lacks(self):
        # the extension's query lacks the alias Ссылка the vendor's new query gives the reference
        mine = list_attr("Список", True, "ВЫБРАТЬ Склады.Ссылка, Склады.Расш_Поле ИЗ Справочник.Склады КАК Склады")
        found = self.checks(mine)
        self.assertIn("в запросе расширения нет добавленного вендором: Ссылка", found[0][1])

    def ported(self, ext_list, base_list=None):
        write(os.path.join(self.ext, "Catalogs", "Склады", "Forms", "ФормаСписка", "Ext", "Form.xml"),
              form_xml([ext_list], ("Список.Ссылка", "Список.Расш_Поле"),
                       form_xml([base_list or self.old_list], ["Список.Ссылка"])))
        ported = []
        _, findings, stats = borrowed.analyze([os.path.join(self.tmp, "ext")], *(
            borrowed.metadata.Dump(os.path.join(self.tmp, r), r) for r in ("old", "new")), ported=ported)
        return [f["check"] for f in findings], ported, stats["lists_ported"]

    def test_list_already_carrying_the_vendor_change_is_ported(self):
        # two real extensions: the extension's own query was moved to the new vendor query, BaseForm stays the old snapshot
        mine = list_attr("Список", True, "ВЫБРАТЬ Склады.Ссылка КАК Ссылка, Склады.Расш_Поле КАК Расш_Поле "
                                         "ИЗ Справочник.Склады КАК Склады")
        self.assertEqual(self.ported(mine), ([], [{"ext": "Расш", "list": "Справочник.Склады.ФормаСписка.Список"}],
                                             1))

    def test_list_reborrowed_from_new_is_ported(self):
        mine = list_attr("Список", True, "ВЫБРАТЬ Склады.Ссылка ИЗ Справочник.Склады КАК Склады")
        checks, ported, count = self.ported(mine, self.new_list)
        self.assertEqual((checks, count), ([], 1))

    def test_vendor_changed_only_the_fields_the_metadata_lists(self):
        # Номенклатура in a real run: the query text is the same in old and new, only the dataset fields the platform lists
        # from the catalog's attributes differ (a renamed attribute): the vendor did not change the list
        query = "ВЫБРАТЬ Склады.Ссылка КАК Ссылка ИЗ Справочник.Склады КАК Склады"
        for root, field in (("old", "Подакцизный"), ("new", "УдалитьПодакцизный")):
            lst = list_attr("Список", True, query).replace(
                "<MainTable>", "<Field><dataPath>%s</dataPath></Field><MainTable>" % field)
            write(os.path.join(self.tmp, root, "Catalogs", "Склады", "Forms", "ФормаСписка", "Ext", "Form.xml"),
                  form_xml([lst], ["Список.Ссылка"]))
            if root == "old":
                self.old_list = lst
        mine = list_attr("Список", True, "ВЫБРАТЬ Склады.Ссылка, Склады.Расш_Поле ИЗ Справочник.Склады КАК Склады")
        self.assertEqual(self.ported(mine), ([], [], 0))

    def test_vendor_did_not_change_the_list(self):
        write(os.path.join(self.tmp, "new", "Catalogs", "Склады", "Forms", "ФормаСписка", "Ext", "Form.xml"),
              form_xml([self.old_list], ["Список.Ссылка"]))
        self.assertEqual(self.checks(self.old_list), [])


class FormBinding(_AdoptedListForm):
    """An adopted form or common form whose XML has no <ExtendedConfigurationObject> is not a finding. 0.2.5 reported it
    on the word of another tool's notes; on 17.10.2026 a real base (platform 8.3.27) showed such forms apply, also after
    a load from that XML, so 0.2.6 dropped the check."""

    def test_form_without_the_link_is_not_reported(self):
        write(os.path.join(self.tmp, "new", "Catalogs", "Склады", "Forms", "ФормаСписка.xml"),
              HEAD + '<Form uuid="f-new"><Properties><Name>ФормаСписка</Name></Properties></Form></MetaDataObject>')
        for root, uuid in (("old", "cf-old"), ("new", "cf-new")):
            write(os.path.join(self.tmp, root, "CommonForms", "Печать.xml"), HEAD + (
                '<CommonForm uuid="%s"><Properties><Name>Печать</Name></Properties></CommonForm>'
                '</MetaDataObject>' % uuid))
        write(os.path.join(self.ext, "CommonForms", "Печать.xml"), HEAD + (
            '<CommonForm uuid="cf-ext"><Properties>%s<Name>Печать</Name></Properties></CommonForm>'
            '</MetaDataObject>' % ADOPTED))
        self.assertEqual(self.checks(self.old_list, ("Список.Ссылка",)), [])


if __name__ == "__main__":
    unittest.main()
