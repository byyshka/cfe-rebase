# -*- coding: utf-8 -*-
"""Light query-language analyser and field resolution for the query sweep (queries.py).

Not a parser: table aliases are bound by regex (an alias used in several subqueries binds to all its tables, and a
field passes if any of them has it). Good enough to find a table, field or value that no source has.

Field sets of a release come from its configuration dump plus the extensions: their own objects and the attributes,
dimensions, resources and tabular sections they add to borrowed objects. Names compare case-insensitively.
"""
import re

KIND_Q = {
    "Справочник": "Справочник", "Catalog": "Справочник",
    "Документ": "Документ", "Document": "Документ",
    "РегистрНакопления": "РегистрНакопления", "AccumulationRegister": "РегистрНакопления",
    "РегистрСведений": "РегистрСведений", "InformationRegister": "РегистрСведений",
    "Перечисление": "Перечисление", "Enum": "Перечисление",
    "ПланВидовХарактеристик": "ПланВидовХарактеристик", "ChartOfCharacteristicTypes": "ПланВидовХарактеристик",
    "ЖурналДокументов": "ЖурналДокументов", "DocumentJournal": "ЖурналДокументов",
    "ПланОбмена": "ПланОбмена", "ExchangePlan": "ПланОбмена",
    "БизнесПроцесс": "БизнесПроцесс", "BusinessProcess": "БизнесПроцесс",
    "Задача": "Задача", "Task": "Задача",
    "ПланСчетов": "ПланСчетов", "ChartOfAccounts": "ПланСчетов",
    "РегистрБухгалтерии": "РегистрБухгалтерии", "AccountingRegister": "РегистрБухгалтерии",
    "РегистрРасчета": "РегистрРасчета", "CalculationRegister": "РегистрРасчета",
    "ПланВидовРасчета": "ПланВидовРасчета", "ChartOfCalculationTypes": "ПланВидовРасчета",
    "Константа": "Константа", "Constant": "Константа",
}
KIND_CF = {k.casefold(): v for k, v in KIND_Q.items()}  # the query language ignores case: ЗНАЧЕНИЕ(справочник.X.Y)
VT = {
    "остатки": "Остатки", "balance": "Остатки", "обороты": "Обороты", "turnovers": "Обороты",
    "остаткииобороты": "ОстаткиИОбороты", "balanceandturnovers": "ОстаткиИОбороты",
    "срезпоследних": "СрезПоследних", "slicelast": "СрезПоследних", "срезпервых": "СрезПервых",
    "slicefirst": "СрезПервых", "движенияссубконто": "ДвиженияССубконто",
    "recordswithextdimensions": "ДвиженияССубконто", "оборотыдткт": "ОборотыДтКт", "drcrturnovers": "ОборотыДтКт",
}
# Virtual tables each register kind has; a calculation register's ones are not modelled (Sources.unjudged).
REG_VT = {
    "РегистрНакопления": ("Остатки", "Обороты", "ОстаткиИОбороты"),
    "РегистрСведений": ("СрезПоследних", "СрезПервых"),
    "РегистрБухгалтерии": ("Остатки", "Обороты", "ОстаткиИОбороты", "ОборотыДтКт", "ДвиженияССубконто"),
}
VT_SUFFIXES = {
    "Остатки": ("Остаток", "Balance"),
    "Обороты": ("Приход", "Расход", "Оборот", "Receipt", "Expense", "Turnover"),
    "ОстаткиИОбороты": ("НачальныйОстаток", "КонечныйОстаток", "Приход", "Расход", "Оборот", "OpeningBalance",
                        "ClosingBalance", "Receipt", "Expense", "Turnover"),
}
TURNOVER_SUFFIXES = ("Оборот", "Turnover")  # Обороты of a turnover-type accumulation register
_ACC_BALANCE = ("Остаток", "ОстатокДт", "ОстатокКт", "РазвернутыйОстатокДт", "РазвернутыйОстатокКт")
_ACC_TURNOVER = ("Оборот", "ОборотДт", "ОборотКт")
ACC_SUFFIXES = {  # resource suffixes of accounting-register virtual tables; the rest of their fields: ACC_UNJUDGED
    "Остатки": _ACC_BALANCE,
    "Обороты": _ACC_TURNOVER,
    "ОстаткиИОбороты": tuple(p + s for p in ("Начальный", "Конечный") for s in _ACC_BALANCE) + _ACC_TURNOVER,
    "ОборотыДтКт": _ACC_TURNOVER,
    "ДвиженияССубконто": ("", "Дт", "Кт"),
}
# Field names of accounting-register virtual tables that are not modelled: an unresolved one is «не судится».
ACC_UNJUDGED = ("кор", "субконто", "видсубконто", "счет", "balanced", "extdimension", "account")
PERIODS = {"Период", "Регистратор", "НомерСтроки", "Секунда", "Минута", "Час", "День", "Неделя", "Декада",
           "Месяц", "Квартал", "Полугодие", "Год", "Period", "Recorder", "LineNumber", "Second", "Minute",
           "Hour", "Day", "Week", "TenDays", "Month", "Quarter", "HalfYear", "Year", "МоментВремени",
           "PointInTime", "ПериодСекунда", "ПериодДень", "ПериодМесяц",
           # the whole periodicity list of a turnover virtual table (help 8.3.27, РегистрНакопления.ОстаткиИОбороты)
           "ПериодМинута", "ПериодЧас", "ПериодНеделя", "ПериодДекада", "ПериодКвартал", "ПериодПолугодие",
           "ПериодГод", "SecondPeriod", "MinutePeriod", "HourPeriod", "DayPeriod", "WeekPeriod", "TenDaysPeriod",
           "MonthPeriod", "QuarterPeriod", "HalfYearPeriod", "YearPeriod"}

