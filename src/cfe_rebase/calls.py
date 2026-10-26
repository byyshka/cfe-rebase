# -*- coding: utf-8 -*-
"""Calls from extension code to configuration methods, checked against the new (and old) release.

The platform's extended module check (/CheckConfig -ExtendedModulesCheck) reports a call of a missing method
but not a wrong argument count; this command checks both. Resolved call forms (live code only - comments,
strings and #Удаление blocks are not code):
  ОбщийМодуль.Метод(...)                          common module (configuration or an extension's own)
  ОбщегоНазначения.ОбщийМодуль("М").Метод(...)    common module by string; ОбщийМодуль("Документы.X") - manager
  Документы.X.Метод(...), РегистрыСведений.X...   manager module; Константы.X -> value manager module
  МенеджерОбъектаПоПолномуИмени("Вид.X").Метод    manager module by string
  Метод(...), ЭтотОбъект.Метод(...)               in an adopted module - the base module; else global modules
  Перем = <one of the above forms>; Перем.Метод() within one method (common module, manager, object module), by
                                                  the nearest assignment above the call; a method found in another
                                                  module the variable is assigned (a branch) is not flagged
Flags: REMOVED (method was in old, absent in new), MISSING (absent in old and new), NOT_EXPORT,
TOO_FEW_ARGS, TOO_MANY_ARGS, SIGNATURE_CHANGED (old != new while the call still fits; with a risk grade).
"""
import argparse
import os
import sys
import time
from collections import defaultdict, OrderedDict

from . import bsl, fileio, metadata, refs
from .control_rebase import refuse_out_inside

PLATFORM_MANAGER = {x.lower() for x in """
ПустаяСсылка ПолучитьСсылку Выбрать ВыбратьИерархически ПолучитьФорму ПолучитьМакет ПолучитьИмяПредопределенного
СоздатьЭлемент СоздатьГруппу НайтиПоКоду НайтиПоНаименованию НайтиПоРеквизиту НайтиПоНомеру ПолучитьДанныеВыбора
СоздатьДокумент СоздатьНаборЗаписей ВыбратьПоРегистратору Остатки Обороты ОстаткиИОбороты СрезПоследних СрезПервых
Получить ПолучитьПоследнее ПолучитьПервое СоздатьМенеджерЗаписи Установить СоздатьМенеджерЗначения Создать
УстановитьИспользованиеИтогов ПолучитьИспользованиеИтогов ПересчитатьИтоги ПересчитатьТекущиеИтоги
ПересчитатьИтогиЗаПериод ПолучитьМаксимальныйПериодРассчитанныхИтогов ПолучитьМинимальныйПериодРассчитанныхИтогов
УстановитьМаксимальныйПериодРассчитанныхИтогов УстановитьМинимальныйПериодРассчитанныхИтогов
ПолучитьПериодРассчитанныхИтогов УстановитьПериодРассчитанныхИтогов УстановитьИспользованиеТекущихИтогов
ПолучитьИспользованиеТекущихИтогов УстановитьРазделениеИтогов ПолучитьРазделениеИтогов УстановитьИспользованиеАгрегатов
ПолучитьИспользованиеАгрегатов ПолучитьАгрегаты ОбновитьАгрегаты ПерестроитьАгрегаты ОчиститьАгрегаты
ПолучитьОптимальныеАгрегаты ИнициализироватьАгрегаты УстановитьРежимАгрегатов ПолучитьРежимАгрегатов
ЭтотУзел СоздатьУзел СоздатьБизнесПроцесс СоздатьЗадачу ВыбратьПоАдресации ПолучитьФормуВыбора ПолучитьФормуСписка
ПолучитьФормуНовогоОбъекта ПолучитьФормуГруппы ПолучитьФормуВыбораГруппы ПолучитьФормуОбъекта ПолучитьФормуЗаписи
ЗарегистрироватьИзменения УдалитьРегистрациюИзменений ВыбратьИзменения ЗаписатьИзменения ПрочитатьИзменения
ПолучитьСсылки ПолучитьМакетОформления ВыбратьЗаписи ВыбратьПоСчету ДвиженияССубконто Выгрузить Загрузить
""".split()}
PLATFORM_OBJECT = {x.lower() for x in """
Добавить Выгрузить Записать Удалить Вставить Найти Получить Очистить Количество Заполнить Прочитать Сдвинуть
Сортировать Свернуть Итог Скопировать ЗагрузитьКолонку ВыгрузитьКолонку НайтиСтроки Установить Загрузить
УникальныйИдентификатор ПолучитьОбъект Метаданные Модифицированность Заблокировать Разблокировать Выбрать
Следующий Выполнить ВыполнитьПакет Индекс Свойство Открыть Закрыть ПроверитьЗаполнение УстановитьНовыйНомер
СкопироватьКолонки Показать Активизировать СкомпоноватьРезультат ПолучитьМакет ПолучитьФорму
УстановитьПометкуУдаления Провести ОтменитьПроведение ЭтоНовый ПолучитьСсылкуНового УстановитьСсылкуНового
УстановитьНовыйКод ОбменДанными ДополнительныеСвойства ЗаполнитьПоЗначениямПоУмолчанию
""".split()}
CREATE_OBJECT = {"создать": "object", "создатьдокумент": "object", "создатьэлемент": "object",
                 "создатьгруппу": "object", "создатьнаборзаписей": "recordset", "создатьузел": "object",
                 "создатьбизнеспроцесс": "object", "создатьзадачу": "object", "создатьменеджерзначения": "valuemanager"}
