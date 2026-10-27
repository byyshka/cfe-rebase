# -*- coding: utf-8 -*-
"""Model of a 1C XML dump (configuration or extension), loaded lazily.

A configuration dump has ~10^4 modules, so nothing is parsed up front: modules, object XML and
common module flags are read on first request and cached. Paths inside a dump are "rel" paths
(forward slashes), e.g. 'Documents/ПриходныйОрдерНаТовары/Ext/ManagerModule.bsl'; an adopted module
of an extension has the same rel path as the configuration module it extends.
"""
import os
import re
import sys
import xml.etree.ElementTree as ET

from . import bsl
from . import fileio

MD = "{http://v8.1c.ru/8.3/MDClasses}"
V8 = "{http://v8.1c.ru/8.1/data/core}"
PD = "{http://v8.1c.ru/8.3/xcf/predef}"

# RU kind -> (folder, manager collection RU, manager collection EN)
KINDS = {
    "Справочник": ("Catalogs", "Справочники", "Catalogs"),
    "Документ": ("Documents", "Документы", "Documents"),
    "РегистрНакопления": ("AccumulationRegisters", "РегистрыНакопления", "AccumulationRegisters"),
    "РегистрСведений": ("InformationRegisters", "РегистрыСведений", "InformationRegisters"),
    "РегистрБухгалтерии": ("AccountingRegisters", "РегистрыБухгалтерии", "AccountingRegisters"),
    "РегистрРасчета": ("CalculationRegisters", "РегистрыРасчета", "CalculationRegisters"),
    "Перечисление": ("Enums", "Перечисления", "Enums"),
    "ПланВидовХарактеристик": ("ChartsOfCharacteristicTypes", "ПланыВидовХарактеристик", "ChartsOfCharacteristicTypes"),
    "ПланСчетов": ("ChartsOfAccounts", "ПланыСчетов", "ChartsOfAccounts"),
    "ПланВидовРасчета": ("ChartsOfCalculationTypes", "ПланыВидовРасчета", "ChartsOfCalculationTypes"),
    "ПланОбмена": ("ExchangePlans", "ПланыОбмена", "ExchangePlans"),
    "БизнесПроцесс": ("BusinessProcesses", "БизнесПроцессы", "BusinessProcesses"),
    "Задача": ("Tasks", "Задачи", "Tasks"),
    "Обработка": ("DataProcessors", "Обработки", "DataProcessors"),
    "Отчет": ("Reports", "Отчеты", "Reports"),
    "Константа": ("Constants", "Константы", "Constants"),
    "ЖурналДокументов": ("DocumentJournals", "ЖурналыДокументов", "DocumentJournals"),
    "Последовательность": ("Sequences", "Последовательности", "Sequences"),
    "КритерийОтбора": ("FilterCriteria", "КритерииОтбора", "FilterCriteria"),
    "ХранилищеНастроек": ("SettingsStorages", "ХранилищаНастроек", "SettingsStorages"),
    "ОбщийМодуль": ("CommonModules", None, None),
    "ОбщаяФорма": ("CommonForms", None, None),
    "ОбщаяКоманда": ("CommonCommands", None, None),
    "ОбщийМакет": ("CommonTemplates", None, None),
    "HTTPСервис": ("HTTPServices", None, None),
    "WebСервис": ("WebServices", None, None),
    "РегламентноеЗадание": ("ScheduledJobs", None, None),
    "ПодпискаНаСобытие": ("EventSubscriptions", None, None),
    "ОпределяемыйТип": ("DefinedTypes", None, None),
    "Роль": ("Roles", None, None),
    "ФункциональнаяОпция": ("FunctionalOptions", None, None),
    "Подсистема": ("Subsystems", None, None),
    "ПараметрСеанса": ("SessionParameters", None, None),
}
FOLDER2KIND = {v[0].lower(): k for k, v in KINDS.items()}
# RU kind -> English element name of Configuration.xml ChildObjects and of «Kind.Name» in ConfigDumpInfo.xml / types
KIND_TAGS = {
    "Справочник": "Catalog", "Документ": "Document", "РегистрНакопления": "AccumulationRegister",
    "РегистрСведений": "InformationRegister", "РегистрБухгалтерии": "AccountingRegister",
    "РегистрРасчета": "CalculationRegister", "Перечисление": "Enum",
    "ПланВидовХарактеристик": "ChartOfCharacteristicTypes", "ПланСчетов": "ChartOfAccounts",
    "ПланВидовРасчета": "ChartOfCalculationTypes", "ПланОбмена": "ExchangePlan", "БизнесПроцесс": "BusinessProcess",
    "Задача": "Task", "Обработка": "DataProcessor", "Отчет": "Report", "Константа": "Constant",
    "ЖурналДокументов": "DocumentJournal", "Последовательность": "Sequence", "КритерийОтбора": "FilterCriterion",
    "ХранилищеНастроек": "SettingsStorage", "ОбщийМодуль": "CommonModule", "ОбщаяФорма": "CommonForm",
    "ОбщаяКоманда": "CommonCommand", "ОбщийМакет": "CommonTemplate", "HTTPСервис": "HTTPService",
    "WebСервис": "WebService", "РегламентноеЗадание": "ScheduledJob", "ПодпискаНаСобытие": "EventSubscription",
    "ОпределяемыйТип": "DefinedType", "Роль": "Role", "ФункциональнаяОпция": "FunctionalOption",
    "Подсистема": "Subsystem", "ПараметрСеанса": "SessionParameter",
}
TAG2KIND = {v.casefold(): k for k, v in KIND_TAGS.items()}
COLL2KIND = {}
for _k, _v in KINDS.items():
    if _v[1]:
        COLL2KIND[_v[1].casefold()] = _k
        COLL2KIND[_v[2].casefold()] = _k

