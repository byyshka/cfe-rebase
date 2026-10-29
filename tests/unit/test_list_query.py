# -*- coding: utf-8 -*-
"""names: LIST_QUERY_FIELD_MISSING - the extension replaces the query of a vendor dynamic list (in the code of the
adopted form or in its Form.xml) without a field the new vendor form needs. Small hand-written dumps."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from cfe_rebase import names, plan, selection
from tests.unit.test_borrowed import HEAD, obj, write

FORM_HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n<Form xmlns="http://v8.1c.ru/8.3/xcf/logform" ' \
            'xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" ' \
            'version="2.20">\n'
FORMS = ("Documents", "Заказ", "Forms")


def list_form(fields, use_always=(), query=None, events=(), lists=("Список",)):
    """A list form: a table bound to each list, a label per DataPath field of the first list, the list attributes."""
    out = ["<ChildItems>"]
    for n, name in enumerate(lists):
        ev = "".join('<Event name="%s">%s</Event>' % e for e in events) if n == 0 else ""
        out.append('<Table name="%s" id="%d"><DataPath>%s</DataPath>%s<ChildItems>' % (
            name, 10 + n, name, "<Events>%s</Events>" % ev if ev else ""))
        if n == 0:
            out.extend('<LabelField name="%s%s" id="%d"><DataPath>%s.%s</DataPath></LabelField>' % (
                name, f, 100 + k, name, f) for k, f in enumerate(fields))
        out.append("</ChildItems></Table>")
    out.append("</ChildItems><Attributes>")
    for name in lists:
        ua = "".join("<Field>%s.%s</Field>" % (name, f) for f in use_always)
        settings = "<ManualQuery>%s</ManualQuery>%s<MainTable>Document.Заказ</MainTable>" % (
            "true" if query else "false", "<QueryText>%s</QueryText>" % query if query else "")
        out.append('<Attribute name="%s" id="%d"><Type><v8:Type>cfg:DynamicList</v8:Type></Type>%s'
                   '<Settings xsi:type="DynamicList">%s</Settings></Attribute>' % (
                       name, 1 + lists.index(name), "<UseAlways>%s</UseAlways>" % ua if ua else "", settings))
    out.append("</Attributes>")
    return "".join(out)


VENDOR_QUERY = ("ВЫБРАТЬ Т.Ссылка, Т.Номер, Т.Состояние КАК Состояние, Т.Сумма, Т.Цвет, Т.Старое, Т.Новое, "
                "Т.Товары.* ИЗ Документ.Заказ КАК Т {ЛЕВОЕ СОЕДИНЕНИЕ РегистрСведений.Р КАК Р ПО Р.Т = Т.Ссылка}")
VENDOR_NEW_MODULE = """&НаКлиенте
Процедура СписокВыбор(Элемент, ВыбраннаяСтрока, Поле, СтандартнаяОбработка)
	Сумма = Элемент.ТекущиеДанные.Сумма;
КонецПроцедуры

&НаКлиенте
Процедура Обновить()
	Данные = Элементы.Список.ТекущиеДанные;
	Если Данные.Цвет = 1 Тогда
		Данные.Свойство("Чужое");
	КонецЕсли;
КонецПроцедуры
"""
EXT_CODE_MODULE = """&НаСервере
Процедура Расш_ПриСозданииНаСервереПеред(Отказ, СтандартнаяОбработка)
	Текст = "ВЫБРАТЬ
	|	Т.Ссылка КАК Ссылка,
	|	Т.Номер КАК Номер,
	|	Т.Старое КАК Старое";
	Текст = Текст + "
	|ИЗ
	|	Документ.Заказ КАК Т";
	Если Истина Тогда
		Список.ТекстЗапроса = Текст;
	КонецЕсли;
КонецПроцедуры

