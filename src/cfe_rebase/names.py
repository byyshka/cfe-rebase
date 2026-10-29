# -*- coding: utf-8 -*-
"""Names of vendor forms that the extensions' code reads, against the vendor's Form.xml in old and new.

    cfe-rebase names --ext-root <extension dumps> --config-old <old> --config-new <new> [--ext NAME] --out <dir>

Interceptors and copies in the module of an adopted form (Form.xml with <BaseForm>) read the form's items,
attributes, parameters and module variables by name; check_calls sees only calls and check_borrowed only the object's
attributes, so a name the vendor dropped in new went unnoticed until the platform refused the module or the code
silently made a local variable of it. For every such module the live code (comments, string literals and #Удаление
blocks are not code) is read for
  items      Элементы.X / Items.X                                   - form items (the whole ChildItems tree with
                                                                       command bars, context menus and tooltips)
  attribute  ЭтотОбъект.X / ЭтаФорма.X / ThisObject.X / ThisForm.X   - form attributes and module variables
             (not a call: methods are check_calls'); and a bare name that is a form attribute or a module variable
             of the vendor form in old, not a parameter or a Перем of the method
  object     Объект.X / Object.X                                     - attributes and tabular sections of the object
             of the main attribute Объект (standard attributes are not compared)
  parameter  Параметры.X / Parameters.X                              - form parameters (standard ones are not
             compared)
Each name is looked up in the vendor form in old and new (Form.xml, the form module's module-level Перем, the
object's metadata for Объект) and among the extension's own additions (what its Form.xml has outside <BaseForm>, its
own Перем, its own attributes of the object). Checks:
  GONE_IN_NEW    in old, not in new, not added by the extension: error - the module does not compile against new or
                 a bare name turns into a local variable; risk when the vendor's new form module still names it
                 (Элементы.X in code or "X" as a string: the vendor may create it from code now); a field of Объект
                 the vendor renamed keeping its uuid is named in the detail
  NOT_IN_VENDOR  qualified name in neither old nor new nor the extension's additions: info - created from code, a
                 field of another extension or a typo; bare names are never reported this way
  XML_UNREADABLE a Form.xml (extension, old or new) does not parse: the form is not checked
  LIST_QUERY_FIELD_MISSING  the extension replaces the query of a vendor dynamic list of the form - in code
                 (<Список>.ТекстЗапроса / .QueryText = a literal, a concatenation of literals or a variable built from
                 them in the same method; СтрЗаменить of such a text with literal arguments is applied) or in its
                 Form.xml (its own ManualQuery text of the list differs from the one in BaseForm) - and the query does
                 not select a field the vendor form of new needs from that list: the DataPath of an item
                 (<Список>.<Поле>), a field of the list's UseAlways, or a read of the vendor form module new
                 (Элементы.<Таблица>.ТекущиеДанные.<Поле>, the same through a variable, Элемент.ТекущиеДанные.<Поле>
                 in a handler of the table). The platform silently drops such a column and the vendor code reading
                 it fails («Поле объекта не обнаружено»). error when the old vendor form did not need the field (the
                 release brought it), risk when it did (the same gap was there in old). Not compared: fields the
                 vendor's own new query does not select either (made by code), the platform's DefaultPicture, a
                 query that selects «*»; a text the extension builds from anything else (a call, the vendor's
                 <Список>.ТекстЗапроса) is not judged (counted in stats: list_queries_unjudged).
  LIST_FIELD_NOT_LOADED  such a replaced list whose UseAlways the extension changed (its Form.xml against BaseForm):
                 a field of the new vendor UseAlways that the extension's query selects, but neither the extension's
                 UseAlways nor a visible item of its form binds - the row data would not hold it if the extension's
                 UseAlways replaces the vendor's. Whether the platform replaces or merges UseAlways of an adopted form
                 is not documented and not provable from the dumps, so it is always a risk; new_in_new - the field
                 is not in the old vendor UseAlways.
  FIELD_NOT_ADOPTED  (how = module) the object module of an object the extension adopts reads, without a qualifier, an
                 attribute or a tabular section the vendor object has in new that the extension's XML of the object
                 does not adopt. The Designer compiles the extension's module against the extension alone: the
                 module check (/CheckConfig) fails with «Переменная не определена (X)» (a real case, РеализацияТоваровУслуг:
                 ОснованиеНомер, ОснованиеДата - only Основание adopted). A read counts when it is not a parameter or a
                 Перем of its method, not a Перем of the module, and no assignment of the name comes before it in the
                 method: an assignment makes a local variable for the Designer and is not reported, nor is
                 ЭтотОбъект.X (the module check passes both - real bases; ЭтотОбъект.X is counted in stats). error when
                 the vendor object of old has no such field (the reference came with the port), risk when it has
                 («было и в old»: the same check failed before the update). Manager and record set modules are not
                 read: no object attributes are in their context.
Names are compared case-insensitively. A form whose vendor Form.xml is missing in new is check_borrowed's (REMOVED)
and is skipped here (counted in stats). Objects renamed by the vendor are looked up by uuid. The dumps are never
written: --out inside --ext-root, --config-old or --config-new is refused.
"""
import argparse
import os
import re
import sys
import time
from collections import Counter, OrderedDict

from . import bsl, fileio, forms, metadata, qparse
from .control_rebase import inside

HOWS = OrderedDict([("items", "элемент формы"), ("attribute", "реквизит формы / переменная модуля"),
                    ("object", "поле объекта"), ("parameter", "параметр формы"),
                    ("list", "поле динамического списка"), ("module", "поле объекта в модуле объекта")])
EXPLAIN = OrderedDict([
    ("GONE_IN_NEW", "имя было в форме вендора old, в new его нет, расширение его не добавляет"),
    ("NOT_IN_VENDOR", "имени нет ни в old, ни в new, ни в добавлениях расширения: создаётся кодом, чужое или опечатка"),
    ("XML_UNREADABLE", "Form.xml (расширения, old или new) не разбирается: форма не проверена"),
    ("LIST_QUERY_FIELD_MISSING", "расширение подменяет запрос динамического списка вендора, а в его запросе нет "
                                 "поля, которое нужно форме вендора new (элемент, UseAlways, код)"),
    ("LIST_FIELD_NOT_LOADED", "поле UseAlways вендора new в запросе расширения есть, но UseAlways расширения и его "
                              "видимые элементы его не загружают (замена или слияние UseAlways не доказаны)"),
    ("FIELD_NOT_ADOPTED", "модуль объекта расширения читает без квалификатора реквизит или табличную часть вендора, "
                          "которых нет в заимствовании: проверка модулей конфигуратора — «Переменная не определена»"),
])
# kinds whose object module has the object's attributes and tabular sections in its context
OBJECT_MODULE_KINDS = {"Справочник", "Документ", "ПланВидовХарактеристик", "ПланСчетов", "ПланВидовРасчета",
                       "ПланОбмена", "БизнесПроцесс", "Задача", "Обработка", "Отчет"}