TYPE_STEM = {"обработкаобъект": "Обработка", "отчетобъект": "Отчет", "документобъект": "Документ",
             "справочникобъект": "Справочник", "планвидовхарактеристикобъект": "ПланВидовХарактеристик",
             "планобменаобъект": "ПланОбмена", "бизнеспроцессобъект": "БизнесПроцесс", "задачаобъект": "Задача"}
SELF_CHAIN = {"этотобъект", "thisobject", "этаформа", "thisform"}
FORM_TO_VALUE = {"реквизитформывзначение", "formattributetovalue"}
DATA_TO_VALUE = {"данныеформывзначение", "formdatatovalue"}
EXPLAIN = OrderedDict([
    ("REMOVED", "метод был в old, в new его нет"),
    ("MISSING", "метода нет ни в old, ни в new (и ни в одном расширении набора)"),
    ("NOT_EXPORT", "метод вызывается из другого модуля, но не экспортный"),
    ("TOO_FEW_ARGS", "аргументов меньше, чем обязательных параметров"),
    ("TOO_MANY_ARGS", "аргументов больше, чем параметров"),
    ("SIGNATURE_CHANGED", "сигнатура old ≠ new, вызов по числу аргументов проходит"),
])
SEVERITY = {"REMOVED": 0, "MISSING": 1, "NOT_EXPORT": 1, "TOO_FEW_ARGS": 0, "TOO_MANY_ARGS": 0,
            "SIGNATURE_CHANGED": 3}


def kind_rel(kind, name, part):
    if part == "manager":
        return metadata.manager_module_rel(kind, name)
    if part == "valuemanager":
        return "%s/%s/Ext/ValueManagerModule.bsl" % (metadata.KINDS[kind][0], name)
    if part == "recordset":
        return "%s/%s/Ext/RecordSetModule.bsl" % (metadata.KINDS[kind][0], name)
    return "%s/%s/Ext/ObjectModule.bsl" % (metadata.KINDS[kind][0], name)


