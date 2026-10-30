# -*- coding: utf-8 -*-
"""template-params: parameter names of the vendor's spreadsheet templates removed or renamed by the new release, and
the extensions that fill or keep the old names, on small hand-written dumps."""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest

from cfe_rebase import cli, selection, template_params as tp
from tests.unit.test_borrowed import HEAD, obj, write

HAVE_MCP = importlib.util.find_spec("mcp") is not None
RENAMED_OLD = "ПредставлениеЕдининицыИзмеренияУпаковки"
RENAMED_NEW = "ПредставлениеЕдиницыИзмеренияУпаковки"
FILLS = ("Parameter", "Template", "Text")  # format 1, 2, 3 of sheet()


def _rows(cells):
    rows = []
    for i, (kind, value) in enumerate(cells):
        row_fmt, body = "", ""
        if kind == "p":
            body = "<f>1</f><parameter>%s</parameter>" % value
        elif kind == "d":
            name, detail = value.split("|")
            body = "<f>1</f><parameter>%s</parameter><detailParameter>%s</detailParameter>" % (name, detail)
        elif kind in ("t", "x", "r"):
            tl = "<tl><v8:item><v8:lang>ru</v8:lang><v8:content>%s</v8:content></v8:item></tl>" % value
            body = tl if kind == "r" else "<f>%d</f>%s" % (2 if kind == "t" else 3, tl)
            row_fmt = "<formatIndex>2</formatIndex>" if kind == "r" else ""
        rows.append("<rowsItem><index>%d</index><row>%s<c><c>%s</c></c></row></rowsItem>" % (i, row_fmt, body))
    return "".join(rows) + "".join("<format><fillType>%s</fillType></format>" % f for f in FILLS)


def sheet(cells, areas=None, base=None):
    """Template.xml of a spreadsheet document, one cell per row. cells: (kind, value): p - <parameter>, d - parameter
    with a detailParameter (value «name|detail»), t - text of a Template fill, x - text of a Text fill, r - text of a
    Template fill set by the row's format (the cell has no <f>). areas: {name: (first row, last row)} - Rows areas;
    base: cells of a <baseSheet> - the merge mode of a borrowed template."""
    named = "".join('<namedItem><name>%s</name><area><type>Rows</type><beginRow>%d</beginRow><endRow>%d</endRow>'
                    '<beginColumn>-1</beginColumn><endColumn>-1</endColumn></area></namedItem>' % (n, a, b)
                    for n, (a, b) in (areas or {}).items())
    merge = "<baseSheet>%s</baseSheet><extensionMethod>Merge</extensionMethod>" % _rows(base) if base else ""
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<document xmlns="http://v8.1c.ru/8.2/data/spreadsheet" '
            'xmlns:v8="http://v8.1c.ru/8.1/data/core">%s%s%s</document>' % (_rows(cells), named, merge))


def template_meta(name, extended):
    """Templates/<name>.xml of a borrowed template; extended - InternalInfo says the platform uses its content."""
    info = ("<InternalInfo><xr:PropertyState><xr:Property>Template</xr:Property><xr:State>Extended</xr:State>"
            "</xr:PropertyState></InternalInfo>" if extended else "<InternalInfo/>")
    return (HEAD + '<Template xmlns:xr="http://v8.1c.ru/8.3/xcf/readable" uuid="t-%s">%s<Properties>'
            '<ObjectBelonging>Adopted</ObjectBelonging><Name>%s</Name><TemplateType>SpreadsheetDocument</TemplateType>'
            '</Properties></Template></MetaDataObject>' % (name, info, name))


def tpl_path(root, owner, name, folder="Documents"):
    if owner is None:
        return os.path.join(root, "CommonTemplates", name, "Ext", "Template.xml")
    return os.path.join(root, folder, owner, "Templates", name, "Ext", "Template.xml")