LOOP_VAR = {"для", "for", "каждого", "each"}
WORDS = {"элементы": "items", "items": "items", "объект": "object", "object": "object", "параметры": "parameter",
         "parameters": "parameter"}
THIS = {"этотобъект", "этаформа", "thisobject", "thisform"}
VAR_WORDS = {"перем", "var"}
# a subtree of Form.xml that holds no items
NOT_ITEMS = {"Attributes", "Parameters", "Commands", "CommandSet", "CommandInterface", "Events", "BaseForm"}
# standard attributes of objects (Объект.Ссылка), standard form parameters and properties of the managed form: they
# are in no Form.xml and no object XML, so they are not compared (a vendor never removes them)
STANDARD = {
    "object": """Ссылка Ref Номер Number Дата Date Проведен Posted ПометкаУдаления DeletionMark Код Code Наименование
        Description Владелец Owner Родитель Parent ЭтоГруппа IsFolder Предопределенный Predefined
        ИмяПредопределенныхДанных PredefinedDataName ВерсияДанных DataVersion ДополнительныеСвойства
        AdditionalProperties ОбменДанными DataExchange Движения RegisterRecords ВидСчета AccountType Забалансовый
        OffBalance Порядок Order ВидыСубконто ExtDimensionTypes ПризнакиУчета ТипЗначения ValueType Выполнена
        Executed ТочкаМаршрута RoutePoint БизнесПроцесс BusinessProcess ВедущаяЗадача HeadTask Стартован Started
        Завершен Completed Отбор Filter Отправитель Sender Получатель Recipient НомерОтправленного SentNo
        НомерПринятого ReceivedNo ЭтотУзел ThisNode Период Period Регистратор Recorder Активность Active
        НомерСтроки LineNumber ВидРасчета CalculationType ПериодРегистрации RegistrationPeriod""",
    "parameter": """Ключ Key ЗначениеКопирования CopyingValue ЗначенияЗаполнения FillingValues Основание Basis
        ТекущаяСтрока CurrentRow РежимВыбора ChoiceMode ЗакрыватьПриВыборе CloseOnChoice МножественныйВыбор
        MultipleChoice Отбор Filter ВыборГруппИЭлементов ChoiceFoldersAndItems РазрешитьВыборКорня
        AllowRootChoice ТекущийРодитель CurrentParent Родитель Parent ТолькоПросмотр ReadOnly
        НазначениеИспользования Purpose КлючНазначенияИспользования PurposeUseKey КлючПользовательскихНастроек
        UserSettingsKey ВыбранныеЗначения ВыборГрупп СтрокаПоиска SearchString ПредставлениеЗаголовка
        ПользовательскиеНастройки UserSettings Вариант Variant КлючВарианта VariantKey КлючНастроек SettingsKey
        Настройки Settings ФиксированныеНастройки FixedSettings Отчет Report ОтборПоВладельцу""",
    "attribute": """Элементы Items Параметры Parameters Команды Commands Заголовок Title ТекущийЭлемент
        CurrentItem Модифицированность Modified ТолькоПросмотр ReadOnly ВладелецФормы FormOwner КлючУникальности
        UniqueKey КлючНазначенияИспользования PurposeUseKey РежимОткрытияОкна WindowOpeningMode ЗакрыватьПриВыборе
        CloseOnChoice ЗакрыватьПриЗакрытииВладельца CloseOnOwnerClose Окно Window ИмяФормы FormName АвтоЗаголовок
        AutoTitle ПутьКДаннымЗаголовка TitleDataPath МножественныйВыбор MultipleChoice ВариантМасштаба ScalingMode
        Масштаб Scale РежимВыбора ChoiceMode ПроверяемыеРеквизиты ВидимостьКоманднойПанели ПредставлениеКлюча
        КлючСохраненияПоложенияОкна WindowOptionsKey СохранениеДанныхВНастройках ЭтотОбъект ThisObject
        ЭтаФорма ThisForm Объект Object""",
}
STANDARD = {k: {x.casefold() for x in v.split()} for k, v in STANDARD.items()}
OBJ_TYPE_RE = re.compile(r"^(?:cfg:)?(\w+?)Object\.(.+)$")


class FormNames(object):
    """Names of one view of a form (a vendor Form.xml, or the extension's own additions): {how: {casefold: name}}
    and the object of its main attribute Объект ((kind, name) or None)."""

    def __init__(self):
        self.names = {how: {} for how in HOWS}
        self.main = None

    def has(self, how, name):
        return name.casefold() in self.names[how]


def _type_of(attr):
    t = attr.find("Type")
    if t is None:
        return None
    for ch in t.children:
        if ch.tag == "Type" and ch.text.strip():
            return ch.text.strip()
    return None


def scope_names(scope, base=None):
    """FormNames of a Form.xml scope (the form root without its BaseForm, or the BaseForm); with base (FormNames of the
    BaseForm) - only what the scope has and base does not: the extension's own additions."""
    out = FormNames()
    stack = list(scope.children)
    while stack:
        node = stack.pop()
        if node.tag in NOT_ITEMS:
            continue
        name = node.get("name")
        if name and node.tag != "Event":
            out.names["items"][name.casefold()] = name
        stack.extend(node.children)
    attrs = scope.find("Attributes")
    for a in (attrs.children if attrs is not None else []):
        if a.tag == "Attribute" and a.get("name"):
            out.names["attribute"][a.get("name").casefold()] = a.get("name")
            if a.get("name").casefold() in ("объект", "object"):
                m = OBJ_TYPE_RE.match(_type_of(a) or "")
                kind = metadata.TAG2KIND.get(m.group(1).casefold()) if m else None
                if kind:
                    out.main = (kind, m.group(2))
    params = scope.find("Parameters")
    for p in (params.children if params is not None else []):
        if p.tag == "Parameter" and p.get("name"):
            out.names["parameter"][p.get("name").casefold()] = p.get("name")
    if base is not None:
        for how in HOWS:
            out.names[how] = {k: v for k, v in out.names[how].items() if k not in base.names[how]}
    return out


