# -*- coding: utf-8 -*-
"""Sweep of the extensions' query texts, DCS schemas and metadata names in string literals against the new release.

Three parts, all on dumps, no base needed (live code only - comments and #Удаление blocks are not code):
  query     live query texts - string literals of one method glued together, <QueryText> of dynamic lists in
            Form.xml, dataset queries of DCS schemas: every table exists (tabular sections and virtual tables too),
            every Alias.Field[.Sub...] resolves (up to three reference levels deep, tabular section columns
            through Alias.Section.Column), every ЗНАЧЕНИЕ(Вид.X.Y) exists;
  dcs       DCS schemas of the extensions: dataset fields the query returns, identifiers of calculated fields,
            resources, fields used in settings variants;
  literal   names in string literals and code chains: "ДокументСсылка.X" type names, "Перечисление.X.Y",
            "Документ.X.Форма|Макет|Команда.Y", Перечисления.X.Y / Справочники.X.Y,
            Метаданные.<Коллекция>.<Имя>[.<Дочерние>.<Имя>], РольДоступна("X"), ПолучитьФункциональнуюОпцию("X").
Every finding carries its state in old: "было корректно" (broken by the update) or "тоже не разрешается".
"""
import argparse
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict, OrderedDict

from . import fileio, metadata, qparse
from .control_rebase import refuse_out_inside

SCH = "{http://v8.1c.ru/8.1/data-composition-system/schema}"
SET = "{http://v8.1c.ru/8.1/data-composition-system/settings}"
XSI = "{http://www.w3.org/2001/XMLSchema-instance}type"
LF = "{http://v8.1c.ru/8.3/xcf/logform}"
SYSTEM_PREFIX = ("СистемныеПоля.", "SystemFields.", "ПользовательскиеПоля.", "UserFields.", "ПараметрыДанных.",
                 "DataParameters.")
CALC_WORDS = {x.casefold() for x in """
ВЫБОР КОГДА ТОГДА ИНАЧЕ КОНЕЦ И ИЛИ НЕ ЕСТЬNULL СУММА МАКСИМУМ МИНИМУМ СРЕДНЕЕ КОЛИЧЕСТВО ИСТИНА ЛОЖЬ NULL ЗНАЧЕНИЕ
ТИПЗНАЧЕНИЯ ТИП ВЫРАЗИТЬ КАК ЧИСЛО СТРОКА ДАТА БУЛЕВО НЕОПРЕДЕЛЕНО ПРЕДСТАВЛЕНИЕ ЕСТЬ В МЕЖДУ ПОДОБНО ОКР ЦЕЛ ПОДСТРОКА
НАЧАЛОПЕРИОДА КОНЕЦПЕРИОДА ДОБАВИТЬКДАТЕ РАЗНОСТЬДАТ ГОД МЕСЯЦ ДЕНЬ ВЫЧИСЛИТЬ ВЫЧИСЛИТЬВЫРАЖЕНИЕ ФОРМАТ СТРДЛИНА ACOS
ASIN
""".split()}
TYPE_STEMS = {
    "СправочникСсылка": "Справочник", "СправочникОбъект": "Справочник", "СправочникМенеджер": "Справочник",
    "СправочникВыборка": "Справочник", "СправочникСписок": "Справочник",
    "ДокументСсылка": "Документ", "ДокументОбъект": "Документ", "ДокументМенеджер": "Документ",
    "ДокументВыборка": "Документ", "ДокументСписок": "Документ",
    "ПеречислениеСсылка": "Перечисление", "ПеречислениеМенеджер": "Перечисление",
    "ПланВидовХарактеристикСсылка": "ПланВидовХарактеристик", "ПланВидовХарактеристикОбъект": "ПланВидовХарактеристик",
    "ПланОбменаСсылка": "ПланОбмена", "ПланОбменаОбъект": "ПланОбмена",
    "БизнесПроцессСсылка": "БизнесПроцесс", "БизнесПроцессОбъект": "БизнесПроцесс",
    "ЗадачаСсылка": "Задача", "ЗадачаОбъект": "Задача",
    "ОбработкаОбъект": "Обработка", "ОбработкаМенеджер": "Обработка", "ОтчетОбъект": "Отчет", "ОтчетМенеджер": "Отчет",
    "РегистрСведенийНаборЗаписей": "РегистрСведений", "РегистрСведенийМенеджерЗаписи": "РегистрСведений",
    "РегистрСведенийКлючЗаписи": "РегистрСведений", "РегистрСведенийЗапись": "РегистрСведений",
    "РегистрСведенийМенеджер": "РегистрСведений",
    "РегистрНакопленияНаборЗаписей": "РегистрНакопления", "РегистрНакопленияЗапись": "РегистрНакопления",
    "РегистрНакопленияМенеджер": "РегистрНакопления",
    "КонстантаМенеджерЗначения": "Константа", "ОпределяемыйТип": "ОпределяемыйТип",
    "CatalogRef": "Справочник", "CatalogObject": "Справочник", "DocumentRef": "Документ",
    "DocumentObject": "Документ", "EnumRef": "Перечисление", "InformationRegisterRecordSet": "РегистрСведений",
    "AccumulationRegisterRecordSet": "РегистрНакопления", "DataProcessorObject": "Обработка",
    "ReportObject": "Отчет", "ChartOfCharacteristicTypesRef": "ПланВидовХарактеристик",
}
TYPE_LIT_RE = re.compile(r"(?<![\wЁё])(%s)\.([A-Za-zА-Яа-яЁё_][\wЁё]*)" % "|".join(
    sorted(TYPE_STEMS, key=len, reverse=True)))
