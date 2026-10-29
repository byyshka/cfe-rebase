# -*- coding: utf-8 -*-
"""names: names of vendor forms read by the code of adopted forms, on small hand-written dumps."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from cfe_rebase import names, selection
from tests.unit.test_borrowed import HEAD, child, obj, tabular, write

FORM_HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n<Form xmlns="http://v8.1c.ru/8.3/xcf/logform" ' \
            'xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config"' \
            ' version="2.20">\n'
FORM_REL = ("Documents", "Заказ", "Forms", "ФормаДокумента", "Ext")


def form_body(items, attrs, params=()):
    """Items (nested under one group, each with a tooltip), attributes (Объект typed by the document) and parameters."""
    out = ['<AutoCommandBar name="ФормаКоманднаяПанель" id="-1"/>', "<ChildItems>",
           '<UsualGroup name="Шапка" id="1"><ChildItems>']
    for n, it in enumerate(items, 2):
        out.append('<InputField name="%s" id="%d"><ExtendedTooltip name="%sПодсказка" id="%d"/>'
                   '<Events><Event name="OnChange">%sПриИзменении</Event></Events></InputField>' % (
                       it, n * 10, it, n * 10 + 1, it))
    out.append("</ChildItems></UsualGroup></ChildItems><Attributes>")
    for a in attrs:
        typ = "cfg:DocumentObject.Заказ" if a == "Объект" else "xs:boolean"
        out.append('<Attribute name="%s" id="%d"><Type><v8:Type>%s</v8:Type></Type>%s</Attribute>' % (
            a, 100 + len(out), typ, "<MainAttribute>true</MainAttribute>" if a == "Объект" else ""))
    out.append("</Attributes>")
    if params:
        out.append("<Parameters>%s</Parameters>" % "".join('<Parameter name="%s"><Type/></Parameter>' % p
                                                             for p in params))
    return "".join(out)


EXT_MODULE = """&НаКлиенте
Перем МояПеременная;

&НаСервере
Процедура Расш_ПриСозданииНаСервереПосле(Отказ, СтандартнаяОбработка)
	Элементы.Есть.Видимость = Истина;
	Элементы.Убран.Видимость = Ложь;
	Элементы.Создаваемый.Видимость = Ложь;
	ЭтаФорма.Элементы.МояКнопка.Видимость = Истина;
	Элементы.Нигде.Доступность = Ложь;
	Элементы.ИзСнимка.Доступность = Ложь;
	Элементы.Найти("ЧерезСтроку");
	Сумма = Объект.Сумма + Объект.Скидка + Объект.Старое + Объект.МоёПоле;
	Если Объект.Ссылка.Пустая() И Параметры.Режим = 1 И Параметры.Ключ = Неопределено Тогда
		ЭтотОбъект.Флаг = Истина;
		ЭтотОбъект.Заголовок = "Элементы.ВСтроке";
	КонецЕсли;
	// Элементы.ВКомментарии.Видимость = Ложь;
	КэшСтарый = 1;
	МояПеременная = 2;
	МойРеквизит = Истина;
	Форма.Элементы.ЧужаяФорма.Видимость = Ложь;
#Удаление
	Элементы.Удалённый.Видимость = Ложь;
#КонецУдаления
КонецПроцедуры

&НаСервере
Процедура Расш_Заполнить(Флаг, Объект)
	Перем КэшСтарый;
	КэшСтарый = Флаг;
	Объект.Чужое = 1;