def classify_change(s_old, s_new, nargs, arg_list):
    """(risk | info, note) for a call whose target signature changed while the argument count still fits."""
    p_old, p_new = s_old.params, s_new.params
    names_old = [p["name"].casefold() for p in p_old]
    names_new = [p["name"].casefold() for p in p_new]
    def_old = [bsl.norm_default(p["default"]) for p in p_old]
    def_new = [bsl.norm_default(p["default"]) for p in p_new]
    n_old = len(p_old)
    if len(p_new) > n_old and names_new[:n_old] == names_old and def_new[:n_old] == def_old and all(
            d is not None for d in def_new[n_old:]):
        return "info", "в конец добавлены необязательные параметры: %s" % ", ".join(
            p["name"] for p in p_new[n_old:])
    notes = []
    risky = []
    for k in range(min(nargs, len(p_new))):
        old = names_old[k] if k < n_old else None
        if old == names_new[k]:
            continue
        arg = (arg_list[k] if k < len(arg_list) else "").casefold()
        if names_new[k] in arg and (old is None or old not in arg):
            notes.append("аргумент %d уже по новому имени" % (k + 1))
        else:
            risky.append("поз. %d: old %s, new %s, аргумент «%s»" % (
                k + 1, p_old[k]["name"] if k < n_old else "—", p_new[k]["name"],
                arg_list[k] if k < len(arg_list) else ""))
    omitted = []
    for k in range(nargs, len(p_new)):
        old = def_old[k] if k < n_old and names_old[k] == names_new[k] else "—"
        if old != def_new[k]:
            omitted.append("%s: умолчание %s → %s" % (p_new[k]["name"], p_old[k]["default"] if k < n_old else "нет",
                                                      p_new[k]["default"]))
    if risky:
        return "risk", "; ".join(notes + ["на тех же позициях другие параметры: " + " | ".join(risky)])
    if omitted:
        return "risk", "; ".join(notes + ["у пропущенного параметра другое умолчание: " + "; ".join(omitted)])
    if s_old.export != s_new.export:
        notes.append("Экспорт old %s → new %s" % (s_old.export, s_new.export))
    return "info", "; ".join(notes) or "изменение не затрагивает этот вызов"


class Resolver(object):
    def __init__(self, cfg_new, cfg_old, exts):
        self.new = cfg_new
        self.old = cfg_old
        self.exts = exts
        self.cm_new = cfg_new.common_modules()
        self.cm_old = cfg_old.common_modules() if cfg_old else {}
        self.cm_ext = defaultdict(list)       # casefold name -> [ext dumps that own or adopt the module]
        for e in exts:
            for k, v in e.common_modules().items():
                self.cm_ext[k].append(e)
        self.global_new = cfg_new.global_common_modules()
        self.global_old = cfg_old.global_common_modules() if cfg_old else []
        self._base_calls = {}
        self._method_calls = {}

    def is_common(self, name):
        k = name.casefold()
        return k in self.cm_new or k in self.cm_old or k in self.cm_ext

    def common_rel(self, name):
        k = name.casefold()
        real = self.cm_new.get(k) or self.cm_old.get(k)
        if real is None and self.cm_ext.get(k):
            real = self.cm_ext[k][0].common_modules()[k]
        return metadata.common_module_rel(real or name)

    def ext_defs(self, rel, name):
        """Methods named so in extension modules at this rel path: [(ext, method)]."""
        out = []
        for e in self.exts:
            mod = e.module(rel)
            if mod is not None:
                m = mod.find(name)
                if m is not None and not m.interceptors:
                    out.append((e, m))
        return out

    def base_calls(self, rel):
        """Normalized call texts of the new configuration module (a copy of the vendor text makes the same calls)."""
        key = metadata.rel_key(rel)
        if key not in self._base_calls:
            mod = self.new.module(rel)
            self._base_calls[key] = {c["norm"] for c in bsl.extract_calls(mod.toks)} if mod else set()
        return self._base_calls[key]

    def method_calls(self, dump, rel, name):
        """Calls of one method of a configuration module (live code only): {normalized name and arguments:
        {qualifier key: the call as written}}, e.g. {'метод(а)': {'общиймодуль': 'ОбщийМодуль.Метод'}}."""
        key = (dump.root, metadata.rel_key(rel), name.casefold())
        if key not in self._method_calls:
            res = defaultdict(dict)
            mod = dump.module(rel)
            m = mod.find(name) if mod is not None else None
            if m is not None:
                for c in mod.calls():
                    if c["method"] is m:
                        res[c["norm"]][qualifier(c["chain"])] = shown_call(c["chain"], c["name"])
            self._method_calls[key] = res
        return self._method_calls[key]

    def global_method(self, name, dump, globals_):
        for g in globals_:
            m = dump.find_method(metadata.common_module_rel(g), name)
            if m is not None:
                return g, m
        return None, None


def module_string(name):
    """ОбщегоНазначения.ОбщийМодуль("Документы.X") gives the manager of X (the БСП function evaluates the name):
    (kind, object name, part) for "<collection>.<name>", else None - a common module name."""
    coll, dot, obj = name.partition(".")
    kind = metadata.COLL2KIND.get(coll.casefold()) if dot else None
    if kind is None or not obj or "." in obj:
        return None
    return kind, obj, "valuemanager" if kind == "Константа" else "manager"


