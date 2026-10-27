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

from . import bsl, fileio, forms, metadata
from .control_rebase import inside

HOWS = OrderedDict([("items", "элемент формы"), ("attribute", "реквизит формы / переменная модуля"),
                    ("object", "поле объекта"), ("parameter", "параметр формы")])
EXPLAIN = OrderedDict([
    ("GONE_IN_NEW", "имя было в форме вендора old, в new его нет, расширение его не добавляет"),
    ("NOT_IN_VENDOR", "имени нет ни в old, ни в new, ни в добавлениях расширения: создаётся кодом, чужое или опечатка"),
    ("XML_UNREADABLE", "Form.xml (расширения, old или new) не разбирается: форма не проверена"),
])
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
    vendor's form module {(how, casefold name)} | string values, error or None); None when Form.xml is missing."""
    path = dump.path(form_rel)
    if not os.path.isfile(path):
        return None
    try:
        root = forms.parse(path)
    except Exception as exc:  # noqa: BLE001 - expat errors of every kind: reported as XML_UNREADABLE
        return None, None, None, "%s: %s" % (type(exc).__name__, exc)
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
    return view, uuids, refs, None


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


def check_form(ext, form_rel, cfg_old, cfg_new, stats):
    """Findings of the module of one adopted form of the extension."""
    module_rel = metadata.form_module_rel(form_rel)
    if not ext.exists(module_rel):
        return []
    title = metadata.form_title(form_rel)
    base = {"ext": ext.name, "form": title, "module": module_rel}
    try:
        root = forms.parse(ext.path(form_rel))
    except Exception as exc:  # noqa: BLE001
        return [dict(base, how="", name="", check="XML_UNREADABLE", level="risk", line=None, lines=[], methods=[],
                     detail="Form.xml расширения не разбирается: %s — %s" % (form_rel, exc))]
    base_form = root.find("BaseForm")
    if base_form is None:
        return []
    stats["forms"] += 1
    views = {}
    for label, dump in (("old", cfg_old), ("new", cfg_new)):
        rel = metadata.counterpart_rel(ext, form_rel, dump)[0]
        views[label] = vendor_view(dump, rel)
    if views["new"] is None:
        stats["forms_missing_new"] += 1
        return []
    broken = [(label, v[3]) for label, v in views.items() if v is not None and v[3]]
    if broken:
        return [dict(base, how="", name="", check="XML_UNREADABLE", level="risk", line=None, lines=[], methods=[],
                     detail="Form.xml в %s не разбирается: %s; форма не проверена" % (
                         ", ".join(lb for lb, _ in broken), "; ".join(e for _, e in broken)))]
    old, old_uuids, _, _ = views["old"] if views["old"] is not None else (FormNames(), {}, set(), None)
    new, new_uuids, new_refs, _ = views["new"]
    own = scope_names(root, scope_names(base_form))
    mod = ext.module(module_rel)
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
    return exts, findings, stats


def render(args, exts, findings, stats, elapsed):
    md = ["# Имена форм вендора, которые читает код расширений", ""]
    md.append("new: `%s`, old: `%s`. Расширений: %d, заимствованных форм с модулем: %d (формы нет в new: %d), "
              "проверено имён: %d. Время: %.1f с." % (args.config_new, args.config_old, len(exts), stats["forms"],
                                                     stats["forms_missing_new"], stats["names"], elapsed))
    md.append("")
    md.append("Код модуля заимствованной формы: `Элементы.X`, `Объект.X`, `Параметры.X`, `ЭтотОбъект.X` / "
              "`ЭтаФорма.X` и имена без квалификатора, которые в old были реквизитами формы или переменными модуля. "
              "Комментарии, строки и блоки `#Удаление` не считаются.")
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
                                             "против Form.xml вендора old и new")
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
    print("extensions: %d, adopted forms with a module: %d, names: %d, findings: %d, %.1fs" % (
        len(exts), stats["forms"], stats["names"], len(findings), elapsed))
    if counts:
        print("checks: " + ", ".join("%s=%d" % kv for kv in sorted(counts.items())))
    print(md_path)
    print(json_path)


if __name__ == "__main__":
    main()
