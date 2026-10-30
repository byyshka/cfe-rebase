# -*- coding: utf-8 -*-
"""Adopted metadata of the extensions against the new release: what the platform's applicability check will refuse.

    cfe-rebase borrowed --ext-root <extension dumps> --config-old <old> --config-new <new> [--ext NAME] --out <dir>

For every object an extension adopts (ObjectBelonging = Adopted in its XML): the object itself, and each adopted
attribute, dimension, resource, journal column, addressing attribute, accounting flag, enum value, tabular section with
its attributes, form, template and command must still exist in the new release, and an adopted attribute or tabular
section attribute whose type (with string length and number digits) the extension stores must keep it in new. Checks:
  OBJECT_REMOVED  the adopted object is gone from new (in old it is there; one in neither release is NOT_IN_VENDOR)
  OBJECT_RENAMED  the vendor renamed the adopted object (the uuid of ExtendedConfigurationObject is in new under
                  another name, «Удалить<Имя>» included): its children are compared with the renamed object; the
                  detail names the borrow-edit --rename call
  REMOVED         an adopted child is gone from new (vendor renaming to «Удалить<Имя>» is named in the detail)
  RENAMED         the vendor renamed an adopted child or tabular section, keeping its uuid
  TYPE_CHANGED    the vendor changed the type old -> new; the extension still holds the old one (compared only
                  where the extension stores a copy of the old type: simple types; a composite one is a placeholder;
                  a composite type the vendor only widened is accepted by the platform and not reported; the order
                  of the types inside a composite type does not count)
  NAME_CLASH      an attribute or tabular section the extension adds (or an attribute it adds to an adopted tabular
                  section) has the name of one the vendor added in new
  OBJECT_NAME_CLASH an object the extension owns (not adopted) has the name of a vendor object of the same kind in new
                  (the same top-level folder of the dump, names case-insensitive): the platform does not apply the
                  extension, «Уже существует объект с именем Обработка.ЗаменаСтавокНДС» (a real case,
                  ЗаменаСтавок_Расш under УТ 11.5.27.79, the data processor is not in 11.5.17.172). Only the same kind
                  is compared: the vendor configuration itself holds objects of different kinds under one name (УТ
                  11.5.27.79: 47 common modules named as a data processor - ЖурналРегистрации; 5 catalogs named as a
                  data processor - НастройкиЭДО), so different kinds share no namespace there; for an extension against
                  the configuration that is not proven separately. A vendor object that is in old too (in_old) is not
                  a clash the release brought: the extension did not apply before it either, or the base is not the
                  configuration of old - the plan puts it under REVIEW
  NOT_IN_VENDOR   an adopted object or child is in neither old nor new: the base configuration is modified or the
                  field belongs to another extension - a question, not a breakage
  LIST_FIELD_LOST an adopted form whose dynamic list query the vendor changed (or made manual) no longer selects a
                  field the extension shows on that form (a DataPath the extension added)
  LIST_CHANGED    the vendor and the extension both changed the settings of one dynamic list of a form (fields the
                  extension's own query selects and shows on the form that the new vendor query does not select are
                  named in the detail: the merge has to carry them over)
  XML_UNREADABLE  the XML of the object exists but does not parse - in new, in old or in the extension: the object is
                  not checked (this is not OBJECT_REMOVED), the detail names the file and the parser error
  COMPAT_MODE     <CompatibilityMode> in Configuration.xml of the extension differs from the one of new: the platform
                  refuses the load («Значение контролируемого свойства РежимСовместимости у объекта  не совпадает со
                  значением в расширяемой конфигурации», a real load: 8_3_24 of 11.5.17 against 8_3_27 of 11.5.27).
                  An extension without the property is not checked; ConfigurationExtensionCompatibilityMode is not
                  compared (the platform took 8_3_21 under 8_3_27 there)
  PROPERTY_CHANGED a controlled property of an adopted object (its <Properties>, of an adopted form, template or
                  command too; CompatibilityMode of the extension's Configuration.xml is COMPAT_MODE) holds the vendor's value of
                  old and the vendor changed it in new: the platform refuses the extension («Значение контролируемого
                  свойства Хранение у объекта ФункциональнаяОпция.X не совпадает со значением в расширяемой
                  конфигурации»); a new value that names an object the extension does not have gets a cfe-borrow hint
Controlled properties: an adopted object holds, besides its identity (ObjectBelonging, Name, Comment,
ExtendedConfigurationObject), only properties the platform either controls (it copied the vendor's value at borrowing
and checks it when it applies the extension) or lets the extension change (ITS «Расширения конфигураций», «Устройство»:
a property is either controlled or modified, never both). The ones the extension changes are NOT_CONTROLLED below; a
property the extension holds with a value other than old's is its own value and is not reported either, except
PROVEN_CONTROLLED (named by the platform's refusal of a real load).

An adopted form without <ExtendedConfigurationObject> is not a finding: checked on 17.10.2026 on a real base
(platform 8.3.27), such a form loaded from XML applies like one with the link (0.2.5 reported it, 0.2.6 does not).
Names are compared case-insensitively, as 1C does: a vendor rename that changes only the case is not REMOVED.
For REMOVED / TYPE_* of an object or tabular section attribute, forms of that object in the extension that bind it
(DataPath ….<Имя>) are listed. The dumps are never written: --out inside --ext-root, --config-old or --config-new
is refused.
"""
import argparse
import html
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter

from . import fileio, metadata
from .control_rebase import inside

BUCKETS = (("attrs", "реквизит"), ("dims", "измерение"), ("res", "ресурс"), ("cols", "графа"),
           ("addr", "реквизит адресации"), ("flags", "признак учёта"), ("enum_values", "значение перечисления"))