MODULE_TITLES = {
    "managermodule.bsl": "МодульМенеджера", "objectmodule.bsl": "МодульОбъекта",
    "recordsetmodule.bsl": "МодульНабораЗаписей", "valuemanagermodule.bsl": "МодульМенеджераЗначения",
    "commandmodule.bsl": "МодульКоманды", "module.bsl": "Модуль",
    "managedapplicationmodule.bsl": "МодульУправляемогоПриложения",
    "ordinaryapplicationmodule.bsl": "МодульОбычногоПриложения", "sessionmodule.bsl": "МодульСеанса",
    "externalconnectionmodule.bsl": "МодульВнешнегоСоединения",
}
# a document's Properties/RegisterRecords item «AccumulationRegister.X» -> RU register kind
RECORDS_KINDS = {"AccumulationRegister": "РегистрНакопления", "InformationRegister": "РегистрСведений",
                 "AccountingRegister": "РегистрБухгалтерии", "CalculationRegister": "РегистрРасчета"}
_RECORD_ITEM_RE = re.compile(r"<(?:\w+:)?Item\b[^>]*>\s*(%s)\.([^<\s]+)\s*</(?:\w+:)?Item>" % "|".join(RECORDS_KINDS))
PURPOSE_RANK = {"patch": 0, "customization": 1, "addon": 2}
PURPOSE_RU = {"patch": "Исправление", "customization": "Адаптация", "addon": "Дополнение"}


def norm_rel(rel):
    return rel.replace("\\", "/").strip("/")


def rel_key(rel):
    return norm_rel(rel).casefold()


def module_kind(rel):
    """form | object | manager | recordset | valuemanager | command | common | app | session | extconn | other"""
    r = rel_key(rel)
    name = r.rsplit("/", 1)[-1]
    if r.endswith("/ext/form/module.bsl"):
        return "form"
    if name == "objectmodule.bsl":
        return "object"
    if name == "managermodule.bsl":
        return "manager"
    if name == "recordsetmodule.bsl":
        return "recordset"
    if name == "valuemanagermodule.bsl":
        return "valuemanager"
    if name == "commandmodule.bsl":
        return "command"
    if r.startswith("commonmodules/"):
        return "common"
    if name in ("managedapplicationmodule.bsl", "ordinaryapplicationmodule.bsl"):
        return "app"
    if name == "sessionmodule.bsl":
        return "session"
    if name == "externalconnectionmodule.bsl":
        return "extconn"
    return "other"


def module_owner(rel):
    """(kind RU, object name) owning the module, or (None, None) for configuration-level modules."""
    parts = norm_rel(rel).split("/")
    kind = FOLDER2KIND.get(parts[0].lower())
    if kind is None or len(parts) < 2:
        return None, None
    return kind, parts[1]