def nearest(assignments, tok):
    """The target of the last assignment before token tok, or None (the variable is used before it is assigned)."""
    before = [t for i, t in assignments if i < tok]
    return before[-1] if before else None


def var_target(rs, t):
    """(resolution class, rel, is_local, part) of a variable bound by var_targets."""
    kind, oname, part = t
    if kind == "ОбщийМодуль":
        return "var-module", rs.common_rel(oname), False, None
    return "var-" + part, kind_rel(kind, oname, part), False, part


def var_targets(toks, a, b, own_rel):
    """Variables assigned inside tokens [a, b) from a recognizable expression -> [(token index, (kind, name, part))]
    in text order: a variable assigned several modules resolves at each call by the nearest assignment above it."""
    res = {}
    i = a
    while i < b - 2:
        k, v, _ = toks[i]
        if k == "id" and toks[i + 1][0] == "op" and toks[i + 1][1] == "=" and not (
                i > 0 and toks[i - 1][0] == "op" and toks[i - 1][1] == "."):
            j = i + 2
            rhs = []
            while j < b and not (toks[j][0] == "op" and toks[j][1] == ";"):
                rhs.append(toks[j])
                j += 1
            vals = [t[1] for t in rhs]
            kinds = [t[0] for t in rhs]
            low = [x.casefold() if isinstance(x, str) else "" for x in vals]
            target = None
            n = len(rhs)
            if n >= 4 and kinds[-4] == "id" and vals[-3] == "(" and kinds[-2] == "str" and vals[-1] == ")":
                fn = low[-4]
                if fn in ("общиймодуль", "commonmodule"):
                    target = module_string(vals[-2]) or ("ОбщийМодуль", vals[-2], "module")
                elif fn in ("менеджеробъектапополномуимени", "objectmanagerbyfullname"):
                    parts = vals[-2].split(".")
                    if len(parts) >= 2 and parts[0] in metadata.KINDS:
                        target = (parts[0], parts[1], "manager")
            if target is None and n == 3 and kinds[0] == "id" and low[0] in metadata.COLL2KIND and vals[1] == "." \
                    and kinds[2] == "id":
                kind = metadata.COLL2KIND[low[0]]
                target = (kind, vals[2], "valuemanager" if kind == "Константа" else "manager")
            if target is None and n >= 6 and kinds[0] == "id" and low[0] in metadata.COLL2KIND and vals[1] == "." \
                    and kinds[2] == "id" and vals[3] == "." and kinds[4] == "id" and low[4] in CREATE_OBJECT \
                    and vals[5] == "(":
                target = (metadata.COLL2KIND[low[0]], vals[2], CREATE_OBJECT[low[4]])
            if target is None and n and kinds[0] == "id" and low[0] in FORM_TO_VALUE and own_rel:
                kind, name = metadata.module_owner(own_rel)
                if kind in metadata.KINDS and metadata.module_kind(own_rel) == "form":
                    target = (kind, name, "object")
            if target is None and n and kinds[0] == "id" and low[0] in DATA_TO_VALUE:
                for p in range(n - 2):
                    if low[p] in ("тип", "type") and vals[p + 1] == "(" and kinds[p + 2] == "str":
                        stem, _, name = vals[p + 2].partition(".")
                        if stem.casefold() in TYPE_STEM and name:
                            target = (TYPE_STEM[stem.casefold()], name, "object")
            res.setdefault(v.casefold(), []).append((i, target))  # None: assigned something else
            i = j
            continue
        i += 1
    return {v: seq for v, seq in res.items() if any(t is not None for _, t in seq)}


def qualifier(chain):
    return ".".join(chain).casefold()


def shown_call(chain, name):
    if chain and chain[0] == "@module":
        return 'ОбщийМодуль("%s").%s' % (chain[1], name)
    if chain and chain[0] == "@manager":
        return 'МенеджерОбъектаПоПолномуИмени("%s").%s' % (chain[1], name)
    return ".".join(chain + [name])