NAMED = (("forms", "форма"), ("templates", "макет"), ("commands", "команда"))
EXPLAIN = {
    "OBJECT_REMOVED": "заимствованного объекта нет в new",
    "OBJECT_RENAMED": "вендор переименовал заимствованный объект (uuid тот же): переименовать заимствование",
    "REMOVED": "заимствованного реквизита / ресурса / значения / формы нет в new",
    "RENAMED": "вендор переименовал заимствованный реквизит или табличную часть (uuid тот же)",
    "TYPE_CHANGED": "вендор сменил тип old → new, в расширении старый",
    "NAME_CLASH": "собственный реквизит или табличная часть расширения совпали по имени с новым полем вендора",
    "OBJECT_NAME_CLASH": "собственный объект расширения совпал по имени с объектом вендора того же вида в new — "
                         "платформа расширение не применит",
    "NOT_IN_VENDOR": "заимствованного объекта или поля нет ни в old, ни в new: доработанная конфигурация или "
                     "чужое поле",
    "LIST_FIELD_LOST": "новый запрос динамического списка заимствованной формы не выбирает поле расширения",
    "LIST_CHANGED": "список заимствованной формы изменили и вендор, и расширение",
    "XML_UNREADABLE": "XML объекта (в new, old или расширении) не разбирается: объект не проверен, это не удаление",
    "COMPAT_MODE": "режим совместимости (CompatibilityMode) в Configuration.xml расширения не совпадает с new",
    "PROPERTY_CHANGED": "контролируемое свойство заимствованного объекта: в расширении значение old, вендор сменил его "
                        "в new — платформа расширение не применит",
}

# properties of an adopted object that are not controlled. Identity: the extension's own. Extended: the extension's
# additions to the vendor's list (the corpora hold only the extension's registers, subsystem content items, filter
# criterion types and content - values other than old's, loaded into old). Modified: the extension's value replaces
# the vendor's (Synonym, ObjectPresentation and MainDataCompositionSchema hold the extension's value in real
# corpora, loaded); the presentations, Explanation and the main forms are taken by analogy - not proven by a load
IDENTITY_PROPS = {"ObjectBelonging", "Name", "Comment", "ExtendedConfigurationObject"}
NOT_CONTROLLED = {"RegisterRecords", "Content", "Synonym", "ObjectPresentation", "ExtendedObjectPresentation",
                  "ListPresentation", "ExtendedListPresentation", "Explanation", "MainDataCompositionSchema",
                  "DefaultObjectForm", "DefaultListForm", "DefaultChoiceForm", "DefaultFolderForm",
                  "DefaultFolderChoiceForm", "DefaultRecordForm", "DefaultForm", "DefaultSettingsForm",
                  "DefaultVariantForm", "AuxiliaryObjectForm", "AuxiliaryListForm", "AuxiliaryChoiceForm",
                  "AuxiliaryFolderForm", "AuxiliaryFolderChoiceForm", "AuxiliaryRecordForm", "AuxiliaryForm",
                  "AuxiliarySettingsForm", "AuxiliaryVariantForm"}
# extended in a filter criterion only: Type there lists the extension's added types
NOT_CONTROLLED_OF = {"FilterCriterion": {"Type"}}
# controlled whatever the extension's value against old: named by the platform's refusal (a real load)
PROVEN_CONTROLLED = {("FunctionalOption", "Location")}
# Russian names of the properties as the platform's message and the syntax reference name them (display only)
PROP_RU = {"Location": "Хранение", "CompatibilityMode": "РежимСовместимости", "Type": "Тип",
           "InformationRegisterPeriodicity": "ПериодичностьРегистраСведений", "WriteMode": "РежимЗаписи",
           "RegisterType": "ВидРегистра", "Category": "Категория", "Group": "Группа", "HierarchyType": "ВидИерархии",
           "CodeLength": "ДлинаКода", "CodeType": "ТипКода", "CodeAllowedLength": "ДопустимаяДлинаКода",
           "DescriptionLength": "ДлинаНаименования", "Hierarchical": "Иерархический", "Owners": "Владельцы",
           "NumberType": "ТипНомера", "NumberLength": "ДлинаНомера", "NumberAllowedLength": "ДопустимаяДлинаНомера",
           "FormType": "ТипФормы", "TemplateType": "ТипМакета", "LanguageCode": "КодЯзыка", "Task": "Задача",
           "Global": "Глобальный", "Server": "Сервер", "ServerCall": "ВызовСервера",
           "ExternalConnection": "ВнешнееСоединение", "ClientManagedApplication": "КлиентУправляемоеПриложение",
           "ClientOrdinaryApplication": "КлиентОбычноеПриложение"}
# kinds outside metadata.KINDS whose adopted objects carry controlled properties (the corpora: StyleItem Type,
# CommandGroup Category, CommonAttribute Type, Language LanguageCode, XDTOPackage Namespace)
EXTRA_KINDS = {"StyleItems": "ЭлементСтиля", "CommandGroups": "ГруппаКоманд", "CommonAttributes": "ОбщийРеквизит",
               "Languages": "Язык", "XDTOPackages": "ПакетXDTO", "CommonPictures": "ОбщаяКартинка",
               "Styles": "Стиль", "FunctionalOptionsParameters": "ПараметрФункциональныхОпций",
               "Interfaces": "Интерфейс", "WSReferences": "WSСсылка", "Bots": "Бот",
               "IntegrationServices": "СервисИнтеграции", "ExternalDataSources": "ВнешнийИсточникДанных"}
REF_RE = re.compile(r"^([A-Za-z]+)\.([^.\s]+)$")


def _short(key):
    """A type key for a person: 'Type=xs:string;StringQualifiers;Length=30;…' -> 'xs:string, Length=30'."""
    if not key:
        return "—"
    parts = [p for p in key.split(";") if "=" in p]
    return ", ".join(p.split("=", 1)[1] if p.startswith(("Type=", "TypeSet=")) else p for p in parts) or key