VALUE_LIT_RE = re.compile(r"^(Перечисление|Справочник|ПланВидовХарактеристик|ПланСчетов)\.([\wЁё]+)\.([\wЁё]+)$")
STANDARD_FORMS = {x.casefold() for x in """
ФормаОбъекта ФормаЭлемента ФормаГруппы ФормаСписка ФормаВыбора ФормаВыбораГруппы ФормаЗаписи ФормаНабораЗаписей
ФормаНовогоОбъекта ФормаОтчета ФормаНастроек ФормаВарианта Форма ObjectForm ItemForm FolderForm ListForm ChoiceForm
FolderChoiceForm RecordForm RecordSetForm Form
""".split()}
VALUE_CHAIN_COLL = {"Перечисления": "Перечисление", "Справочники": "Справочник",
                    "ПланыВидовХарактеристик": "ПланВидовХарактеристик", "ПланыСчетов": "ПланСчетов"}
MD_COLL = {"ОбщиеМодули": "ОбщийМодуль", "Роли": "Роль", "ФункциональныеОпции": "ФункциональнаяОпция",
           "ОбщиеФормы": "ОбщаяФорма", "ОбщиеМакеты": "ОбщийМакет", "ОбщиеКоманды": "ОбщаяКоманда",
           "Подсистемы": "Подсистема", "ОпределяемыеТипы": "ОпределяемыйТип", "ПараметрыСеанса": "ПараметрСеанса",
           "РегламентныеЗадания": "РегламентноеЗадание", "ПодпискиНаСобытия": "ПодпискаНаСобытие",
           "CommonModules": "ОбщийМодуль", "Roles": "Роль"}
MD_COLL.update({coll: kind for kind, v in metadata.KINDS.items() if v[1] for coll in v[1:]})
MD_CHILD = {"Реквизиты": "attrs", "ТабличныеЧасти": "ts", "Измерения": "dims", "Ресурсы": "res",
            "ЗначенияПеречисления": "enum_values", "Макеты": "templates", "Формы": "forms", "Команды": "commands",
            "Attributes": "attrs", "TabularSections": "ts", "Dimensions": "dims", "Resources": "res"}
COLLECTION_METHODS = {x.casefold() for x in ("Найти", "Find", "Количество", "Count", "Получить", "Get", "Содержит",
                                             "Contains")}
MANAGER_PROPS = {"пустаяссылка", "emptyref"}
REPLACE_FUNCS = {"стрзаменить", "strreplace"}
NAMED_CALLS = {"рольдоступна": "Роль", "isinrole": "Роль", "получитьфункциональнуюопцию": "ФункциональнаяОпция",
               "getfunctionaloption": "ФункциональнаяОпция"}
FULL_KINDS = dict(qparse.KIND_Q)
FULL_KINDS.update({k: k for k, v in metadata.KINDS.items() if v[1]})
FULL_KINDS.update({"DataProcessor": "Обработка", "Report": "Отчет"})
FULL_CHILD = {"Форма": "forms", "Form": "forms", "Макет": "templates", "Template": "templates",
              "Команда": "commands", "Command": "commands"}