def copy_context(rs, cfg_new, cfg_old, rel, caller, call):
    """A call inside an &Вместо / &ИзменениеИКонтроль method of an adopted module, against the vendor body of the
    intercepted method: {annotation, target_in_new, vendor_old, vendor_new, new_as}. target_in_new - the intercepted
    method still exists in the new release; vendor_old / vendor_new - the same call (qualifier,
    name and arguments as written) is in the old / new body (vendor_old None without an old release); new_as - how the
    new body calls the same name with the same arguments under another qualifier (where the vendor moved it). None for
    any other caller."""
    if caller is None:
        return None
    for kind, target, _ in caller.interceptors:
        if kind in ("Вместо", "ИзменениеИКонтроль") and target:
            q = qualifier(call["chain"])
            new = rs.method_calls(cfg_new, rel, target).get(call["norm"], {})
            old = rs.method_calls(cfg_old, rel, target).get(call["norm"], {}) if cfg_old is not None else None
            return {"annotation": '&%s("%s")' % (kind, target), "target_in_new": cfg_new.find_method(rel, target)
                    is not None, "vendor_old": None if old is None else q in old, "vendor_new": q in new,
                    "new_as": [new[k] for k in sorted(new) if k != q][:3]}
    return None


def fixed_by_report(finding):
    """The finding goes away when the copy is re-ported onto the new vendor body."""
    copy = finding.get("copy")
    return bool(copy and copy["target_in_new"] and copy["vendor_old"] and not copy["vendor_new"])


def advice(flags, copy):
    """What to do with a finding inside an interceptor copy, or None."""
    if not copy or not flags:
        return None
    tool = "port_replace / merge-copy" if copy["annotation"].startswith("&Вместо") else "rebase_control / control-rebase"
    moved = "; в new вендор вызывает: %s" % ", ".join(copy["new_as"]) if copy["new_as"] else ""
    if not copy["target_in_new"]:
        return "перехватываемого метода в new нет (MISSING_BASE): перехват снять или перенести его логику, вызов уйдёт " \
               "вместе с копией"
    if copy["vendor_old"] and not copy["vendor_new"]:
        return "вызов из текста вендора в копии, в new у вендора его нет: переснять копию по new (%s)%s" % (tool, moved)
    if copy["vendor_old"] is False and not copy["vendor_new"]:
        return "вызов добавил автор копии: нужна правка автора" + moved
    if copy["vendor_new"]:
        return "тот же вызов есть и в теле вендора new"
    return None