# Standard attributes (RU/EN) by kind; used for field resolution only.
STD_COMMON = {"Ссылка", "Ref", "ПометкаУдаления", "DeletionMark", "Предопределенный", "Predefined",
              "ИмяПредопределенныхДанных", "PredefinedDataName", "ВерсияДанных", "DataVersion",
              "Представление", "Presentation", "МоментВремени", "PointInTime"}
STD = {
    "Справочник": STD_COMMON | {"Код", "Code", "Наименование", "Description", "Родитель", "Parent",
                                "Владелец", "Owner", "ЭтоГруппа", "IsFolder"},
    "Документ": STD_COMMON | {"Дата", "Date", "Номер", "Number", "Проведен", "Posted"},
    "ПланВидовХарактеристик": STD_COMMON | {"Код", "Code", "Наименование", "Description", "Родитель", "Parent",
                                            "ЭтоГруппа", "IsFolder", "ТипЗначения", "ValueType"},
    "ПланСчетов": STD_COMMON | {"Код", "Code", "Наименование", "Description", "Родитель", "Parent", "Вид", "Type",
                                "Забалансовый", "OffBalance", "Порядок", "Order", "ВидыСубконто",
                                "ExtDimensionTypes"},
    "ПланОбмена": STD_COMMON | {"Код", "Code", "Наименование", "Description", "НомерОтправленного", "SentNo",
                                "НомерПринятого", "ReceivedNo", "ЭтотУзел", "ThisNode"},
    "БизнесПроцесс": STD_COMMON | {"Дата", "Date", "Номер", "Number", "Завершен", "Completed", "Стартован",
                                   "Started", "ВедущаяЗадача", "HeadTask"},
    "Задача": STD_COMMON | {"Дата", "Date", "Номер", "Number", "Наименование", "Description", "Выполнена",
                            "Executed", "БизнесПроцесс", "BusinessProcess", "ТочкаМаршрута", "RoutePoint"},
    "Перечисление": {"Ссылка", "Ref", "Порядок", "Order", "Представление", "Presentation"},
    "РегистрНакопления": {"Период", "Period", "Регистратор", "Recorder", "НомерСтроки", "LineNumber",
                          "Активность", "Active", "ВидДвижения", "RecordType", "МоментВремени", "PointInTime"},
    "РегистрСведений": {"Период", "Period", "Регистратор", "Recorder", "НомерСтроки", "LineNumber",
                        "Активность", "Active", "МоментВремени", "PointInTime"},
    "РегистрБухгалтерии": {"Период", "Period", "Регистратор", "Recorder", "НомерСтроки", "LineNumber",
                           "Активность", "Active", "Счет", "Account", "СчетДт", "СчетКт"},
    "ЖурналДокументов": STD_COMMON | {"Дата", "Date", "Номер", "Number", "Проведен", "Posted", "Тип", "Type"},
    "Константа": {"Значение", "Value"},
    "ПланВидовРасчета": STD_COMMON | {"Код", "Code", "Наименование", "Description", "ПериодДействияБазовый",
                                      "ActionPeriodIsBasic", "ВедущиеВидыРасчета", "LeadingCalculationTypes",
                                      "ВытесняющиеВидыРасчета", "DisplacingCalculationTypes", "БазовыеВидыРасчета",
                                      "BaseCalculationTypes"},
    "РегистрРасчета": {"Регистратор", "Recorder", "НомерСтроки", "LineNumber", "Активность", "Active",
                       "ПериодРегистрации", "RegistrationPeriod", "ВидРасчета", "CalculationType", "ПериодДействия",
                       "ActionPeriod", "ПериодДействияНачало", "BegOfActionPeriod", "ПериодДействияКонец",
                       "EndOfActionPeriod", "БазовыйПериодНачало", "BegOfBasePeriod", "БазовыйПериодКонец",
                       "EndOfBasePeriod", "Сторно", "ReversingEntry"},
}