def module_title(rel):
    """Human name: 'Документ.ПриходныйОрдерНаТовары.МодульМенеджера', 'ОбщийМодуль.X',
    'Обработка.Y.Форма.Форма', 'Конфигурация.МодульУправляемогоПриложения'."""
    parts = norm_rel(rel).split("/")
    fname = parts[-1].lower()
    if parts[0].lower() == "ext" and len(parts) == 2:
        return "Конфигурация." + MODULE_TITLES.get(fname, parts[-1])
    kind = FOLDER2KIND.get(parts[0].lower())
    if kind is None:
        return norm_rel(rel)
    if kind == "ОбщийМодуль":
        return "ОбщийМодуль." + parts[1]
    if kind == "ОбщаяФорма":
        return "ОбщаяФорма." + parts[1]
    if kind == "ОбщаяКоманда":
        return "ОбщаяКоманда." + parts[1]
    if len(parts) >= 4 and parts[2].lower() == "forms":
        return "%s.%s.Форма.%s" % (kind, parts[1], parts[3])
    if len(parts) >= 4 and parts[2].lower() == "commands":
        return "%s.%s.Команда.%s" % (kind, parts[1], parts[3])
    return "%s.%s.%s" % (kind, parts[1], MODULE_TITLES.get(fname, parts[-1]))


def form_xml_rel(module_rel):
    """.../Ext/Form/Module.bsl -> .../Ext/Form.xml"""
    r = norm_rel(module_rel)
    if r.lower().endswith("/ext/form/module.bsl"):
        return r[:-len("/Form/Module.bsl")] + "/Form.xml"
    return None


def form_module_rel(form_rel):
    """.../Ext/Form.xml -> .../Ext/Form/Module.bsl"""
    r = norm_rel(form_rel)
    return r[:-len("Form.xml")] + "Form/Module.bsl"


def form_title(form_rel):
    return module_title(form_module_rel(form_rel))


def manager_module_rel(kind, name):
    folder = KINDS[kind][0]
    fname = "ValueManagerModule.bsl" if kind == "Константа" else "ManagerModule.bsl"
    return "%s/%s/Ext/%s" % (folder, name, fname)


def object_module_rel(kind, name):
    folder = KINDS[kind][0]
    fname = "RecordSetModule.bsl" if kind.startswith("Регистр") else "ObjectModule.bsl"
    return "%s/%s/Ext/%s" % (folder, name, fname)


def common_module_rel(name):
    return "CommonModules/%s/Ext/Module.bsl" % name


def _child_names(el, tag):
    out = {}
    for ch in el.findall(MD + tag):
        props = ch.find(MD + "Properties")
        if props is None:
            continue
        nm = props.findtext(MD + "Name")
        if nm:
            out[nm] = {"uuid": ch.get("uuid"), "ext_uuid": props.findtext(MD + "ExtendedConfigurationObject"),
                       "adopted": props.findtext(MD + "ObjectBelonging") == "Adopted",
                       "types": _types(props), "type_key": type_key(props),
                       # a dimension of an accumulation register: whether totals are kept by it
                       "use_in_totals": (props.findtext(MD + "UseInTotals") or "").strip() or None}
    return out


def type_key(props):
    """The whole Properties/Type - types and qualifiers (string length, digits) - as one comparable string, or None."""
    own = props.find(MD + "Type")
    if own is None:
        return None
    parts = []
    for el in own.iter():
        if el is own:
            continue
        text = (el.text or "").strip()
        parts.append("%s=%s" % (el.tag.rsplit("}", 1)[-1], text) if text else el.tag.rsplit("}", 1)[-1])
    return ";".join(parts)


def _types(props):
    """Type names from Properties/Type only, defined-type sets (v8:TypeSet) included."""
    own = props.find(MD + "Type")
    if own is None:
        return []
    return [t.text.strip() for t in own if t.tag in (V8 + "Type", V8 + "TypeSet") and t.text]