def _type_diff(a, b):
    sa, sb = set((a or "").split(";")), set((b or "").split(";"))
    gone, came = [p for p in (a or "").split(";") if p not in sb and "=" in p], \
        [p for p in (b or "").split(";") if p not in sa and "=" in p]
    return "%s → %s" % (_short(";".join(gone)) if gone else "—", _short(";".join(came)) if came else "—")


def _split(key):
    parts = [p for p in (key or "").split(";") if p]
    return ({p for p in parts if p.startswith(("Type=", "TypeSet="))},
            Counter(p for p in parts if not p.startswith(("Type=", "TypeSet="))))


def _same_type(a, b):
    """One type whatever the order of the types inside a composite one: the vendor reorders them between releases."""
    if a is None or b is None:
        return a is b
    return Counter(p for p in a.split(";") if p) == Counter(p for p in b.split(";") if p)


def _widened(old_key, new_key):
    """The vendor only added types to a composite type, qualifiers kept: the platform applies the extension as is (in
    the hand ports the narrower type of the extension was kept)."""
    (ot, oq), (nt, nq) = _split(old_key), _split(new_key)
    return ot <= nt and ot != nt and oq == nq


def _forms_binding(ext, kind, obj, name):
    """Forms of the object in the extension that bind a field named `name` (DataPath ending in .<name>)."""
    folder = os.path.join(ext.root, metadata.KINDS[kind][0], obj, "Forms")
    if not os.path.isdir(folder):
        return []
    rx = re.compile(r"<DataPath>[^<]*\.%s</DataPath>" % re.escape(name), re.I)
    out = []
    for dp, _, names in os.walk(folder):
        for n in names:
            if n == "Form.xml":
                p = os.path.join(dp, n)
                if rx.search(fileio.read_text(p)):
                    out.append(os.path.relpath(p, ext.root).replace("\\", "/"))
    return sorted(out)


LIST_ATTR_RE = re.compile(r'<Attribute name="([^"]+)"[^>]*>(.*?)</Attribute>', re.S)
SETTINGS_RE = re.compile(r"<Settings[^>]*>(.*?)</Settings>", re.S)
QUERY_RE = re.compile(r"<QueryText>(.*?)</QueryText>", re.S)
# what makes a dynamic list's data: the query, whether it is manual, the main table. The rest of Settings - the
# dataset fields the platform lists from the object's attributes, the user settings - follows the metadata and is
# judged by the other checks (an attribute the vendor removed is REMOVED, a field a query reads is check_queries')
LIST_KEY_RES = (QUERY_RE, re.compile(r"<ManualQuery>(.*?)</ManualQuery>", re.S),
                re.compile(r"<MainTable>(.*?)</MainTable>", re.S))
QUERY_IDENT_RE = re.compile(r"[A-Za-zА-Яа-яЁё_][\wЁё]*(?:\.[A-Za-zА-Яа-яЁё_][\wЁё]*)*")
LIST_NAMES_MAX = 12  # identifiers of the vendor's change named in one LIST_CHANGED


def _lists(part):
    """{dynamic list attribute: its Settings with whitespace collapsed} of one part of a Form.xml."""
    out = {}
    for m in LIST_ATTR_RE.finditer(part):
        if "DynamicList" in m.group(2):
            s = SETTINGS_RE.search(m.group(2))
            out[m.group(1)] = " ".join((s.group(1) if s else "").split())
    return out


# a form binds standard attributes of a dynamic list by their English names (Список.Ref, Список.Date) while the query
# names them in Russian: such a field is not compared with the query text
STANDARD_PATHS = {"Ref", "Date", "Number", "DeletionMark", "Posted", "Code", "Description", "Owner", "Parent",
                  "IsFolder", "Predefined", "PredefinedDataName", "Recorder", "Period", "LineNumber", "Active"}


def _list_key(settings):
    """The query text (whitespace collapsed), ManualQuery and MainTable of a list: equal keys - the vendor did not
    change the list's data, only what follows the metadata."""
    return tuple(" ".join(html.unescape(m.group(1)).split()) if m else None
                 for m in (rx.search(settings or "") for rx in LIST_KEY_RES))


def _query_idents(settings):
    """{casefolded: as written} of the identifiers and dotted chains (Документ.X.Поле) of a list's query text."""
    q = QUERY_RE.search(settings or "")
    return {x.casefold(): x for x in QUERY_IDENT_RE.findall(html.unescape(q.group(1)))} if q else {}


def _vendor_change_carried(old, new, mine):
    """([vendor additions the extension's query lacks], [vendor removals it still has]): the identifiers and dotted
    chains the vendor added to the list query old -> new must be in the extension's own query, the ones it removed
    must not. Both empty: the extension's list already carries the vendor's change."""
    o, n, m = _query_idents(old), _query_idents(new), _query_idents(mine)
    return sorted(n[k] for k in n.keys() - o.keys() - m.keys()), sorted(o[k] for k in (o.keys() - n.keys()) & m.keys())


def _names(items):
    return ", ".join(items[:LIST_NAMES_MAX]) + (" и ещё %d" % (len(items) - LIST_NAMES_MAX)
                                                if len(items) > LIST_NAMES_MAX else "")


def _selects(query, field):
    """The query text names the field as a whole word, any case."""
    return re.search(r"(?<![\wЁё])%s(?![\wЁё])" % re.escape(field), query, re.I) is not None


def _form_parts(path):
    """(the extension's form, its BaseForm) of an adopted Form.xml; the vendor's at borrowing time is in BaseForm."""
    text = fileio.read_text(path)
    main, sep, base = text.partition("<BaseForm")
    return main, base if sep else ""