def _module_vars(mod):
    if mod is None:
        return {}
    toks = mod.toks
    names = bsl.module_vars(toks, [(m.start_line, m.end_line) for m in mod.methods])
    # module_vars casefolds: the names as written are taken from the tokens
    return {t[1].casefold(): t[1] for t in toks if t[0] == "id" and t[1].casefold() in names}


def _object_fields(dump, main, own_only=False):
    """{casefold: name} of attributes and tabular sections of the object of the main attribute, and {uuid: name}."""
    if main is None or dump is None:
        return {}, {}
    o = dump.object(*main)
    if o is None:
        return {}, {}
    fields, uuids = {}, {}
    for bucket in ("attrs", "dims", "res"):
        for nm, info in getattr(o, bucket).items():
            if own_only and info["adopted"]:
                continue
            fields[nm.casefold()] = nm
            if info.get("uuid"):
                uuids[info["uuid"].lower()] = nm
    for nm in o.ts:
        if own_only and nm in o.ts_adopted:
            continue
        fields[nm.casefold()] = nm
        uid = o.ts_uuids.get(nm, (None, None))[0]
        if uid:
            uuids[uid.lower()] = nm
    return fields, uuids


def vendor_view(dump, form_rel):
    """(FormNames of the vendor form with module variables and object fields, {uuid: field name}, refs of the
    vendor's form module {(how, casefold name)} | string values, error or None, the parsed Form.xml, the form module
    or None); None when Form.xml is missing."""
    path = dump.path(form_rel)
    if not os.path.isfile(path):
        return None
    try:
        root = forms.parse(path)
    except Exception as exc:  # noqa: BLE001 - expat errors of every kind: reported as XML_UNREADABLE
        return None, None, None, "%s: %s" % (type(exc).__name__, exc), None, None
    view = scope_names(root)
    mod = dump.module(metadata.form_module_rel(form_rel))
    view.names["attribute"].update({k: v for k, v in _module_vars(mod).items()
                                    if k not in view.names["attribute"]})
    fields, uuids = _object_fields(dump, view.main)
    view.names["object"] = fields
    refs = set()
    if mod is not None:
        refs = {(how, name.casefold()) for how, name, _, _ in references(mod.toks)}
        refs |= {("str", t[1].casefold()) for t in mod.toks if t[0] == "str" and bsl.IDENT_RE.fullmatch(t[1])}
    return view, uuids, refs, None, root, mod


def references(toks, shadowed=None):
    """(how, name, line, token index) of the qualified names of the form in live code: Элементы.X, Объект.X,
    Параметры.X and ЭтотОбъект.X / ЭтаФорма.X (not followed by «(»: a method); a qualifier after a dot counts only
    behind ЭтотОбъект / ЭтаФорма (ЭтаФорма.Элементы.X). shadowed(line, casefolded qualifier) -> True skips a
    qualifier that is a parameter or a Перем of its method (Функция Х(Знач Object))."""
    n = len(toks)
    for i in range(n - 2):
        k, v, line = toks[i]
        if k != "id":
            continue
        if shadowed is not None and shadowed(line, v.casefold()):
            continue
        if i > 0 and toks[i - 1][:2] == ("op", "."):
            if not (i > 1 and toks[i - 2][0] == "id" and toks[i - 2][1].casefold() in THIS
                    and not (i > 2 and toks[i - 3][:2] == ("op", "."))):
                continue
        if toks[i + 1][:2] != ("op", ".") or toks[i + 2][0] != "id":
            continue
        name = toks[i + 2][1]
        call = i + 3 < n and toks[i + 3][:2] == ("op", "(")
        low = v.casefold()
        if low in WORDS and not call:
            yield WORDS[low], name, toks[i + 2][2], i + 2
        elif low in THIS and not call and name.casefold() not in WORDS:
            yield "attribute", name, toks[i + 2][2], i + 2


def method_locals(mod):
    """({line: method}, {id(method): casefolded names of its parameters and Перем})."""
    toks = mod.toks
    at_line = {}
    for m in mod.methods:
        for ln in range(m.decl_line, m.end_line + 1):
            at_line[ln] = m
    local = {id(m): {p["name"].casefold() for p in m.params} for m in mod.methods}
    for i, (k, v, line) in enumerate(toks):
        m = at_line.get(line)
        if m is not None and k == "id" and v.casefold() in VAR_WORDS:
            j = i + 1
            while j < len(toks) and toks[j][0] in ("id", "op") and toks[j][1] != ";":
                if toks[j][0] == "id":
                    local[id(m)].add(toks[j][1].casefold())
                j += 1
    return at_line, local


def bare_names(mod, at_line, local):
    """(name, line, method) of the unqualified identifiers of live code that are not calls, types after Новый, or
    parameters and Перем of their method."""
    toks = mod.toks
    for i, (k, v, line) in enumerate(toks):
        if k != "id":
            continue
        if i > 0 and (toks[i - 1][:2] == ("op", ".") or (toks[i - 1][0] == "id" and
                                                          toks[i - 1][1].casefold() in bsl.KW_NEW)):
            continue
        if i + 1 < len(toks) and toks[i + 1][:2] == ("op", "("):
            continue
        m = at_line.get(line)
        if m is not None and (line <= m.decl_end_line or v.casefold() in local[id(m)]):
            continue
        yield v, line, m


# ---- dynamic lists whose query the extension replaces (LIST_QUERY_FIELD_MISSING) ---------------------------------

QUERY_PROPS = {"текстзапроса", "querytext"}
CURRENT_DATA = {"текущиеданные", "currentdata"}
# a form binds standard attributes of a dynamic list by their English names (Список.Ref) while a query names them in
# Russian (Ссылка); DefaultPicture is the platform's own picture of a row, no query selects it
LIST_EN = {"ref": "Ссылка", "date": "Дата", "number": "Номер", "deletionmark": "ПометкаУдаления",
           "posted": "Проведен", "code": "Код", "description": "Наименование", "owner": "Владелец",
           "parent": "Родитель", "isfolder": "ЭтоГруппа", "predefined": "Предопределенный",
           "predefineddataname": "ИмяПредопределенныхДанных", "recorder": "Регистратор", "period": "Период",
           "linenumber": "НомерСтроки", "active": "Активность", "type": "Тип", "dataversion": "ВерсияДанных"}