OLD_ORDER = [("p", RENAMED_OLD), ("p", RENAMED_OLD), ("p", "Номер"), ("p", "Колонка1"), ("p", "Акциз"),
             ("t", "Документ от [Дата] № [Номер], сноска [1], [Формат(Дата, \"ДФ=dd\")]"),
             ("x", "Текст [НеПараметр]"), ("d", "Товар|НРег(Строка(Отбор)) = НРег(\"Все\")")]
NEW_ORDER = [("p", RENAMED_NEW), ("p", RENAMED_NEW), ("p", "Номер"), ("p", "Колонка2"), ("p", "АкцизДоИзменения"),
             ("r", "Документ от [ДатаДокумента] № [Номер]"), ("x", "Текст [НеПараметр]"),
             ("d", "Товар|НРег(Строка(Отбор)) = НРег(\"Кроме\")")]


def release(root, new):
    write(os.path.join(root, "Configuration.xml"), HEAD + '<Configuration uuid="c"><Properties><Name>Типовая</Name>'
          '</Properties></Configuration></MetaDataObject>')
    write(os.path.join(root, "Documents", "Ордер.xml"), obj("Document", "Ордер", []))
    write(tpl_path(root, "Ордер", "ПФ_MXL_Ордер"), sheet(NEW_ORDER if new else OLD_ORDER))
    write(tpl_path(root, "Ордер", "Служебный"), sheet([("p", "Служебное2" if new else "Служебное")]))
    write(tpl_path(root, "Ордер", "ПФ_MXL_Неизменный"), sheet([("p", "Остаётся")]))
    write(tpl_path(root, None, "ПФ_MXL_Общий"), sheet([("p", "Было")] + ([("p", "Добавлено")] if new else [])))
    write(tpl_path(root, "Отчёт", "ОсновнаяСхема", "Reports"),
          '<?xml version="1.0"?><DataCompositionSchema xmlns="http://v8.1c.ru/8.1/data-composition-system/schema"/>')


def extension(root, name, modules=None, templates=None):
    """templates: {(owner, template): cells or {cells, areas, base, extended}}; extended (True / False) also writes
    the metadata XML of a borrowed template."""
    write(os.path.join(root, name, "Configuration.xml"), HEAD + '<Configuration uuid="e-%s"><Properties><Name>%s'
          '</Name><ConfigurationExtensionPurpose>Customization</ConfigurationExtensionPurpose></Properties>'
          '</Configuration></MetaDataObject>' % (name, name))
    for rel, text in (modules or {}).items():
        write(os.path.join(root, name, *rel.split("/")), text)
    for (owner, tname), spec in (templates or {}).items():
        spec = spec if isinstance(spec, dict) else {"cells": spec}
        path = tpl_path(os.path.join(root, name), owner, tname)
        write(path, sheet(spec["cells"], spec.get("areas"), spec.get("base")))
        if "extended" in spec:
            write(os.path.dirname(os.path.dirname(path)) + ".xml", template_meta(tname, spec["extended"]))


MANAGER = "Documents/Ордер/Ext/ManagerModule.bsl"
OLD_CODE = "\n".join([
    "Процедура Заполнить(Область, Выборка)",
    "\tЗапрос = Новый Запрос(\"ВЫБРАТЬ",
    "\t|\tТ.Упаковка КАК %s\");" % RENAMED_OLD,
    "\tОбласть.Параметры.%s = Выборка.%s;" % (RENAMED_OLD, RENAMED_OLD),
    "\t// Область.Параметры.Акциз = 1;",
    "#Удаление",
    "\tОбласть.Параметры[\"Акциз\"] = 2;",
    "#КонецУдаления",
    "\tОбласть.Параметры[\"Служебное\"] = 3;",
    "КонецПроцедуры",
])


def new_code(*area_lines):
    """The code moved to the new name; area_lines take areas of the own copy."""
    return "\n".join([
        "Процедура Заполнить(Область, Выборка)",
        "\tЗапрос = Новый Запрос(\"ВЫБРАТЬ",
        "\t|\tТ.Упаковка КАК %s\");" % RENAMED_OLD,
        "\tОбласть.Параметры.%s = Выборка.%s;" % (RENAMED_NEW, RENAMED_OLD),
    ] + ["\t" + x for x in area_lines] + ["КонецПроцедуры"])