def check_form_lists(ext, kind, name, form, cfg_old, cfg_new, ported=None):
    """An adopted form whose dynamic list the vendor changed (query text, ManualQuery or MainTable): the fields the
    extension adds to the form must still be selected by the new query; a list the extension changed itself must be
    merged with the vendor's change - unless it already is («Форма.Список» is then appended to ported)."""
    if cfg_old is None:
        return []
    rel = "%s/%s/Forms/%s/Ext/Form.xml" % (metadata.KINDS[kind][0], name, form)
    paths = [os.path.join(d.root, *rel.split("/")) for d in (ext, cfg_old, cfg_new)]
    if not all(os.path.isfile(p) for p in paths):
        return []
    main, base = _form_parts(paths[0])
    if not base:
        return []
    # list names are matched case-insensitively, as every other name of the configuration
    old, new = ({k.casefold(): v for k, v in _lists(fileio.read_text(p)).items()} for p in paths[1:])
    mine, at_borrowing = _lists(main), {k.casefold(): v for k, v in _lists(base).items()}
    data_paths = lambda part: set(re.findall(r"<DataPath>([^<]+)</DataPath>", part))  # noqa: E731
    added_paths = data_paths(main) - data_paths(base)
    out = []
    for attr, settings in mine.items():
        key = attr.casefold()
        if key not in old or key not in new or _list_key(old[key]) == _list_key(new[key]):
            continue
        what = "форма %s, динамический список %s: вендор изменил его настройки (запрос)" % (form, attr)
        q = QUERY_RE.search(new[key])
        fields = sorted({p.split(".", 1)[1].split(".")[0] for p in added_paths
                         if p.casefold().startswith(key + ".")} - STANDARD_PATHS)
        lost = [f for f in fields if q is not None and not _selects(q.group(1), f)]
        if settings != at_borrowing.get(key, settings):
            # the list is ported already: re-borrowed from new (BaseForm holds the new vendor list), or the
            # extension's own query carries every identifier the vendor added and none it removed
            lacks, keeps = _vendor_change_carried(old[key], new[key], settings)
            if _list_key(at_borrowing.get(key)) == _list_key(new[key]) or not (lacks or keeps):
                if ported is not None:
                    ported.append("%s.%s" % (form, attr))
                continue
            # what the merge must carry over: the fields the extension's own query adds; a field its own query does
            # not select either (a standard attribute bound as Список.Ref, Список.Date) is not part of its change
            own = QUERY_RE.search(settings)
            lost = [f for f in lost if own is None or _selects(own.group(1), f)]
            todo = (["в запросе расширения нет добавленного вендором: " + _names(lacks)] if lacks else []) + \
                (["в нём осталось убранное вендором: " + _names(keeps)] if keeps else [])
            out.append({"part": "форма", "name": form, "check": "LIST_CHANGED",
                        "detail": what + ", и расширение меняло этот список само — перенести его правку на новые "
                                         "настройки вендора: " + "; ".join(todo) +
                                         ("; новый запрос вендора не выбирает поля, которые выводит расширение: %s"
                                          % ", ".join(lost) if lost else "")})
            continue
        if lost:
            out.append({"part": "форма", "name": form, "check": "LIST_FIELD_LOST",
                        "detail": what + " — новый запрос не выбирает поля, которые выводит расширение: %s; добавить "
                                         "их в запрос формы в расширении" % ", ".join(lost)})
    return out


def _canon(el):
    """A property value comparable across dumps: tag, attributes, text and the children as a multiset (the vendor
    reorders the items of a list, the types of a composite type)."""
    kids = sorted(_canon(c) for c in el)
    return "%s%s=%s[%s]" % (el.tag, sorted(el.attrib.items()), (el.text or "").strip(), ",".join(kids))


def _show(el):
    """A property value for a person: its text, or the texts inside it."""
    if el is None:
        return "—"
    texts = [(c.text or "").strip() for c in el.iter() if (c.text or "").strip()]
    out = ", ".join(texts) or "пусто"
    return out if len(out) <= 200 else out[:200] + "…"


def _properties(path, child=None):
    """<Properties> of the object in path (child: (tag, name) of an element of its ChildObjects); None when the file is
    absent, does not parse or has no such element."""
    if not path or not os.path.isfile(path):
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None
    obj = next(iter(root), None)
    if obj is None:
        return None
    if child is not None:
        co = obj.find(metadata.MD + "ChildObjects")
        hit = None
        for el in co.findall(metadata.MD + child[0]) if co is not None else []:
            nm = el.findtext(metadata.MD + "Properties/" + metadata.MD + "Name") or ""
            if nm.strip().casefold() == child[1].casefold():
                hit = el
                break
        obj = hit
    return obj.find(metadata.MD + "Properties") if obj is not None else None


def _kind_tag(path):
    """The element name of the object in path (FunctionalOption, Form, Template, Configuration)."""
    try:
        obj = next(iter(ET.parse(path).getroot()), None)
    except (ET.ParseError, OSError):
        return None
    return obj.tag.rsplit("}", 1)[-1] if obj is not None else None


def _same_value(tag, a_props, b_props):
    a, b = a_props.find(metadata.MD + tag), b_props.find(metadata.MD + tag)
    if tag == "Type" and a is not None and b is not None and \
            any(c.tag in (metadata.V8 + "Type", metadata.V8 + "TypeSet") for c in list(a) + list(b)):
        return _same_type(metadata.type_key(a_props), metadata.type_key(b_props))
    return _canon(a) == _canon(b)


EN2RU = {v: k for k, v in metadata.KIND_TAGS.items()}