LIST_PLATFORM = {"defaultpicture"}
# a statement starts after these: «Если … Тогда Список.ТекстЗапроса = …»
STMT_START = {"тогда", "then", "иначе", "else", "цикл", "do", "попытка", "try", "исключение", "except"}
STMT_END = {"конецесли", "endif", "иначе", "else", "иначеесли", "elsif", "конеццикла", "enddo", "исключение",
            "except", "конецпопытки", "endtry"}
REPLACE_FUNCS = {"стрзаменить", "strreplace"}


def _query_name(field):
    """The name a query gives a list field a DataPath names (Ref -> Ссылка)."""
    return LIST_EN.get(field.casefold(), field)


def list_attrs(scope):
    """{casefold name: Attribute node} of the dynamic lists among the attributes of a Form.xml scope."""
    out = {}
    attrs = scope.find("Attributes")
    for a in (attrs.children if attrs is not None else []):
        t = a.find("Type")
        if a.tag == "Attribute" and a.get("name") and t is not None and \
                any(ch.text.strip().endswith("DynamicList") for ch in t.children):
            out[a.get("name").casefold()] = a
    return out


def list_query(attr):
    """(ManualQuery, query text or None) of a dynamic list attribute."""
    st = attr.find("Settings") if attr is not None else None
    if st is None:
        return False, None
    manual = st.find("ManualQuery")
    q = st.find("QueryText")
    return (manual is not None and manual.text.strip() == "true"), (q.text if q is not None and q.text.strip()
                                                                     else None)


def _select_items(q):
    """Items of the select list qparse.select_aliases reads: the first ВЫБРАТЬ of the last query of a batch."""
    parts = [p for p in q.split(";") if qparse.SELECT_RE.search(p)]
    kws = qparse.top_level_keywords(parts[-1]) if parts else []
    for k, (start, word) in enumerate(kws):
        if word in ("ВЫБРАТЬ", "SELECT"):
            end = kws[k + 1][0] if k + 1 < len(kws) else len(parts[-1])
            return qparse._split_top(parts[-1][start + len(word):end])
    return []


ALL_FIELDS_RE = re.compile(r"^(?:%s\s*\.\s*)?\*$" % qparse.ID)


def selected(query):
    """{casefold: expression} of the fields a list query selects, or None when it cannot be told (no ВЫБРАТЬ, «*» or
    «Таблица.*» in the select list; «Документ.Товары.*» is a tabular section, a «.*» of a DCS clause elsewhere is not
    the select list)."""
    if not query:
        return None
    q = qparse.clean(query)
    if not qparse.SELECT_RE.search(q) or any(ALL_FIELDS_RE.match(it.strip()) for it in _select_items(q)):
        return None
    return {k.casefold(): " ".join(v.split()) for k, v in qparse.select_aliases(q).items()}


def _tables_of(root, list_name):
    """{casefold table item name: [handler method names]} of the Table items bound to the list."""
    out = {}
    for node in root.iter():
        if node.tag != "Table" or not node.get("name"):
            continue
        dp = node.find("DataPath")
        if dp is None or dp.text.strip().casefold() != list_name.casefold():
            continue
        ev = node.find("Events")
        out[node.get("name").casefold()] = [e.text.strip() for e in (ev.children if ev is not None else [])
                                            if e.tag == "Event" and e.text.strip()]
    return out


def list_needs(root, mod, list_name):
    """{casefolded query name of a field: (field as the form names it, [where])} of what a vendor form needs from its
    dynamic list: DataPath of items (<Список>.<Поле>), UseAlways, and reads of its module:
    Элементы.<Таблица>.ТекущиеДанные.<Поле> (directly or through a variable of the method) and
    Элемент.ТекущиеДанные.<Поле> in a handler of the table's events."""
    needs = OrderedDict()
    low = list_name.casefold()

    def need(field, where):
        if field.casefold() in LIST_PLATFORM:
            return
        e = needs.setdefault(_query_name(field).casefold(), (_query_name(field), []))
        if where not in e[1]:
            e[1].append(where)

    for node in root.iter():
        if node.tag == "DataPath" and node.parent is not None and node.parent.get("name"):
            path = node.text.strip().split(".")
            if len(path) >= 2 and path[0].casefold() == low:
                need(path[1], "элемент %s (Form.xml, стр. %d)" % (node.parent.get("name"), node.parent.line))
    attr = list_attrs(root).get(low)
    ua = attr.find("UseAlways") if attr is not None else None
    for f in (ua.children if ua is not None else []):
        path = f.text.strip().split(".")
        if f.tag == "Field" and len(path) >= 2 and path[0].casefold() == low:
            need(path[1], "UseAlways")
    if mod is None:
        return needs
    tables = _tables_of(root, list_name)
    handlers = {h.casefold() for hs in tables.values() for h in hs}
    toks = mod.toks
    for m in mod.methods:
        a, b = mod.tok_range(m)
        rows = set()  # casefolded variables of the method holding the current row of the list
        item = m.params[0]["name"].casefold() if m.name.casefold() in handlers and m.params else None
        for i in range(a, b):
            k, v, line = toks[i]
            if k != "id" or (i > 0 and toks[i - 1][:2] == ("op", ".")):
                continue
            low_v = v.casefold()
            end = None
            if low_v in ("элементы", "items") and i + 4 < b and toks[i + 1][:2] == ("op", ".") and \
                    toks[i + 2][1].casefold() in tables and toks[i + 3][:2] == ("op", ".") and \
                    toks[i + 4][1].casefold() in CURRENT_DATA:
                end = i + 4
            elif item and low_v == item and i + 2 < b and toks[i + 1][:2] == ("op", ".") and \
                    toks[i + 2][1].casefold() in CURRENT_DATA:
                end = i + 2
            elif low_v in rows:
                end = i
            if end is None:
                continue
            if end + 2 < b and toks[end + 1][:2] == ("op", ".") and toks[end + 2][0] == "id" and \
                    not (end + 3 < b and toks[end + 3][:2] == ("op", "(")):
                need(toks[end + 2][1], "код вендора: %s, стр. %d" % (m.name, toks[end + 2][2]))
            elif end != i and i >= 2 and toks[i - 1][:2] == ("op", "=") and toks[i - 2][0] == "id" and \
                    end + 1 < b and toks[end + 1][:2] == ("op", ";"):
                rows.add(toks[i - 2][1].casefold())
    return needs