КонецПроцедуры
"""


class Names(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_names_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        t = self.tmp
        doc_old = [child("Attribute", "Сумма"), child("Attribute", "Скидка"),
                   '<Attribute uuid="u-ren"><Properties><Name>Старое</Name></Properties></Attribute>']
        doc_new = [child("Attribute", "Сумма"), child("Attribute", "Новое"),
                   '<Attribute uuid="u-ren"><Properties><Name>Новое2</Name></Properties></Attribute>']
        forms = {"old": form_body(["Есть", "Убран", "Создаваемый"], ["Объект", "Флаг"], ["Режим"]),
                 "new": form_body(["Есть"], ["Объект"])}
        modules = {"old": "Перем КэшСтарый;\n\nПроцедура ПриСозданииНаСервере(Отказ, СтандартнаяОбработка)\n"
                          "\tФлаг = Истина;\nКонецПроцедуры\n",
                   "new": "Процедура ПриСозданииНаСервере(Отказ, СтандартнаяОбработка)\n"
                          "\tЭлементы.Создаваемый.Видимость = Ложь;\nКонецПроцедуры\n"}
        for root, children in (("old", doc_old), ("new", doc_new)):
            write(os.path.join(t, root, "Configuration.xml"), HEAD + '<Configuration uuid="c-%s"><Properties><Name>'
                  'Типовая</Name></Properties></Configuration></MetaDataObject>' % root)
            write(os.path.join(t, root, "Documents", "Заказ.xml"), obj("Document", "Заказ", children))
            write(os.path.join(t, root, *FORM_REL, "Form.xml"), FORM_HEAD + forms[root] + "</Form>")
            write(os.path.join(t, root, *FORM_REL, "Form", "Module.bsl"), modules[root])
        ext = os.path.join(t, "ext", "Расш")
        write(os.path.join(ext, "Configuration.xml"), HEAD + '<Configuration uuid="c-ext"><Properties><Name>Расш</Name>'
              '<ConfigurationExtensionPurpose>Customization</ConfigurationExtensionPurpose></Properties>'
              '</Configuration></MetaDataObject>')
        write(os.path.join(ext, "Documents", "Заказ.xml"), obj("Document", "Заказ", [
            child("Attribute", "Сумма", adopted=True), child("Attribute", "МоёПоле")], True))
        snapshot = ["Есть", "Убран", "Создаваемый", "ИзСнимка"]
        main = form_body(snapshot + ["МояКнопка"], ["Объект", "МойРеквизит"])
        write(os.path.join(ext, *FORM_REL, "Form.xml"), FORM_HEAD + main + '<BaseForm version="2.20">' +
              form_body(snapshot, ["Объект"]) + "</BaseForm></Form>")
        write(os.path.join(ext, *FORM_REL, "Form", "Module.bsl"), EXT_MODULE)
        # an own form of the extension (no BaseForm) is not checked
        write(os.path.join(ext, "Documents", "Заказ", "Forms", "Своя", "Ext", "Form.xml"),
              FORM_HEAD + form_body([], []) + "</Form>")
        write(os.path.join(ext, "Documents", "Заказ", "Forms", "Своя", "Ext", "Form", "Module.bsl"),
              "Процедура А()\n\tЭлементы.Чего.Видимость = Ложь;\nКонецПроцедуры\n")
        self.out = os.path.join(t, "out")

    def run_tool(self):
        with contextlib.redirect_stdout(io.StringIO()):
            names.main(["--ext-root", os.path.join(self.tmp, "ext"), "--config-old", os.path.join(self.tmp, "old"),
                        "--config-new", os.path.join(self.tmp, "new"), "--out", self.out])
        with open(os.path.join(self.out, "names.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_findings(self):
        data = self.run_tool()
        got = {(f["how"], f["name"]): (f["check"], f["level"]) for f in data["findings"]}
        self.assertEqual(got, {
            ("items", "Убран"): ("GONE_IN_NEW", "error"),
            ("items", "Создаваемый"): ("GONE_IN_NEW", "risk"),  # the new form module still names it
            ("items", "Нигде"): ("NOT_IN_VENDOR", "info"),
            ("items", "ИзСнимка"): ("NOT_IN_VENDOR", "risk"),  # in BaseForm only: dropped before old
            ("object", "Скидка"): ("GONE_IN_NEW", "error"),
            ("object", "Старое"): ("GONE_IN_NEW", "error"),
            ("parameter", "Режим"): ("GONE_IN_NEW", "error"),
            ("attribute", "Флаг"): ("GONE_IN_NEW", "error"),
            ("attribute", "КэшСтарый"): ("GONE_IN_NEW", "error"),
        })
        by = {(f["how"], f["name"]): f for f in data["findings"]}
        self.assertIn("Новое2 (uuid тот же)", by[("object", "Старое")]["detail"])
        self.assertIn("локальная переменная", by[("attribute", "КэшСтарый")]["detail"])
        self.assertNotIn("локальная переменная", by[("attribute", "Флаг")]["detail"])
        cache = by[("attribute", "КэшСтарый")]
        self.assertEqual((cache["lines"], cache["methods"]), ([19], ["Расш_ПриСозданииНаСервереПосле"]))
        self.assertEqual(by[("items", "Убран")]["form"], "Документ.Заказ.Форма.ФормаДокумента")
        self.assertEqual(by[("items", "Убран")]["module"], "Documents/Заказ/Forms/ФормаДокумента/Ext/Form/Module.bsl")
        self.assertEqual(data["stats"]["forms"], 1)

    def test_selection_levels(self):
        data = self.run_tool()
        levels = {selection.level("names", f) for f in data["findings"]}
        self.assertEqual(levels, {"error", "risk", "info"})
        flt = selection.Filter(flag=["GONE_IN_NEW"], severity="error")
        picked, summary = selection.select("names", data["findings"], flt)
        self.assertEqual(summary["matched"], 6)
        self.assertTrue(all(e["key"].startswith("Расш|Documents/Заказ/") for e in picked))

    def test_form_missing_in_new_is_skipped(self):
        os.remove(os.path.join(self.tmp, "new", *FORM_REL, "Form.xml"))
        data = self.run_tool()
        self.assertEqual(data["findings"], [])
        self.assertEqual(data["stats"]["forms_missing_new"], 1)

    def test_unreadable_vendor_form(self):
        write(os.path.join(self.tmp, "new", *FORM_REL, "Form.xml"), FORM_HEAD + "<ChildItems>")
        data = self.run_tool()
        self.assertEqual([(f["check"], f["level"]) for f in data["findings"]], [("XML_UNREADABLE", "risk")])

    def test_references_skip_strings_comments_calls_and_other_forms(self):
        from cfe_rebase import bsl
        toks = bsl.tokenize('// Элементы.А\nX = "Элементы.Б"; Элементы.В(1); Форма.Элементы.Г = 1;\n'
                            'ЭтаФорма.Элементы.Д = 1; ЭтотОбъект.Е = 1; ЭтотОбъект.Ж(); Объект.З.И = 1;')
        self.assertEqual([(h, n) for h, n, _, _ in names.references(toks)],
                         [("items", "Д"), ("attribute", "Е"), ("object", "З")])


# the object module of an adopted document: a real case 11.5.17 -> 11.5.27, РеализацияТоваровУслуг adopted with Основание
# only; the Designer's module check said «Переменная не определена (ОснованиеНомер)» at 297 and (ОснованиеДата) at 304
OBJ_MODULE = """Перем Организация;

