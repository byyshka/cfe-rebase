# -*- coding: utf-8 -*-
"""registers: writes to a register whose dimensions changed in the new release, on small hand-written dumps."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from cfe_rebase import plan, registers
from tests.unit.test_borrowed import HEAD, child, obj, write

MODULE = "\n".join([
    "Процедура Удалить(Штрихкод) Экспорт",
    "\tРС = РегистрыСведений.Штрихкоды.СоздатьМенеджерЗаписи();",
    "\tРС.Штрихкод = Штрихкод;",
    "\tРС.Прочитать();",
    "КонецПроцедуры",
    "",
    "Процедура Готово(Ш, Н) Экспорт",
    "\tРС = РегистрыСведений.Штрихкоды.СоздатьМенеджерЗаписи();",
    "\tРС.Штрихкод = Ш;",
    "\tРС.Номенклатура = Н;",
    "\tРС.Записать();",
    "КонецПроцедуры",
    "",
    "Процедура Вызов()",
    "\tУдалить(\"1\");",
    "КонецПроцедуры",
    "",
    "Процедура Старое()",
    "\tНабор = РегистрыСведений.Штрихкоды.СоздатьНаборЗаписей();",
    "\tЗ = Набор.Добавить();",
    "\tЗ.Штрихкод = \"1\";",
    "\tЗ.Номенклатура = 1;",
    "\tЗ.Цена = 2;",
    "КонецПроцедуры",
    "",
    "Процедура ВКомментарии()",
    "\t// РС = РегистрыСведений.Штрихкоды.СоздатьМенеджерЗаписи();",
    "\tТекст = \"РегистрыСведений.Штрихкоды.СоздатьНаборЗаписей()\";",
    "КонецПроцедуры",
])
DOCUMENT = "Процедура ОбработкаПроведения(Отказ, Режим)\n\tЗ = Движения.Штрихкоды.Добавить();\n\tЗ.Штрихкод = 1;\n" \
           "КонецПроцедуры"


class Registers(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_registers_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        t = self.tmp
        old = [child("Dimension", "Штрихкод"), child("Resource", "Номенклатура"), child("Resource", "Цена")]
        new = [child("Dimension", "Штрихкод"), child("Dimension", "Номенклатура"), child("Dimension", "УдалитьСклад"),
               child("Resource", "УдалитьЦена")]
        for root, children in (("old", old), ("new", new)):
            write(os.path.join(t, root, "InformationRegisters", "Штрихкоды.xml"),
                  obj("InformationRegister", "Штрихкоды", children))
            write(os.path.join(t, root, "Configuration.xml"), HEAD + '<Configuration uuid="c-%s"><Properties><Name>'
                  'Типовая</Name></Properties></Configuration></MetaDataObject>' % root)
        ext = os.path.join(t, "ext", "Расш")
        write(os.path.join(ext, "Configuration.xml"), HEAD + '<Configuration uuid="c-ext"><Properties><Name>Расш</Name>'
              '</Properties></Configuration></MetaDataObject>')
        write(os.path.join(ext, "CommonModules", "Расш_Штрихкоды", "Ext", "Module.bsl"), MODULE)
        write(os.path.join(ext, "Documents", "Приход", "Ext", "ObjectModule.bsl"), DOCUMENT)
        self.out = os.path.join(t, "out")

    def run_tool(self):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            registers.main(["--ext-root", os.path.join(self.tmp, "ext"), "--config-old", os.path.join(self.tmp, "old"),
                            "--config-new", os.path.join(self.tmp, "new"), "--out", self.out])
        with open(os.path.join(self.out, "registers.json"), encoding="utf-8") as fh:
            return json.load(fh), buf.getvalue()

    def test_findings(self):
        data, console = self.run_tool()
        got = {f["method"]: (f["how"], f["missing"], f["stale"], f["callers"]) for f in data["findings"]}
        self.assertEqual(got, {
            "Удалить": ("manager", ["Номенклатура"], [], ["Вызов"]),           # the key lost a dimension
            "Старое": ("recordset", [], ["Цена"], []),                        # a resource the vendor renamed УдалитьЦена
            "ОбработкаПроведения": ("movements", ["Номенклатура"], [], []),   # Движения.<register> of a document
        })  # Готово fills the new dimension; УдалитьСклад is the vendor's own deprecation; comments and strings
        old = next(f for f in data["findings"] if f["method"] == "Старое")
        self.assertIn("вендор переименовал в УдалитьЦена", old["detail"])
        self.assertEqual(old["to_dims"], ["Номенклатура"])
        self.assertIn("findings: 3", console)

    def test_unparsable_register_xml(self):
        # a register XML that does not parse was «no change»: every write to it is reported unchecked
        path = os.path.join(self.tmp, "new", "InformationRegisters", "Штрихкоды.xml")
        write(path, HEAD + "<InformationRegister><Properties>")
        data, console = self.run_tool()
        self.assertEqual({f["method"]: f["check"] for f in data["findings"]},
                         {m: "XML_UNREADABLE" for m in ("Удалить", "Готово", "Старое", "ОбработкаПроведения")})
        self.assertIn(path, data["findings"][0]["detail"])
        self.assertIn("ParseError", data["findings"][0]["detail"])
        self.assertEqual(data["stats"]["registers_unreadable"], 1)
        self.assertIn("unreadable XML: 1", console)

    def test_register_removed_by_the_vendor(self):
        os.remove(os.path.join(self.tmp, "new", "InformationRegisters", "Штрихкоды.xml"))
        # the new dump keeps other objects: an empty dump is refused before the analysis (check_config)
        write(os.path.join(self.tmp, "new", "InformationRegisters", "Цены.xml"), obj("InformationRegister", "Цены", []))
        write(os.path.join(self.tmp, "ext", "Расш", "CommonModules", "Расш_Свой", "Ext", "Module.bsl"),
              "Процедура Своё()\n\tН = РегистрыСведений.Расш_Свой.СоздатьНаборЗаписей();\nКонецПроцедуры")
        data, console = self.run_tool()
        got = {f["method"]: (f["check"], f["register"]) for f in data["findings"]}
        self.assertEqual(got, {m: ("REGISTER_REMOVED", "РегистрСведений.Штрихкоды")
                               for m in ("Удалить", "Готово", "Старое", "ОбработкаПроведения")})
        # Своё writes the extension's own register, in neither release: not reported
        removed = next(f for f in data["findings"] if f["method"] == "Удалить")
        self.assertIn("регистра в new нет", removed["detail"])
        self.assertEqual(removed["callers"], ["Вызов"])
        self.assertEqual(data["stats"]["registers_removed"], 1)
        self.assertIn("removed: 1", console)

    def test_movements_through_an_object(self):
        # ДокументОбъект.Движения.<register> in a manager or common module is a write too, not only bare Движения
        write(os.path.join(self.tmp, "ext", "Расш", "Documents", "Приход", "Ext", "ManagerModule.bsl"),
              "Процедура Провести(ДокументОбъект) Экспорт\n\tЗ = ДокументОбъект.Движения.Штрихкоды.Добавить();\n"
              "\tЗ.Штрихкод = 1;\nКонецПроцедуры")
        data, _ = self.run_tool()
        got = {f["method"]: (f["how"], f["missing"]) for f in data["findings"]}
        self.assertEqual(got["Провести"], ("movements", ["Номенклатура"]))

    def test_case_only_rename_is_no_change(self):
        new = [child("Dimension", "ШТРИХКОД"), child("Dimension", "Номенклатура"), child("Dimension", "УдалитьСклад"),
               child("Resource", "УдалитьЦена")]
        write(os.path.join(self.tmp, "new", "InformationRegisters", "Штрихкоды.xml"),
              obj("InformationRegister", "Штрихкоды", new))
        data, _ = self.run_tool()
        got = {f["method"]: (f["missing"], f["stale"]) for f in data["findings"]}
        self.assertEqual(got, {"Удалить": (["Номенклатура"], []), "Старое": ([], ["Цена"]),
                               "ОбработкаПроведения": (["Номенклатура"], [])})  # Штрихкод is not removed

    def test_plan(self):
        data, _ = self.run_tool()
        p = plan.build({}, {}, {}, {}, {}, None, data)
        # Удалить and Старое: nothing live in the extension calls them (method_live) - «проверить» with the dead-code note;
        # ОбработкаПроведения is a platform event
        self.assertEqual(p["counts"], {"MUST_FIX": 1, "REVIEW": 2, "CLARIFY": 0})
        self.assertEqual({x["method"]: x["group"] for x in p["items"]},
                         {"ОбработкаПроведения": "MUST_FIX", "Удалить": "REVIEW", "Старое": "REVIEW"})
        self.assertEqual({f["method"]: f["method_live"] for f in data["findings"]},
                         {"ОбработкаПроведения": "событие платформы",
                          "Удалить": "вызывается только из мёртвого кода",  # from Вызов, which nothing calls
                          "Старое": "ссылок в расширениях нет"})
        self.assertTrue(all(x["what"][0].startswith("запись в регистр РегистрСведений.Штрихкоды") for x in p["items"]))
        self.assertTrue(all("мёртвый код" in x["what"][-1] for x in p["items"] if x["group"] == "REVIEW"))

    def test_dimension_that_became_a_resource_or_an_attribute(self):
        # the vendor kept Склад as a resource and Партия as an attribute: the field is there, the key and the filter of
        # a record set no longer have it - Набор.Отбор.Склад fails; it was «no change» and the register not even counted
        old = [child("Dimension", "Склад"), child("Dimension", "Товар"), child("Dimension", "Партия"),
               child("Resource", "Цена")]
        new = [child("Dimension", "Товар"), child("Resource", "Склад"), child("Resource", "Цена"),
               child("Attribute", "Партия")]
        for root, children in (("old", old), ("new", new)):
            write(os.path.join(self.tmp, root, "InformationRegisters", "Цены.xml"), obj("InformationRegister", "Цены",
                                                                                          children))
        write(os.path.join(self.tmp, "ext", "Расш", "CommonModules", "Расш_Цены", "Ext", "Module.bsl"), "\n".join([
            "Процедура Очистить(Склад) Экспорт",
            "\tНабор = РегистрыСведений.Цены.СоздатьНаборЗаписей();",
            "\tНабор.Отбор.Склад.Установить(Склад);",
            "\tЗ = Набор.Добавить();",
            "\tЗ.Партия = 1;",
            "\tНабор.Записать();",
            "КонецПроцедуры",
            "",
            "Процедура ПоТовару(Т) Экспорт",
            "\tНабор = РегистрыСведений.Цены.СоздатьНаборЗаписей();",
            "\tНабор.Отбор.Товар.Установить(Т);",
            "\tНабор.Записать();",
            "КонецПроцедуры"]))
        data, console = self.run_tool()
        got = {f["method"]: (f["check"], f["key_lost"], f["missing"], f["stale"]) for f in data["findings"]
               if f["register"] == "РегистрСведений.Цены"}
        self.assertEqual(got, {"Очистить": ("CHANGED", ["Склад", "Партия"], [], [])})  # ПоТовару uses neither
        found = next(f for f in data["findings"] if f["method"] == "Очистить")
        self.assertEqual(found["to_res"], ["Склад", "Партия"])
        self.assertIn("Склад (ресурс), Партия (реквизит)", found["detail"])
        self.assertEqual(data["stats"]["registers_changed"], 2)  # Штрихкоды and Цены
        self.assertIn("changed in new: 2", console)
        p = plan.build({}, {}, {}, {}, {}, None, data)
        item = next(x for x in p["items"] if x["method"] == "Очистить")
        # nothing in the extension calls Очистить: «проверить», not «исправить»
        self.assertEqual((item["group"], item["sources"]), ("REVIEW", ["registers:key_lost"]))

    def test_out_inside_a_dump_is_refused(self):
        for root in (os.path.join(self.tmp, "ext"), os.path.join(self.tmp, "old"), os.path.join(self.tmp, "new")):
            out = os.path.join(root, "отчёт")
            with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()):
                registers.main(["--ext-root", os.path.join(self.tmp, "ext"), "--config-old",
                                os.path.join(self.tmp, "old"), "--config-new", os.path.join(self.tmp, "new"),
                                "--out", out])
            self.assertIn("в выгрузки не пишет", str(cm.exception.code))
            self.assertFalse(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