def analyze(args):
    metadata.check_config(args.config_new, "--config-new")
    if args.config_old:
        metadata.check_config(args.config_old, "--config-old")
    cfg_new = metadata.Dump(args.config_new, "new")
    cfg_old = metadata.Dump(args.config_old, "old") if args.config_old else None
    exts = metadata.find_extensions(args.ext_root, args.ext, args.skip)
    if not exts:
        sys.exit("no extension dumps found under --ext-root")
    rs = Resolver(cfg_new, cfg_old, exts)
    index = refs.build(exts, refs.form_cache())
    findings = []
    stats = defaultdict(int)
    for ext in exts:
        for rel in ext.files(".bsl"):
            mod = ext.module(rel)
            if mod is None:
                continue
            stats["modules"] += 1
            adopted = cfg_new.exists(rel) or (cfg_old is not None and cfg_old.exists(rel))
            toks = mod.toks
            ranges = [(m.decl_line, m.end_line) for m in mod.methods]
            mvars = bsl.module_vars(toks, ranges)
            vt_cache = {}
            locals_cache = {}
            for c in mod.calls():
                chain, name, nargs = c["chain"], c["name"], c["args"]
                caller = c["method"]
                low = name.casefold()
                target = None      # (resolution class, rel, is_local, kind hint)
                alts = set()       # other modules the same variable is assigned in this method
                if chain and chain[0] == "?":
                    continue
                if not chain or (len(chain) == 1 and chain[0].casefold() in SELF_CHAIN):
                    if low in bsl.KEYWORDS:
                        continue
                    if mod.find(name) is not None:
                        continue  # own method of the module: not a configuration call
                    if adopted and (cfg_new.find_method(rel, name) is not None or rs.ext_defs(rel, name) or (
                            cfg_old is not None and cfg_old.find_method(rel, name) is not None)):
                        target = ("local", rel, True, None)
                    else:
                        g, _ = rs.global_method(name, cfg_new, rs.global_new)
                        if g is None and cfg_old is not None:
                            g, _ = rs.global_method(name, cfg_old, rs.global_old)
                        if g is None:
                            continue  # platform global context
                        target = ("global", metadata.common_module_rel(g), False, None)
                elif chain[0] == "@module":
                    ms = module_string(chain[1])
                    if ms is not None:
                        target = ("manager-str", kind_rel(*ms), False, ms[2])
                    else:
                        target = ("module-str", rs.common_rel(chain[1]), False, None)
                elif chain[0] == "@manager":
                    parts = chain[1].split(".")
                    if len(parts) < 2 or parts[0] not in metadata.KINDS or not metadata.KINDS[parts[0]][1]:
                        continue
                    target = ("manager-str", kind_rel(parts[0], parts[1], "manager"), False, "manager")
                elif len(chain) == 1:
                    q = chain[0].casefold()
                    vt = {}
                    if caller is not None:
                        if caller.name not in vt_cache:
                            a, b = mod.tok_range(caller)
                            vt_cache[caller.name] = var_targets(toks, a, b, rel)
                            locals_cache[caller.name] = bsl.assigned_names(toks, a, b) | {
                                p["name"].casefold() for p in caller.params}
                        vt = vt_cache[caller.name]
                    if q in vt:
                        picked = nearest(vt[q], c["tok"])
                        if picked is None:
                            stats["skipped_var_unknown_value"] += 1
                            continue
                        target = var_target(rs, picked)
                        alts = {var_target(rs, t)[1] for _, t in vt[q] if t is not None} - {target[1]}
                    elif q in mvars or (caller is not None and q in locals_cache.get(caller.name, ())):
                        stats["skipped_shadowed"] += 1
                        continue
                    elif rs.is_common(chain[0]):
                        target = ("module", rs.common_rel(chain[0]), False, None)
                    else:
                        continue
                elif len(chain) == 2 and chain[0].casefold() in metadata.COLL2KIND:
                    kind = metadata.COLL2KIND[chain[0].casefold()]
                    part = "valuemanager" if kind == "Константа" else "manager"
                    target = ("manager", kind_rel(kind, chain[1], part), False, part)
                else:
                    continue
                cls, trel, is_local, part = target
                stats["calls_" + cls] += 1
                if part in ("manager", "valuemanager") and low in PLATFORM_MANAGER:
                    stats["platform_manager"] += 1
                    continue
                if part in ("object", "recordset") and low in PLATFORM_OBJECT:
                    stats["platform_object"] += 1
                    continue
                t_new = cfg_new.find_method(trel, name)
                t_old = cfg_old.find_method(trel, name) if cfg_old else None
                flags = []
                note = ""
                layer = "конфигурация"
                sig_new = t_new
                if t_new is None and any(cfg_new.find_method(r, name) is not None or rs.ext_defs(r, name) or (
                        cfg_old is not None and cfg_old.find_method(r, name) is not None) for r in alts):
                    stats["skipped_var_other_assignment"] += 1  # an #Если / Если branch may assign that module
                    continue
                if t_new is None:
                    defs = rs.ext_defs(trel, name)
                    if defs:
                        layer = "расширение " + defs[0][0].name
                        sig_new = defs[0][1]
                        stats["resolved_to_extension"] += 1
                    elif t_old is not None:
                        flags.append("REMOVED")
                        note = "был в old (стр. %d), в new нет" % t_old.decl_line
                    else:
                        module_known = cfg_new.exists(trel) or (cfg_old is not None and cfg_old.exists(trel)) or any(
                            e.exists(trel) for e in exts)
                        if part in ("object", "recordset"):
                            stats["unresolved_object_method"] += 1
                            continue
                        if not module_known and part in ("manager", "valuemanager"):
                            kind, oname = metadata.module_owner(trel)
                            if cfg_new.object_exists(kind, oname) or any(e.object_exists(kind, oname) for e in exts):
                                stats["unresolved_manager_method"] += 1
                                continue
                        flags.append("MISSING")
                        note = "метода нет ни в old, ни в new" + ("" if module_known else "; модуля нет")
                        if part in ("manager", "valuemanager"):
                            kind, oname = metadata.module_owner(trel)
                            if not cfg_new.object_exists(kind, oname):
                                note += "; объекта %s.%s нет в new" % (kind, oname)
                if sig_new is not None:
                    if not is_local and not sig_new.export:
                        flags.append("NOT_EXPORT")
                    req, tot = sig_new.required(), sig_new.total()
                    fits_old = t_old is not None and t_old.required() <= nargs <= t_old.total()
                    if nargs < req:
                        flags.append("TOO_FEW_ARGS")
                    elif nargs > tot:
                        flags.append("TOO_MANY_ARGS")
                    if ("TOO_FEW_ARGS" in flags or "TOO_MANY_ARGS" in flags) and t_old is not None:
                        note = "аргументов %d, в new [%d..%d]; в old вызов %s" % (
                            nargs, req, tot, "проходил — сломано обновлением" if fits_old else "тоже не проходил")
                    elif "TOO_FEW_ARGS" in flags or "TOO_MANY_ARGS" in flags:
                        note = "аргументов %d, допустимо [%d..%d]" % (nargs, req, tot)
                    if not flags and t_new is not None and t_old is not None and t_new.sig_key() != t_old.sig_key():
                        grade, note = classify_change(t_old, t_new, nargs, c["arg_list"])
                        if grade == "risk" and adopted and c["norm"] in rs.base_calls(rel):
                            grade, note = "info", "вызов дословно как в модуле new (перенесённая копия); " + note
                        flags.append("SIGNATURE_CHANGED")
                        stats["sig_changed_" + grade] += 1
                        note = "[%s] %s" % ("риск" if grade == "risk" else "инфо", note)
                if not flags:
                    continue
                shown = shown_call(chain, name)
                copy = copy_context(rs, cfg_new, cfg_old, rel, caller, c) if adopted else None
                findings.append({
                    "ext": ext.name, "module": rel, "module_title": metadata.module_title(rel), "line": c["line"],
                    "caller": caller.name if caller else "", "call": shown, "args": nargs, "class": cls,
                    "target": "%s.%s" % (metadata.module_title(trel), name), "layer": layer,
                    "sig_new": sig_new.signature() if sig_new is not None else None,
                    "sig_old": t_old.signature() if t_old is not None else None,
                    "line_new": t_new.decl_line if t_new else None, "line_old": t_old.decl_line if t_old else None,
                    "flags": flags, "note": note, "copy": copy, "advice": advice(flags, copy),
                    "caller_live": index.liveness(rel, caller),
                    "src": mod.lines[c["line"] - 1].strip()[:200] if 0 < c["line"] <= len(mod.lines) else ""})
    return cfg_new, cfg_old, exts, findings, stats