&После("ОбработкаПроверкиЗаполнения")
Процедура Расш_ОбработкаПроверкиЗаполнения(Отказ, ПроверяемыеРеквизиты)
	ШаблонСообщения = НСтр("ru = 'Не заполнено поле ""%1"" в реквизитах печати (вкладка ""Дополнительно"").'");
	Если Не ЗначениеЗаполнено(Основание) Тогда
		ОбщегоНазначения.СообщитьПользователю(ШаблонСообщения, ЭтотОбъект, "Основание", , Отказ);
	КонецЕсли;
	Если Не ЗначениеЗаполнено(ОснованиеНомер) Тогда
		ОбщегоНазначения.СообщитьПользователю(ШаблонСообщения, ЭтотОбъект, "ОснованиеНомер", , Отказ);
	КонецЕсли;
	Если Не ЗначениеЗаполнено(ОснованиеДата) Тогда
		ОбщегоНазначения.СообщитьПользователю(ШаблонСообщения, ЭтотОбъект, "ОснованиеДата", , Отказ);
	КонецЕсли;
	// Комментарий = Склад;
	Если ЭтотОбъект.ОснованиеНомер = "" И Объект.Партнер = Неопределено Тогда
		Возврат;
	КонецЕсли;
#Удаление
	Сумма = Комментарий;
#КонецУдаления
КонецПроцедуры

&Перед("ОбработкаЗаполнения")
Процедура Расш_ОбработкаЗаполнения(ДанныеЗаполнения, Валюта, СтандартнаяОбработка)
	Автор = ДанныеЗаполнения.Автор;
	Если Не ЗначениеЗаполнено(Автор) Тогда
		Автор = Неопределено;
	КонецЕсли;
	Если Не ЗначениеЗаполнено(Склад) Тогда
		Склад = ДанныеЗаполнения.Склад;
	КонецЕсли;
	Для Каждого Контрагент Из ДанныеЗаполнения.Контрагенты Цикл
		Партнер = Контрагент;
	КонецЦикла;
	Сумма = Валюта + Организация + Расходы.Итог("Сумма") + Новое;