def _rhs(toks, i, b):
    """Tokens of the right-hand side starting at i up to «;» or the end of a statement block, and the index after."""
    j, depth = i, 0
    while j < b:
        k, v, _ = toks[j]
        if k == "op" and v in ("(", "["):
            depth += 1
        elif k == "op" and v in (")", "]"):
            depth -= 1
        elif depth <= 0 and ((k == "op" and v == ";") or (k == "id" and v.casefold() in STMT_END)):
            break
        j += 1
    return toks[i:j], j


def _args(toks):
    """The arguments of a call given its tokens from «(» to the matching «)», split at top-level commas."""
    out, cur, depth = [], [], 0
    for t in toks:
        if t[:2] in (("op", "("), ("op", "[")):
            depth += 1
            if depth == 1:
                continue
        elif t[:2] in (("op", ")"), ("op", "]")):
            depth -= 1
            if depth == 0:
                out.append(cur)
                break
        elif t[:2] == ("op", ",") and depth == 1:
            out.append(cur)
            cur = []
            continue
        cur.append(t)
    return out


def resolve_text(rhs, env):
    """The string an expression makes of literals, «+», variables of env (text or None) and СтрЗаменить; None when
    any part is something else (a call, a property, the vendor's own ТекстЗапроса)."""
    parts, i, n = [], 0, len(rhs)
    while i < n:
        k, v, _ = rhs[i]
        nxt = rhs[i + 1][:2] if i + 1 < n else None
        if k == "str":
            parts.append(v)
            i += 1
        elif k == "id" and v.casefold() in REPLACE_FUNCS and nxt == ("op", "("):
            depth, j = 0, i + 1
            while j < n:
                if rhs[j][:2] == ("op", "("):
                    depth += 1
                elif rhs[j][:2] == ("op", ")"):
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            args = _args(rhs[i + 1:j + 1])
            if len(args) != 3:
                return None
            texts = [resolve_text(x, env) for x in args]
            if texts[0] is None:
                return None
            parts.append(texts[0].replace(texts[1], texts[2]) if texts[1] and texts[2] is not None else texts[0])
            i = j + 1
        elif k == "id" and v.casefold() in env and nxt not in (("op", "."), ("op", "("), ("op", "[")):
            if env[v.casefold()] is None:
                return None
            parts.append(env[v.casefold()])
            i += 1
        else:
            return None
        if i < n:
            if rhs[i][:2] != ("op", "+"):
                return None
            i += 1
    return "".join(parts) if parts else None


def code_list_queries(mod, lists):
    """[(list name as written, query text or None, line, method)] of the assignments «<Список>.ТекстЗапроса = …» (also
    ЭтотОбъект.<Список>, .QueryText) to the lists `lists` (casefolded names) in the methods of the module; the text is
    None when it is not made of literals of the same method."""
    out = []
    toks = mod.toks
    for m in mod.methods:
        a, b = mod.tok_range(m)
        env = {}
        i = a
        while i < b:
            k, v, line = toks[i]
            start = i == a or toks[i - 1][:2] == ("op", ";") or toks[i - 1][0] == "pre" or \
                (toks[i - 1][0] == "id" and toks[i - 1][1].casefold() in STMT_START)
            if k != "id" or not start:
                i += 1
                continue
            j = i + 2 if v.casefold() in THIS and i + 2 < b and toks[i + 1][:2] == ("op", ".") else i
            if toks[j][0] == "id" and toks[j][1].casefold() in lists and j + 3 < b and \
                    toks[j + 1][:2] == ("op", ".") and toks[j + 2][1].casefold() in QUERY_PROPS and \
                    toks[j + 3][:2] == ("op", "="):
                rhs, i = _rhs(toks, j + 4, b)
                out.append((toks[j][1], resolve_text(rhs, env), toks[j][2], m))
                continue
            if i + 1 < b and toks[i + 1][:2] == ("op", "="):
                rhs, i = _rhs(toks, i + 2, b)
                env[v.casefold()] = resolve_text(rhs, env)
                continue
            i += 1
    return out


def use_always(attr, list_name):
    """{casefolded query name: field} of the UseAlways fields of a list attribute; a «~» entry (a field the designer
    could not resolve) loads nothing."""
    out = {}
    ua = attr.find("UseAlways") if attr is not None else None
    for f in (ua.children if ua is not None else []):
        path = f.text.strip().split(".")
        if f.tag == "Field" and len(path) >= 2 and path[0].casefold() == list_name.casefold():
            out[_query_name(path[1]).casefold()] = _query_name(path[1])
    return out


def _ua_raw(attr):
    ua = attr.find("UseAlways") if attr is not None else None
    return sorted(f.text.strip().casefold() for f in (ua.children if ua is not None else []) if f.tag == "Field")


def visible_bound(scope, list_name):
    """Casefolded query names of the list fields bound (DataPath <Список>.<Поле>) to items of the scope that are not
    hidden (<Visible>false</Visible>); the BaseForm subtree is not the scope."""
    out = set()
    stack = list(scope.children)
    while stack:
        node = stack.pop()
        if node.tag == "BaseForm":
            continue
        stack.extend(node.children)
        if node.tag != "DataPath" or node.parent is None:
            continue
        path = node.text.strip().split(".")
        hidden = node.parent.find("Visible")
        if len(path) >= 2 and path[0].casefold() == list_name.casefold() and \
                not (hidden is not None and hidden.text.strip() == "false"):
            out.add(_query_name(path[1]).casefold())
    return out