&НаСервере
Процедура Расш_Поправить()
	Список.ТекстЗапроса = СтрЗаменить(Список.ТекстЗапроса, "Т.Цвет", "1");
	ЭтотОбъект.Список.ТекстЗапроса = СтрЗаменить("ВЫБРАТЬ Т.Ссылка, Т.Номер, Т.Состояние, Т.Сумма, Т.Цвет, Т.Новое,
	|Т.Старое ИЗ Документ.Заказ КАК Т", "Т.Новое", "Т.Новое КАК Новое");
	// Список.ТекстЗапроса = "ВЫБРАТЬ 1 КАК А";
КонецПроцедуры
"""


class ListQuery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_list_query_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        t = self.tmp
        # ФормаКод: the extension's code replaces the query; old had a plain list (no query), new a manual one with
        # Состояние (an item), Сумма (read by the vendor handler of the table) and Цвет (read through a variable);
        # Ref is the vendor's English name of Ссылка, DefaultPicture the platform's, Вычисляемое is in no vendor query
        new_code = list_form(["Ref", "Номер", "Состояние", "Старое", "DefaultPicture", "Вычисляемое"], ["Ссылка"],
                             VENDOR_QUERY, [("Selection", "СписокВыбор")])
        old_code = list_form(["Ref", "Номер", "Старое"], ["Ссылка"])
        # ФормаXML: the extension's Form.xml has its own query of the list; ФормаТаже: the same as in BaseForm
        new_xml = list_form(["Старое"], ["Новое"], VENDOR_QUERY)
        old_xml = list_form(["Старое"], (), "ВЫБРАТЬ Т.Ссылка, Т.Старое ИЗ Документ.Заказ КАК Т")
        vendor = {"ФормаКод": (old_code, new_code), "ФормаXML": (old_xml, new_xml), "ФормаТаже": (old_xml, new_xml)}
        for k, root in enumerate(("old", "new")):
            d = os.path.join(t, root)
            write(os.path.join(d, "Configuration.xml"), HEAD + '<Configuration uuid="c-%s"><Properties><Name>Т'
                  '</Name></Properties></Configuration></MetaDataObject>' % root)
            write(os.path.join(d, "Documents", "Заказ.xml"), obj("Document", "Заказ", []))
            for form, bodies in vendor.items():
                write(os.path.join(d, *FORMS, form, "Ext", "Form.xml"), FORM_HEAD + bodies[k] + "</Form>")
            write(os.path.join(d, *FORMS, "ФормаКод", "Ext", "Form", "Module.bsl"),
                  VENDOR_NEW_MODULE if root == "new" else "")
        ext = os.path.join(t, "ext", "Расш")
        write(os.path.join(ext, "Configuration.xml"), HEAD + '<Configuration uuid="c-ext"><Properties><Name>Расш'
              '</Name><ConfigurationExtensionPurpose>Customization</ConfigurationExtensionPurpose></Properties>'
              '</Configuration></MetaDataObject>')
        write(os.path.join(ext, "Documents", "Заказ.xml"), obj("Document", "Заказ", [], True))
        # like the real case: the adopted form does not adopt the list attribute, the code replaces the query
        write(os.path.join(ext, *FORMS, "ФормаКод", "Ext", "Form.xml"), FORM_HEAD + '<Attributes/><BaseForm '
              'version="2.20"><Attributes/></BaseForm></Form>')
        write(os.path.join(ext, *FORMS, "ФормаКод", "Ext", "Form", "Module.bsl"), EXT_CODE_MODULE)
        mine = list_form(["Старое"], (), "ВЫБРАТЬ Т.Ссылка, Т.Старое ИЗ Документ.Заказ КАК Т ГДЕ Т.Проведен")
        write(os.path.join(ext, *FORMS, "ФормаXML", "Ext", "Form.xml"), FORM_HEAD + mine + '<BaseForm version='
              '"2.20">' + old_xml + "</BaseForm></Form>")
        write(os.path.join(ext, *FORMS, "ФормаТаже", "Ext", "Form.xml"), FORM_HEAD + old_xml + '<BaseForm version='
              '"2.20">' + old_xml + "</BaseForm></Form>")
        self.out = os.path.join(t, "out")
        with contextlib.redirect_stdout(io.StringIO()):
            names.main(["--ext-root", os.path.join(t, "ext"), "--config-old", os.path.join(t, "old"),
                        "--config-new", os.path.join(t, "new"), "--out", self.out])
        with open(os.path.join(self.out, "names.json"), encoding="utf-8") as fh:
            self.data = json.load(fh)
        self.rows = [f for f in self.data["findings"] if f["check"] == "LIST_QUERY_FIELD_MISSING"]

    def test_code_assigned_query_missing_fields(self):
        got = {(f["name"], f["level"], f["line"], f["source"]) for f in self.rows if f["form"].endswith("ФормаКод")}
        # Состояние - an item new in new; Сумма - the vendor's table handler; Цвет - the vendor's variable of the
        # current row. Ссылка (bound as Ref), Номер, Старое are selected; DefaultPicture is the platform's;
        # Вычисляемое is in no vendor query; the second method edits the vendor's text (not judged) and assigns a
        # full text; the commented line is not code
        self.assertEqual(got, {("Список.Состояние", "error", 11, "код"), ("Список.Сумма", "error", 11, "код"),
                               ("Список.Цвет", "error", 11, "код")})
        row = next(f for f in self.rows if f["name"] == "Список.Сумма")
        self.assertEqual(row["module"], "Documents/Заказ/Forms/ФормаКод/Ext/Form/Module.bsl")
        self.assertEqual(row["methods"], ["Расш_ПриСозданииНаСервереПеред"])
        self.assertEqual(row["needed_by"], ["код вендора: СписокВыбор, стр. 3"])
        self.assertTrue(row["new_in_new"])
        self.assertIn("добавить в запрос расширения поле Сумма (у вендора new: Т.Сумма КАК Сумма", row["detail"])
        cvet = next(f for f in self.rows if f["name"] == "Список.Цвет")
        self.assertEqual(cvet["needed_by"], ["код вендора: Обновить, стр. 9"])
        self.assertEqual(self.data["stats"]["list_queries_unjudged"], 1)

    def test_form_xml_replaced_query(self):
        got = {(f["name"], f["level"], f["new_in_new"]) for f in self.rows if f["form"].endswith("ФормаXML")}
        # Новое - UseAlways of new only; Старое is selected by the extension's query
        self.assertEqual(got, {("Список.Новое", "error", True)})
        row = next(f for f in self.rows if f["form"].endswith("ФормаXML"))
        self.assertEqual(row["module"], "Documents/Заказ/Forms/ФормаXML/Ext/Form.xml")
        self.assertEqual(row["needed_by"], ["UseAlways"])
        self.assertEqual(row["methods"], [])
        # the query in BaseForm unchanged: the vendor's new query applies, nothing to report
        self.assertFalse([f for f in self.rows if f["form"].endswith("ФормаТаже")])

    def test_field_needed_in_old_too_is_a_risk(self):
        # the old vendor form already needed Старое: the extension's query without it lost the column in old too
        lst = list_form(["Старое"], ["Новое"], "ВЫБРАТЬ Т.Ссылка, Т.Новое ИЗ Документ.Заказ КАК Т")
        path = os.path.join(self.tmp, "ext", "Расш", *FORMS, "ФормаXML", "Ext", "Form.xml")
        base = list_form(["Старое"], (), "ВЫБРАТЬ Т.Ссылка, Т.Старое ИЗ Документ.Заказ КАК Т")
        write(path, FORM_HEAD + lst + '<BaseForm version="2.20">' + base + "</BaseForm></Form>")
        with contextlib.redirect_stdout(io.StringIO()):
            names.main(["--ext-root", os.path.join(self.tmp, "ext"), "--config-old", os.path.join(self.tmp, "old"),
                        "--config-new", os.path.join(self.tmp, "new"), "--out", self.out])
        with open(os.path.join(self.out, "names.json"), encoding="utf-8") as fh:
            rows = [f for f in json.load(fh)["findings"] if f["form"].endswith("ФормаXML")]
        self.assertEqual([(f["name"], f["level"], f["new_in_new"]) for f in rows], [("Список.Старое", "risk", False)])
        self.assertIn("колонки не было и до обновления", rows[0]["detail"])

    def rerun_xml(self, mine, base):
        path = os.path.join(self.tmp, "ext", "Расш", *FORMS, "ФормаXML", "Ext", "Form.xml")
        write(path, FORM_HEAD + mine + '<BaseForm version="2.20">' + base + "</BaseForm></Form>")
        with contextlib.redirect_stdout(io.StringIO()):
            names.main(["--ext-root", os.path.join(self.tmp, "ext"), "--config-old", os.path.join(self.tmp, "old"),
                        "--config-new", os.path.join(self.tmp, "new"), "--out", self.out])
        with open(os.path.join(self.out, "names.json"), encoding="utf-8") as fh:
            return [f for f in json.load(fh)["findings"] if f["form"].endswith("ФормаXML")]

    def test_use_always_not_loaded(self):
        query = "ВЫБРАТЬ Т.Ссылка, Т.Старое, Т.Новое ИЗ Документ.Заказ КАК Т"
        base = list_form(["Старое"], (), "ВЫБРАТЬ Т.Ссылка, Т.Старое ИЗ Документ.Заказ КАК Т")
        # the extension's UseAlways (Старое) differs from BaseForm's (none) and lacks Новое of the new vendor
        # UseAlways; the query selects it and no item of the extension shows it
        rows = self.rerun_xml(list_form(["Старое"], ["Старое"], query), base)
        self.assertEqual([(f["check"], f["name"], f["level"], f["new_in_new"], f["needed_by"]) for f in rows],
                         [("LIST_FIELD_NOT_LOADED", "Список.Новое", "risk", True, ["UseAlways"])])
        self.assertIn("статикой не доказано", rows[0]["detail"])
        self.assertIn("добавить Список.Новое в UseAlways", rows[0]["detail"])
        # a visible item bound to it loads it
        self.assertEqual(self.rerun_xml(list_form(["Старое", "Новое"], ["Старое"], query), base), [])
        # a hidden one does not
        hidden = list_form(["Старое", "Новое"], ["Старое"], query).replace(
            "<DataPath>Список.Новое</DataPath>", "<DataPath>Список.Новое</DataPath><Visible>false</Visible>")
        self.assertEqual([f["check"] for f in self.rerun_xml(hidden, base)], ["LIST_FIELD_NOT_LOADED"])
        # the extension keeps BaseForm's UseAlways: the vendor's new one applies, whatever the platform does
        self.assertEqual(self.rerun_xml(list_form(["Старое"], ["Старое"], query),
                                        list_form(["Старое"], ["Старое"], "ВЫБРАТЬ Т.Ссылка ИЗ Документ.Заказ КАК Т")),
                         [])
        # in the plan: a question, not a fix
        self.rerun_xml(list_form(["Старое"], ["Старое"], query), base)
        with open(os.path.join(self.out, "names.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        p = plan.build({}, {}, {}, {}, {}, names=data)
        self.assertEqual([x["module_title"] for x in p["items"] if x["group"] == "CLARIFY"],
                         ["Документ.Заказ.Форма.ФормаXML"])

    def test_selection_and_plan(self):
        flt = selection.Filter(flag=["LIST_QUERY_FIELD_MISSING"], severity="error")
        picked, summary = selection.select("names", self.data["findings"], flt)
        self.assertEqual(summary["matched"], 4)
        self.assertTrue(all(e["key"].endswith("|list|%s|LIST_QUERY_FIELD_MISSING" % e["row"]["name"])
                            for e in picked))
        p = plan.build({}, {}, {}, {}, {}, names=self.data)
        must = [x for x in p["items"] if x["group"] == "MUST_FIX"]
        self.assertEqual({(x["module_title"], x["method"]) for x in must}, {
            ("Документ.Заказ.Форма.ФормаКод", "Расш_ПриСозданииНаСервереПеред"),
            ("Документ.Заказ.Форма.ФормаXML", None)})
        self.assertTrue(all(w.startswith("динамический список Список") for x in must for w in x["what"]))


class Parts(unittest.TestCase):
    def test_selected(self):
        self.assertEqual(sorted(names.selected("ВЫБРАТЬ Т.А, Т.Б КАК В, 1 КАК Г, Т.ТЧ.* ИЗ Документ.Х КАК Т")),
                         ["а", "в", "г"])
        self.assertIsNone(names.selected("ВЫБРАТЬ * ИЗ Документ.Х КАК Т"))
        self.assertIsNone(names.selected("ВЫБРАТЬ Т.* ИЗ Документ.Х КАК Т"))
        self.assertIsNone(names.selected("Т.А"))
        # «.*» in a DCS clause of a virtual table's parameters is not the select list
        self.assertEqual(sorted(names.selected("ВЫБРАТЬ Ц.Цена КАК Цена ИЗ РегистрСведений.Ц.СрезПоследних(, "
                                               "{(Номенклатура).* КАК Номенклатура}) КАК Ц")), ["цена"])

    def test_resolve_text(self):
        from cfe_rebase import bsl
        toks = bsl.tokenize('"А" + Х + СтрЗаменить(Х, "б", "в")')
        self.assertEqual(names.resolve_text(toks, {"х": "аб"}), "Аабав")
        self.assertIsNone(names.resolve_text(toks, {"х": None}))
        self.assertIsNone(names.resolve_text(bsl.tokenize("Список.ТекстЗапроса + \"А\""), {}))
        self.assertIsNone(names.resolve_text(bsl.tokenize('ПолучитьТекст() + "А"'), {}))


if __name__ == "__main__":
    unittest.main()