ID = r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*"
KIND_ALT = "|".join(sorted(KIND_Q, key=len, reverse=True))
TABLE_RE = re.compile(r"(?<![\wЁё.&])(%s)\.(%s)(?:\.(%s))?" % (KIND_ALT, ID, ID))
VT_CALL_RE = re.compile(r"(?<![\wЁё.&])(?:%s)\.%s\.%s\s*$" % (KIND_ALT, ID, ID))
FIELD_RE = re.compile(r"(?<![\wЁё.&])(%s)((?:\.%s)+)" % (ID, ID))
VALUE_RE = re.compile(r"(?:ЗНАЧЕНИЕ|VALUE)\s*\(\s*(%s)\.(%s)\.(%s)\s*\)" % (KIND_ALT, ID, ID), re.I)
SELECT_RE = re.compile(r"(?:ВЫБРАТЬ|SELECT)\b", re.I)

TYPE_RE = re.compile(r"^cfg:([A-Za-z]+?)(Ref|Object)\.(.+)$")
EN = {"Catalog": "Справочник", "Document": "Документ", "Enum": "Перечисление",
      "ChartOfCharacteristicTypes": "ПланВидовХарактеристик", "ChartOfAccounts": "ПланСчетов",
      "ChartOfCalculationTypes": "ПланВидовРасчета", "ExchangePlan": "ПланОбмена", "BusinessProcess": "БизнесПроцесс",
      "Task": "Задача"}
RU2EN = {v: k for k, v in EN.items()}
LITERAL_OR_COMMENT_RE = re.compile(r'"(?:[^"]|"")*"|//[^\n]*')
KEYWORD_RE = re.compile(r"(?<![\wЁё])(ВЫБРАТЬ|SELECT|ИЗ|FROM|ПОМЕСТИТЬ|INTO|ОБЪЕДИНИТЬ|UNION)(?![\wЁё])", re.I)


def clean(q):
    """Query text without comments and DCS braces; '//' inside a string literal is not a comment."""
    # a comment becomes spaces of its length: positions in the result stay those of the source text, so
    # queries.line_of maps a table back to its own line, not to a line above every cut `////` separator
    q = LITERAL_OR_COMMENT_RE.sub(lambda m: m.group(0) if m.group(0).startswith('"') else " " * len(m.group(0)), q)
    return q.replace("{", " ").replace("}", " ")


