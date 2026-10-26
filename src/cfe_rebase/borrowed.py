# -*- coding: utf-8 -*-
"""Adopted metadata of the extensions against the new release: what the platform's applicability check will refuse.

    cfe-rebase borrowed --ext-root <extension dumps> --config-old <old> --config-new <new> [--ext NAME] --out <dir>

For every object an extension adopts (ObjectBelonging = Adopted in its XML): the object itself, and each adopted
attribute, dimension, resource, journal column, addressing attribute, accounting flag, enum value, tabular section with
its attributes, form, template and command must still exist in the new release, and an adopted attribute or tabular
section attribute whose type (with string length and number digits) the extension stores must keep it in new. Checks:
  OBJECT_REMOVED  the adopted object is gone from new (in old it is there; one in neither release is NOT_IN_VENDOR)
  REMOVED         an adopted child is gone from new (vendor renaming to «Удалить<Имя>» is named in the detail)
  TYPE_CHANGED    the vendor changed the type old -> new; the extension still holds the old one (compared only
                  where the extension stores a copy of the old type: simple types; a composite one is a placeholder;
                  a composite type the vendor only widened is accepted by the platform and not reported; the order
                  of the types inside a composite type does not count)
  NAME_CLASH      an attribute or tabular section the extension adds (or an attribute it adds to an adopted tabular
                  section) has the name of one the vendor added in new
  NOT_IN_VENDOR   an adopted object or child is in neither old nor new: the base configuration is modified or the
                  field belongs to another extension - a question, not a breakage
  LIST_FIELD_LOST an adopted form whose dynamic list query the vendor changed (or made manual) no longer selects a
                  field the extension shows on that form (a DataPath the extension added)
  LIST_CHANGED    the vendor and the extension both changed the settings of one dynamic list of a form (fields the
                  extension's own query selects and shows on the form that the new vendor query does not select are
                  named in the detail: the merge has to carry them over)
  XML_UNREADABLE  the XML of the object exists but does not parse - in new, in old or in the extension: the object is
                  not checked (this is not OBJECT_REMOVED), the detail names the file and the parser error
An adopted form without <ExtendedConfigurationObject> is not a finding: checked on 17.10.2026 on a real base
(platform 8.3.27), such a form loaded from XML applies like one with the link (0.2.5 reported it, 0.2.6 does not).
Names are compared case-insensitively, as 1C does: a vendor rename that changes only the case is not REMOVED.
For REMOVED / TYPE_* of an object or tabular section attribute, forms of that object in the extension that bind it
(DataPath ….<Имя>) are listed. The dumps are never written: --out inside --ext-root, --config-old or --config-new
is refused.
"""
import argparse
import os
import re
import sys
import time
from collections import Counter

from . import fileio, metadata
from .control_rebase import inside

BUCKETS = (("attrs", "реквизит"), ("dims", "измерение"), ("res", "ресурс"), ("cols", "графа"),
           ("addr", "реквизит адресации"), ("flags", "признак учёта"), ("enum_values", "значение перечисления"))
NAMED = (("forms", "форма"), ("templates", "макет"), ("commands", "команда"))
EXPLAIN = {
    "OBJECT_REMOVED": "заимствованного объекта нет в new",
    "REMOVED": "заимствованного реквизита / ресурса / значения / формы нет в new",
    "TYPE_CHANGED": "вендор сменил тип old → new, в расширении старый",
    "NAME_CLASH": "собственный реквизит или табличная часть расширения совпали по имени с новым полем вендора",
    "NOT_IN_VENDOR": "заимствованного объекта или поля нет ни в old, ни в new: доработанная конфигурация или "
                     "чужое поле",
    "LIST_FIELD_LOST": "новый запрос динамического списка заимствованной формы не выбирает поле расширения",
    "LIST_CHANGED": "список заимствованной формы изменили и вендор, и расширение",
    "XML_UNREADABLE": "XML объекта (в new, old или расширении) не разбирается: объект не проверен, это не удаление",
}


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


def _selects(query, field):
    """The query text names the field as a whole word, any case."""
    return re.search(r"(?<![\wЁё])%s(?![\wЁё])" % re.escape(field), query, re.I) is not None


def _form_parts(path):
    """(the extension's form, its BaseForm) of an adopted Form.xml; the vendor's at borrowing time is in BaseForm."""
    text = fileio.read_text(path)
    main, sep, base = text.partition("<BaseForm")
    return main, base if sep else ""