class ObjectInfo(object):
    """Metadata object read from '<Folder>/<Name>.xml'."""

    def __init__(self, kind, name, path):
        self.kind = kind
        self.name = name
        self.path = path
        self.adopted = False
        self.props = {}
        self.types = []       # own type: constant, defined type
        self.type_key = None  # own type with qualifiers (metadata.type_key)
        self.attrs = {}
        self.dims = {}
        self.res = {}
        self.cols = {}        # document journal columns
        self.addr = {}        # task addressing attributes
        self.flags = {}       # chart of accounts accounting flags
        self.enum_values = {}
        self.ts = {}          # name -> {columns}
        self.ts_adopted = set()
        self.ts_uuids = {}    # tabular section name -> (own uuid, ExtendedConfigurationObject)
        self.uuid = None      # own uuid of the object
        self.forms = []
        self.templates = []
        self.commands = []
        self.commands_adopted = set()
        self.recalcs = []     # calculation register recalculations
        self._predefined = None
        self._parse()

    def adopted_names(self, bucket):
        """Casefolded names of one child collection that this (extension) object only borrows."""
        items = getattr(self, bucket)
        if isinstance(items, dict) and bucket != "ts":
            return {n.casefold() for n, i in items.items() if i["adopted"]}
        if bucket == "ts":
            return {n.casefold() for n in self.ts_adopted}
        if bucket == "commands":
            return {n.casefold() for n in self.commands_adopted}
        if bucket in ("forms", "templates"):
            folder = os.path.join(os.path.splitext(self.path)[0], "Forms" if bucket == "forms" else "Templates")
            out = set()
            for n in items:
                p = os.path.join(folder, n + ".xml")
                if os.path.isfile(p) and "<ObjectBelonging>Adopted</ObjectBelonging>" in fileio.read_text(p)[:4000]:
                    out.add(n.casefold())
            return out
        return set()

    @property
    def predefined(self):
        """Names of predefined items from '<Folder>/<Name>/Ext/Predefined.xml' (nested items included)."""
        if self._predefined is None:
            names = set()
            p = os.path.join(os.path.splitext(self.path)[0], "Ext", "Predefined.xml")
            if os.path.isfile(p):
                try:
                    for item in ET.parse(p).getroot().iter(PD + "Item"):
                        nm = item.findtext(PD + "Name")
                        if nm:
                            names.add(nm.strip())
                except ET.ParseError:
                    pass
            self._predefined = names
        return self._predefined

    def _parse(self):
        root = ET.parse(self.path).getroot()
        obj = next(iter(root), None)
        if obj is None:
            return
        self.uuid = obj.get("uuid")
        props = obj.find(MD + "Properties")
        if props is not None:
            self.adopted = props.findtext(MD + "ObjectBelonging") == "Adopted"
            for ch in props:
                tag = ch.tag.replace(MD, "")
                if len(ch) == 0 and ch.text is not None and ch.text.strip():
                    self.props[tag] = ch.text.strip()
            own = props.find(MD + "Type")
            if own is not None:
                self.types = [t.text.strip() for t in own if t.tag in (V8 + "Type", V8 + "TypeSet") and t.text]
                self.type_key = type_key(props)
        co = obj.find(MD + "ChildObjects")
        if co is None:
            return
        self.attrs = _child_names(co, "Attribute")
        self.dims = _child_names(co, "Dimension")
        self.res = _child_names(co, "Resource")
        self.cols = _child_names(co, "Column")
        self.addr = _child_names(co, "AddressingAttribute")
        self.flags = _child_names(co, "AccountingFlag")
        self.flags.update(_child_names(co, "ExtDimensionAccountingFlag"))
        self.enum_values = _child_names(co, "EnumValue")
        for ts in co.findall(MD + "TabularSection"):
            props = ts.find(MD + "Properties")
            nm = props.findtext(MD + "Name") if props is not None else None
            if not nm:
                continue
            cols = {}
            tco = ts.find(MD + "ChildObjects")
            if tco is not None:
                cols = _child_names(tco, "Attribute")
            self.ts[nm] = cols
            self.ts_uuids[nm] = (ts.get("uuid"), props.findtext(MD + "ExtendedConfigurationObject"))
            if props.findtext(MD + "ObjectBelonging") == "Adopted":
                self.ts_adopted.add(nm)
        self.forms = [x.text.strip() for x in co.findall(MD + "Form") if x.text]
        self.templates = [x.text.strip() for x in co.findall(MD + "Template") if x.text]
        commands = _child_names(co, "Command")
        self.commands = list(commands.keys())
        self.commands_adopted = {n for n, i in commands.items() if i["adopted"]}
        self.recalcs = [x.text.strip() for x in co.findall(MD + "Recalculation") if x.text and x.text.strip()]
        self.recalcs += list(_child_names(co, "Recalculation").keys())

    def fields(self):
        """First-level field names (attributes, dimensions, resources, tabular sections)."""
        return set(self.attrs) | set(self.dims) | set(self.res) | set(self.ts)