def render(cfg_new, cfg_old, exts, findings, stats, elapsed):
    md = ["# Вызовы методов конфигурации из расширений", ""]
    md.append("new: `%s` (%s), old: `%s` (%s). Расширений: %d, модулей: %d. Время: %.1f с." % (
        cfg_new.root, cfg_new.version, cfg_old.root if cfg_old else "—", cfg_old.version if cfg_old else "—",
        len(exts), stats.get("modules", 0), elapsed))
    md.append("")
    counts = defaultdict(int)
    for f in findings:
        for fl in f["flags"]:
            counts[fl] += 1
    md.append(fileio.md_table(["Флаг", "Вызовов", "Смысл"], [(k, counts.get(k, 0), v) for k, v in EXPLAIN.items()]))
    md.append("")
    resolved = ", ".join("%s — %d" % (k[6:], v) for k, v in sorted(stats.items()) if k.startswith("calls_"))
    md.append("Разрешено вызовов по видам: %s. Методы платформы у менеджеров/объектов пропущены: %d/%d; "
              "не разрешено (метод объекта/менеджера не найден, вероятно платформенный): %d/%d; "
              "локальные переменные и параметры: %d." % (
                  resolved, stats.get("platform_manager", 0), stats.get("platform_object", 0),
                  stats.get("unresolved_object_method", 0), stats.get("unresolved_manager_method", 0),
                  stats.get("skipped_shadowed", 0)))
    md.append("")
    main = [f for f in findings if f["flags"] != ["SIGNATURE_CHANGED"] or f["note"].startswith("[риск]")]
    info = [f for f in findings if f["flags"] == ["SIGNATURE_CHANGED"] and not f["note"].startswith("[риск]")]
    main.sort(key=lambda f: (min(SEVERITY[x] for x in f["flags"]), f["ext"], f["module"], f["line"]))
    md.append("## Находки")
    md.append("")
    in_copy = [f for f in main if f.get("copy")]
    if in_copy:
        md.append("Внутри копий `&Вместо` / `&ИзменениеИКонтроль` — %d из %d; из них %d — вызов из тела вендора, "
                  "который вендор в new убрал: такие находки уходят пересъёмкой копии по new (`port_replace` / "
                  "`rebase_control`), замену методу искать не нужно." % (len(in_copy), len(main),
                                                                          sum(1 for f in in_copy if fixed_by_report(f))))
        md.append("")
    md.append(fileio.md_table(
        ["Флаги", "Расширение", "Модуль:строка", "В методе (живость)", "Вызов", "Арг.", "Сигнатура new", "Сигнатура old",
         "Пояснение"],
        [(", ".join(f["flags"]), f["ext"], "%s:%d" % (f["module_title"], f["line"]),
          "%s (%s)" % (f["caller"] or "—", f["caller_live"]), f["call"], f["args"], f["sig_new"] or "—",
          f["sig_old"] or "—", f["note"] + ("; " + f["advice"] if f.get("advice") else "")) for f in main]))
    md.append("")
    md.append("## SIGNATURE_CHANGED без риска для вызова — %d" % len(info))
    md.append("")
    md.append("Сигнатура поменялась, но вызов совместим: добавлены необязательные параметры в конец, аргументы уже по "
              "новым именам или вызов дословно повторяет вызов в модуле new.")
    md.append("")
    by_target = defaultdict(list)
    for f in info:
        by_target[(f["target"], f["sig_old"], f["sig_new"])].append(f)
    md.append(fileio.md_table(["Цель", "Вызовов", "Где (первые)", "old → new", "Пояснение (первый вызов)"],
                              [(t[0], len(v), ", ".join("%s:%d" % (x["module_title"], x["line"]) for x in v[:3]),
                                "%s → %s" % (t[1], t[2]), v[0]["note"]) for t, v in sorted(by_target.items())]))
    return "\n".join(md)