FULL_NAME_RE = re.compile(r"^(%s)\.([\wЁё]+)\.(%s)\.([\wЁё]+)$" % (
    "|".join(sorted(FULL_KINDS, key=len, reverse=True)), "|".join(FULL_CHILD)))
FORM3_RE = re.compile(r"^(%s)\.([\wЁё]+)\.([\wЁё]+)$" % "|".join(sorted(FULL_KINDS, key=len, reverse=True)))
RELEASE_FREE = "от релиза не зависит"
EXPLAIN = OrderedDict([
    ("query/таблица", "таблицы (объекта, табличной части, виртуальной таблицы) нет в new"),
    ("query/поле", "поле запроса не разрешается ни в одной из таблиц псевдонима"),
    ("query/значение", "ЗНАЧЕНИЕ(...): нет объекта, значения перечисления или предопределённого"),
    ("dcs/поле набора", "поле набора СКД не возвращается его запросом"),
    ("dcs/выражение", "идентификатор в выражении вычисляемого поля не найден среди полей схемы"),
    ("dcs/ресурс", "ресурс по полю, которого нет в наборах и вычисляемых полях"),
    ("dcs/поле настроек", "поле варианта настроек не найдено в схеме или не разрешается"),
    ("literal/тип", "имя типа в строке: объекта нет в new"),
    ("literal/значение", "«Вид.X.Y» строкой или цепочкой менеджера: нет объекта, значения или предопределённого"),
    ("literal/метаданные", "Метаданные.<Коллекция>.<Имя>[...]: нет объекта или дочернего элемента"),
    ("literal/имя", "«Вид.X.Форма|Макет|Команда.Y» и «Вид.X.ФормаОбъекта» строкой, РольДоступна(\"X\"), "
                    "ПолучитьФункциональнуюОпцию(\"X\"): нет объекта или дочернего элемента"),
])


def line_of(strs, pos):
    """Source line of a position in literals glued with '\\n' (approximate)."""
    if not strs:
        return 0
    off = 0
    for v, line in strs:
        if pos <= off + len(v):
            return line + v[:max(0, pos - off)].count("\n")
        off += len(v) + 1
    return strs[-1][1]


def dcs_root(ext, rel):
    """Parsed Template.xml if it is a DCS schema, else None."""
    head = fileio.read_text(ext.path(rel))[:3000]
    if "DataCompositionSchema" not in head:
        return None
    try:
        return ET.parse(ext.path(rel)).getroot()
    except ET.ParseError:
        return None


def replace_args(toks):
    """Indexes of string tokens that make the second argument - the search pattern - of СтрЗаменить / StrReplace;
    the replacement never stands in the query text, so it masks nothing."""
    out = set()
    for i, (k, v, _) in enumerate(toks):
        if k != "id" or v.casefold() not in REPLACE_FUNCS or i + 1 >= len(toks) or toks[i + 1][1] != "(":
            continue
        depth = 0
        arg = 0
        for j in range(i + 1, len(toks)):
            tk, tv, _ = toks[j]
            if tk == "op" and tv in ("(", "["):
                depth += 1
            elif tk == "op" and tv in (")", "]"):
                depth -= 1
                if depth == 0:
                    break
            elif tk == "op" and tv == "," and depth == 1:
                arg += 1
            elif tk == "str" and arg == 1 and depth == 1:
                out.add(j)
    return out


def query_texts(ext):
    """(rel, where, text, strs, repl) for every live query text of one extension; strs maps back to lines, repl holds
    the casefolded identifier-like literals passed to СтрЗаменить in the same method."""
    for rel in ext.files(".bsl"):
        mod = ext.module(rel)
        if mod is None:
            continue
        repl_idx = replace_args(mod.toks)
        buckets = OrderedDict()
        for idx, (kind, value, line) in enumerate(mod.toks):
            if kind != "str":
                continue
            m = mod.method_at_line(line)
            strs, repl = buckets.setdefault(m.name if m else "(модуль)", ([], set()))
            strs.append((value, line))
            if idx in repl_idx and re.fullmatch(qparse.ID, value.strip() or "-"):
                repl.add(value.strip().casefold())
        for name, (strs, repl) in buckets.items():
            blob = "\n".join(v for v, _ in strs)
            if qparse.SELECT_RE.search(blob) or qparse.VALUE_RE.search(blob):
                yield rel, "метод " + name, blob, strs, repl
    for rel in ext.files("Form.xml"):
        try:
            root = ET.parse(ext.path(rel)).getroot()
        except ET.ParseError:
            continue
        for a in root.iter(LF + "Attribute"):
            st = a.find(LF + "Settings")
            if st is None:
                continue
            q = st.findtext(LF + "QueryText")
            if q and (st.findtext(LF + "ManualQuery") or "").strip() == "true":
                yield rel, "список " + (a.get("name") or ""), q, None, set()
    for rel in ext.files("Template.xml"):
        root = dcs_root(ext, rel)
        if root is None:
            continue
        for ds in root.iter(SCH + "dataSet"):
            q = ds.findtext(SCH + "query")
            if q:
                yield rel, "набор " + (ds.findtext(SCH + "name") or ""), q, None, set()