def check_lists(base, form_rel, ext_root, base_form, module_rel, mod, old, new, stats):
    """LIST_QUERY_FIELD_MISSING and LIST_FIELD_NOT_LOADED of one adopted form. old / new - (Form.xml root, form
    module) of the vendor (old may be (None, None)); the queries the extension's code assigns to the vendor's lists
    (mod) and its own Form.xml list queries that differ from BaseForm are compared with what the new vendor form
    needs from each list; for such a list whose UseAlways the extension changed (its Form.xml against BaseForm), a
    field of the new vendor UseAlways that the extension's query selects but its UseAlways and its visible items do
    not load."""
    new_root, new_mod = new
    new_lists = list_attrs(new_root)
    if not new_lists:
        return []
    replaced = []  # (list name, query text, file, line, method or None, source)
    if mod is not None:
        for name, text, line, m in code_list_queries(mod, set(new_lists)):
            stats["list_queries"] += 1
            if selected(text) is None:
                stats["list_queries_unjudged"] += 1
                continue
            replaced.append((name, text, module_rel, line, m.name, "код"))
    snapshot = list_attrs(base_form)
    for key, attr in list_attrs(ext_root).items():
        manual, text = list_query(attr)
        if key not in new_lists or key not in snapshot or not manual or not text:
            continue
        if " ".join(text.split()) == " ".join((list_query(snapshot[key])[1] or "").split()):
            continue  # the extension did not change the query: the vendor's new one applies
        stats["list_queries"] += 1
        if selected(text) is None:
            stats["list_queries_unjudged"] += 1
            continue
        replaced.append((attr.get("name"), text, form_rel, attr.line, None, "Form.xml"))
    found, cache, unloaded = [], {}, set()
    mine_lists = list_attrs(ext_root)
    for name, text, rel, line, method, source in replaced:
        key = name.casefold()
        if key not in cache:
            old_root, old_mod = old
            v_manual, v_text = list_query(new_lists[key])
            cache[key] = (list_needs(new_root, new_mod, name),
                          list_needs(old_root, old_mod, name) if old_root is not None else {},
                          selected(v_text) if v_manual else None)
        needs, old_needs, vendor_sel = cache[key]
        mine = selected(text)
        place = ("код, метод %s, стр. %d" % (method, line)) if method else ("Form.xml расширения, стр. %d" % line)
        for fkey, (field, where) in needs.items():
            stats["list_fields"] += 1
            if fkey in mine:
                continue
            if vendor_sel is not None and fkey not in vendor_sel:
                stats["list_fields_vendor_code"] += 1  # the vendor's own query does not select it: made by code
                continue
            fresh = fkey not in old_needs
            expr = (vendor_sel or {}).get(fkey)
            detail = "динамический список %s: расширение подменяет его запрос (%s), в запросе нет поля %s, которое " \
                     "нужно форме вендора new: %s; %s — добавить в запрос расширения поле %s%s" % (
                         name, place, field, "; ".join(where),
                         "поле новое в new: форма old его не требовала" if fresh else
                         "форма old его тоже требовала: колонки не было и до обновления",
                         field, " (у вендора new: %s КАК %s, с источником выражения)" % (expr, field) if expr else "")
            found.append(dict(base, module=rel, how="list", name="%s.%s" % (name, field), line=line, lines=[line],
                              methods=[method] if method else [], check="LIST_QUERY_FIELD_MISSING",
                              level="error" if fresh else "risk", detail=detail, list=name, field=field,
                              needed_by=where, new_in_new=fresh, source=source))
        attr, snap = mine_lists.get(key), snapshot.get(key)
        if attr is None or _ua_raw(attr) == _ua_raw(snap):
            continue  # the extension keeps the vendor's UseAlways: the new one applies
        ext_ua, shown = use_always(attr, name), visible_bound(ext_root, name)
        old_ua = use_always(list_attrs(old[0]).get(key) if old[0] is not None else None, name)
        for fkey, field in use_always(new_lists[key], name).items():
            if fkey in ext_ua or fkey in shown or fkey not in mine or (key, fkey) in unloaded:
                continue
            unloaded.add((key, fkey))
            fresh = fkey not in old_ua
            detail = "динамический список %s: расширение подменяет его запрос (%s) и меняет его UseAlways (Form.xml " \
                     "расширения, стр. %d); поле %s есть в UseAlways вендора new и в запросе расширения, но не в " \
                     "UseAlways расширения и не в его видимых элементах — если UseAlways расширения заменяет " \
                     "вендорский, поле не загружается в данные строки и код вендора, читающий его, его не получит; " \
                     "%s; заменяет платформа UseAlways или сливает — статикой не доказано, проверить в базе — " \
                     "добавить %s.%s в UseAlways списка в расширении" % (
                         name, place, attr.line, field,
                         "поле новое в UseAlways new" if fresh else "в UseAlways old оно тоже было", name, field)
            found.append(dict(base, module=form_rel, how="list", name="%s.%s" % (name, field), line=attr.line,
                              lines=[attr.line], methods=[], check="LIST_FIELD_NOT_LOADED", level="risk",
                              detail=detail, list=name, field=field, needed_by=["UseAlways"], new_in_new=fresh,
                              source="Form.xml"))
    return found


