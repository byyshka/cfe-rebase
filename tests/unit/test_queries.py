# -*- coding: utf-8 -*-
"""qparse and queries.py on a synthetic pair of dumps: tables, fields, virtual tables, values, literals."""
import os
import shutil
import sys
import tempfile
import unittest

from cfe_rebase import metadata, qparse, queries

HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" '
        'xmlns:v8="http://v8.1c.ru/8.1/data/core">')


def child(tag, name, types=(), adopted=False):
    own = "<ObjectBelonging>Adopted</ObjectBelonging>" if adopted else ""
    typ = "<Type>%s</Type>" % "".join("<v8:Type>%s</v8:Type>" % t for t in types) if types else ""
    return "<%s><Properties>%s<Name>%s</Name>%s</Properties></%s>" % (tag, own, name, typ, tag)


def obj(root, folder, tag, name, children="", adopted=False, props=""):
    os.makedirs(os.path.join(root, folder), exist_ok=True)
    own = "<ObjectBelonging>Adopted</ObjectBelonging>" if adopted else ""
    with open(os.path.join(root, folder, name + ".xml"), "w", encoding="utf-8") as fh:
        fh.write('%s<%s><Properties>%s<Name>%s</Name>%s</Properties><ChildObjects>%s</ChildObjects></%s>'
                 '</MetaDataObject>' % (HEAD, tag, own, name, props, children, tag))


def ts(name, columns, adopted=False):
    own = "<ObjectBelonging>Adopted</ObjectBelonging>" if adopted else ""
    return ("<TabularSection><Properties>%s<Name>%s</Name></Properties><ChildObjects>%s</ChildObjects>"
            "</TabularSection>" % (own, name, columns))


def config(root, name, extension=False):
    os.makedirs(root, exist_ok=True)
    purpose = "<ConfigurationExtensionPurpose>AddOn</ConfigurationExtensionPurpose>" if extension else ""
    with open(os.path.join(root, "Configuration.xml"), "w", encoding="utf-8") as fh:
        fh.write("%s<Configuration><Properties><Name>%s</Name>%s<Version>1</Version></Properties>"
                 "</Configuration></MetaDataObject>" % (HEAD, name, purpose))


def release(root, with_size):
    config(root, "Конфигурация")
    size = child("Attribute", "Размер") if with_size else ""
    share = child("Attribute", "Процент") if with_size else ""
    archive = ts("Архив", child("Attribute", "Поле")) + "<Form>СтараяФорма</Form>" if with_size else ""
    obj(root, "Catalogs", "Catalog", "Товары",
        child("Attribute", "Цвет", ["cfg:CatalogRef.Цвета"]) + size +
        ts("Состав", child("Attribute", "Доля") + share) + archive + "<Form>ФормаЭлемента</Form>")
    obj(root, "Catalogs", "Catalog", "Цвета", child("Attribute", "Оттенок"))
    obj(root, "Documents", "Document", "Заказ")
    obj(root, "AccumulationRegisters", "AccumulationRegister", "Запасы",
        child("Dimension", "Товар", ["cfg:CatalogRef.Товары"]) + child("Resource", "Количество"),
        props="<RegisterType>Balance</RegisterType>")
    obj(root, "AccumulationRegisters", "AccumulationRegister", "Продажи",
        child("Dimension", "Товар", ["cfg:CatalogRef.Товары"]) + child("Resource", "Сумма"),
        props="<RegisterType>Turnovers</RegisterType>")
    obj(root, "InformationRegisters", "InformationRegister", "Цены",
        child("Dimension", "Товар") + child("Resource", "Цена") + child("Attribute", "Адрес"),
        props="<InformationRegisterPeriodicity>Day</InformationRegisterPeriodicity>"
              "<WriteMode>RecorderSubordinate</WriteMode>")
    obj(root, "InformationRegisters", "InformationRegister", "Настройки",
        child("Dimension", "Ключ") + child("Resource", "Значение"),
        props="<InformationRegisterPeriodicity>Nonperiodical</InformationRegisterPeriodicity>"
              "<WriteMode>Independent</WriteMode>")
    unit = child("Dimension", "Подразделение") if with_size else ""
    obj(root, "AccountingRegisters", "AccountingRegister", "Хозрасчетный", unit + child("Resource", "Сумма"))
    obj(root, "CalculationRegisters", "CalculationRegister", "Начисления",
        child("Dimension", "Сотрудник") + child("Resource", "Результат"),
        props="<ActionPeriod>true</ActionPeriod><Schedule>InformationRegister.Графики</Schedule>")
    obj(root, "Enums", "Enum", "Статусы", child("EnumValue", "Открыт") +
        (child("EnumValue", "Архивный") if with_size else ""))
    obj(root, "Roles", "Role", "Администратор")