def check_form_lists(ext, kind, name, form, cfg_old, cfg_new):
    """An adopted form whose dynamic list the vendor changed: the fields the extension adds to the form must still be
    selected by the new query; a list the extension changed itself must be merged with the vendor's change."""
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
        if key not in old or key not in new or old[key] == new[key]:
            continue
        what = "форма %s, динамический список %s: вендор изменил его настройки (запрос)" % (form, attr)
        q = QUERY_RE.search(new[key])
        fields = sorted({p.split(".", 1)[1].split(".")[0] for p in added_paths
                         if p.casefold().startswith(key + ".")} - STANDARD_PATHS)
        lost = [f for f in fields if q is not None and not _selects(q.group(1), f)]
        if settings != at_borrowing.get(key, settings):
            # what the merge must carry over: the fields the extension's own query adds; a field its own query does
            # not select either (a standard attribute bound as Список.Ref, Список.Date) is not part of its change
            own = QUERY_RE.search(settings)
            lost = [f for f in lost if own is None or _selects(own.group(1), f)]
            out.append({"part": "форма", "name": form, "check": "LIST_CHANGED",
                        "detail": what + ", и расширение меняло этот список само — перенести его правку на новые "
                                         "настройки вендора" + ("; новый запрос вендора не выбирает поля, которые "
                                                                "выводит расширение: %s" % ", ".join(lost)
                                                                if lost else "")})
            continue
        if lost:
            out.append({"part": "форма", "name": form, "check": "LIST_FIELD_LOST",
                        "detail": what + " — новый запрос не выбирает поля, которые выводит расширение: %s; добавить "
                                         "их в запрос формы в расширении" % ", ".join(lost)})
    return out


def _folded(children):
    """{casefolded name: info} of one child collection: 1C names are case-insensitive, and the vendor may change only
    the case of a name between releases."""
    return {k.casefold(): v for k, v in children.items()}


def _unreadable(base, name, where, dump, kind):
    return dict(base, part="объект", name=name, check="XML_UNREADABLE",
                detail="XML объекта в %s не разбирается: %s — %s; объект не проверен (это не удаление)" % (
                    where, dump.object_path(kind, name), dump.object_error(kind, name)))


def check_object(ext, kind, name, cfg_old, cfg_new):
    """Findings of one object of the extension (none for an object the extension owns)."""
    o = ext.object(kind, name)
    title = "%s.%s" % (kind, name)
    rel = "%s/%s.xml" % (metadata.KINDS[kind][0], name)
    base = {"ext": ext.name, "object": title, "file": rel}
    if o is None:
        return [_unreadable(base, name, "расширении", ext, kind)] if ext.object_error(kind, name) else []
    if not o.adopted:
        return []
    n = cfg_new.object(kind, name)
    old = cfg_old.object(kind, name) if cfg_old is not None else None
    # every verdict of the object rests on both releases: an unparsable one is reported, not read as «no object»
    broken = [_unreadable(base, name, where, d, kind) for where, d in (("new", cfg_new), ("old", cfg_old))
              if d is not None and d.object_error(kind, name)]
    if broken:
        return broken
    if n is None:
        if old is None and cfg_old is not None:
            # in neither release (an XML that does not parse was reported above): the same question as a field there
            return [dict(base, part="объект", name=name, check="NOT_IN_VENDOR",
                         detail="объекта нет ни в old, ни в new: конфигурация базы доработана — проверить по базе")]
        return [dict(base, part="объект", name=name, check="OBJECT_REMOVED",
                     detail="объекта нет в new" + ("" if old is None else " (в old был)"))]
    found = []
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
    new_ts, old_ts = _folded(n.ts), _folded(old.ts) if old else {}
    for ts, cols in o.ts.items():
        check("табличная часть", ts, {"adopted": ts in o.ts_adopted}, new_ts, old_ts, new_fields, old_fields, False)
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
                found.extend(dict(base, **f) for f in check_form_lists(ext, kind, name, original, cfg_old, cfg_new))
    return found


def analyze(ext_roots, cfg_old, cfg_new, only=None, skip=None):
    exts = metadata.find_extensions(ext_roots, only, skip)
    findings, stats = [], Counter()
    for ext in exts:
        for kind, (folder, _, _) in sorted(metadata.KINDS.items()):
            root = os.path.join(ext.root, folder)
            if not os.path.isdir(root):
                continue
            for f in sorted(os.listdir(root)):
                if not f.endswith(".xml"):
                    continue
                stats["objects"] += 1
                res = check_object(ext, kind, f[:-4], cfg_old, cfg_new)
                if ext.object(kind, f[:-4]) is not None and ext.object(kind, f[:-4]).adopted:
                    stats["adopted"] += 1
                findings.extend(res)
    stats["unreadable"] = sum(1 for f in findings if f["check"] == "XML_UNREADABLE")
    return exts, findings, stats


def render(args, exts, findings, stats, elapsed):
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
    exts, findings, stats = analyze(args.ext_root, cfg_old, cfg_new, args.ext, args.skip)
    if not exts:
        sys.exit("no extension dumps found under --ext-root")
    elapsed = time.time() - t0
    data = {"config_new": args.config_new, "config_old": args.config_old, "extensions": [e.name for e in exts],
            "stats": dict(stats), "findings": findings}
    md_path, json_path = fileio.write_report(args.out, "borrowed", render(args, exts, findings, stats, elapsed), data)
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