def str_end(q, i):
    """q[i] == '"' -> index after the closing quote; '""' inside a literal is an escaped quote."""
    n = len(q)
    j = i + 1
    while j < n:
        if q[j] == '"':
            if j + 1 < n and q[j + 1] == '"':
                j += 2
                continue
            return j + 1
        j += 1
    return n


def skip_parens(q, i):
    """q[i] == '(' -> index after the matching ')'."""
    depth = 0
    n = len(q)
    while i < n:
        c = q[i]
        if c == '"':
            i = str_end(q, i)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def top_level_keywords(q):
    """[(start, WORD)] of query keywords outside parentheses and string literals."""
    out = []
    depth = 0
    i = 0
    n = len(q)
    while i < n:
        c = q[i]
        if c == '"':
            i = str_end(q, i)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0 and c.isalpha():
            m = KEYWORD_RE.match(q, i)
            if m:
                out.append((i, m.group(1).upper()))
                i = m.end()
                continue
            while i < n and (q[i].isalnum() or q[i] == "_"):
                i += 1
            continue
        i += 1
    return out


def _alias_words(q):
    """Kind words used as table aliases («... КАК Справочник»): «Справочник.Поле» is then a field, not a table.
    «ВЫРАЗИТЬ(... КАК Справочник.X)» is a type, not an alias."""
    return {a for a in re.findall(r"(?<![\wЁё])(?:КАК|AS)\s+(%s)(?![\wЁё]|\s*\.)" % ID, q, re.I) if a in KIND_Q}


def bind_aliases(q):
    """[(alias, kind, name, sub, pos)]; sub is a virtual table or tabular section name or None."""
    out = []
    alias_words = _alias_words(q)
    for m in TABLE_RE.finditer(q):
        if m.group(1) in alias_words:
            continue
        before = q[max(0, m.start() - 12):m.start()]
        if re.search(r"(?:ЗНАЧЕНИЕ|VALUE|ТИП|TYPE)\s*\(\s*$", before, re.I):
            continue  # value and type literals are not tables
        j = m.end()
        while j < len(q) and q[j] in " \t\r\n":
            j += 1
        if j < len(q) and q[j] == "(":
            j = skip_parens(q, j)
        am = re.match(r"\s*(?:КАК|AS)\s+(%s)" % ID, q[j:], re.I)
        out.append((am.group(1) if am else None, KIND_Q[m.group(1)], m.group(2), m.group(3), m.start()))
    return out


def field_refs(q):
    """[(alias, [seg, seg...], pos)] for Alias.Field... chains."""
    out = []
    alias_words = _alias_words(q)
    for m in FIELD_RE.finditer(q):
        head = m.group(1)
        if head in KIND_Q and head not in alias_words:
            continue
        out.append((head, [s for s in m.group(2).split(".") if s], m.start()))
    return out


def other_sources(q):
    """Casefolded aliases given to temporary tables and subqueries: their fields cannot be judged by metadata.
    «Вид.X.Таблица(...) КАК А» is a virtual table with parameters, not a subquery."""
    res = set(re.findall(r"(?:ИЗ|FROM|СОЕДИНЕНИЕ|JOIN|,)\s+(?!(?:%s)\.)%s\s+(?:КАК|AS)\s+(%s)" % (KIND_ALT, ID, ID),
                         q, re.I))
    opens = _open_parens(q)
    for m in re.finditer(r"\)\s*(?:КАК|AS)\s+(%s)\s*(?:ПО|ON|,|\n|ГДЕ|WHERE|$)" % ID, q, re.I):
        o = opens.get(m.start())
        if o is not None and VT_CALL_RE.search(q[:o]):
            continue
        res.add(m.group(1))
    return {a.casefold() for a in res}