_CFG_HEAD_RE = {
    "name": re.compile(r"<Properties>.*?<Name>([^<]+)</Name>", re.S),
    "purpose": re.compile(r"<ConfigurationExtensionPurpose>(\w+)</ConfigurationExtensionPurpose>"),
    "prefix": re.compile(r"<NamePrefix>([^<]*)</NamePrefix>"),
    "version": re.compile(r"<Properties>.*?<Version>([^<]*)</Version>", re.S),
}


def check_config(path, flag):
    """Exit when `path` is not a configuration dump: with a wrong path every vendor object is «missing» (calls,
    borrowed) or nothing is found at all (registers), and the report looks like a real one. A dump needs at least one
    object folder: Configuration.xml alone (a stub, a half-copied dump) would make every adopted object «removed»."""
    problem = config_problem(path)
    if problem:
        sys.exit("%s %s: %s" % (flag, path, problem))


def config_problem(path):
    """Why `path` is not a configuration dump, or None: it needs an object folder with at least one object XML (an
    empty Catalogs is a half-copied dump too)."""
    if not os.path.isdir(path):
        return "папки нет"
    for folder, _, _ in KINDS.values():
        p = os.path.join(path, folder)
        if os.path.isdir(p) and any(n.lower().endswith(".xml") for n in os.listdir(p)):
            return None
    stub = os.path.isfile(os.path.join(path, "Configuration.xml"))
    return "это не выгрузка конфигурации — нет папок объектов с XML объектов (Catalogs, Documents, CommonModules, …)%s" % (
        ", есть только Configuration.xml" if stub else "")