def _missing_ref(ext, value):
    """«Constant.X» named by a new value that the extension does not have: (EN kind, name, RU title), else None."""
    m = REF_RE.match(value or "")
    if not m or m.group(1) not in EN2RU:
        return None
    ru = EN2RU[m.group(1)]
    if ext.object_exists(ru, m.group(2)):
        return None
    return m.group(1), m.group(2), "%s.%s" % (ru, m.group(2))


def check_properties(ext, base, kind_tag, ext_path, old_path, new_path, child=None, sub=None):
    """PROPERTY_CHANGED findings of one adopted object, form, template or command: a controlled property whose value
    in the extension is the vendor's old one (or any value, for PROVEN_CONTROLLED) and differs from new. kind_tag -
    the element name (FunctionalOption, Form, Command, …); child - (tag, name) of an element of ChildObjects; sub -
    (part, name) of a form, template or command of the object, named in part and detail."""
    mine = _properties(ext_path, child)
    theirs = _properties(new_path, child)
    if mine is None or theirs is None:
        return []
    before = _properties(old_path, child) if old_path else None
    skip = IDENTITY_PROPS | NOT_CONTROLLED | NOT_CONTROLLED_OF.get(kind_tag, set())
    out = []
    for el in mine:
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in skip or theirs.find(metadata.MD + tag) is None or _same_value(tag, mine, theirs):
            continue
        proven = (kind_tag, tag) in PROVEN_CONTROLLED
        was = before.find(metadata.MD + tag) if before is not None else None
        # a value other than old's is the extension's own (a type placeholder of a composite type, an extended one)
        if not proven and (was is None or not _same_value(tag, mine, before)):
            continue
        if tag == "Type" and _widened(metadata.type_key(mine), metadata.type_key(theirs)):
            continue  # the vendor only added types: accepted, as for attributes (ОснованияЗапросаКоммерческих…, a real case)
        new_el = theirs.find(metadata.MD + tag)
        name = "%s (%s)" % (PROP_RU[tag], tag) if tag in PROP_RU else tag
        where = "%s %s: " % sub if sub else ""
        detail = "%sконтролируемое свойство %s: в расширении %s, в new %s%s — поставить значение new" % (
            where, name, _show(el), _show(new_el),
            "" if was is None or _same_value(tag, mine, before) else " (и с old %s не совпадало)" % _show(was))
        ref = _missing_ref(ext, (new_el.text or "").strip())
        if ref:
            detail += "; %s в расширении нет — заимствовать: cfe-borrow -Object \"%s.%s\"" % (ref[2], ref[0], ref[1])
        out.append(dict(base, part="свойство" + (" %s %s" % sub if sub else ""), name=tag,
                        check="PROPERTY_CHANGED", detail=detail, value=_show(el), new_value=_show(new_el)))
    return out


def _folded(children):
    """{casefolded name: info} of one child collection: 1C names are case-insensitive, and the vendor may change only
    the case of a name between releases. Each info gets the name as written ("name")."""
    return {k.casefold(): dict(v, name=k) for k, v in children.items()}


def _unreadable(base, name, where, dump, kind):
    return dict(base, part="объект", name=name, check="XML_UNREADABLE",
                detail="XML объекта в %s не разбирается: %s — %s; объект не проверен (это не удаление)" % (
                    where, dump.object_path(kind, name), dump.object_error(kind, name)))