def _open_parens(q):
    """{index of ')': index of its '('} outside string literals."""
    out = {}
    stack = []
    i = 0
    n = len(q)
    while i < n:
        c = q[i]
        if c == '"':
            i = str_end(q, i)
            continue
        if c == "(":
            stack.append(i)
        elif c == ")" and stack:
            out[i] = stack.pop()
        i += 1
    return out


def _split_top(body):
    """Select list items split by commas outside parentheses and string literals."""
    items = []
    depth = 0
    start = 0
    i = 0
    n = len(body)
    while i < n:
        c = body[i]
        if c == '"':
            i = str_end(body, i)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "," and depth == 0:
            items.append(body[start:i])
            start = i + 1
        i += 1
    items.append(body[start:])
    return items


def select_aliases(q):
    """{name: expression} the LAST query of a batch returns: aliases and bare last segments of its first select list.
    A union takes column names from its first query; the list ends at the outer ИЗ / ПОМЕСТИТЬ / ОБЪЕДИНИТЬ or at
    a DCS «{ВЫБРАТЬ …}» clause, not at one inside a subquery."""
    parts = [p for p in q.split(";") if SELECT_RE.search(p)]
    if not parts:
        return {}
    last = parts[-1]
    kws = top_level_keywords(last)
    exprs = {}
    for k, (start, word) in enumerate(kws):
        if word not in ("ВЫБРАТЬ", "SELECT"):
            continue
        end = kws[k + 1][0] if k + 1 < len(kws) else len(last)
        body = last[start + len(word):end]
        body = re.sub(r"^\s*(?:РАЗРЕШЕННЫЕ|ALLOWED)\b", "", body, flags=re.I)
        body = re.sub(r"^\s*(?:РАЗЛИЧНЫЕ|DISTINCT)\b", "", body, flags=re.I)
        body = re.sub(r"^\s*(?:ПЕРВЫЕ|TOP)\s+\d+", "", body, flags=re.I)
        for it in _split_top(body):
            it = it.strip()
            if not it:
                continue
            am = re.search(r"(?:КАК|AS)\s+(%s)\s*$" % ID, it, re.I)
            if am:
                exprs.setdefault(am.group(1), it[:am.start()].strip())
                continue
            fm = re.search(r"(%s)\s*$" % ID, it)
            if fm:
                exprs.setdefault(fm.group(1), it)
        break
    return exprs


def _fold(d):
    return {k.casefold(): v for k, v in d.items()}