MODULE = '''Функция Данные() Экспорт
	Запрос = Новый Запрос;
	Запрос.Текст =
	"ВЫБРАТЬ
	|	Т.Размер КАК Размер,
	|	Т.Цвет.Оттенок КАК Оттенок,
	|	Т.Цвет.Яркость КАК Яркость,
	|	Т.Вес КАК Вес,
	|	Т.Метка КАК Метка
	|ИЗ
	|	Справочник.Товары КАК Т
	|ГДЕ
	|	Т.Статус = ЗНАЧЕНИЕ(Перечисление.Статусы.Закрыт)
	|;
	|ВЫБРАТЬ
	|	З.КоличествоОстаток КАК Остаток,
	|	З.товар.цвет КАК Цвет
	|ИЗ
	|	РегистрНакопления.Запасы.Остатки КАК З
	|	ЛЕВОЕ СОЕДИНЕНИЕ Справочник.Удаленный КАК У
	|	ПО ИСТИНА";
	// Т.Вес в комментарии - не код: "ВЫБРАТЬ Т.Призрак ИЗ Справочник.Товары КАК Т"
	Тип = Тип("СправочникСсылка.Удаленный");
	Статус = Перечисления.Статусы.Открыт;
	Возврат Запрос;
КонецФункции
'''