def main(argv=None):
    fileio.console_utf8()
    ap = argparse.ArgumentParser(description="Calls from extensions to configuration methods: presence and "
                                             "argument count against the new (and old) release")
    ap.add_argument("--ext-root", action="extend", nargs="+", required=True)
    ap.add_argument("--ext", action="extend", nargs="+")
    ap.add_argument("--skip", action="extend", nargs="+")
    ap.add_argument("--config-old")
    ap.add_argument("--config-new", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    refuse_out_inside(args.out, [("--ext-root", r) for r in args.ext_root] +
                      [("--config-old", args.config_old), ("--config-new", args.config_new)], "calls")
    t0 = time.time()
    cfg_new, cfg_old, exts, findings, stats = analyze(args)
    elapsed = time.time() - t0
    md = render(cfg_new, cfg_old, exts, findings, stats, elapsed)
    data = {"config_new": cfg_new.root, "config_old": cfg_old.root if cfg_old else None,
            "extensions": [e.name for e in exts], "stats": dict(stats), "findings": findings}
    md_path, json_path = fileio.write_report(args.out, "calls", md, data)
    counts = defaultdict(int)
    for f in findings:
        for fl in f["flags"]:
            counts[fl] += 1
    print("extensions: %d, modules: %d, findings: %d, %.1fs" % (len(exts), stats.get("modules", 0), len(findings),
                                                                elapsed))
    print("flags: " + ", ".join("%s=%d" % kv for kv in sorted(counts.items())))
    in_copy = [f for f in findings if f.get("copy")]
    if in_copy:
        print("in interceptor copies: %d, of them the vendor dropped the call in new (re-port the copy): %d" % (
            len(in_copy), sum(1 for f in in_copy if fixed_by_report(f))))
    print(md_path)
    print(json_path)


if __name__ == "__main__":
    main()