class Dump(object):
    def __init__(self, root, label=None):
        self.root = os.path.abspath(root)
        self.label = label
        self.name = os.path.basename(self.root.rstrip("\\/"))
        self.purpose = None
        self.prefix = ""
        self.version = ""
        self.is_extension = False
        cfg = os.path.join(self.root, "Configuration.xml")
        if os.path.isfile(cfg):
            head = fileio.read_text(cfg)
            cut = head.find("<ChildObjects>")
            head = head if cut < 0 else head[:cut]
            m = _CFG_HEAD_RE["name"].search(head)
            if m:
                self.name = m.group(1).strip()
            m = _CFG_HEAD_RE["purpose"].search(head)
            if m:
                self.purpose = m.group(1)
                self.is_extension = True
            m = _CFG_HEAD_RE["prefix"].search(head)
            if m:
                self.prefix = m.group(1)
            m = _CFG_HEAD_RE["version"].search(head)
            if m:
                self.version = m.group(1)
        self._modules = {}
        self._objects = {}
        self._errors = {}
        self.unreadable = []  # object XML that exists but does not parse: {kind, name, path, error}
        self._common = None
        self._global = None
        self._cm_props = {}
        self._files = None
        self._recorders = None
        self._uuids = {}
        self._counterparts = {}

    # -- files ---------------------------------------------------------------------------------------------------
    def path(self, rel):
        return os.path.join(self.root, *norm_rel(rel).split("/"))

    def exists(self, rel):
        return os.path.isfile(self.path(rel))

    def files(self, suffix=None):
        """All rel paths of the dump (walks the tree once); meant for extensions, not configurations."""
        if self._files is None:
            out = []
            for dp, _, fn in os.walk(self.root):
                for f in fn:
                    out.append(norm_rel(os.path.relpath(os.path.join(dp, f), self.root)))
            out.sort()
            self._files = out
        if suffix is None:
            return list(self._files)
        suffix = suffix.lower()
        return [f for f in self._files if f.lower().endswith(suffix)]

    # -- modules -------------------------------------------------------------------------------------------------
    def module(self, rel):
        key = rel_key(rel)
        if key not in self._modules:
            p = self.path(rel)
            self._modules[key] = bsl.load_module(p) if os.path.isfile(p) else None
        return self._modules[key]

    def find_method(self, rel, name):
        mod = self.module(rel)
        return mod.find(name) if mod is not None else None

    # -- objects -------------------------------------------------------------------------------------------------
    def object_path(self, kind, name):
        return os.path.join(self.root, KINDS[kind][0], name + ".xml")

    def object_exists(self, kind, name):
        return kind in KINDS and os.path.isfile(self.object_path(kind, name))

    def object(self, kind, name):
        """ObjectInfo, or None when the XML is absent or does not parse; the second case is told apart by
        object_error() and listed in self.unreadable."""
        key = (kind, name.casefold())
        if key not in self._objects:
            p = self.object_path(kind, name) if kind in KINDS else None
            info = None
            if p and os.path.isfile(p):
                try:
                    info = ObjectInfo(kind, name, p)
                except ET.ParseError as exc:
                    info = None
                    self._errors[key] = "%s: %s" % (type(exc).__name__, exc)
                    self.unreadable.append({"kind": kind, "name": name, "path": p, "error": self._errors[key]})
            self._objects[key] = info
        return self._objects[key]

    def object_uuid(self, kind, name):
        """uuid attribute of the object XML, read from the head of the file; None when the file is absent."""
        return head_uuid(self.object_path(kind, name)) if kind in KINDS else None

    def uuid_names(self, kind):
        """{lowercased uuid: name} of the objects of one kind, from the head of every object XML (read once per kind):
        the vendor keeps the uuid when it renames an object."""
        if kind not in self._uuids:
            out = {}
            folder = os.path.join(self.root, KINDS[kind][0]) if kind in KINDS else None
            for f in sorted(os.listdir(folder)) if folder and os.path.isdir(folder) else []:
                if f.lower().endswith(".xml"):
                    uid = head_uuid(os.path.join(folder, f))
                    if uid:
                        out.setdefault(uid.lower(), f[:-4])
            self._uuids[kind] = out
        return self._uuids[kind]

    def object_error(self, kind, name):
        """The parser error of an object whose XML exists but does not parse; None when it is absent or read."""
        self.object(kind, name)
        return self._errors.get((kind, name.casefold()))

    def enum_values(self, name):
        o = self.object("Перечисление", name)
        return set(o.enum_values) if o else None

    def register_fields(self, kind, name):
        o = self.object(kind, name)
        if o is None:
            return None
        return {"dims": set(o.dims), "res": set(o.res), "attrs": set(o.attrs)}

    def recorders(self):
        """{(RU register kind, casefolded register name): {document names}} from Properties/RegisterRecords of every
        Documents/<Имя>.xml, scanned once per dump. Only that block is decoded: a document XML runs to several MB and
        ObjectInfo does not keep non-leaf properties."""
        if self._recorders is None:
            out = {}
            folder = os.path.join(self.root, "Documents")
            for f in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
                if not f.lower().endswith(".xml"):
                    continue
                data = fileio.read_bytes(os.path.join(folder, f))
                start = data.find(b"<RegisterRecords>")
                end = data.find(b"</RegisterRecords>", start) if start >= 0 else -1
                if end < 0:
                    continue
                for m in _RECORD_ITEM_RE.finditer(data[start:end].decode("utf-8", "replace")):
                    out.setdefault((RECORDS_KINDS[m.group(1)], m.group(2).casefold()), set()).add(f[:-4])
            self._recorders = out
        return self._recorders

    # -- common modules ------------------------------------------------------------------------------------------
    def common_modules(self):
        """{casefold name: name}"""
        if self._common is None:
            d = os.path.join(self.root, "CommonModules")
            names = {}
            if os.path.isdir(d):
                for e in os.listdir(d):
                    nm = e[:-4] if e.lower().endswith(".xml") else e
                    names.setdefault(nm.casefold(), nm)
            self._common = names
        return self._common

    def common_module_name(self, name):
        return self.common_modules().get(name.casefold())

    def common_module_props(self, name):
        real = self.common_module_name(name)
        if real is None:
            return None
        if real not in self._cm_props:
            p = os.path.join(self.root, "CommonModules", real + ".xml")
            props = {}
            if os.path.isfile(p):
                text = fileio.read_text(p)
                for key in ("Global", "Server", "ClientManagedApplication", "ClientOrdinaryApplication",
                            "ServerCall", "Privileged", "ExternalConnection", "ReturnValuesReuse",
                            "ObjectBelonging"):
                    m = re.search(r"<%s>([^<]*)</%s>" % (key, key), text)
                    if m:
                        props[key] = m.group(1)
            self._cm_props[real] = props
        return self._cm_props[real]

    def global_common_modules(self):
        if self._global is None:
            out = []
            d = os.path.join(self.root, "CommonModules")
            if os.path.isdir(d):
                for e in sorted(os.listdir(d)):
                    if not e.lower().endswith(".xml"):
                        continue
                    with open(os.path.join(d, e), "rb") as fh:
                        head = fh.read(20000)
                    if b"<Global>true</Global>" in head:
                        out.append(e[:-4])
            self._global = out
        return self._global

    def __repr__(self):
        return "Dump(%s)" % self.name