def check_form(ext, form_rel, cfg_old, cfg_new, stats):
    """Findings of the module of one adopted form of the extension."""
    module_rel = metadata.form_module_rel(form_rel)
    has_module = ext.exists(module_rel)
    title = metadata.form_title(form_rel)
    base = {"ext": ext.name, "form": title, "module": module_rel}
    try:
        root = forms.parse(ext.path(form_rel))
    except Exception as exc:  # noqa: BLE001
        if not has_module:
            return []
        return [dict(base, how="", name="", check="XML_UNREADABLE", level="risk", line=None, lines=[], methods=[],
                     detail="Form.xml расширения не разбирается: %s — %s" % (form_rel, exc))]
    base_form = root.find("BaseForm")
    if base_form is None:
        return []
    if has_module:
        stats["forms"] += 1
    views = {}
    for label, dump in (("old", cfg_old), ("new", cfg_new)):
        rel = metadata.counterpart_rel(ext, form_rel, dump)[0]
        views[label] = vendor_view(dump, rel)
    if views["new"] is None:
        if has_module:
            stats["forms_missing_new"] += 1
        return []
    broken = [(label, v[3]) for label, v in views.items() if v is not None and v[3]]
    if broken:
        if not has_module:
            return []
        return [dict(base, how="", name="", check="XML_UNREADABLE", level="risk", line=None, lines=[], methods=[],
                     detail="Form.xml в %s не разбирается: %s; форма не проверена" % (
                         ", ".join(lb for lb, _ in broken), "; ".join(e for _, e in broken)))]
    old, old_uuids, _, _, old_root, old_mod = views["old"] if views["old"] is not None else \
        (FormNames(), {}, set(), None, None, None)
    new, new_uuids, new_refs, _, new_root, new_mod = views["new"]
    mod = ext.module(module_rel) if has_module else None
    lists = check_lists(base, form_rel, root, base_form, module_rel, mod, (old_root, old_mod), (new_root, new_mod),
                        stats)
    if mod is None:
        return lists
    own = scope_names(root, scope_names(base_form))
    own.names["attribute"].update(_module_vars(mod))
    # the extension's own fields of the object: its form need not adopt the attribute Объект, so the object is also
    # taken from the vendor's main attribute, by the extension's name of it
    main = scope_names(root).main
    owner = metadata.module_owner(form_rel)
    for cand in (main, owner[1] and new.main and (new.main[0], owner[1]), old.main):
        if cand and ext.object(*cand) is not None:
            own.names["object"] = _object_fields(ext, cand, own_only=True)[0]
            break
    has_object = bool(old.main or new.main or main)
    snapshot = scope_names(base_form)

    occ = OrderedDict()  # (how, casefold name) -> {name, lines, methods, qualified}

    def note(how, name, line, method, qualified):
        e = occ.setdefault((how, name.casefold()), {"name": name, "lines": [], "methods": [], "qualified": False})
        if line not in e["lines"]:
            e["lines"].append(line)
        if method is not None and method.name not in e["methods"]:
            e["methods"].append(method.name)
        e["qualified"] = e["qualified"] or qualified

    at_line, local = method_locals(mod)

    def shadowed(line, word):
        m = at_line.get(line)
        return m is not None and word in local[id(m)]

    for how, name, line, _ in references(mod.toks, shadowed):
        if how == "object" and not has_object:
            continue
        note(how, name, line, at_line.get(line), True)
    for name, line, method in bare_names(mod, at_line, local):
        key = name.casefold()
        if key in old.names["attribute"] and key not in new.names["attribute"]:
            note("attribute", name, line, method, False)
    found = []
    for (how, key), e in occ.items():
        stats["names"] += 1
        if new.has(how, key) or own.has(how, key):
            continue
        row = dict(base, how=how, name=e["name"], line=e["lines"][0], lines=e["lines"], methods=e["methods"])
        if old.has(how, key):
            vendor_still = (how, key) in new_refs or ("str", key) in new_refs
            detail = "%s %s: в форме вендора old есть, в new нет" % (HOWS[how], e["name"])
            if how == "object":
                uid = next((u for u, nm in old_uuids.items() if nm.casefold() == key), None)
                if uid and uid in new_uuids:
                    detail += "; вендор переименовал в %s (uuid тот же)" % new_uuids[uid]
            if not e["qualified"]:
                detail += "; обращение без квалификатора: в new это локальная переменная метода, а не реквизит формы"
            if vendor_still:
                detail += "; модуль формы new всё ещё называет это имя (создаётся кодом?) — проверить"
            found.append(dict(row, check="GONE_IN_NEW", level="risk" if vendor_still else "error", detail=detail))
        elif e["qualified"] and key not in STANDARD.get(how, ()):
            if snapshot.has(how, key):
                # in <BaseForm>: the vendor had it when the form was adopted and dropped it before old
                found.append(dict(row, check="NOT_IN_VENDOR", level="risk",
                                  detail="%s %s: нет ни в old, ни в new, но есть в BaseForm расширения — вендор убрал "
                                         "его до old (форма заимствована из более раннего релиза)" % (
                                             HOWS[how], e["name"])))
                continue
            found.append(dict(row, check="NOT_IN_VENDOR", level="info",
                              detail="%s %s: нет ни в old, ни в new, ни в добавлениях расширения — создаётся кодом, "
                                     "чужое или опечатка" % (HOWS[how], e["name"])))
    return found + lists


# ---- object modules: fields of the vendor object the extension does not adopt (FIELD_NOT_ADOPTED) ----------------

def object_fields(o):
    """{casefold: (name, what)} of the attributes, addressing attributes and tabular sections of an object."""
    out = {}
    if o is None:
        return out
    for bucket, what in (("attrs", "реквизит"), ("addr", "реквизит адресации"), ("ts", "табличная часть")):
        for nm in getattr(o, bucket):
            out.setdefault(nm.casefold(), (nm, what))
    return out


def unassigned_reads(mod):
    """(name, line, method) of the unqualified identifiers live code reads (not a call, not a type after Новый) that
    are not a parameter or a Перем of their method and are not assigned earlier in it: for the Designer an assignment
    (at the start of a statement, or a loop variable) declares a local variable from there on. Module-level code
    counts as one more method."""
    toks = mod.toks
    at_line, local = method_locals(mod)
    seen = {}
    for i, (k, v, line) in enumerate(toks):
        if k != "id" or v.casefold() in bsl.KEYWORDS:
            continue
        prev = toks[i - 1] if i > 0 else None
        if prev is not None and (prev[:2] == ("op", ".") or (prev[0] == "id" and prev[1].casefold() in bsl.KW_NEW)):
            continue
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if nxt is not None and nxt[:2] == ("op", "("):
            continue
        m = at_line.get(line)
        if m is not None and line <= m.decl_end_line:
            continue
        key = v.casefold()
        assigned = seen.setdefault(id(m), set())
        start = (prev is None or prev[:2] == ("op", ";") or prev[0] in ("pre", "ann")
                 or (prev[0] == "id" and prev[1].casefold() in STMT_START)
                 or (m is not None and prev[2] <= m.decl_end_line))
        if (start and nxt is not None and nxt[:2] == ("op", "=")) or (
                prev is not None and prev[0] == "id" and prev[1].casefold() in LOOP_VAR):
            assigned.add(key)
            continue
        if key in assigned or (m is not None and key in local[id(m)]):
            continue
        yield v, line, m


def check_object_module(ext, rel, cfg_old, cfg_new, stats):
    """FIELD_NOT_ADOPTED findings of the object module rel of an object the extension adopts."""
    kind, name = metadata.module_owner(rel)
    if kind not in OBJECT_MODULE_KINDS:
        return []
    own = ext.object(kind, name)
    if own is None or not own.adopted:
        return []
    vendor = {}
    for label, dump in (("old", cfg_old), ("new", cfg_new)):
        vname = metadata.counterpart_name(ext, kind, name, dump)
        vendor[label] = (vname, object_fields(dump.object(kind, vname) if vname else None))
    new_name, new_fields = vendor["new"]
    if new_name is None:
        return []  # the vendor removed the object: borrowed (REMOVED)
    adopted = object_fields(own)
    missing = {k: v for k, v in new_fields.items() if k not in adopted}
    mod = ext.module(rel)
    if mod is None:
        return []
    stats["object_modules"] += 1
    if not missing:
        return []
    toks = mod.toks
    ignore = set(bsl.module_vars(toks, [(m.start_line, m.end_line) for m in mod.methods]))
    ignore |= set(mod.by_name)
    vendor_mod = cfg_new.module(metadata.object_module_rel(kind, new_name))
    if vendor_mod is not None:
        ignore |= set(vendor_mod.by_name)
    ignore |= set(cfg_new.common_modules()) | set(ext.common_modules())
    for how, nm, _, _ in references(toks):
        if how == "attribute" and nm.casefold() in missing:
            stats["object_module_this_unadopted"] += 1
    occ = OrderedDict()
    for nm, line, m in unassigned_reads(mod):
        key = nm.casefold()
        if key not in missing or key in ignore:
            continue
        e = occ.setdefault(key, {"lines": [], "methods": []})
        if line not in e["lines"]:
            e["lines"].append(line)
        if m is not None and m.name not in e["methods"]:
            e["methods"].append(m.name)
    found = []
    title = metadata.module_title(rel)
    for key, e in occ.items():
        field, what = missing[key]
        in_old = key in vendor["old"][1]
        detail = ("%s %s объекта %s.%s есть у вендора в new, в заимствовании расширения нет: в модуле объекта "
                  "расширения имя без квалификатора не определено — проверка модулей конфигуратора даёт «Переменная не "
                  "определена (%s)»; заимствовать или обращаться через ЭтотОбъект.%s" % (
                      what, field, kind, new_name, field, field))
        if in_old:
            detail += "; было и в old: в old поле тоже есть и тоже не заимствовано — не от обновления"
        else:
            detail += "; в old у вендора этого поля нет — обращение пришло с переносом"
        found.append(dict(ext=ext.name, form=title, module=rel, how="module", name=field, check="FIELD_NOT_ADOPTED",
                          level="risk" if in_old else "error", line=e["lines"][0], lines=e["lines"],
                          methods=e["methods"], detail=detail))
    return found


