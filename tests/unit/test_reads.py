# -*- coding: utf-8 -*-
"""reads: vendor registers the extensions read in query texts whose composition or recorders changed, on small
hand-written dumps."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from cfe_rebase import cli, metadata, reads
from tests.unit.test_borrowed import HEAD, child, decimal, obj, refs, string, write

MODULE = "\n".join([
    "Функция Остатки() Экспорт",
    "\tЗапрос = Новый Запрос;",
    "\tЗапрос.Текст =",
    "\t\"ВЫБРАТЬ",
    "\t|\tТ.Номенклатура КАК Номенклатура,",
    "\t|\tСУММА(Т.КоличествоОстаток) КАК Количество",
    "\t|ИЗ",
    "\t|\tРегистрНакопления.ТоварыНаСкладах.Остатки(&Дата, ) КАК Т",
    "\t//|\tЛЕВОЕ СОЕДИНЕНИЕ РегистрСведений.Закомментированный КАК З",
    "\t//|\tПО Т.Номенклатура = З.Номенклатура",
    "\t|СГРУППИРОВАТЬ ПО",
    "\t|\tТ.Номенклатура\";",
    "\t// Запрос.Текст = \"ВЫБРАТЬ З.Номенклатура ИЗ РегистрСведений.Закомментированный КАК З\";",
    "\tВозврат Запрос.Выполнить();",
    "КонецФункции",
    "",
    "Функция Прочее() Экспорт",
    "\tЗапрос = Новый Запрос;",
    "\tЗапрос.Текст =",
    "\t\"ВЫБРАТЬ",
    "\t|\tН.Номенклатура КАК Номенклатура",
    "\t|ПОМЕСТИТЬ ВТ",
    "\t|ИЗ",
    "\t|\tРегистрНакопления.Неизменный КАК Н",
    "\t|;",
    "\t|ВЫБРАТЬ",
    "\t|\tВТ.Номенклатура КАК Номенклатура,",
    "\t|\tС.Ключ КАК Ключ,",
    "\t|\tБ.Поле КАК Поле,",
    "\t|\tН2.Поле КАК Поле2",
    "\t|ИЗ",
    "\t|\tВТ КАК ВТ",
    "\t|\t\tЛЕВОЕ СОЕДИНЕНИЕ РегистрСведений.Расш_Свой КАК С",
    "\t|\t\tПО ИСТИНА",
    "\t|\t\tЛЕВОЕ СОЕДИНЕНИЕ РегистрСведений.Битый КАК Б",
    "\t|\t\tПО ИСТИНА",
    "\t|\t\tЛЕВОЕ СОЕДИНЕНИЕ РегистрСведений.Новый КАК Н2",
    "\t|\t\tПО ИСТИНА",
    "\t|\t\tЛЕВОЕ СОЕДИНЕНИЕ &Таблица КАК П",
    "\t|\t\tПО ИСТИНА\";",
    "\tВозврат Запрос.Выполнить();",
    "КонецФункции",
])
FORM = '''<?xml version="1.0" encoding="UTF-8"?>
<Form xmlns="http://v8.1c.ru/8.3/xcf/logform" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" version="2.20">
	<Attributes>
		<Attribute name="Список" id="1">
			<Settings xsi:type="DynamicList">
				<ManualQuery>true</ManualQuery>
				<QueryText>ВЫБРАТЬ
	Ц.Номенклатура КАК Номенклатура,
	Ц.Цена КАК Цена
ИЗ
	РегистрСведений.ЦеныНоменклатуры.СрезПоследних КАК Ц</QueryText>
			</Settings>
		</Attribute>
		<Attribute name="Авто" id="2">
			<Settings xsi:type="DynamicList">
				<ManualQuery>false</ManualQuery>
				<QueryText>ВЫБРАТЬ А.Номенклатура ИЗ РегистрСведений.Закомментированный КАК А</QueryText>
			</Settings>
		</Attribute>
	</Attributes>
</Form>
'''
DCS = '''<?xml version="1.0" encoding="UTF-8"?>
<DataCompositionSchema xmlns="http://v8.1c.ru/8.1/data-composition-system/schema"
		xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
	<dataSet xsi:type="DataSetQuery">
		<name>НаборДанных1</name>
		<query>ВЫБРАТЬ
	О.Номенклатура КАК Номенклатура,
	О.КоличествоОборот КАК Оборот,
	У.Поле КАК Поле
ИЗ
	РегистрНакопления.ТоварыНаСкладах.Обороты КАК О
	ЛЕВОЕ СОЕДИНЕНИЕ РегистрСведений.Удаленный КАК У
	ПО О.Номенклатура = У.Номенклатура</query>
	</dataSet>
</DataCompositionSchema>
'''


def document(name, registers):
    items = "".join('<xr:Item xsi:type="xr:MDObjectRef">%s</xr:Item>' % r for r in registers)
    return HEAD + '<Document xmlns:xr="http://v8.1c.ru/8.3/xcf/readable" ' \
                  'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" uuid="d-%s"><Properties><Name>%s</Name>' \
                  '<RegisterRecordsDeletion>AutoDeleteOff</RegisterRecordsDeletion><RegisterRecords>%s' \
                  '</RegisterRecords></Properties><ChildObjects/></Document></MetaDataObject>' % (name, name, items)


def release(root, new):
    """One release of the vendor configuration: old (new=False) or new."""
    write(os.path.join(root, "Configuration.xml"), HEAD + '<Configuration uuid="c"><Properties><Name>Типовая</Name>'
          '</Properties></Configuration></MetaDataObject>')
    stock = [child("Dimension", "Номенклатура", refs("Номенклатура")), child("Dimension", "Склад", refs("Склады")),
             child("Resource", "Количество", decimal(15, 3))]
    if new:
        stock.insert(2, child("Dimension", "КонтролируемаяПартия", refs("Партии")))
    write(os.path.join(root, "AccumulationRegisters", "ТоварыНаСкладах.xml"),
          obj("AccumulationRegister", "ТоварыНаСкладах", stock))
    prices = [child("Dimension", "Номенклатура", refs("Номенклатура"))]
    prices += [child("Resource", "УдалитьЦена", decimal(15, 2)), child("Resource", "ЦенаСНДС", decimal(15, 2)),
               child("Attribute", "Комментарий", string(100))] if new else \
        [child("Resource", "Цена", decimal(15, 2)), child("Attribute", "Комментарий", string(50))]
    write(os.path.join(root, "InformationRegisters", "ЦеныНоменклатуры.xml"),
          obj("InformationRegister", "ЦеныНоменклатуры", prices))
    write(os.path.join(root, "AccumulationRegisters", "Неизменный.xml"), obj("AccumulationRegister", "Неизменный", [
        child("Dimension", "Номенклатура", refs("Номенклатура")), child("Resource", "Количество", decimal(15, 3))]))
    gone = "УдалитьУдаленный" if new else "Удаленный"
    write(os.path.join(root, "InformationRegisters", gone + ".xml"), obj("InformationRegister", gone, [
        child("Dimension", "Номенклатура"), child("Resource", "Поле")]))
    commented = [child("Dimension", "Номенклатура")] + ([child("Dimension", "Склад")] if new else [])
    write(os.path.join(root, "InformationRegisters", "Закомментированный.xml"),
          obj("InformationRegister", "Закомментированный", commented))
    write(os.path.join(root, "InformationRegisters", "Битый.xml"), "<MetaDataObject><broken" if new else
          obj("InformationRegister", "Битый", [child("Dimension", "Поле")]))
    if new:
        write(os.path.join(root, "InformationRegisters", "Новый.xml"),
              obj("InformationRegister", "Новый", [child("Dimension", "Поле")]))
    docs = {"Приход": ["AccumulationRegister.ТоварыНаСкладах", "InformationRegister.ЦеныНоменклатуры"]}
    docs.update({"Перемещение": ["AccumulationRegister.ТоварыНаСкладах"], "Списание": []} if new else
                {"Списание": ["AccumulationRegister.ТоварыНаСкладах"]})
    for name, regs in docs.items():
        write(os.path.join(root, "Documents", name + ".xml"), document(name, regs))


class Reads(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cfe_reads_")
        t = cls.tmp
        release(os.path.join(t, "old"), new=False)
        release(os.path.join(t, "new"), new=True)
        ext = os.path.join(t, "ext", "Расш")
        cls.ext = ext
        write(os.path.join(ext, "Configuration.xml"), HEAD + '<Configuration uuid="c-ext"><Properties><Name>Расш'
              '</Name></Properties></Configuration></MetaDataObject>')
        # an adopted vendor register is read like any other, the extension's own one is not compared
        write(os.path.join(ext, "AccumulationRegisters", "ТоварыНаСкладах.xml"),
              obj("AccumulationRegister", "ТоварыНаСкладах", [], adopted=True))
        write(os.path.join(ext, "InformationRegisters", "Расш_Свой.xml"),
              obj("InformationRegister", "Расш_Свой", [child("Dimension", "Ключ")]))
        write(os.path.join(ext, "CommonModules", "Расш_Запросы", "Ext", "Module.bsl"), MODULE)
        write(os.path.join(ext, "CommonForms", "Расш_Цены", "Ext", "Form.xml"), FORM)
        write(os.path.join(ext, "Reports", "Расш_Отчет", "Templates", "Схема", "Ext", "Template.xml"), DCS)
        cls.out = os.path.join(t, "out")
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            reads.main(["--ext-root", os.path.join(t, "ext"), "--config-old", os.path.join(t, "old"),
                        "--config-new", os.path.join(t, "new"), "--out", cls.out])
        cls.console = buf.getvalue()
        with open(os.path.join(cls.out, "reads.json"), encoding="utf-8") as fh:
            cls.data = json.load(fh)
        with open(os.path.join(cls.out, "reads.md"), encoding="utf-8") as fh:
            cls.md = fh.read()
        cls.items = {f["register"]: f for f in cls.data["findings"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_items_and_summary(self):
        self.assertEqual(set(self.items), {"РегистрНакопления.ТоварыНаСкладах", "РегистрСведений.ЦеныНоменклатуры",
                                           "РегистрСведений.Удаленный", "РегистрСведений.Битый"})
        s = self.data["stats"]
        self.assertEqual({k: s[k] for k in ("extensions", "reads", "registers_read", "registers_changed",
                                            "registers_removed", "registers_unreadable", "registers_unchanged")},
                         {"extensions": 1, "reads": 6, "registers_read": 5, "registers_changed": 2,
                          "registers_removed": 1, "registers_unreadable": 1, "registers_unchanged": 1})
        self.assertEqual((s["registers_own"], s["registers_not_in_old"]), (1, 1))
        for f in self.data["findings"]:
            self.assertEqual((f["ext"], f["sev"]), ("Расш", "ПРОВЕРИТЬ"))
            self.assertEqual(set(f), {"ext", "register", "check", "sev", "dims_added", "dims_removed", "res_added",
                                      "res_removed", "attrs_added", "attrs_removed", "type_changed",
                                      "props_changed", "recorders_added", "recorders_removed", "reads", "detail"})
        self.assertIn("registers read: 5", self.console)

    def test_new_dimension_and_recorders(self):
        f = self.items["РегистрНакопления.ТоварыНаСкладах"]
        self.assertEqual(f["check"], "CHANGED")
        self.assertEqual(f["dims_added"], ["КонтролируемаяПартия"])
        self.assertEqual((f["dims_removed"], f["res_added"], f["res_removed"], f["type_changed"]), ([], [], [], []))
        self.assertEqual((f["recorders_added"], f["recorders_removed"]), (["Перемещение"], ["Списание"]))
        self.assertIn("новые измерения: КонтролируемаяПартия", f["detail"])

    def test_virtual_table_line_and_fields(self):
        f = self.items["РегистрНакопления.ТоварыНаСкладах"]
        by_table = {r["table"]: r for r in f["reads"]}
        self.assertEqual(set(by_table), {"ТоварыНаСкладах.Остатки", "ТоварыНаСкладах.Обороты"})
        module = by_table["ТоварыНаСкладах.Остатки"]
        self.assertEqual((module["line"], module["where"]), (8, "метод Остатки"))
        self.assertEqual(module["fields"], ["Номенклатура", "КоличествоОстаток"])
        self.assertEqual(os.path.normcase(module["path"]), os.path.normcase(
            os.path.join(self.ext, "CommonModules", "Расш_Запросы", "Ext", "Module.bsl")))
        dcs = by_table["ТоварыНаСкладах.Обороты"]  # a DCS data set: no line
        self.assertEqual((dcs["line"], dcs["where"]), (0, "набор НаборДанных1"))
        self.assertTrue(dcs["path"].endswith("Template.xml"))
        self.assertEqual(dcs["fields"], ["Номенклатура", "КоличествоОборот"])

    def test_resource_added_renamed_and_type_changed(self):
        f = self.items["РегистрСведений.ЦеныНоменклатуры"]
        self.assertEqual(f["check"], "CHANGED")
        # «Удалить<Имя>» is the old resource marked for removal: removed, not added
        self.assertEqual((f["res_added"], f["res_removed"]), (["ЦенаСНДС"], ["Цена"]))
        self.assertIn("Цена → УдалитьЦена", f["detail"])
        self.assertEqual(f["type_changed"], [{"part": "attribute", "name": "Комментарий",
                                              "old": "xs:string, Length=50, AllowedLength=Variable",
                                              "new": "xs:string, Length=100, AllowedLength=Variable"}])
        self.assertEqual((f["recorders_added"], f["recorders_removed"]), ([], []))
        read, = f["reads"]  # the manual dynamic list of Form.xml; the automatic one is not a query text
        self.assertEqual((read["table"], read["where"], read["line"]), ("ЦеныНоменклатуры.СрезПоследних",
                                                                       "список Список", 0))
        self.assertEqual(read["fields"], ["Номенклатура", "Цена"])

    def test_removed_and_unreadable(self):
        gone = self.items["РегистрСведений.Удаленный"]
        self.assertEqual(gone["check"], "REGISTER_REMOVED")
        self.assertIn("УдалитьУдаленный", gone["detail"])
        self.assertEqual(gone["reads"][0]["where"], "набор НаборДанных1")
        broken = self.items["РегистрСведений.Битый"]
        self.assertEqual(broken["check"], "XML_UNREADABLE")
        self.assertIn("Битый.xml", broken["detail"])
        for f in (gone, broken):
            self.assertFalse(any(f[k] for k in reads.LISTS))

    def test_skipped(self):
        # unchanged: counted only; commented code and an automatic list are not reads; the extension's own register
        # and one absent from old are not compared; a temporary table and a parameter are not registers
        for name in ("Неизменный", "Закомментированный", "Расш_Свой", "Новый"):
            self.assertFalse([r for r in self.items if r.endswith("." + name)], name)
        tables = {r["table"] for f in self.data["findings"] for r in f["reads"]}
        self.assertFalse({"ВТ", "Таблица"} & tables)

    def test_markdown_sections(self):
        heads = [x for x in self.md.splitlines() if x.startswith("## ")]
        self.assertEqual(heads, ["## РегистрНакопления.ТоварыНаСкладах — CHANGED",
                                 "## РегистрСведений.Битый — XML_UNREADABLE",
                                 "## РегистрСведений.Удаленный — REGISTER_REMOVED",
                                 "## РегистрСведений.ЦеныНоменклатуры — CHANGED"])
        self.assertIn("new: `%s`, old: `%s`" % (os.path.join(self.tmp, "new"), os.path.join(self.tmp, "old")), self.md)
        self.assertIn("CommonModules/Расш_Запросы/Ext/Module.bsl:8", self.md)
        self.assertIn("новые регистраторы (1): Перемещение", self.md)
        self.assertIn("тип реквизит Комментарий: `Length=50` → `Length=100`", self.md)

    def test_recorders_scan(self):
        rec = metadata.Dump(os.path.join(self.tmp, "old")).recorders()
        self.assertEqual(rec, {("РегистрНакопления", "товарынаскладах"): {"Приход", "Списание"},
                               ("РегистрСведений", "ценыноменклатуры"): {"Приход"}})
        self.assertEqual(metadata.Dump(os.path.join(self.tmp, "new")).recorders()[
            ("РегистрНакопления", "товарынаскладах")], {"Приход", "Перемещение"})

    def test_moved_between_parts(self):
        class Reg(object):
            def __init__(self, **parts):
                self.props = {}
                for bucket in ("dims", "res", "attrs"):
                    setattr(self, bucket, {n: {"type_key": "Type=xs:string"} for n in parts.get(bucket, [])})
        st = reads.compare(Reg(dims=["Склад"], res=["Сумма"]), Reg(dims=["Сумма"], attrs=["Склад"]), set(), set())
        self.assertEqual((st["dims_added"], st["dims_removed"], st["res_removed"], st["attrs_added"]),
                         (["Сумма"], ["Склад"], ["Сумма"], ["Склад"]))
        self.assertEqual(st["moved"], [("Склад", "dimension", "attribute"), ("Сумма", "resource", "dimension")])
        self.assertEqual(st["renamed"], {})

    def test_cli_command(self):
        self.assertEqual(cli.COMMANDS["reads"][0], "reads")
        self.assertEqual(cli.DB_FLAGS["reads"], cli.DB_FLAGS["registers"])
        self.assertEqual(cli.OPTIONS["reads"], cli.OPTIONS["registers"])

    def test_out_inside_a_dump_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            reads.main(["--ext-root", os.path.join(self.tmp, "ext"), "--config-old", os.path.join(self.tmp, "old"),
                        "--config-new", os.path.join(self.tmp, "new"), "--out", os.path.join(self.ext, "out")])
        self.assertIn("reads в выгрузки не пишет", str(cm.exception.code))
        self.assertFalse(os.path.exists(os.path.join(self.ext, "out")))


BATCH = "\n".join([
    "Функция Пакет() Экспорт",
    "\tЗапрос = Новый Запрос;",
    "\tЗапрос.Текст =",
    "\t\"ВЫБРАТЬ",
    "\t|\t1 КАК Поле",
    "\t|;",
    "\t|",
    "\t|" + "/" * 80,
    "\t|ВЫБРАТЬ // остатки на дату",
    "\t|\tТ.Номенклатура КАК Номенклатура",
    "\t|ИЗ",
    "\t|\tРегистрНакопления.ТоварыНаСкладах.Остатки(&Дата, ) КАК Т\";",
    "\tВозврат Запрос.ВыполнитьПакет();",
    "КонецФункции",
])


class LineAfterComments(unittest.TestCase):
    """A comment inside a query text (a `////` separator of a batch, a trailing `// ...`) does not shift the line of
    a table below it: positions are taken in the text with comments blanked, not cut."""

    def test_table_line_is_exact(self):
        t = tempfile.mkdtemp(prefix="cfe_reads_lines_")
        self.addCleanup(shutil.rmtree, t, True)
        release(os.path.join(t, "old"), new=False)
        release(os.path.join(t, "new"), new=True)
        ext = os.path.join(t, "ext", "Расш")
        write(os.path.join(ext, "Configuration.xml"), HEAD + '<Configuration uuid="c-ext"><Properties><Name>Расш'
              '</Name></Properties></Configuration></MetaDataObject>')
        write(os.path.join(ext, "CommonModules", "Расш_Пакет", "Ext", "Module.bsl"), BATCH)
        out = os.path.join(t, "out")
        with contextlib.redirect_stdout(io.StringIO()):
            reads.main(["--ext-root", os.path.join(t, "ext"), "--config-old", os.path.join(t, "old"),
                        "--config-new", os.path.join(t, "new"), "--out", out])
        with open(os.path.join(out, "reads.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        read = data["findings"][0]["reads"][0]
        self.assertEqual((read["table"], read["line"]), ("ТоварыНаСкладах.Остатки", 12))
        self.assertEqual(read["fields"], ["Номенклатура"])


def register(tag, name, children, props=""):
    """A register XML with extra leaf properties right after the name."""
    return obj(tag, name, children).replace("<Name>%s</Name>" % name, "<Name>%s</Name>%s" % (name, props), 1)


def dimension(name, typ, use_in_totals):
    return child("Dimension", name, typ).replace("</Properties>", "<UseInTotals>%s</UseInTotals></Properties>"
                                                 % use_in_totals)


PROPS_MODULE = "\n".join([
    "Функция Ф() Экспорт",
    "\tЗапрос = Новый Запрос;",
    "\tЗапрос.Текст =",
    "\t\"ВЫБРАТЬ А.КоличествоОборот КАК К ИЗ РегистрНакопления.Вид.Обороты КАК А",
    "\t|;ВЫБРАТЬ Б.Документ КАК Д ИЗ РегистрНакопления.Партии КАК Б",
    "\t|;ВЫБРАТЬ В.Сумма КАК С ИЗ РегистрНакопления.Перенос КАК В",
    "\t|;ВЫБРАТЬ Г.Цена КАК Ц ИЗ РегистрСведений.Цены.СрезПоследних КАК Г",
    "\t|;ВЫБРАТЬ Д.Склад КАК С ИЗ РегистрНакопления.Итоги.Остатки КАК Д\";",
    "\tВозврат Запрос.ВыполнитьПакет();",
    "КонецФункции",
])


def props_release(root, new):
    """Registers whose field names stay while what a query over them returns changes."""
    write(os.path.join(root, "Configuration.xml"), HEAD + '<Configuration uuid="c"><Properties><Name>Типовая</Name>'
          '</Properties></Configuration></MetaDataObject>')
    write(os.path.join(root, "AccumulationRegisters", "Вид.xml"), register("AccumulationRegister", "Вид", [
        child("Dimension", "Номенклатура", refs("Номенклатура")), child("Resource", "Количество", decimal(15, 3))],
        "<RegisterType>%s</RegisterType>" % ("Turnovers" if new else "Balance")))
    write(os.path.join(root, "AccumulationRegisters", "Партии.xml"), obj("AccumulationRegister", "Партии", [
        child("Dimension", "Документ", "<Type><v8:TypeSet>cfg:DefinedType.ДокументПартии</v8:TypeSet></Type>"),
        child("Resource", "Количество", decimal(15, 3))]))
    write(os.path.join(root, "DefinedTypes", "ДокументПартии.xml"), HEAD + '<DefinedType uuid="dt"><Properties>'
          '<Name>ДокументПартии</Name><Type><v8:Type>cfg:DocumentRef.Приход</v8:Type>%s</Type></Properties>'
          '</DefinedType></MetaDataObject>' % ("<v8:Type>cfg:DocumentRef.Отгрузка</v8:Type>" if new else ""))
    moved = [child("Dimension", "Сумма", refs("Валюты"))] if new else [child("Resource", "Сумма", decimal(15, 2))]
    write(os.path.join(root, "AccumulationRegisters", "Перенос.xml"), obj("AccumulationRegister", "Перенос", [
        child("Dimension", "Номенклатура", refs("Номенклатура"))] + moved))
    write(os.path.join(root, "InformationRegisters", "Цены.xml"), register("InformationRegister", "Цены", [
        child("Dimension", "Номенклатура", refs("Номенклатура")), child("Resource", "Цена", decimal(15, 2))],
        "<InformationRegisterPeriodicity>%s</InformationRegisterPeriodicity><WriteMode>%s</WriteMode>"
        % (("Month", "RecorderSubordinate") if new else ("Day", "Independent"))))
    write(os.path.join(root, "AccumulationRegisters", "Итоги.xml"), obj("AccumulationRegister", "Итоги", [
        dimension("Склад", refs("Склады"), "false" if new else "true"),
        child("Resource", "Количество", decimal(15, 3))]))
    # a document without movements dumps <RegisterRecords/>: no recorders, no error
    write(os.path.join(root, "Documents", "Пустой.xml"), HEAD + '<Document uuid="d"><Properties><Name>Пустой</Name>'
          '<RegisterRecords/></Properties><ChildObjects/></Document></MetaDataObject>')


class PropertiesAndDefinedTypes(unittest.TestCase):
    """Changes that keep every field name: register type, periodicity, write mode, UseInTotals of a dimension, the type
    of a defined type a field is typed by, and the type of a field that moved to another part."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cfe_reads_props_")
        t = cls.tmp
        props_release(os.path.join(t, "old"), new=False)
        props_release(os.path.join(t, "new"), new=True)
        ext = os.path.join(t, "ext", "Расш")
        write(os.path.join(ext, "Configuration.xml"), HEAD + '<Configuration uuid="c-ext"><Properties><Name>Расш'
              '</Name></Properties></Configuration></MetaDataObject>')
        write(os.path.join(ext, "CommonModules", "Расш_Свойства", "Ext", "Module.bsl"), PROPS_MODULE)
        out = os.path.join(t, "out")
        with contextlib.redirect_stdout(io.StringIO()):
            reads.main(["--ext-root", os.path.join(t, "ext"), "--config-old", os.path.join(t, "old"),
                        "--config-new", os.path.join(t, "new"), "--out", out])
        with open(os.path.join(out, "reads.json"), encoding="utf-8") as fh:
            cls.data = json.load(fh)
        with open(os.path.join(out, "reads.md"), encoding="utf-8") as fh:
            cls.md = fh.read()
        cls.items = {f["register"]: f for f in cls.data["findings"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_register_is_reported(self):
        self.assertEqual(self.data["stats"]["registers_unchanged"], 0)
        self.assertEqual(len(self.items), 5)

    def test_register_type(self):
        f = self.items["РегистрНакопления.Вид"]
        self.assertEqual(f["props_changed"], [{"name": "RegisterType", "old": "Balance", "new": "Turnovers"}])
        self.assertIn("вид регистра Balance → Turnovers", f["detail"])

    def test_periodicity_and_write_mode(self):
        f = self.items["РегистрСведений.Цены"]
        self.assertEqual({p["name"]: (p["old"], p["new"]) for p in f["props_changed"]},
                         {"InformationRegisterPeriodicity": ("Day", "Month"),
                          "WriteMode": ("Independent", "RecorderSubordinate")})

    def test_use_in_totals(self):
        f = self.items["РегистрНакопления.Итоги"]
        self.assertEqual(f["props_changed"], [{"name": "Склад.UseInTotals", "old": "true", "new": "false"}])
        self.assertIn("Склад: использовать в итогах true → false", f["detail"])

    def test_defined_type(self):
        f = self.items["РегистрНакопления.Партии"]
        self.assertEqual(len(f["type_changed"]), 1)
        t = f["type_changed"][0]
        self.assertEqual((t["part"], t["name"], t["defined_type"]),
                         ("dimension", "Документ", "ОпределяемыйТип.ДокументПартии"))
        self.assertIn("cfg:DocumentRef.Отгрузка", t["new"])
        self.assertNotIn("cfg:DocumentRef.Отгрузка", t["old"])
        self.assertIn("через ОпределяемыйТип.ДокументПартии", self.md)

    def test_moved_field_type(self):
        f = self.items["РегистрНакопления.Перенос"]
        self.assertEqual((f["res_removed"], f["dims_added"]), (["Сумма"], ["Сумма"]))
        self.assertEqual([(t["part"], t["name"]) for t in f["type_changed"]], [("dimension", "Сумма")])
        self.assertIn("перешли в другую часть: Сумма (ресурс → измерение)", f["detail"])

    def test_self_closing_register_records(self):
        cfg = metadata.Dump(os.path.join(self.tmp, "new"), "new")
        self.assertEqual(cfg.recorders(), {})


if __name__ == "__main__":
    unittest.main()