class TemplateParams(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_tplparams_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.old, self.new, self.ext = (os.path.join(self.tmp, x) for x in ("old", "new", "ext"))
        release(self.old, False)
        release(self.new, True)
        copy = {"cells": [("p", "Шапка"), ("p", RENAMED_OLD)], "areas": {"Шапка": (0, 0), "Строка": (1, 1)}}
        # writes the old name, keeps an own copy with it
        extension(self.ext, "Старое", {MANAGER: OLD_CODE}, {("Ордер", "ПФ_MXL_Ордер1"): copy})
        # moved to the new name; old query aliases are plumbing; takes only Шапка of the copy, the old name is in
        # Строка: unproven
        extension(self.ext, "Новое", {MANAGER: new_code(
            "Ш = ПолучитьМакет(\"ПФ_MXL_Ордер1\").ПолучитьОбласть(\"Шапка\");")}, {("Ордер", "ПФ_MXL_Ордер1"): copy})
        # the same, but takes Строка of the copy through a variable: the old name is printed
        extension(self.ext, "НовоеВОбласти", {MANAGER: new_code(
            "Макет1 = ПолучитьМакет(\"ПФ_MXL_Ордер1\");", "С = Макет1.ПолучитьОбласть(\"Строка\");")},
            {("Ордер", "ПФ_MXL_Ордер1"): copy})
        # borrowed vendor templates filled by the new vendor code:
        # Extended without a base sheet - the extension's sheet replaces the vendor's
        extension(self.ext, "Заменил", templates={("Ордер", "ПФ_MXL_Ордер"): {"cells": OLD_ORDER, "extended": True}})
        # Extended + Merge, the old name untouched by the extension - the vendor's rename arrives (a real case)
        extension(self.ext, "Объединил", templates={("Ордер", "ПФ_MXL_Ордер"): {
            "cells": OLD_ORDER, "base": OLD_ORDER, "extended": True}})
        # Extended + Merge, the extension added a cell with the old name
        extension(self.ext, "ОбъединилИзменил", templates={("Ордер", "ПФ_MXL_Ордер"): {
            "cells": OLD_ORDER + [("p", RENAMED_OLD)], "base": OLD_ORDER, "extended": True}})
        # adopted without Extended - the session prints the vendor's template
        extension(self.ext, "Ссылка", templates={("Ордер", "ПФ_MXL_Ордер"): {"cells": OLD_ORDER, "extended": False}})
        # the old name in another object's module: not the template's code
        extension(self.ext, "Чужое", {"Documents/Другой/Ext/ManagerModule.bsl":
                                      "Процедура П(О)\n\tО.Параметры.%s = 1;\nКонецПроцедуры" % RENAMED_OLD})

    def run_analyze(self):
        return tp.analyze([self.ext], self.old, self.new)

    # -- parsing -------------------------------------------------------------------------------------------------
    def test_parse_names(self):
        names = tp.parse_names(sheet(OLD_ORDER).encode("utf-8"))
        got = {v[0]: v[1] for v in names.values()}
        self.assertEqual(got, {RENAMED_OLD: 2, "Номер": 2, "Колонка1": 1, "Акциз": 1, "Дата": 1, "Товар": 1})
        # a Template fill set by the row's format when the cell has no format of its own
        names = tp.parse_names(sheet(NEW_ORDER).encode("utf-8"))
        self.assertIn("датадокумента", names)
        self.assertNotIn("непараметр", names)

    def test_not_a_spreadsheet(self):
        path = os.path.join(self.new, "Reports", "Отчёт", "Templates", "ОсновнаяСхема", "Ext", "Template.xml")
        self.assertEqual(tp.read_names(path), (None, None))

    # -- pairs ---------------------------------------------------------------------------------------------------
    def test_levenshtein(self):
        self.assertEqual(tp.levenshtein("единининицы", "единицы"), 3)
        self.assertEqual(tp.levenshtein(RENAMED_OLD.casefold(), RENAMED_NEW.casefold()), 2)
        self.assertEqual(tp.levenshtein("абв", "абв"), 0)

    def test_pairs(self):
        def d(*items):
            return {n.casefold(): [n, c] for n, c in items}
        self.assertEqual(tp.pair_names(d((RENAMED_OLD, 4)), d((RENAMED_NEW, 4))),
                         [(RENAMED_OLD.casefold(), RENAMED_NEW.casefold())])
        self.assertEqual(tp.pair_names(d((RENAMED_OLD, 4)), d((RENAMED_NEW, 3))), [], "cells differ")
        self.assertEqual(tp.pair_names(d(("Колонка1", 1)), d(("Колонка2", 1))), [], "digits only")
        self.assertEqual(tp.pair_names(d(("Акциз", 1)), d(("Акцизы", 1))), [], "too short")
        self.assertEqual(tp.pair_names(d(("ЦенаТовара", 1)), d(("ЦенаТоварах", 1), ("ЦенаТоварб", 1))), [],
                         "two candidates")

    # -- findings ------------------------------------------------------------------------------------------------
    def test_vendor_diff(self):
        changes, st = tp.vendor_diff(self.old, self.new)
        by = {c["title"]: c for c in changes}
        self.assertEqual(set(by), {"Документ.Ордер.ПФ_MXL_Ордер", "Документ.Ордер.Служебный",
                                   "ОбщийМакет.ПФ_MXL_Общий"})
        order = by["Документ.Ордер.ПФ_MXL_Ордер"]
        self.assertEqual(order["renamed"], [{"old": RENAMED_OLD, "new": RENAMED_NEW, "cells": 2}])
        self.assertEqual(sorted(g["name"] for g in order["gone"]), ["Акциз", "Дата", "Колонка1"])
        self.assertEqual(sorted(a["name"] for a in order["added"]), ["АкцизДоИзменения", "ДатаДокумента", "Колонка2"])
        self.assertFalse(by["Документ.Ордер.Служебный"]["print"])
        self.assertEqual(st["spreadsheets"], 4)  # the DCS template is not one
        self.assertEqual(st["spreadsheets_changed"], 3)  # ПФ_MXL_Неизменный is the same file
        self.assertEqual((st["with_gone"], st["print_with_gone"], st["added_only"], st["renamed_pairs"]), (2, 1, 1, 1))

    def test_findings(self):
        _, _, findings, stats = self.run_analyze()
        got = [(f["check"], f["ext"], f["template"]) for f in findings]
        order = "Документ.Ордер.ПФ_MXL_Ордер"
        self.assertEqual(got, [
            ("CODE_OLD_NAME", "Старое", order),
            ("CODE_OLD_NAME", "Старое", "Документ.Ордер.Служебный"),
            ("TEMPLATE_OLD_NAME", "Заменил", order),
            ("TEMPLATE_OLD_NAME", "НовоеВОбласти", order),
            ("TEMPLATE_OLD_NAME", "ОбъединилИзменил", order),
            ("VENDOR_RENAMED", "", order),
            ("COPY_OLD_NAME", "Новое", order),
        ])
        by = {(f["check"], f["ext"]): f for f in findings if f["template"] == order}
        old = by[("CODE_OLD_NAME", "Старое")]
        # the comment and the #Удаление block are not code; the query alias of a renamed name is a loose hit
        self.assertEqual([(p["line"], p["how"]) for p in old["places"]],
                         [(3, "строка"), (4, "Параметры.%s" % RENAMED_OLD)])
        self.assertEqual(old["templates"], [])  # the code still writes the old name: its copy is consistent with it
        self.assertEqual(old["sev"], tp.RISK)
        replaced = by[("TEMPLATE_OLD_NAME", "Заменил")]["templates"][0]
        self.assertEqual((replaced["borrowed"], sorted(replaced["names"])),
                         (True, sorted([RENAMED_OLD, "Колонка1", "Акциз", "Дата"])))
        merged = by[("TEMPLATE_OLD_NAME", "ОбъединилИзменил")]
        self.assertEqual((merged["templates"][0]["names"], merged["templates"][0]["merge"]), ([RENAMED_OLD], True))
        self.assertIn("baseSheet", merged["detail"])
        in_area = by[("TEMPLATE_OLD_NAME", "НовоеВОбласти")]
        self.assertEqual(in_area["templates"][0]["areas_used"], ["Строка"])
        self.assertIn("код расширения пишет новое имя", in_area["detail"])
        copy = by[("COPY_OLD_NAME", "Новое")]
        self.assertEqual((copy["sev"], copy["templates"][0]["areas_used"]), (tp.INFO, ["Шапка"]))
        self.assertIn("проверить, читается ли область", copy["detail"])
        self.assertIn("пустой", by[("VENDOR_RENAMED", "")]["detail"])
        self.assertEqual((stats["code_old_name"], stats["template_old_name"], stats["vendor_renamed"],
                          stats["copy_old_name"], stats["vendor_gone"]), (2, 3, 1, 1, 0))

    def test_copy_without_areas_by_name(self):
        extension(self.ext, "Новое", {MANAGER: new_code("Т = ПолучитьМакет(\"ПФ_MXL_Ордер1\");")},
                  {("Ордер", "ПФ_MXL_Ордер1"): {"cells": [("p", RENAMED_OLD)]}})
        _, _, findings, _ = self.run_analyze()
        copy = [f for f in findings if f["ext"] == "Новое"]
        self.assertEqual([(f["check"], f["templates"][0]["areas_used"]) for f in copy], [("COPY_OLD_NAME", [])])
        self.assertIn("по коду не видно", copy[0]["detail"])

    def test_sheet_and_base_sheet(self):
        data = sheet([("p", "А"), ("p", "Б")], {"Шапка": (1, 1)}, base=[("p", "А"), ("p", "А")]).encode("utf-8")
        sh = tp.parse_sheet(data)
        self.assertEqual({k: v[1] for k, v in sh["names"].items()}, {"а": 1, "б": 1})
        self.assertEqual({k: v[1] for k, v in sh["base"].items()}, {"а": 2})
        self.assertEqual(sh["method"], "Merge")
        self.assertEqual(sh["names"]["б"][2], [(1, 0)])
        self.assertTrue(tp.in_area((1, 0), sh["areas"]["шапка"]))
        self.assertFalse(tp.in_area((0, 0), sh["areas"]["шапка"]))
        # parse_names reads the extension's sheet only
        self.assertEqual(tp.parse_names(data)["а"][1], 1)

    def test_copy_areas(self):
        code = "\n".join([
            "Процедура П()",
            "\tМ = УправлениеПечатью.МакетПечатнойФормы(\"Документ.Ордер.ПФ_MXL_Ордер1\");",
            "\tА = М.ПолучитьОбласть(\"Шапка|Колонка\");",
            "\tБ = ПолучитьМакет(\"ПФ_MXL_Ордер1\").ПолучитьОбласть(\"Подвал\");",
            "\tВ = ПолучитьМакет(\"Другой\").ПолучитьОбласть(\"Чужая\");",
            "\t// Г = М.ПолучитьОбласть(\"Закомментирована\");",
            "КонецПроцедуры"])
        self.assertEqual(tp.copy_areas([code], "ПФ_MXL_Ордер1"),
                         {"шапка": "Шапка", "колонка": "Колонка", "подвал": "Подвал"})
        self.assertIsNone(tp.copy_areas([code], "Нет"))

    def test_vendor_gone_without_pair(self):
        write(tpl_path(self.new, "Ордер", "ПФ_MXL_Ордер"), sheet([("p", "Номер")]))
        _, _, findings, _ = self.run_analyze()
        vendor = [f for f in findings if not f["ext"]]
        self.assertEqual([(f["check"], f["sev"]) for f in vendor], [("VENDOR_GONE", tp.INFO)])

    def test_common_template_scans_every_module(self):
        write(tpl_path(self.new, None, "ПФ_MXL_Общий"), sheet([("p", "Стало")]))
        extension(self.ext, "Общее", {"CommonModules/Печать/Ext/Module.bsl":
                                      "Процедура П(О)\n\tО.Параметры.Было = 1;\nКонецПроцедуры"})
        _, _, findings, _ = self.run_analyze()
        got = [(f["check"], f["ext"]) for f in findings if f["template"] == "ОбщийМакет.ПФ_MXL_Общий"]
        self.assertEqual(got, [("CODE_OLD_NAME", "Общее"), ("VENDOR_GONE", "")])

    def test_snippet(self):
        _, _, findings, _ = self.run_analyze()
        sql = tp.snippet(findings)
        self.assertEqual(sql.count("ПользовательскиеМакетыПечати.Объект = \"Документ.Ордер\""), 1)
        self.assertIn("ИмяМакета ПОДОБНО \"ПФ~_MXL~_Ордер%\" СПЕЦСИМВОЛ \"~\"", sql)
        self.assertIn("Использование КАК Использование", sql)
        self.assertNotIn("Служебный", sql)  # not a print template
        self.assertEqual(tp.snippet([]), "")

    # -- selection, CLI, MCP -------------------------------------------------------------------------------------
    def test_selection(self):
        _, _, findings, _ = self.run_analyze()
        levels = {f["check"]: selection.level("template-params", f) for f in findings}
        self.assertEqual(levels, {"CODE_OLD_NAME": "risk", "TEMPLATE_OLD_NAME": "risk", "VENDOR_RENAMED": "risk",
                                  "COPY_OLD_NAME": "info"})
        self.assertEqual(selection.level("template-params", {"check": "VENDOR_GONE"}), "info")
        self.assertEqual(selection.key("template-params", findings[0]),
                         "Старое|Документ.Ордер.ПФ_MXL_Ордер|CODE_OLD_NAME")
        flt = selection.Filter(module="ManagerModule")
        self.assertEqual(sum(flt.match("template-params", f) for f in findings), 4)

    def test_cli(self):
        out = os.path.join(self.tmp, "out")
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            cli.main(["template-params", "--ext-root", self.ext, "--config-old", self.old, "--config-new", self.new,
                      "--out", out])
        self.assertIn("vendor renamed 1", buf.getvalue())
        with open(os.path.join(out, "template-params.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(len(data["findings"]), 7)
        self.assertTrue(data["snippet"].startswith("ВЫБРАТЬ"))
        with open(os.path.join(out, "template-params.md"), encoding="utf-8") as fh:
            self.assertIn("## Пользовательские макеты в базе", fh.read())
        name, _ = selection.load_report(out)
        self.assertEqual(name, "template-params")
        with self.assertRaises(SystemExit):
            tp.main(["--ext-root", self.ext, "--config-old", self.old, "--config-new", self.new,
                     "--out", os.path.join(self.new, "out")])

    @unittest.skipUnless(HAVE_MCP, "MCP SDK is not installed")
    def test_mcp_tool(self):
        from cfe_rebase import mcp_server as srv
        out = os.path.join(self.tmp, "mcp")
        res = srv.check_template_params([self.ext], self.old, self.new, out)
        self.assertEqual(res["status"], "ok", res.get("error"))
        self.assertEqual(res["summary"]["vendor_renamed"], 1)
        self.assertEqual(len(res["items"]), 7)
        self.assertIn("ПользовательскиеМакетыПечати", res["snippet"])
        only = srv.check_template_params([self.ext], self.old, self.new, out, flag=["TEMPLATE_OLD_NAME"])
        self.assertEqual({it["check"] for it in only["items"]}, {"TEMPLATE_OLD_NAME"})


if __name__ == "__main__":
    unittest.main()