def analyze(ext_roots, cfg_old, cfg_new, only=None, skip=None):
    exts = metadata.find_extensions(ext_roots, only, skip)
    findings, stats = [], Counter()
    for ext in exts:
        for rel in ext.files("Form.xml"):
            if not rel.casefold().endswith("/ext/form.xml"):
                continue
            stats["form_files"] += 1
            findings.extend(check_form(ext, rel, cfg_old, cfg_new, stats))
        for rel in ext.files("ObjectModule.bsl"):
            if metadata.module_kind(rel) == "object":
                findings.extend(check_object_module(ext, rel, cfg_old, cfg_new, stats))
    return exts, findings, stats


def render(args, exts, findings, stats, elapsed):
    md = ["# Имена форм вендора, которые читает код расширений", ""]
    md.append("new: `%s`, old: `%s`. Расширений: %d, заимствованных форм с модулем: %d (формы нет в new: %d), "
              "проверено имён: %d, модулей объектов: %d. Время: %.1f с." % (
                  args.config_new, args.config_old, len(exts), stats["forms"], stats["forms_missing_new"],
                  stats["names"], stats["object_modules"], elapsed))
    md.append("")
    md.append("Код модуля заимствованной формы: `Элементы.X`, `Объект.X`, `Параметры.X`, `ЭтотОбъект.X` / "
              "`ЭтаФорма.X` и имена без квалификатора, которые в old были реквизитами формы или переменными модуля. "
              "Модуль объекта заимствованного объекта: имена без квалификатора, которые в new — реквизит или "
              "табличная часть вендора, а в заимствовании их нет (`ЭтотОбъект.X` таких полей — только счётом: %d). "
              "Комментарии, строки и блоки `#Удаление` не считаются." % stats["object_module_this_unadopted"])
    md.append("")
    counts = Counter(f["check"] for f in findings)
    md.append(fileio.md_table(["Проверка", "Находок", "Смысл"], [(k, counts.get(k, 0), v) for k, v in EXPLAIN.items()]))
    md.append("")
    md.append("## Находки")
    md.append("")
    md.append(fileio.md_table(["Проверка", "Уровень", "Расширение", "Форма", "Что", "Имя", "Строки", "Методы",
                               "Пояснение"],
                              [(f["check"], f["level"], f["ext"], f["form"], HOWS.get(f["how"], "—"), f["name"] or "—",
                                ", ".join(str(x) for x in f["lines"]) or "—", ", ".join(f["methods"]) or "—",
                                f["detail"]) for f in findings]) if findings else "Нет.")
    return "\n".join(md)


def main(argv=None):
    fileio.console_utf8()
    ap = argparse.ArgumentParser(description="Имена форм вендора (элементы, реквизиты, параметры, поля объекта, "
                                             "переменные модуля), которые читает код заимствованных форм расширений, "
                                             "против Form.xml вендора old и new; поля объекта вендора, которые модуль "
                                             "объекта расширения читает без заимствования")
    ap.add_argument("--ext-root", action="extend", nargs="+", required=True,
                    help="папка выгрузок расширений (или одна выгрузка); можно несколько")
    ap.add_argument("--ext", action="extend", nargs="+", help="только эти расширения (имя)")
    ap.add_argument("--skip", action="extend", nargs="+", help="пропустить эти расширения (имя)")
    ap.add_argument("--config-old", required=True, help="выгрузка типовой релиза, под который написаны расширения")
    ap.add_argument("--config-new", required=True, help="выгрузка типовой нового релиза")
    ap.add_argument("--out", required=True, help="папка отчёта names.json / names.md, вне выгрузок")
    args = ap.parse_args(argv)
    for label, root in [("--ext-root", r) for r in args.ext_root] + [("--config-old", args.config_old),
                                                                     ("--config-new", args.config_new)]:
        if inside(args.out, root):
            sys.exit("--out %s внутри %s %s: names в выгрузки не пишет" % (args.out, label, root))
    t0 = time.time()
    metadata.check_config(args.config_new, "--config-new")
    metadata.check_config(args.config_old, "--config-old")
    cfg_new, cfg_old = metadata.Dump(args.config_new, "new"), metadata.Dump(args.config_old, "old")
    exts, findings, stats = analyze(args.ext_root, cfg_old, cfg_new, args.ext, args.skip)
    if not exts:
        sys.exit("no extension dumps found under --ext-root")
    elapsed = time.time() - t0
    data = {"config_new": args.config_new, "config_old": args.config_old, "extensions": [e.name for e in exts],
            "stats": dict(stats), "findings": findings}
    md_path, json_path = fileio.write_report(args.out, "names", render(args, exts, findings, stats, elapsed), data)
    counts = Counter(f["check"] for f in findings)
    print("extensions: %d, adopted forms with a module: %d, names: %d, object modules: %d, findings: %d, %.1fs" % (
        len(exts), stats["forms"], stats["names"], stats["object_modules"], len(findings), elapsed))
    if counts:
        print("checks: " + ", ".join("%s=%d" % kv for kv in sorted(counts.items())))
    print(md_path)
    print(json_path)


if __name__ == "__main__":
    main()