_HEAD_UUID_RE = re.compile(rb"<MetaDataObject\b[^>]*>\s*<\w+\s+uuid=\"([^\"]+)\"")


def head_uuid(path):
    """uuid of the object element right under MetaDataObject; the head of the file is enough (a document XML runs to
    several MB)."""
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        m = _HEAD_UUID_RE.search(fh.read(16384))
    return m.group(1).decode("ascii", "replace") if m else None


def counterpart_name(src, kind, name, dump):
    """Name in `dump` of the object (kind, name) of the extension dump `src`, or None when dump has none. The vendor
    keeps the uuid when it renames an object: a configuration is matched by ExtendedConfigurationObject of the
    extension's object, another dump of an extension (its work copy) by the object's own uuid, which survives the
    rename of an adoption. Without a uuid to go by, the object of the same name."""
    if dump is None or kind not in KINDS:
        return None
    key = (os.path.normcase(src.root), kind, name.casefold())
    if key not in dump._counterparts:
        o = src.object(kind, name)
        uid = None
        if o is not None:
            uid = o.uuid if dump.is_extension else o.props.get("ExtendedConfigurationObject")
        res = None
        if dump.object_exists(kind, name) and (not uid or (dump.object_uuid(kind, name) or "").lower() == uid.lower()):
            res = name
        elif uid:
            res = dump.uuid_names(kind).get(uid.lower())
        if res is None and dump.object_exists(kind, name):
            res = name
        dump._counterparts[key] = res
    return dump._counterparts[key]


def counterpart_rel(src, rel, dump):
    """(rel of the same file in `dump`, (name in src, name in dump) or None) for a file of an object of the extension
    dump src: a renamed object (counterpart_name) moves its files to the folder of the new name."""
    kind, name = module_owner(rel)
    if kind is None or dump is None:
        return rel, None
    other = counterpart_name(src, kind, name, dump)
    if other is None or other.casefold() == name.casefold():
        return rel, None
    parts = norm_rel(rel).split("/")
    parts[1] = other
    return "/".join(parts), (name, other)


def renamed_note(kind, old, new):
    """A finding's words for a vendor rename."""
    note = "вендор переименовал %s.%s в %s (uuid тот же)" % (kind, old, new)
    if new.casefold().startswith("удалить"):
        note += " — пометил к удалению"
    return note


def find_extensions(roots, only=None, skip=None):
    """Extension dumps under the given roots: a root that holds Configuration.xml is an extension itself,
    otherwise every subdirectory with Configuration.xml is. only/skip filter by extension name."""
    only = {x.casefold() for x in (only or [])}
    skip = {x.casefold() for x in (skip or [])}
    found = []
    seen = set()
    for root in roots:
        cands = []
        if os.path.isfile(os.path.join(root, "Configuration.xml")):
            cands.append(root)
        elif os.path.isdir(root):
            for e in sorted(os.listdir(root)):
                p = os.path.join(root, e)
                if os.path.isfile(os.path.join(p, "Configuration.xml")):
                    cands.append(p)
        for p in cands:
            d = Dump(p)
            key = d.name.casefold()
            if key in seen:
                continue
            if only and key not in only and os.path.basename(p).casefold() not in only:
                continue
            if key in skip or os.path.basename(p).casefold() in skip:
                continue
            seen.add(key)
            found.append(d)
    return found


def load_order(path):
    """Extension names in session order from a text file: one per line, '#' comments allowed; a line may carry
    extra columns (number, '|', tab) - the first token that looks like a name is taken."""
    names = []
    for raw in fileio.read_lines(path):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        for tok in re.split(r"[\s|;,]+", s):
            tok = tok.strip().strip('"')
            if tok and not re.fullmatch(r"\d+\.?", tok):
                names.append(tok)
                break
    return names