MODULE2 = '''Функция Данные2() Экспорт
	Запрос = Новый Запрос;
	Запрос.Текст =
	"ВЫБРАТЬ
	|	Т.Состав.Доля КАК Доля,
	|	Т.Состав.ДоляРасш КАК ДоляРасш,
	|	Т.Состав.Процент КАК Процент,
	|	Т.Упаковки.Вес КАК ВесУпаковки,
	|	т.Призрак2 КАК Призрак2,
	|	Т.ИмяПоля КАК ИмяПоля,
	|	Т.Габарит КАК Габарит,
	|	Т.Архив.Поле КАК АрхивПоле,
	|	ЗНАЧЕНИЕ(Перечисление.Статусы.Архивный) КАК Архивный,
	|	ЗНАЧЕНИЕ(перечисление.Статусы.Удален) КАК Удален,
	|	ВЫРАЗИТЬ(Т.Цвет КАК Справочник.Цвета) КАК ЦветТовара
	|ИЗ
	|	Справочник.Товары КАК Т
	|	ЛЕВОЕ СОЕДИНЕНИЕ Справочник.Удаленный2 КАК У2
	|	ПО ИСТИНА
	|;
	|ВЫБРАТЬ
	|	ПР.СуммаОборот КАК СуммаОборот,
	|	ПР.ПериодНеделя КАК ПериодНеделя,
	|	ПР.ПериодДекада КАК ПериодДекада,
	|	ПР.HalfYearPeriod КАК ПериодПолугодие,
	|	ПР.СуммаПриход КАК СуммаПриход,
	|	З.КоличествоПриход КАК КоличествоПриход,
	|	Ц.Цена КАК Цена,
	|	Ц.Нет КАК Нет,
	|	Х.СуммаОстатокДт КАК СуммаОстатокДт,
	|	Х.СуммаДт КАК СуммаДт,
	|	Х.СубконтоДт1 КАК Субконто,
	|	ХО.СуммаОстатокДт КАК ОстатокДт,
	|	ХО.Подразделение КАК Подразделение,
	|	ХО.КорСчет КАК КорСчет,
	|	ПБ.ВидДвижения КАК ВидДвижения,
	|	ПБ.Регистратор КАК РегистраторПродаж,
	|	НБ.Ключ КАК Ключ,
	|	НБ.Период КАК ПериодНастроек,
	|	НБ.Регистратор КАК РегистраторНастроек,
	|	ЦБ.Период КАК ПериодЦены,
	|	ЦБ.Регистратор КАК РегистраторЦены,
	|	НЧ.ПериодРегистрации КАК ПериодРегистрации,
	|	НЧ.Сотрудник КАК Сотрудник,
	|	ФП.ЧтоУгодно КАК ЧтоУгодно
	|ИЗ
	|	РегистрНакопления.Продажи.Обороты КАК ПР,
	|	РегистрНакопления.Запасы.Остатки КАК З,
	|	РегистрСведений.Цены.СрезПоследних(, Адрес ПОДОБНО ""http://x"") КАК Ц,
	|	РегистрБухгалтерии.Хозрасчетный.ДвиженияССубконто КАК Х,
	|	РегистрБухгалтерии.Хозрасчетный.Остатки КАК ХО,
	|	РегистрНакопления.Продажи.Остатки КАК ПРО,
	|	РегистрСведений.Настройки.СрезПоследних КАК Н,
	|	РегистрНакопления.Продажи КАК ПБ,
	|	РегистрСведений.Настройки КАК НБ,
	|	РегистрСведений.Цены КАК ЦБ,
	|	РегистрРасчета.Начисления КАК НЧ,
	|	РегистрРасчета.Начисления.ФактическийПериодДействия КАК ФП,
	|	РегистрРасчета.Начисления.ДанныеГрафика КАК ДГ,
	|	РегистрРасчета.Начисления.БазаНачисления КАК БН,
	|	РегистрРасчета.Начисления.НетТакой КАК НТ";
	Запрос.Текст = СтрЗаменить(Запрос.Текст, "ИмяПоля", "Цвет");
	ТекстСообщения = СтрЗаменить("Товар %1", "%1", "Габарит");
	Возврат Запрос;
КонецФункции

Процедура Сообщение()
	Текст = "Склад";
	Запрос = Новый Запрос("ВЫБРАТЬ Т.Склад КАК Склад ИЗ Справочник.Товары КАК Т");
	Если РольДоступна("Администратор") И РольДоступна("Удаленная") Тогда
		Ф = ПолучитьФункциональнуюОпцию("НетОпции");
	КонецЕсли;
КонецПроцедуры

Процедура Формы()
	ОткрытьФорму("Справочник.Товары.Форма.ФормаЭлемента");
	ОткрытьФорму("Справочник.Товары.Форма.Пропавшая");
	ОткрытьФорму("Справочник.Товары.Форма.СтараяФорма");
	Макет = "Документ.Нет.Макет.М";
	Форма = ПолучитьФорму("Документ.Заказ.ФормаОбъекта");
	Форма = ПолучитьФорму("Документ.Удаленный.ФормаОбъекта");
КонецПроцедуры
'''

MODULE3 = '''Процедура Предопределенные(Товар) Экспорт
	Открыт = ПредопределенноеЗначение("Перечисление.Статусы.Открыт");
	Удален = PredefinedValue("Перечисление.Статусы.Удален");
	Запрос = Новый Запрос;
	Запрос.Текст =
	"ВЫБРАТЬ
	|	Т.Ссылка КАК Ссылка
	|ИЗ
	|	Справочник.Товары КАК Т";
КонецПроцедуры
'''