class Sources(object):
    """Query sources of one release: a configuration dump (metadata.Dump) plus extension dumps."""

    def __init__(self, cfg, exts):
        self.cfg = cfg
        self.exts = exts

    def obj(self, kind, name):
        """The object in the configuration, or an extension's own (not borrowed) object; None if absent."""
        o = self.cfg.object(kind, name)
        if o is not None:
            return o
        for e in self.exts:
            o = e.object(kind, name)
            if o is not None and not o.adopted:
                return o
        return None

    def _all(self, kind, name):
        """(object, from an extension) for the configuration object and every extension's object of that name."""
        o = self.cfg.object(kind, name)
        if o is not None:
            yield o, False
        for e in self.exts:
            o = e.object(kind, name)
            if o is not None:
                yield o, True

    def _ext_add(self, kind, name, bucket):
        res = {}
        for e in self.exts:
            o = e.object(kind, name)
            if o is None:
                continue
            for n, i in getattr(o, bucket).items():
                if not i["adopted"]:
                    res[n] = i
        return res

    def names(self, kind, name, bucket):
        """Casefolded names of one child collection ('attrs', 'ts', 'templates', ...): the configuration's plus what
        extensions add - a child an extension only borrows exists only if the configuration has it."""
        pool = set()
        for o, ext in self._all(kind, name):
            skip = o.adopted_names(bucket) if ext else set()
            pool.update(x.casefold() for x in getattr(o, bucket) if x.casefold() not in skip)
        return pool

    def values(self, kind, name):
        """Casefolded enum values and predefined item names, extensions' own values included."""
        pool = self.names(kind, name, "enum_values")
        for o, _ in self._all(kind, name):
            pool.update(x.casefold() for x in o.predefined)
        return pool

    def _std(self, o, kind):
        """Standard fields of a query source, narrowed by the register's type, periodicity and write mode."""
        std = set(STD.get(kind, ()))
        if kind == "РегистрНакопления" and o.props.get("RegisterType") == "Turnovers":
            std -= {"ВидДвижения", "RecordType"}
        if kind == "РегистрСведений":
            if o.props.get("InformationRegisterPeriodicity") == "Nonperiodical":
                std -= {"Период", "Period", "МоментВремени", "PointInTime"}
            if o.props.get("WriteMode") == "Independent":
                std -= {"Регистратор", "Recorder", "НомерСтроки", "LineNumber", "Активность", "Active"}
        return std

    def _calc_vt(self, o, sub):
        """Is sub a virtual table or recalculation of the calculation register o."""
        s = sub.casefold()
        if s in ("фактическийпериоддействия", "actualactionperiod"):
            return o.props.get("ActionPeriod") != "false"
        if s in ("данныеграфика", "scheduledata"):
            return bool(o.props.get("Schedule"))
        for prefix in ("база", "base"):
            if s.startswith(prefix) and self.obj("РегистрРасчета", sub[len(prefix):]) is not None:
                return True
        return s in {r.casefold() for r in o.recalcs}

    def table_fields(self, kind, name, sub):
        """{casefolded field: types} of a query source, or None if the table does not exist."""
        o = self.obj(kind, name)
        if o is None:
            return None
        f = {}
        if sub is None:
            for b in ("attrs", "dims", "res", "cols", "addr", "flags"):
                for n, i in list(getattr(o, b).items()) + list(self._ext_add(kind, name, b).items()):
                    f[n] = i["types"]
            for n in self.names(kind, name, "ts"):
                f[n] = ["@TS"]
            for n in self._std(o, kind):
                f[n] = ["@std"]
            if kind == "Константа":
                f["Значение"] = f["Value"] = o.types
            return _fold(f)
        if kind == "РегистрРасчета":  # fields of its virtual tables are not modelled: see unjudged()
            return {} if self._calc_vt(o, sub) else None
        if kind not in REG_VT:  # tabular section; an extension's borrowed section or column is not evidence
            if sub.casefold() not in self.names(kind, name, "ts"):
                return None
            cols = {}
            for src, ext in self._all(kind, name):
                ts = {k.casefold(): v for k, v in src.ts.items()}.get(sub.casefold())
                if ts is not None:
                    cols.update({n: i for n, i in ts.items() if not (ext and i["adopted"])})
            for n, i in cols.items():
                f[n] = i["types"]
            ref = ["cfg:%sRef.%s" % (RU2EN.get(kind, kind), name)]
            f["Ссылка"] = f["Ref"] = ref
            f["НомерСтроки"] = f["LineNumber"] = ["@std"]
            return _fold(f)
        vt = VT.get(sub.casefold())
        if vt not in REG_VT[kind]:
            return None
        turnovers = kind == "РегистрНакопления" and o.props.get("RegisterType") == "Turnovers"
        if turnovers and vt != "Обороты":
            return None
        if kind == "РегистрСведений" and o.props.get("InformationRegisterPeriodicity") == "Nonperiodical":
            return None
        dims = dict(o.dims)
        dims.update(self._ext_add(kind, name, "dims"))
        res = dict(o.res)
        res.update(self._ext_add(kind, name, "res"))
        attrs = dict(o.attrs)
        attrs.update(self._ext_add(kind, name, "attrs"))
        for n, i in dims.items():
            f[n] = i["types"]
        if vt in ("СрезПоследних", "СрезПервых"):
            for n, i in list(res.items()) + list(attrs.items()):
                f[n] = i["types"]
            for n in self._std(o, kind) & {"Период", "Period", "Регистратор", "Recorder", "НомерСтроки", "LineNumber",
                                            "Активность", "Active"}:
                f[n] = ["@std"]
            return _fold(f)
        if kind == "РегистрБухгалтерии":
            suffixes = ACC_SUFFIXES[vt]
            f["Счет"] = f["Account"] = ["@std"]
            if vt in ("ОборотыДтКт", "ДвиженияССубконто"):  # a dimension without balance splits into Дт / Кт
                for n, i in dims.items():
                    f[n + "Дт"] = f[n + "Кт"] = i["types"]
        else:
            suffixes = TURNOVER_SUFFIXES if turnovers else VT_SUFFIXES[vt]
        for n in res:
            for s in suffixes:
                f[n + s] = ["@num"]
        if vt != "Остатки":
            for n in PERIODS:
                f[n] = ["@std"]
            for n, i in attrs.items():
                f[n] = i["types"]
        return _fold(f)

    def unjudged(self, tables, segs):
        """Why the unresolved chain segs of these tables is not a finding (its field is not modelled), or None."""
        first = segs[0].casefold() if segs else ""
        for kind, _, sub in tables:
            if sub is not None and kind == "РегистрБухгалтерии" and first.startswith(ACC_UNJUDGED):
                return "поля Кор*, Субконто*, Счет* виртуальных таблиц регистра бухгалтерии не моделируются"
            if sub is not None and kind == "РегистрРасчета":
                return "поля виртуальных таблиц регистра расчёта не моделируются"
        return None

    def _expand(self, types):
        out = []
        for t in types:
            m = re.match(r"^cfg:DefinedType\.(.+)$", t)
            if m:
                d = self.cfg.object("ОпределяемыйТип", m.group(1))
                if d is None:
                    for e in self.exts:
                        d = e.object("ОпределяемыйТип", m.group(1)) or d
                if d is not None:
                    out.extend(d.types)
                    continue
            out.append(t)
        return out

    def ref_objects(self, types):
        res = []
        for t in self._expand(types):
            m = TYPE_RE.match(t)
            if m and m.group(1) in EN:
                res.append((EN[m.group(1)], m.group(3)))
        return res

    def resolve_chain(self, tables, segs, depth=0):
        """tables: [(kind, name, sub)]; segs: field chain. None if it resolves, else the problem text."""
        if not segs:
            return None
        s = segs[0].casefold()
        found = None
        hit = None
        any_table = False
        for kind, name, sub in tables:
            flds = self.table_fields(kind, name, sub)
            if flds is None:
                continue
            any_table = True
            if s in flds:
                found = flds[s]
                hit = (kind, name)
                break
        if not any_table:
            return None  # a missing table is reported by the table check
        if found is None:
            return "поля «%s» нет у %s" % (segs[0], ", ".join(
                "%s.%s%s" % (k, n, ("." + sb) if sb else "") for k, n, sb in tables))
        if len(segs) == 1 or depth >= 3:
            return None
        if found == ["@TS"]:  # Д.Товары.Колонка: the next segment is a column of that tabular section
            return self.resolve_chain([(hit[0], hit[1], segs[0])], segs[1:], depth)
        targets = self.ref_objects(found)
        if not targets:
            return None
        probs = [self.resolve_chain([(k, n, None)], segs[1:], depth + 1) for k, n in targets]
        return None if any(p is None for p in probs) else probs[0]

    def value_problem(self, kind, name, value):
        """ЗНАЧЕНИЕ(Вид.Имя.Значение): None if it resolves, else the problem text."""
        if self.obj(kind, name) is None:
            return "объекта %s.%s нет" % (kind, name)
        if value.casefold() in ("пустаяссылка", "emptyref"):
            return None
        if kind == "Перечисление":
            return None if value.casefold() in self.values(kind, name) else \
                "значения «%s» нет в Перечисление.%s" % (value, name)
        if kind in ("Справочник", "ПланВидовХарактеристик", "ПланСчетов", "ПланВидовРасчета"):
            return None if value.casefold() in self.values(kind, name) else \
                "предопределённого «%s» нет у %s.%s" % (value, kind, name)
        return None  # route points of business processes are not modelled