def check_object(ext, kind, name, cfg_old, cfg_new, ported=None):
    """Findings of one object of the extension (none for an object the extension owns); ported - a list that gets
    «Вид.Имя.Форма.Список» of each dynamic list both changed and the extension has already ported."""
    o = ext.object(kind, name)
    title = "%s.%s" % (kind, name)
    rel = "%s/%s.xml" % (metadata.KINDS[kind][0], name)
    base = {"ext": ext.name, "object": title, "file": rel}
    if o is None:
        return [_unreadable(base, name, "расширении", ext, kind)] if ext.object_error(kind, name) else []
    if not o.adopted:
        return []
    # the vendor keeps the uuid when it renames an object: the adopted one is looked up by ExtendedConfigurationObject
    new_name = metadata.counterpart_name(ext, kind, name, cfg_new) or name
    old_name = (metadata.counterpart_name(ext, kind, name, cfg_old) or name) if cfg_old is not None else name
    n = cfg_new.object(kind, new_name)
    old = cfg_old.object(kind, old_name) if cfg_old is not None else None
    # every verdict of the object rests on both releases: an unparsable one is reported, not read as «no object»
    broken = [_unreadable(base, nm, where, d, kind) for where, d, nm in (("new", cfg_new, new_name),
                                                                          ("old", cfg_old, old_name))
              if d is not None and d.object_error(kind, nm)]
    if broken:
        return broken
    found = []
    if n is not None and new_name.casefold() != name.casefold():
        inner = o.props.get("Name") or name
        if inner.casefold() == new_name.casefold():
            todo = "имя в XML расширения уже новое, файлы — под старым: переименовать файлы и папку"
        else:
            todo = "переименовать заимствование"
        hint = "; снять заимствование, если расширение им не пользуется (cfe-rebase borrow-edit --remove %s.%s), " \
               "иначе " % (kind, name) if new_name.casefold().startswith("удалить") else "; "
        found.append(dict(base, part="объект", name=name, check="OBJECT_RENAMED", new_name=new_name,
                          detail="%s%s%s: cfe-rebase borrow-edit --rename %s.%s=%s; ссылки на старое имя в коде "
                                 "расширения (менеджер, тексты запросов) borrow-edit называет, правятся руками" % (
                                     metadata.renamed_note(kind, name, new_name), hint, todo, kind, name, new_name)))
    if n is None:
        if old is None and cfg_old is not None:
            # in neither release (an XML that does not parse was reported above): the same question as a field there
            return [dict(base, part="объект", name=name, check="NOT_IN_VENDOR",
                         detail="объекта нет ни в old, ни в new: конфигурация базы доработана — проверить по базе")]
        return [dict(base, part="объект", name=name, check="OBJECT_REMOVED",
                     detail="объекта нет в new" + ("" if old is None else " (в old был)") +
                     "; снять заимствование: cfe-rebase borrow-edit --remove %s.%s" % (kind, name))]
    new_fields = {x.casefold() for x in n.fields()} | {x.casefold() for x in n.enum_values}
    old_fields = {x.casefold() for x in old.fields()} | {x.casefold() for x in old.enum_values} if old else set()

    def removed(part, child, names, forms_of=None):
        hint = ""
        if ("удалить" + child).casefold() in names:
            hint = "; вендор переименовал в «Удалить%s» — пометил к удалению" % child
        f = dict(base, part=part, name=child, check="REMOVED", detail="%s %s в new нет%s" % (part, child, hint))
        if forms_of:
            f["forms"] = _forms_binding(ext, kind, name, child)
        found.append(f)

    def check(part, child, info, theirs, before, new_names, old_names, forms_of):
        """One child against the vendor: the same rules for attributes, tabular sections and their attributes.
        theirs / before - the children of that collection in new / old keyed by casefolded name, new_names /
        old_names - casefolded names a child of the extension must not take."""
        key = child.casefold()
        if not info["adopted"]:
            if key in new_names and key not in old_names:
                found.append(dict(base, part=part, name=child, check="NAME_CLASH",
                                  detail="%s %s расширения — в new у вендора появилось поле с тем же именем" % (
                                      part, child)))
            return
        if key not in theirs:
            # a vendor rename keeps the uuid: ExtendedConfigurationObject of the child names it in new
            uid = (info.get("ext_uuid") or "").lower()
            renamed = next((nm for nm, i in theirs.items() if uid and (i.get("uuid") or "").lower() == uid), None)
            if renamed is not None:
                to = theirs[renamed].get("name") or renamed
                found.append(dict(base, part=part, name=child, check="RENAMED", new_name=to,
                                  detail="%s %s: вендор переименовал в %s (uuid тот же)%s — переименовать в "
                                         "заимствовании и в коде и формах расширения" % (
                                             part, child, to, ", пометил к удалению"
                                             if to.casefold().startswith("удалить") else ""),
                                  forms=_forms_binding(ext, kind, name, child) if forms_of else []))
                return
            if old is not None and key not in before:
                found.append(dict(base, part=part, name=child, check="NOT_IN_VENDOR",
                                  detail="%s %s нет ни в old, ни в new: конфигурация базы доработана или это поле "
                                         "другого расширения — проверить по базе" % (part, child)))
                return
            removed(part, child, new_names, forms_of)
            return
        # the extension stores a real type only for simple ones; a composite type is a placeholder (AnyRef,
        # AnyIBRef), an extended one carries ExtendValue: only a stored copy of the old type can be compared
        mk = info.get("type_key")
        if mk is None:
            return
        nk = theirs[key].get("type_key")
        if _same_type(mk, nk) or not _same_type(before.get(key, {}).get("type_key"), mk) or _widened(mk, nk):
            return
        found.append(dict(base, part=part, name=child, check="TYPE_CHANGED",
                          detail="тип %s %s: %s" % (part, child, _type_diff(mk, nk)),
                          forms=_forms_binding(ext, kind, name, child)))

    for bucket, part in BUCKETS:
        theirs, before = _folded(getattr(n, bucket)), _folded(getattr(old, bucket)) if old else {}
        for child, info in getattr(o, bucket).items():
            check(part, child, info, theirs, before, new_fields, old_fields, bucket in ("attrs", "dims", "res"))
    new_ts = {k.casefold(): v for k, v in n.ts.items()}
    old_ts = {k.casefold(): v for k, v in old.ts.items()} if old else {}
    ts_info = lambda info: {k.casefold(): {"name": k, "uuid": info.ts_uuids.get(k, (None, None))[0]}  # noqa: E731
                            for k in info.ts}
    new_ts_info, old_ts_info = ts_info(n), ts_info(old) if old else {}
    for ts, cols in o.ts.items():
        check("табличная часть", ts, {"adopted": ts in o.ts_adopted, "ext_uuid": o.ts_uuids.get(ts, (None, None))[1]},
              new_ts_info, old_ts_info, new_fields, old_fields, False)
        if ts not in o.ts_adopted or ts.casefold() not in new_ts:
            continue
        theirs, before = _folded(new_ts[ts.casefold()]), _folded(old_ts.get(ts.casefold(), {}))
        for child, info in cols.items():
            check("реквизит табличной части %s" % ts, child, info, theirs, before, set(theirs), set(before), True)
    for bucket, part in NAMED:
        have = {x.casefold() for x in getattr(n, bucket)}
        for child in sorted(o.adopted_names(bucket)):
            original = next((x for x in getattr(o, bucket) if x.casefold() == child), child)
            if child not in have:
                removed(part, original, have)  # «Удалить<Имя>» is looked for among the vendor's forms / templates / …
            elif bucket == "forms":
                done = []
                found.extend(dict(base, **f) for f in check_form_lists(ext, kind, name, original, cfg_old, cfg_new,
                                                                      done))
                if ported is not None:
                    ported.extend("%s.%s" % (title, x) for x in done)
    found.extend(_object_properties(ext, kind, name, o, base, cfg_old, old_name, cfg_new, new_name))
    return found