class Sweep(object):
    def __init__(self, cfg_new, cfg_old, exts):
        self.new = qparse.Sources(cfg_new, exts)
        self.old = qparse.Sources(cfg_old, exts) if cfg_old is not None else None
        self.findings = []
        self.masked = []
        self.masked_seen = set()
        self.stats = defaultdict(int)

    def add(self, part, kind, sev, ext, rel, where, line, ref, note, old_ok):
        """old_ok: True / False from the old run, None without old, or a label (RELEASE_FREE) as is."""
        if isinstance(old_ok, str):
            old = old_ok
        else:
            old = None if old_ok is None else ("было корректно" if old_ok else "тоже не разрешается")
        self.findings.append({"part": part, "kind": kind, "sev": sev, "ext": ext.name, "file": rel, "where": where,
                              "line": line, "ref": ref, "note": note, "old": old})

    def mask(self, ext, rel, where, line, ref, note, why):
        self.masked.append({"ext": ext.name, "file": rel, "where": where, "line": line, "ref": ref, "note": note,
                            "why": why})

    def old_check(self, fn):
        """True if the check passes in old, False if not, None without old."""
        return None if self.old is None else fn(self.old)

    # -- query texts ---------------------------------------------------------------------------------------------
    def queries(self, ext):
        for rel, where, text, strs, repl in query_texts(ext):
            self.stats["query_texts"] += 1
            q = qparse.clean(text)
            alias_tables = defaultdict(list)
            for alias, kind, name, sub, pos in qparse.bind_aliases(q):
                self.stats["query_tables"] += 1
                line = line_of(strs, pos)
                if self.new.obj(kind, name) is None:
                    self.add("query", "таблица", "ОШИБКА?", ext, rel, where, line, "%s.%s" % (kind, name),
                             "объекта нет в new", self.old_check(lambda s: s.obj(kind, name) is not None))
                    continue
                if sub is not None and self.new.table_fields(kind, name, sub) is None:
                    if sub.casefold() in self.new.names(kind, name, "templates"):
                        continue  # Отчет.X.Макет and the like
                    self.add("query", "таблица", "ОШИБКА?", ext, rel, where, line, "%s.%s.%s" % (kind, name, sub),
                             "нет табличной части или виртуальной таблицы «%s»" % sub,
                             self.old_check(lambda s: s.table_fields(kind, name, sub) is not None))
                    continue
                if alias:
                    alias_tables[alias.casefold()].append((kind, name, sub))
            other = qparse.other_sources(q)
            seen = set()
            for alias, segs, pos in qparse.field_refs(q):
                akey = alias.casefold()
                if akey not in alias_tables:
                    continue
                tables = alias_tables[akey]
                masked = None
                if akey in other:
                    masked = "псевдоним переиспользован для временной таблицы или подзапроса"
                elif any(s.casefold() in repl for s in segs):
                    masked = "имя подставляется через СтрЗаменить"
                if masked:
                    self.stats["query_fields_masked"] += 1
                    mkey = (ext.name, rel, where, akey, tuple(s.casefold() for s in segs))
                    if mkey in self.masked_seen:
                        continue
                    self.masked_seen.add(mkey)
                    prob = self.new.resolve_chain(tables, segs)
                    if prob:
                        self.mask(ext, rel, where, line_of(strs, pos), alias + "." + ".".join(segs), prob, masked)
                    continue
                key = (akey, tuple(s.casefold() for s in segs))
                if key in seen:
                    continue
                seen.add(key)
                self.stats["query_fields"] += 1
                prob = self.new.resolve_chain(tables, segs)
                why = self.new.unjudged(tables, segs) if prob else None
                if why:
                    self.mask(ext, rel, where, line_of(strs, pos), alias + "." + ".".join(segs), prob, why)
                elif prob:
                    self.add("query", "поле", "ОШИБКА?", ext, rel, where, line_of(strs, pos),
                             alias + "." + ".".join(segs), prob,
                             self.old_check(lambda s: s.resolve_chain(tables, segs) is None))
            for m in qparse.VALUE_RE.finditer(q):
                kind, name, value = qparse.KIND_CF[m.group(1).casefold()], m.group(2), m.group(3)
                self.stats["query_values"] += 1
                prob = self.new.value_problem(kind, name, value)
                if prob:
                    self.add("query", "значение", "ОШИБКА?", ext, rel, where, line_of(strs, m.start()), m.group(0),
                             prob, self.old_check(lambda s: s.value_problem(kind, name, value) is None))

    # -- DCS schemas ---------------------------------------------------------------------------------------------
    def dcs(self, ext):
        for rel in ext.files("Template.xml"):
            root = dcs_root(ext, rel)
            if root is None:
                continue
            self.stats["dcs_schemas"] += 1
            known = {}
            field_src = {}
            for ds in root.iter(SCH + "dataSet"):
                dsname = ds.findtext(SCH + "name") or ""
                q = qparse.clean(ds.findtext(SCH + "query") or "")
                exprs = qparse.select_aliases(q) if q.strip() else {}
                exprs_cf = {k.casefold(): v for k, v in exprs.items()}
                alias_tables = defaultdict(list)
                for alias, kind, name, sub, _ in (qparse.bind_aliases(q) if q.strip() else []):
                    if alias:
                        alias_tables[alias.casefold()].append((kind, name, sub))
                for f in ds.findall(SCH + "field"):
                    dpath = (f.findtext(SCH + "dataPath") or "").strip()
                    fld = (f.findtext(SCH + "field") or "").strip()
                    if not dpath:
                        continue
                    known[dpath] = (dsname, fld)
                    self.stats["dcs_dataset_fields"] += 1
                    if q.strip() and fld and "." not in fld and fld.casefold() not in exprs_cf:
                        self.add("dcs", "поле набора", "РИСК", ext, rel, "набор " + dsname, 0, fld,
                                 "поле набора не возвращается его запросом", RELEASE_FREE)
                    e = exprs_cf.get(fld.casefold())
                    m = re.match(r"^(%s)((?:\.%s)+)$" % (qparse.ID, qparse.ID), e.strip()) if e else None
                    if m and m.group(1).casefold() in alias_tables:
                        field_src[dpath] = (alias_tables[m.group(1).casefold()],
                                            [s for s in m.group(2).split(".") if s])
            for cf in root.iter(SCH + "calculatedField"):
                dpath = (cf.findtext(SCH + "dataPath") or "").strip()
                if dpath:
                    known[dpath] = ("calc", cf.findtext(SCH + "expression") or "")
            for p in root.iter(SCH + "parameter"):
                nm = (p.findtext(SCH + "name") or "").strip()
                if nm:
                    known["ПараметрыДанных." + nm] = ("param", "")
            for cf in root.iter(SCH + "calculatedField"):
                expr = cf.findtext(SCH + "expression") or ""
                for m in re.finditer(r"(?<![\wЁё.&\"])(%s(?:\.%s)*)" % (qparse.ID, qparse.ID), expr):
                    token = m.group(1)
                    if token.split(".")[0].casefold() in CALC_WORDS or token in known:
                        continue
                    if any(token.startswith(k + ".") for k in known) or token.startswith(SYSTEM_PREFIX):
                        continue
                    if re.match(r"^(Перечисление|Справочник|Документ)\.", token):
                        continue
                    self.add("dcs", "выражение", "ПРОВЕРИТЬ", ext, rel, "поле " + (cf.findtext(SCH + "dataPath") or ""),
                             0, token, "идентификатор не найден среди полей схемы", RELEASE_FREE)
            for tf in root.iter(SCH + "totalField"):
                dpath = (tf.findtext(SCH + "dataPath") or "").strip()
                self.stats["dcs_total_fields"] += 1
                if dpath and dpath not in known:
                    self.add("dcs", "ресурс", "РИСК", ext, rel, "ресурсы", 0, dpath,
                             "ресурс по полю, которого нет в наборах и вычисляемых полях", RELEASE_FREE)
            for vname, path, where in settings_fields(root):
                self.stats["dcs_settings_fields"] += 1
                if path in known or path.startswith(SYSTEM_PREFIX):
                    continue
                prefix = next((k for k in sorted(known, key=len, reverse=True) if path.startswith(k + ".")), None)
                if prefix is None:
                    self.add("dcs", "поле настроек", "РИСК", ext, rel, "вариант «%s» (%s)" % (vname, where), 0, path,
                             "поля нет в схеме", RELEASE_FREE)
                    continue
                src = field_src.get(prefix)
                if src:
                    tables, segs = src
                    chain = segs + path[len(prefix) + 1:].split(".")
                    prob = self.new.resolve_chain(tables, chain)
                    why = self.new.unjudged(tables, chain) if prob else None
                    if why:
                        self.mask(ext, rel, "вариант «%s» (%s)" % (vname, where), 0, path, prob, why)
                    elif prob:
                        self.add("dcs", "поле настроек", "РИСК", ext, rel, "вариант «%s» (%s)" % (vname, where), 0,
                                 path, prob, self.old_check(lambda s: s.resolve_chain(tables, chain) is None))

    # -- names in literals and code chains -----------------------------------------------------------------------
    def literals(self, ext):
        for rel in ext.files(".bsl"):
            mod = ext.module(rel)
            if mod is None:
                continue
            toks = mod.toks
            for i, (k, v, line) in enumerate(toks):
                if k == "str":
                    self._string(ext, rel, line, v)
                elif k == "id":
                    self._chain(ext, rel, toks, i)
                    self._named_call(ext, rel, toks, i)

    def _string(self, ext, rel, line, v):
        for m in TYPE_LIT_RE.finditer(v):
            kind, name = TYPE_STEMS[m.group(1)], m.group(2)
            self.stats["literal_types"] += 1
            if self.new.obj(kind, name) is None:
                self.add("literal", "тип", "ОШИБКА?", ext, rel, "строка", line, m.group(0), "объекта нет в new",
                         self.old_check(lambda s: s.obj(kind, name) is not None))
        pm = VALUE_LIT_RE.match(v.strip())
        if pm:
            kind, name, val = pm.groups()
            self.stats["literal_values"] += 1
            self._value(ext, rel, line, v.strip(), kind, name, val)
        sm = None if pm else FORM3_RE.match(v.strip())
        if sm and sm.group(3).casefold() in STANDARD_FORMS:  # "Документ.X.ФормаОбъекта": the object must exist
            kind, name = FULL_KINDS[sm.group(1)], sm.group(2)
            self.stats["literal_full_names"] += 1
            if self.new.obj(kind, name) is None:
                self.add("literal", "имя", "ОШИБКА?", ext, rel, "строка", line, v.strip(), "объекта нет в new",
                         self.old_check(lambda s: s.obj(kind, name) is not None))
        fm = FULL_NAME_RE.match(v.strip())
        if fm:
            kind, name, bucket, child = FULL_KINDS[fm.group(1)], fm.group(2), FULL_CHILD[fm.group(3)], fm.group(4)
            self.stats["literal_full_names"] += 1
            if self.new.obj(kind, name) is None:
                self.add("literal", "имя", "ОШИБКА?", ext, rel, "строка", line, v.strip(), "объекта нет в new",
                         self.old_check(lambda s: s.obj(kind, name) is not None))
                return

            def ok(s):
                return s.obj(kind, name) is not None and child.casefold() in s.names(kind, name, bucket)
            if not ok(self.new):
                self.add("literal", "имя", "ОШИБКА?", ext, rel, "строка", line, v.strip(),
                         "нет «%s» у %s.%s в new" % (child, kind, name), self.old_check(ok))

    def _named_call(self, ext, rel, toks, i):
        """РольДоступна("X"), ПолучитьФункциональнуюОпцию("X"): the object named by the first argument."""
        kind = NAMED_CALLS.get(toks[i][1].casefold())
        if kind is None or i + 3 >= len(toks) or (i > 0 and toks[i - 1][1] == "."):
            return
        if toks[i + 1][1] != "(" or toks[i + 2][0] != "str" or toks[i + 3][1] not in (")", ","):
            return
        name = toks[i + 2][1].strip()
        if not re.fullmatch(qparse.ID, name or "-"):
            return
        self.stats["literal_named_calls"] += 1
        if self.new.obj(kind, name) is None:
            self.add("literal", "имя", "ОШИБКА?", ext, rel, "код", toks[i][2], '%s("%s")' % (toks[i][1], name),
                     "объекта нет в new", self.old_check(lambda s: s.obj(kind, name) is not None))

    def _value(self, ext, rel, line, ref, kind, name, val):
        if self.new.obj(kind, name) is None:
            self.add("literal", "значение", "ОШИБКА?", ext, rel, "строка", line, ref, "объекта нет в new",
                     self.old_check(lambda s: s.obj(kind, name) is not None))
            return
        low = val.casefold()
        if low in MANAGER_PROPS or low in STANDARD_FORMS or low in self.new.names(kind, name, "forms"):
            return  # "Справочник.X.ФормаЭлемента" names a form, not a value

        def ok(s):
            return low in s.values(kind, name)
        if not ok(self.new):
            self.add("literal", "значение", "ОШИБКА?", ext, rel, "строка", line, ref,
                     "значения или предопределённого нет в new", self.old_check(ok))

    def _chain(self, ext, rel, toks, i):
        v = toks[i][1]
        line = toks[i][2]

        def tok(j):
            return toks[j] if j < len(toks) else ("", "", 0)

        def dot_id(j):
            return tok(j)[1] == "." and tok(j + 1)[0] == "id"
        if i > 0 and toks[i - 1][1] == ".":
            return
        if v in VALUE_CHAIN_COLL and dot_id(i + 1) and dot_id(i + 3) and tok(i + 5)[1] != "(":
            kind, name, val = VALUE_CHAIN_COLL[v], tok(i + 2)[1], tok(i + 4)[1]
            if val.casefold() in MANAGER_PROPS:
                return
            self.stats["literal_value_chains"] += 1
            self._value(ext, rel, line, "%s.%s.%s" % (v, name, val), kind, name, val)
            return
        if v not in ("Метаданные", "Metadata") or not dot_id(i + 1) or not dot_id(i + 3):
            return
        coll, name = tok(i + 2)[1], tok(i + 4)[1]
        kind = MD_COLL.get(coll)
        if kind is None or name.casefold() in COLLECTION_METHODS or tok(i + 5)[1] == "(":
            return
        self.stats["literal_metadata_chains"] += 1
        ref = "Метаданные.%s.%s" % (coll, name)
        if self.new.obj(kind, name) is None:
            self.add("literal", "метаданные", "ОШИБКА?", ext, rel, "код", line, ref, "объекта нет в new",
                     self.old_check(lambda s: s.obj(kind, name) is not None))
            return
        bucket = MD_CHILD.get(tok(i + 6)[1])
        if tok(i + 5)[1] != "." or bucket is None or not dot_id(i + 7):
            return
        child = tok(i + 8)[1]
        if child.casefold() in COLLECTION_METHODS:
            return
        self.stats["literal_metadata_child_chains"] += 1

        def ok(s):
            return child.casefold() in s.names(kind, name, bucket)
        if not ok(self.new):
            self.add("literal", "метаданные", "ОШИБКА?", ext, rel, "код", line,
                     "%s.%s.%s" % (ref, tok(i + 6)[1], child), "нет в new", self.old_check(ok))