КонецПроцедуры
"""


class ObjectModuleFields(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_names_obj_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        t = self.tmp
        common = ["Основание", "ОснованиеНомер", "ОснованиеДата", "Автор", "Склад", "Контрагент", "Партнер",
                  "Комментарий", "Валюта", "Организация"]
        vendor = {"old": [child("Attribute", n) for n in common],
                  "new": [child("Attribute", n) for n in common + ["Новое"]] + [tabular("Расходы", [])]}
        for root, children in vendor.items():
            write(os.path.join(t, root, "Configuration.xml"), HEAD + '<Configuration uuid="c-%s"><Properties><Name>'
                  'Типовая</Name></Properties></Configuration></MetaDataObject>' % root)
            write(os.path.join(t, root, "Documents", "Реализация.xml"), obj("Document", "Реализация", children))
        ext = os.path.join(t, "ext", "Расш")
        write(os.path.join(ext, "Configuration.xml"), HEAD + '<Configuration uuid="c-ext"><Properties><Name>Расш</Name>'
              '<ConfigurationExtensionPurpose>Customization</ConfigurationExtensionPurpose></Properties>'
              '</Configuration></MetaDataObject>')
        write(os.path.join(ext, "Documents", "Реализация.xml"), obj("Document", "Реализация", [
            child("Attribute", "Основание", adopted=True)], True))
        write(os.path.join(ext, "Documents", "Реализация", "Ext", "ObjectModule.bsl"), OBJ_MODULE)
        # a manager module has no object attributes in its context: not read
        write(os.path.join(ext, "Documents", "Реализация", "Ext", "ManagerModule.bsl"),
              "Функция Расш_Склад() Экспорт\n\tВозврат Склад;\nКонецФункции\n")
        self.out = os.path.join(t, "out")

    def run_tool(self):
        with contextlib.redirect_stdout(io.StringIO()):
            names.main(["--ext-root", os.path.join(self.tmp, "ext"), "--config-old", os.path.join(self.tmp, "old"),
                        "--config-new", os.path.join(self.tmp, "new"), "--out", self.out])
        with open(os.path.join(self.out, "names.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_reads_of_fields_not_adopted(self):
        data = self.run_tool()
        got = {f["name"]: (f["level"], f["lines"], f["methods"]) for f in data["findings"]}
        self.assertEqual(got, {
            "ОснованиеНомер": ("risk", [9], ["Расш_ОбработкаПроверкиЗаполнения"]),
            "ОснованиеДата": ("risk", [12], ["Расш_ОбработкаПроверкиЗаполнения"]),
            # read before its first assignment in the method: not yet a local variable for the Designer
            "Склад": ("risk", [30], ["Расш_ОбработкаЗаполнения"]),
            # the vendor added these in new: the reference came with the port
            "Расходы": ("error", [36], ["Расш_ОбработкаЗаполнения"]),
            "Новое": ("error", [36], ["Расш_ОбработкаЗаполнения"]),
        })
        f = next(f for f in data["findings"] if f["name"] == "ОснованиеНомер")
        self.assertEqual((f["check"], f["how"], f["form"], f["module"]), (
            "FIELD_NOT_ADOPTED", "module", "Документ.Реализация.МодульОбъекта",
            "Documents/Реализация/Ext/ObjectModule.bsl"))
        self.assertIn("Переменная не определена (ОснованиеНомер)", f["detail"])
        self.assertIn("было и в old", f["detail"])
        self.assertIn("ЭтотОбъект.ОснованиеНомер", f["detail"])
        self.assertIn("пришло с переносом", next(f for f in data["findings"] if f["name"] == "Новое")["detail"])
        self.assertEqual(data["stats"]["object_modules"], 1)
        self.assertEqual(data["stats"]["object_module_this_unadopted"], 1)

    def test_adopting_the_field_clears_it(self):
        write(os.path.join(self.tmp, "ext", "Расш", "Documents", "Реализация.xml"), obj("Document", "Реализация", [
            child("Attribute", n, adopted=True) for n in ("Основание", "ОснованиеНомер", "ОснованиеДата", "Склад",
                                                           "Новое")] + [tabular("Расходы", [], True)], True))
        self.assertEqual(self.run_tool()["findings"], [])

    def test_in_plan_and_selection(self):
        from cfe_rebase import plan
        data = self.run_tool()
        p = plan.build({"methods": []}, {"cases": []}, {"registry": [], "dead_code": {}}, {"findings": []},
                       {"findings": []}, None, None, data)
        self.assertEqual({(x["method"], x["group"]) for x in p["items"]},
                         {("Расш_ОбработкаПроверкиЗаполнения", "REVIEW"), ("Расш_ОбработкаЗаполнения", "MUST_FIX")})
        picked, _ = selection.select("names", data["findings"],
                                     selection.Filter(flag=["FIELD_NOT_ADOPTED"], severity="error"))
        self.assertEqual(sorted(e["key"].split("|")[3] for e in picked), ["Новое", "Расходы"])

    def test_unassigned_reads(self):
        from cfe_rebase import bsl
        mod = bsl.Module("Процедура П(Парам)\n\tЕсли А = 1 Тогда Б = 2; КонецЕсли;\n\tВ = Б + Г.Сумма + Парам;\n"
                         "\tДля Д = 1 По 3 Цикл Е = Д; КонецЦикла;\n\tЖ(1); Новый З;\nКонецПроцедуры\n")
        self.assertEqual([(n, ln) for n, ln, _ in names.unassigned_reads(mod)], [("А", 2), ("Г", 3)])


if __name__ == "__main__":
    unittest.main()