def check_compat(ext, cfg_new):
    """COMPAT_MODE of one extension, or None: its <CompatibilityMode> is controlled against the configuration it
    extends. Compared only when both have the property; the values are enum names (Version8_3_27), compared as is."""
    if not ext.compat or not cfg_new.compat or ext.compat == cfg_new.compat:
        return None
    return {"ext": ext.name, "object": "Конфигурация." + ext.name, "file": "Configuration.xml", "part": "свойство",
            "name": "CompatibilityMode", "check": "COMPAT_MODE", "ext_value": ext.compat, "new_value": cfg_new.compat,
            "detail": "режим совместимости расширения %s, у new — %s%s: платформа откажет в загрузке («Значение "
                      "контролируемого свойства РежимСовместимости … не совпадает со значением в расширяемой "
                      "конфигурации») — заменить в Configuration.xml расширения <CompatibilityMode>%s</CompatibilityMode>"
                      " на %s; ConfigurationExtensionCompatibilityMode не трогать" % (
                          ext.compat, cfg_new.compat, " (свойство на контроле: %s)" % ext.compat_state
                          if ext.compat_state else "", ext.compat, cfg_new.compat)}
def _object_properties(ext, kind, name, o, base, cfg_old, old_name, cfg_new, new_name):
    """PROPERTY_CHANGED of an adopted object, of its adopted forms and templates (Forms/<X>.xml, Templates/<X>.xml)
    and of its adopted commands."""
    paths = (o.path, cfg_old.object_path(kind, old_name) if cfg_old is not None else None,
             cfg_new.object_path(kind, new_name))
    out = check_properties(ext, base, metadata.KIND_TAGS[kind], *paths)
    for bucket, folder, part, tag in (("forms", "Forms", "формы", "Form"),
                                      ("templates", "Templates", "макета", "Template")):
        for child in sorted(o.adopted_names(bucket)):
            original = next((x for x in getattr(o, bucket) if x.casefold() == child), child)
            sub_paths = [os.path.join(os.path.splitext(p)[0], folder, original + ".xml") if p else None for p in paths]
            out.extend(check_properties(ext, base, tag, *sub_paths, sub=(part, original)))
    for command in sorted(o.commands_adopted):
        out.extend(check_properties(ext, base, "Command", *paths, child=("Command", command),
                                    sub=("команды", command)))
    return out


def check_extra(ext, folder, name, cfg_old, cfg_new):
    """PROPERTY_CHANGED of an adopted object of a kind outside metadata.KINDS (EXTRA_KINDS: style items, command
    groups, common attributes, languages, XDTO packages, …), looked up in old and new by the same file name."""
    rel = "%s/%s.xml" % (folder, name)
    paths = [os.path.join(d.root, folder, name + ".xml") if d is not None else None for d in (ext, cfg_old, cfg_new)]
    head = fileio.read_text(paths[0])[:6000] if os.path.isfile(paths[0]) else ""
    if "<ObjectBelonging>Adopted</ObjectBelonging>" not in head:
        return []
    base = {"ext": ext.name, "object": "%s.%s" % (EXTRA_KINDS[folder], name), "file": rel}
    return check_properties(ext, base, _kind_tag(paths[0]), *paths)


def _folder_names(dump, folder, cache):
    """{casefolded name: name} of the object XML files of one top-level folder of a dump, read once per dump."""
    key = (dump.root, folder)
    if key not in cache:
        root = os.path.join(dump.root, folder)
        cache[key] = {f[:-4].casefold(): f[:-4] for f in os.listdir(root) if f.lower().endswith(".xml")} \
            if os.path.isdir(root) else {}
    return cache[key]


def check_own_names(ext, cfg_old, cfg_new, cache=None):
    """OBJECT_NAME_CLASH of the objects the extension owns: a vendor object of the same kind (top-level folder) and
    name in new. Ext (the configuration's own modules and command interface) is not a kind."""
    cache = {} if cache is None else cache
    found = []
    for folder in sorted(os.listdir(ext.root)):
        root = os.path.join(ext.root, folder)
        if folder == "Ext" or not os.path.isdir(root):
            continue
        theirs = _folder_names(cfg_new, folder, cache)
        if not theirs:
            continue
        before = _folder_names(cfg_old, folder, cache) if cfg_old is not None else {}
        kind = metadata.FOLDER2KIND.get(folder.lower()) or EXTRA_KINDS.get(folder) or folder
        for f in sorted(os.listdir(root)):
            if not f.lower().endswith(".xml"):
                continue
            name = f[:-4]
            vendor = theirs.get(name.casefold())
            if vendor is None or metadata.adopted_file(os.path.join(root, f)):
                continue
            title = "%s.%s" % (kind, name)
            in_old = name.casefold() in before
            if in_old:
                why = ("объект того же вида с тем же именем у вендора есть и в new, и в old (%s/%s.xml) — коллизия не "
                       "от обновления: расширение не применялось и до него или конфигурация базы не совпадает с old "
                       "(проверить в базе, применяется ли расширение)" % (folder, vendor))
            else:
                why = "в new у вендора появился объект того же вида с тем же именем (%s/%s.xml)" % (folder, vendor)
            found.append({"ext": ext.name, "object": title, "file": "%s/%s.xml" % (folder, name), "part": "объект",
                          "name": name, "check": "OBJECT_NAME_CLASH", "vendor_name": vendor, "in_old": in_old,
                          "detail": "собственный объект расширения %s: %s; платформа не применит расширение: «Уже "
                                    "существует объект с именем %s.%s» — переименовать объект расширения вместе со "
                                    "ссылками на него (код, формы, Rights.xml ролей, ConfigDumpInfo.xml) или убрать его "
                                    "из расширения, если вендор закрыл задачу своим объектом" % (title, why, kind,
                                                                                                vendor)})
    return found