def settings_fields(root):
    """(variant, field path, where) of every field a settings variant of a DCS schema uses."""
    res = []
    for sv in root.iter(SCH + "settingsVariant"):
        vname = sv.findtext(SET + "name") or ""
        for el in sv.iter():
            if el.tag == SET + "field" and el.text:
                res.append((vname, el.text.strip(), "field"))
            elif el.tag in (SET + "left", SET + "right") and (el.get(XSI) or "").endswith("Field") and el.text:
                res.append((vname, el.text.strip(), el.tag.replace(SET, "")))
    return res


def render(cfg_new, cfg_old, exts, sw, elapsed):
    md = ["# Сверка текстов запросов, схем СКД и имён в строках", ""]
    md.append("new: `%s` (%s), old: `%s` (%s). Расширений: %d. Время: %.1f с." % (
        cfg_new.root, cfg_new.version, cfg_old.root if cfg_old else "—", cfg_old.version if cfg_old else "—",
        len(exts), elapsed))
    md.append("")
    counts = defaultdict(int)
    for f in sw.findings:
        counts["%s/%s" % (f["part"], f["kind"])] += 1
    md.append(fileio.md_table(["Класс", "Находок", "Смысл"], [(k, counts.get(k, 0), v) for k, v in EXPLAIN.items()]))
    md.append("")
    md.append("Проверено: " + ", ".join("%s — %d" % kv for kv in sorted(sw.stats.items())) + ".")
    md.append("")
    md.append("«old»: «было корректно» — сломано обновлением; «тоже не разрешается» — ошибка была и до него; "
              "«%s» — проверка внутри схемы СКД, релиз на неё не влияет; пусто — сравнение с old не выполнялось. "
              "Находки — кандидаты: каждую подтвердить по выгрузке new." % RELEASE_FREE)
    md.append("")
    md.append("## Находки")
    md.append("")
    order = {"ОШИБКА?": 0, "РИСК": 1, "ПРОВЕРИТЬ": 2}
    rows = sorted(sw.findings, key=lambda f: (order.get(f["sev"], 9), f["part"], f["ext"], f["file"], f["line"]))
    md.append(fileio.md_table(["Уровень", "Класс", "Расширение", "Файл:строка", "Где", "Что", "Проблема", "old"],
                              [(f["sev"], "%s/%s" % (f["part"], f["kind"]), f["ext"], "%s:%s" % (f["file"], f["line"]),
                                f["where"], f["ref"], f["note"], f["old"] or "") for f in rows]))
    md.append("")
    md.append("## Не судится по метаданным — %d" % len(sw.masked))
    md.append("")
    md.append("Поле не разрешается, но псевдоним переиспользован для временной таблицы или подзапроса, имя "
              "подставляется через `СтрЗаменить`, или поля такой виртуальной таблицы (регистр бухгалтерии, регистр "
              "расчёта) не моделируются. Смотреть глазами.")
    md.append("")
    md.append(fileio.md_table(["Расширение", "Файл:строка", "Где", "Что", "Проблема", "Почему не судится"],
                              [(m["ext"], "%s:%s" % (m["file"], m["line"]), m["where"], m["ref"], m["note"], m["why"])
                               for m in sw.masked]))
    return "\n".join(md)