DCS = '''<?xml version="1.0" encoding="UTF-8"?>
<DataCompositionSchema xmlns="http://v8.1c.ru/8.1/data-composition-system/schema"
		xmlns:dcsset="http://v8.1c.ru/8.1/data-composition-system/settings"
		xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
	<dataSet xsi:type="DataSetQuery">
		<name>НаборДанных1</name>
		<field xsi:type="DataSetFieldField"><dataPath>Оттенок</dataPath><field>Оттенок</field></field>
		<field xsi:type="DataSetFieldField"><dataPath>Цвет</dataPath><field>Цвет</field></field>
		<field xsi:type="DataSetFieldField"><dataPath>Пропавшее</dataPath><field>Пропавшее</field></field>
		<query>ВЫБРАТЬ (ВЫБРАТЬ ПЕРВЫЕ 1 Ц.Оттенок ИЗ Справочник.Цвета КАК Ц) КАК Оттенок, Т.Цвет КАК Цвет
ИЗ Справочник.Товары КАК Т</query>
	</dataSet>
	<settingsVariant>
		<dcsset:name>Основной</dcsset:name>
		<dcsset:settings><dcsset:selection><dcsset:item xsi:type="dcsset:SelectedItemField">
			<dcsset:field>Призрак</dcsset:field>
		</dcsset:item></dcsset:selection></dcsset:settings>
	</settingsVariant>
</DataCompositionSchema>
'''


class QueriesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.old = os.path.join(cls.tmp, "old")
        cls.new = os.path.join(cls.tmp, "new")
        release(cls.old, with_size=True)
        release(cls.new, with_size=False)
        ext = os.path.join(cls.tmp, "ext", "Расш")
        config(ext, "Расш", extension=True)
        # the extension borrows what the old release had: a column, a section, a form, an enum value
        obj(ext, "Catalogs", "Catalog", "Товары",
            child("Attribute", "Метка") +
            ts("Состав", child("Attribute", "ДоляРасш") + child("Attribute", "Процент", adopted=True), adopted=True) +
            ts("Архив", child("Attribute", "Поле", adopted=True), adopted=True) +
            ts("Упаковки", child("Attribute", "Вес")) + "<Form>СтараяФорма</Form>", adopted=True)
        form = os.path.join(ext, "Catalogs", "Товары", "Forms")
        os.makedirs(form)
        with open(os.path.join(form, "СтараяФорма.xml"), "w", encoding="utf-8") as fh:
            fh.write("%s<Form><Properties><ObjectBelonging>Adopted</ObjectBelonging><Name>СтараяФорма</Name>"
                     "</Properties></Form></MetaDataObject>" % HEAD)
        obj(ext, "Enums", "Enum", "Статусы", child("EnumValue", "Архивный", adopted=True), adopted=True)
        for name, text in (("М", MODULE), ("М2", MODULE2), ("М3", MODULE3)):
            mod = os.path.join(ext, "CommonModules", name, "Ext")
            os.makedirs(mod)
            with open(os.path.join(mod, "Module.bsl"), "w", encoding="utf-8-sig") as fh:
                fh.write(text)
        sch = os.path.join(ext, "Reports", "Отч", "Templates", "Схема", "Ext")
        os.makedirs(sch)
        with open(os.path.join(sch, "Template.xml"), "w", encoding="utf-8-sig") as fh:
            fh.write(DCS)
        cls.exts = metadata.find_extensions([os.path.join(cls.tmp, "ext")])
        cls.sw = queries.Sweep(metadata.Dump(cls.new), metadata.Dump(cls.old), cls.exts)
        cls.sw.queries(cls.exts[0])
        cls.sw.dcs(cls.exts[0])
        cls.sw.literals(cls.exts[0])
        cls.by_ref = {f["ref"]: f for f in cls.sw.findings if f["part"] != "dcs"}
        cls.dcs = {f["ref"]: f for f in cls.sw.findings if f["part"] == "dcs"}
        cls.masked = {m["ref"]: m for m in cls.sw.masked}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_field_removed_by_update(self):
        f = self.by_ref["Т.Размер"]
        self.assertEqual((f["kind"], f["old"]), ("поле", "было корректно"))

    def test_field_missing_in_both(self):
        self.assertEqual(self.by_ref["Т.Вес"]["old"], "тоже не разрешается")

    def test_reference_field_one_level_deeper(self):
        self.assertIn("Т.Цвет.Яркость", self.by_ref)
        self.assertNotIn("Т.Цвет.Оттенок", self.by_ref)

    def test_extension_attribute_and_virtual_table_resolve(self):
        for ref in ("Т.Метка", "З.КоличествоОстаток", "З.товар.цвет"):
            self.assertNotIn(ref, self.by_ref)
        self.assertGreaterEqual(self.sw.stats["query_fields"], 7)

    def test_missing_table_and_value(self):
        self.assertEqual(self.by_ref["Справочник.Удаленный"]["kind"], "таблица")
        self.assertEqual(self.by_ref["ЗНАЧЕНИЕ(Перечисление.Статусы.Закрыт)"]["kind"], "значение")

    def test_comments_are_not_code(self):
        self.assertNotIn("Т.Призрак", self.by_ref)

    def test_literals(self):
        self.assertEqual(self.by_ref["СправочникСсылка.Удаленный"]["part"], "literal")
        self.assertNotIn("Перечисления.Статусы.Открыт", self.by_ref)

    def test_predefined_value_is_not_query_text(self):
        # a real case: ПредопределенноеЗначение("Вид.X.Y") in a method with a query read as the table «Вид.X.Y»
        for ref in ("Перечисление.Статусы.Открыт", "Перечисление.Статусы.Удален"):
            self.assertFalse([f for f in self.sw.findings if f["part"] == "query" and f["ref"] == ref], ref)
        f = self.by_ref["Перечисление.Статусы.Удален"]  # the value itself is still checked, as a literal
        self.assertEqual((f["part"], f["kind"], f["where"], f["line"]), ("literal", "значение",
                                                                         "метод Предопределенные", 3))

    def test_literal_findings_name_their_method(self):
        self.assertEqual(self.by_ref["СправочникСсылка.Удаленный"]["where"], "метод Данные")
        self.assertEqual(self.by_ref['РольДоступна("Удаленная")']["where"], "метод Сообщение")

    def test_bind_aliases_skips_value_and_type(self):
        q = "ВЫБРАТЬ ЗНАЧЕНИЕ(Перечисление.Статусы.Открыт), ТИП(Справочник.Цвета) ИЗ Справочник.Товары КАК Т"
        self.assertEqual([(a, k, n) for a, k, n, _, _ in qparse.bind_aliases(q)], [("Т", "Справочник", "Товары")])

    def test_select_aliases_last_query_of_batch(self):
        q = "ВЫБРАТЬ 1 КАК А ПОМЕСТИТЬ ВТ; ВЫБРАТЬ Т.Б, Т.В КАК Г ИЗ ВТ КАК Т"
        self.assertEqual(set(qparse.select_aliases(q)), {"Б", "Г"})

    # -- casts, nested selects, schemas, tabular sections, virtual tables -------------------------------------------
    def test_cast_type_is_not_an_alias(self):
        q = "ВЫБРАТЬ ВЫРАЗИТЬ(Т.Цвет КАК Справочник.Цвета) КАК Ц ИЗ Справочник.Товары КАК Т"
        self.assertIn(("Т", "Справочник", "Товары"), [(a, k, n) for a, k, n, _, _ in qparse.bind_aliases(q)])
        self.assertEqual(self.by_ref["Справочник.Удаленный2"]["kind"], "таблица")
        self.assertIn("т.Призрак2", self.by_ref)

    def test_select_list_ends_at_outer_from(self):
        q = ("ВЫБРАТЬ (ВЫБРАТЬ ПЕРВЫЕ 1 Ц.Оттенок ИЗ Справочник.Цвета КАК Ц) КАК Оттенок, Т.Цвет КАК Цвет "
             "ИЗ Справочник.Товары КАК Т")
        self.assertEqual(set(qparse.select_aliases(q)), {"Оттенок", "Цвет"})
        self.assertEqual(set(qparse.select_aliases("ВЫБРАТЬ В.А КАК А ИЗ (ВЫБРАТЬ 1 КАК Б) КАК В")), {"А"})
        self.assertEqual(set(qparse.select_aliases("ВЫБРАТЬ 1 КАК А ОБЪЕДИНИТЬ ВСЕ ВЫБРАТЬ 2 КАК Б")), {"А"})
        self.assertEqual(set(qparse.select_aliases("ВЫБРАТЬ Т.А КАК А ВЫБРАТЬ Т.* ИЗ Т КАК Т")), {"А"})
        self.assertNotIn("Цвет", self.dcs)
        self.assertNotIn("Оттенок", self.dcs)

    def test_dcs_schema_findings_do_not_depend_on_release(self):
        self.assertEqual(self.dcs["Пропавшее"]["old"], queries.RELEASE_FREE)

    def test_tabular_section_column(self):
        f = self.by_ref["Т.Состав.Процент"]
        self.assertEqual((f["kind"], f["old"]), ("поле", "было корректно"))
        for ref in ("Т.Состав.Доля", "Т.Состав.ДоляРасш", "Т.Упаковки.Вес"):
            self.assertNotIn(ref, self.by_ref)

    def test_virtual_table_per_register_kind(self):
        for ref in ("РегистрНакопления.Продажи.Остатки", "РегистрСведений.Настройки.СрезПоследних"):
            self.assertEqual(self.by_ref[ref]["kind"], "таблица")
        for ref in ("ПР.СуммаПриход", "З.КоличествоПриход"):
            self.assertEqual(self.by_ref[ref]["kind"], "поле")
        for ref in ("ПР.СуммаОборот", "Ц.Цена"):
            self.assertNotIn(ref, self.by_ref)

    def test_periodicity_fields_of_virtual_tables(self):
        # trial run 16.10.2026: ПериодНеделя/Декада/Квартал/Год of a vendor DCS schema were reported as missing
        for ref in ("ПР.ПериодНеделя", "ПР.ПериодДекада", "ПР.HalfYearPeriod"):
            self.assertNotIn(ref, self.by_ref)

    def test_accounting_virtual_tables(self):
        self.assertNotIn("Х.СубконтоДт1", self.by_ref)
        self.assertIn("регистра бухгалтерии", self.masked["Х.СубконтоДт1"]["why"])
        self.assertIn("регистра бухгалтерии", self.masked["ХО.КорСчет"]["why"])
        self.assertEqual(self.by_ref["Х.СуммаОстатокДт"]["kind"], "поле")  # a balance suffix on ДвиженияССубконто
        f = self.by_ref["ХО.Подразделение"]  # a removed dimension is a finding, not «не судится»
        self.assertEqual((f["kind"], f["old"]), ("поле", "было корректно"))
        for ref in ("Х.СуммаДт", "ХО.СуммаОстатокДт"):
            self.assertNotIn(ref, self.by_ref)
            self.assertNotIn(ref, self.masked)

    # -- adopted children, replace masks, register tables, form names, literals -------------------------------------
    def test_borrowed_children_are_not_evidence(self):
        for ref in ("Т.Состав.Процент", "Т.Архив.Поле", "ЗНАЧЕНИЕ(Перечисление.Статусы.Архивный)",
                    "Справочник.Товары.Форма.СтараяФорма"):
            self.assertEqual(self.by_ref[ref]["old"], "было корректно", ref)
        self.assertNotIn("Т.Состав.ДоляРасш", self.by_ref)

    def test_replace_mask_takes_the_search_pattern_only(self):
        self.assertIn("Т.Габарит", self.by_ref)
        self.assertNotIn("Т.Габарит", self.masked)

    def test_register_main_table_fields_follow_its_properties(self):
        for ref in ("ПБ.ВидДвижения", "НБ.Период", "НБ.Регистратор"):
            self.assertEqual(self.by_ref[ref]["kind"], "поле", ref)
        for ref in ("ПБ.Регистратор", "НБ.Ключ", "ЦБ.Период", "ЦБ.Регистратор"):
            self.assertNotIn(ref, self.by_ref)

    def test_calculation_register(self):
        self.assertEqual(self.by_ref["РегистрРасчета.Начисления.НетТакой"]["kind"], "таблица")
        for ref in ("РегистрРасчета.Начисления.ФактическийПериодДействия", "РегистрРасчета.Начисления.ДанныеГрафика",
                    "РегистрРасчета.Начисления.БазаНачисления", "НЧ.ПериодРегистрации", "НЧ.Сотрудник"):
            self.assertNotIn(ref, self.by_ref)
        self.assertIn("регистра расчёта", self.masked["ФП.ЧтоУгодно"]["why"])

    def test_standard_form_names(self):
        self.assertEqual(self.by_ref["Документ.Удаленный.ФормаОбъекта"]["kind"], "имя")
        self.assertNotIn("Документ.Заказ.ФормаОбъекта", self.by_ref)

    def test_value_kind_in_any_case(self):
        self.assertEqual(self.by_ref["ЗНАЧЕНИЕ(перечисление.Статусы.Удален)"]["kind"], "значение")

    def test_settings_field_missing_in_schema_does_not_depend_on_release(self):
        self.assertEqual(self.dcs["Призрак"]["old"], queries.RELEASE_FREE)

    def test_replace_mask_only_for_str_replace_arguments(self):
        self.assertIn("СтрЗаменить", self.masked["Т.ИмяПоля"]["why"])
        self.assertNotIn("Т.ИмяПоля", self.by_ref)
        self.assertIn("Т.Склад", self.by_ref)

    def test_comment_marks_and_doubled_quotes_inside_literals(self):
        # the comment is blanked to spaces of its length, so positions keep pointing at the source lines
        self.assertEqual(qparse.clean('ВЫБРАТЬ "a//b" КАК А // хвост'), 'ВЫБРАТЬ "a//b" КАК А ' + " " * 8)
        self.assertEqual(qparse.clean('ВЫБРАТЬ "a""//b" КАК А'), 'ВЫБРАТЬ "a""//b" КАК А')
        q = 'Р.Ц.СрезПоследних(, А = "x"")(") КАК Ц'
        self.assertEqual(q[qparse.skip_parens(q, q.index("(")):], " КАК Ц")
        self.assertIn("Ц.Нет", self.by_ref)

    def test_virtual_table_with_parameters_is_not_a_subquery(self):
        q = ('ИЗ РегистрСведений.Цены.СрезПоследних(, Адрес ПОДОБНО "(x") КАК Ц\n'
             "ЛЕВОЕ СОЕДИНЕНИЕ (ВЫБРАТЬ 1 КАК Б) КАК П ПО ИСТИНА")
        self.assertEqual(qparse.other_sources(q), {"п"})

    def test_named_calls_and_full_names(self):
        for ref in ('РольДоступна("Удаленная")', 'ПолучитьФункциональнуюОпцию("НетОпции")',
                    "Справочник.Товары.Форма.Пропавшая", "Документ.Нет.Макет.М"):
            self.assertEqual(self.by_ref[ref]["kind"], "имя")
        for ref in ('РольДоступна("Администратор")', "Справочник.Товары.Форма.ФормаЭлемента"):
            self.assertNotIn(ref, self.by_ref)

    def test_calculation_kinds_are_query_sources(self):
        self.assertEqual(qparse.KIND_Q["ПланВидовРасчета"], "ПланВидовРасчета")
        self.assertEqual(qparse.KIND_Q["РегистрРасчета"], "РегистрРасчета")

    def test_child_types_come_from_own_type_only(self):
        props = metadata.ET.fromstring(
            '<Properties xmlns="http://v8.1c.ru/8.3/MDClasses" xmlns:v8="http://v8.1c.ru/8.1/data/core">'
            '<Type><v8:Type>cfg:CatalogRef.А</v8:Type></Type>'
            '<ChoiceParameters><v8:Type>cfg:CatalogRef.Б</v8:Type></ChoiceParameters></Properties>')
        self.assertEqual(metadata._types(props), ["cfg:CatalogRef.А"])


if __name__ == "__main__":
    unittest.main()