def analyze(ext_roots, cfg_old, cfg_new, only=None, skip=None, ported=None):
    """(extensions, findings, stats); ported - a list that gets {ext, list} of the lists already ported."""
    exts = metadata.find_extensions(ext_roots, only, skip)
    findings, stats = [], Counter()
    ported = [] if ported is None else ported
    names = {}
    for ext in exts:
        compat = check_compat(ext, cfg_new)
        if compat is not None:
            findings.append(compat)
        findings.extend(check_own_names(ext, cfg_old, cfg_new, names))
        for kind, (folder, _, _) in sorted(metadata.KINDS.items()):
            root = os.path.join(ext.root, folder)
            if not os.path.isdir(root):
                continue
            for f in sorted(os.listdir(root)):
                if not f.endswith(".xml"):
                    continue
                stats["objects"] += 1
                done = []
                res = check_object(ext, kind, f[:-4], cfg_old, cfg_new, done)
                ported.extend({"ext": ext.name, "list": x} for x in done)
                if ext.object(kind, f[:-4]) is not None and ext.object(kind, f[:-4]).adopted:
                    stats["adopted"] += 1
                findings.extend(res)
        for folder in sorted(EXTRA_KINDS):
            root = os.path.join(ext.root, folder)
            for f in sorted(os.listdir(root)) if os.path.isdir(root) else []:
                if f.endswith(".xml"):
                    findings.extend(check_extra(ext, folder, f[:-4], cfg_old, cfg_new))
    stats["unreadable"] = sum(1 for f in findings if f["check"] == "XML_UNREADABLE")
    stats["lists_ported"] = len(ported)
    return exts, findings, stats


def render(args, exts, findings, stats, elapsed, ported=()):
    md = ["# Заимствованные объекты расширений против нового релиза", ""]
    md.append("new: `%s`, old: `%s`. Расширений: %d, объектов в них: %d, из них заимствованных: %d. Время: %.1f с." % (
        args.config_new, args.config_old, len(exts), stats["objects"], stats["adopted"], elapsed))
    md.append("")
    if stats["unreadable"]:
        md.append("**Не разбираются XML объектов: %d** — эти объекты не проверены (XML_UNREADABLE), отчёт по ним "
                  "неполон." % stats["unreadable"])
        md.append("")
    md.append("Каждая находка — отказ проверки применимости расширения платформой: правится в XML расширения "
              "(снять заимствование, перезаимствовать объект, поправить тип) и в коде и формах, которые этим "
              "пользуются.")
    md.append("")
    counts = Counter(f["check"] for f in findings)
    md.append(fileio.md_table(["Проверка", "Находок", "Смысл"], [(k, counts.get(k, 0), v) for k, v in EXPLAIN.items()]))
    md.append("")
    if ported:
        md.append("Списки, которые меняли и вендор, и расширение, но расширение уже перенесло правку вендора (в его "
                  "запросе всё, что вендор добавил, и нет убранного, или BaseForm — список new) — не находки: " +
                  ", ".join("%s (%s)" % (x["list"], x["ext"]) for x in ported) + ".")
        md.append("")
    md.append("## Находки")
    md.append("")
    md.append(fileio.md_table(["Проверка", "Расширение", "Объект", "Что", "Формы расширения с этим полем"],
                              [(f["check"], f["ext"], f["object"], f["detail"], ", ".join(f.get("forms") or []) or "—")
                               for f in findings]) if findings else "Нет.")
    return "\n".join(md)


def main(argv=None):
    fileio.console_utf8()
    ap = argparse.ArgumentParser(description="Заимствованные объекты расширений против нового релиза: удалённые "
                                             "реквизиты, ресурсы, значения, формы и сменившиеся типы")
    ap.add_argument("--ext-root", action="extend", nargs="+", required=True,
                    help="папка выгрузок расширений (или одна выгрузка); можно несколько")
    ap.add_argument("--ext", action="extend", nargs="+", help="только эти расширения (имя)")
    ap.add_argument("--skip", action="extend", nargs="+", help="пропустить эти расширения (имя)")
    ap.add_argument("--config-old", required=True, help="выгрузка типовой релиза, под который написаны расширения: "
                                                        "без неё не сверить типы и не отличить удалённое поле от чужого")
    ap.add_argument("--config-new", required=True, help="выгрузка типовой нового релиза")
    ap.add_argument("--out", required=True, help="папка отчёта borrowed.json / borrowed.md, вне выгрузок")
    args = ap.parse_args(argv)
    for label, root in [("--ext-root", r) for r in args.ext_root] + [("--config-old", args.config_old),
                                                                     ("--config-new", args.config_new)]:
        if inside(args.out, root):
            sys.exit("--out %s внутри %s %s: borrowed в выгрузки не пишет" % (args.out, label, root))
    t0 = time.time()
    metadata.check_config(args.config_new, "--config-new")
    metadata.check_config(args.config_old, "--config-old")
    cfg_new = metadata.Dump(args.config_new, "new")
    cfg_old = metadata.Dump(args.config_old, "old")
    ported = []
    exts, findings, stats = analyze(args.ext_root, cfg_old, cfg_new, args.ext, args.skip, ported)
    if not exts:
        sys.exit("no extension dumps found under --ext-root")
    elapsed = time.time() - t0
    data = {"config_new": args.config_new, "config_old": args.config_old, "extensions": [e.name for e in exts],
            "stats": dict(stats), "findings": findings, "lists_ported": ported}
    md_path, json_path = fileio.write_report(args.out, "borrowed", render(args, exts, findings, stats, elapsed,
                                                                          ported), data)
    counts = Counter(f["check"] for f in findings)
    print("extensions: %d, adopted objects: %d, findings: %d%s, %.1fs" % (
        len(exts), stats["adopted"], len(findings),
        ", unreadable XML: %d" % stats["unreadable"] if stats["unreadable"] else "", elapsed))
    if counts:
        print("checks: " + ", ".join("%s=%d" % kv for kv in sorted(counts.items())))
    print(md_path)
    print(json_path)


if __name__ == "__main__":
    main()