def main(argv=None):
    fileio.console_utf8()
    ap = argparse.ArgumentParser(description="Query texts, DCS schemas and metadata names in string literals of the "
                                             "extensions, checked against the new (and old) release")
    ap.add_argument("--ext-root", action="extend", nargs="+", required=True)
    ap.add_argument("--ext", action="extend", nargs="+")
    ap.add_argument("--skip", action="extend", nargs="+")
    ap.add_argument("--config-old")
    ap.add_argument("--config-new", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    refuse_out_inside(args.out, [("--ext-root", r) for r in args.ext_root] +
                      [("--config-old", args.config_old), ("--config-new", args.config_new)], "queries")
    t0 = time.time()
    metadata.check_config(args.config_new, "--config-new")
    if args.config_old:
        metadata.check_config(args.config_old, "--config-old")
    cfg_new = metadata.Dump(args.config_new, "new")
    cfg_old = metadata.Dump(args.config_old, "old") if args.config_old else None
    exts = metadata.find_extensions(args.ext_root, args.ext, args.skip)
    if not exts:
        sys.exit("no extension dumps found under --ext-root")
    sw = Sweep(cfg_new, cfg_old, exts)
    for ext in exts:
        sw.queries(ext)
        sw.dcs(ext)
        sw.literals(ext)
    elapsed = time.time() - t0
    md = render(cfg_new, cfg_old, exts, sw, elapsed)
    data = {"config_new": cfg_new.root, "config_old": cfg_old.root if cfg_old else None,
            "extensions": [e.name for e in exts], "stats": dict(sw.stats), "findings": sw.findings,
            "masked": sw.masked}
    md_path, json_path = fileio.write_report(args.out, "queries", md, data)
    counts = defaultdict(int)
    for f in sw.findings:
        counts["%s/%s" % (f["part"], f["kind"])] += 1
    print("extensions: %d, findings: %d, masked: %d, %.1fs" % (len(exts), len(sw.findings), len(sw.masked), elapsed))
    print("classes: " + ", ".join("%s=%d" % kv for kv in sorted(counts.items())))
    print(md_path)
    print(json_path)


if __name__ == "__main__":
    main()
