# -*- coding: utf-8 -*-
"""Vendor registers the extensions read in query texts whose composition or recorders the new release changed.

    cfe-rebase reads --ext-root <extension dumps> --config-old <old> --config-new <new> [--ext NAME] --out <dir>

A query over a register still compiles when the vendor adds a dimension, a resource or a recorder document, so
`queries` reports nothing, yet the numbers the query returns may change: a new dimension splits balances and turnovers
that used to be one row, a new recorder adds movements, a changed type changes joins and sums. The check lists such
registers, one row per (extension, register), for a person to look at. It is informational: no verdicts, `sev` is
always ПРОВЕРИТЬ.

Reads are table references in query texts only, collected as `queries` collects them (queries.query_texts): the string
literals of one method glued together, <QueryText> of a dynamic list with ManualQuery in Form.xml, dataset queries of
DCS schemas. Comments and #Удаление blocks are not code. Kinds: РегистрНакопления, РегистрСведений, РегистрБухгалтерии,
РегистрРасчета (English names too), with or without a virtual table. Temporary tables and parameters are not registers.
Reads through a manager in code (РегистрыНакопления.X.Остатки(), .СрезПоследних(), .Выбрать()) are out of scope of
this version.

Not reported: a register that is the extension's own (not adopted), one absent from old (nothing to compare; whether it
is in new is the business of `queries`), and one whose composition and recorders did not change (counted only).

For a register, old -> new, names casefolded as 1C compares them: dimensions, resources and attributes added and
removed - a field the vendor renamed to «Удалить<Имя>» is removed (the new name is in the detail), not added; the types
of the fields, also of a field that moved to another part (the order of the types inside a composite type does not
count), and of the defined types a field is typed by (DefinedTypes/<Name>.xml changed, the field XML did not); the
register properties that change what a query returns - RegisterType, InformationRegisterPeriodicity, WriteMode and
UseInTotals of a dimension; recorder documents added and removed (Properties/RegisterRecords of Documents/*.xml).
A register name in a string literal of a method that also has a query text counts as a read, as in `queries`.
Each item has `check`:
  CHANGED           something of the above changed
  REGISTER_REMOVED  the register is in old and not in new (a vendor rename to «Удалить<Имя>» is named in the detail)
  XML_UNREADABLE    the register XML exists in old or new but does not parse: nothing is compared, the detail names
                    the file and the parser error
The dumps are never written: --out inside --ext-root, --config-old or --config-new is refused.
"""
import argparse
import os
import re
import sys
import time
from collections import Counter, OrderedDict

from . import fileio, metadata, qparse, queries
from .borrowed import _same_type, _short
from .control_rebase import inside
from .registers import KINDS

SEV = "ПРОВЕРИТЬ"
DELETE = "удалить"  # «Удалить<Имя>»: the vendor marks a field or an object for removal
# bucket of ObjectInfo, part in type_changed, RU part, RU plural
PARTS = (("dims", "dimension", "измерение", "измерения"), ("res", "resource", "ресурс", "ресурсы"),
         ("attrs", "attribute", "реквизит", "реквизиты"))
PART_RU = {p[1]: p[2] for p in PARTS}
LISTS = ("dims_added", "dims_removed", "res_added", "res_removed", "attrs_added", "attrs_removed", "type_changed",
         "props_changed", "recorders_added", "recorders_removed")
# register properties that change what a query over the register returns (balance / turnovers, slice periods, whether
# records are subordinate to a recorder); Master, MainFilter, DenyIncompleteValues change writing and forms, not reads
REGISTER_PROPS = ("RegisterType", "InformationRegisterPeriodicity", "WriteMode")
PROP_RU = {"RegisterType": "вид регистра", "InformationRegisterPeriodicity": "периодичность",
           "WriteMode": "режим записи", "UseInTotals": "использовать в итогах"}
DEFINED_RE = re.compile(r"TypeSet=cfg:DefinedType\.([^;]+)")


def _by_name(names):
    return {n.casefold(): n for n in names}


def _defined_changes(key, defined):
    """[(defined type, old type key, new type key)] for every defined type the field's type names whose own type the
    release changed: the field XML stays the same and only DefinedTypes/<Name>.xml differs."""
    out = []
    for name in DEFINED_RE.findall(key or "") if defined else []:
        a, b = defined(name)
        if a is not None and b is not None and not _same_type(a, b):
            out.append((name, a, b))
    return out


def compare(old, new, rec_old, rec_new, defined=None):
    """What the release changed in a register a query reads: the LISTS of an item plus `renamed` ({old: «Удалить…»})
    and `moved` ([(name, part in old, part in new)]) for the detail. rec_old / rec_new: recorder documents;
    defined(name) -> (old type key, new type key) of a defined type, or None not to look into defined types."""
    old_all, new_all = {}, {}
    for bucket, part, _, _ in PARTS:
        for n in getattr(old, bucket):
            old_all.setdefault(n.casefold(), (part, bucket, n))
        for n in getattr(new, bucket):
            new_all.setdefault(n.casefold(), (part, bucket, n))
    gone = {k for k in old_all if k not in new_all}
    out = {"renamed": {}, "moved": [], "type_changed": [], "props_changed": []}
    for bucket, part, _, _ in PARTS:
        o, n = getattr(old, bucket), getattr(new, bucket)
        of, nf = _by_name(o), _by_name(n)
        out[bucket + "_added"] = [v for k, v in nf.items() if k not in of
                                  and not (k.startswith(DELETE) and k[len(DELETE):] in gone)]
        out[bucket + "_removed"] = [v for k, v in of.items() if k not in nf]
        for k, v in of.items():
            if k in nf:
                ok, nk = o[v].get("type_key"), n[nf[k]].get("type_key")
                if not _same_type(ok, nk):
                    out["type_changed"].append({"part": part, "name": nf[k], "old": _short(ok), "new": _short(nk)})
                else:
                    for dt, a, b in _defined_changes(nk, defined):
                        out["type_changed"].append({"part": part, "name": nf[k], "old": _short(a), "new": _short(b),
                                                    "defined_type": "ОпределяемыйТип." + dt})
                ou, nu = o[v].get("use_in_totals"), n[nf[k]].get("use_in_totals")
                if bucket == "dims" and ou is not None and nu is not None and ou != nu:
                    out["props_changed"].append({"name": "%s.UseInTotals" % nf[k], "old": ou, "new": nu})
            elif k in new_all:
                new_part, new_bucket, new_name = new_all[k]
                out["moved"].append((v, part, new_part))
                ok, nk = o[v].get("type_key"), getattr(new, new_bucket)[new_name].get("type_key")
                if not _same_type(ok, nk):
                    # the part changed and so did the type: both matter to a query that reads the field
                    out["type_changed"].append({"part": new_part, "name": new_name, "old": _short(ok),
                                                "new": _short(nk)})
            elif k in gone and DELETE + k in new_all:
                out["renamed"][v] = new_all[DELETE + k][2]
    for p in REGISTER_PROPS:
        a, b = old.props.get(p), new.props.get(p)
        if a != b and (a or b):
            out["props_changed"].append({"name": p, "old": a, "new": b})
    ro, rn = _by_name(rec_old), _by_name(rec_new)
    out["recorders_added"] = sorted(v for k, v in rn.items() if k not in ro)
    out["recorders_removed"] = sorted(v for k, v in ro.items() if k not in rn)
    return out


def state(cfg_old, cfg_new, kind, name):
    """None for a register absent from old (not compared), else a dict with `check` CHANGED, REGISTER_REMOVED,
    XML_UNREADABLE (with `files`) or UNCHANGED, the LISTS, `renamed` and `moved`."""
    if not cfg_old.object_exists(kind, name):
        return None
    st = dict({k: [] for k in LISTS}, renamed={}, moved=[])
    broken = [(where, d.object_path(kind, name), d.object_error(kind, name))
              for where, d in (("new", cfg_new), ("old", cfg_old)) if d.object_error(kind, name)]
    if broken:
        return dict(st, check="XML_UNREADABLE", files=broken)
    old, new = cfg_old.object(kind, name), cfg_new.object(kind, name)
    if new is None:
        return dict(st, check="REGISTER_REMOVED", renamed=DELETE.capitalize() + name if cfg_new.object_exists(
            kind, DELETE.capitalize() + name) else None)
    key = (kind, name.casefold())

    def defined(dt):
        a, b = cfg_old.object("ОпределяемыйТип", dt), cfg_new.object("ОпределяемыйТип", dt)
        return (a.type_key if a else None, b.type_key if b else None)

    st.update(compare(old, new, cfg_old.recorders().get(key, set()), cfg_new.recorders().get(key, set()), defined))
    return dict(st, check="CHANGED" if any(st[k] for k in LISTS) else "UNCHANGED")


def _prop_ru(name):
    """'RegisterType' -> 'вид регистра', 'Склад.UseInTotals' -> 'Склад: использовать в итогах'."""
    field, _, prop = name.rpartition(".")
    return "%s: %s" % (field, PROP_RU.get(prop, prop)) if field else PROP_RU.get(prop, prop)


def detail(st):
    """A short Russian note on what changed."""
    if st["check"] == "XML_UNREADABLE":
        return "XML регистра не разбирается — %s; состав не сравнивался" % "; ".join("%s: %s — %s" % f
                                                                                     for f in st["files"])
    if st["check"] == "REGISTER_REMOVED":
        return "регистра в new нет (в old был)%s" % (", вендор переименовал в %s — пометил к удалению" % st["renamed"]
                                                     if st["renamed"] else "")
    parts = []
    for bucket, _, _, plural in PARTS:
        if st[bucket + "_added"]:
            parts.append("новые %s: %s" % (plural, ", ".join(st[bucket + "_added"])))
        if st[bucket + "_removed"]:
            parts.append("убраны %s: %s" % (plural, ", ".join(st[bucket + "_removed"])))
    if st["renamed"]:
        parts.append("вендор пометил к удалению: %s" % ", ".join("%s → %s" % kv for kv in st["renamed"].items()))
    if st["moved"]:
        parts.append("перешли в другую часть: %s" % ", ".join("%s (%s → %s)" % (n, PART_RU[a], PART_RU[b])
                                                              for n, a, b in st["moved"]))
    if st["type_changed"]:
        parts.append("сменился тип: %s" % ", ".join(
            "%s %s%s" % (PART_RU[t["part"]], t["name"], " (через %s)" % t["defined_type"] if t.get("defined_type")
                         else "") for t in st["type_changed"]))
    if st["props_changed"]:
        parts.append("сменились свойства: %s" % ", ".join("%s %s → %s" % (_prop_ru(p["name"]), p["old"] or "—",
                                                                          p["new"] or "—")
                                                         for p in st["props_changed"]))
    if st["recorders_added"]:
        parts.append("новые регистраторы: %d" % len(st["recorders_added"]))
    if st["recorders_removed"]:
        parts.append("убраны регистраторы: %d" % len(st["recorders_removed"]))
    return "; ".join(parts)


def reads_of(ext):
    """[(kind, name, read)] for every register table in the query texts of one extension; read: {path, line, where,
    table, fields} - one per table spelling and line of a query text, fields merged over its aliases."""
    out = []
    for rel, where, text, strs, _ in queries.query_texts(ext):
        q = qparse.clean(text)
        binds = [b for b in qparse.bind_aliases(q) if b[1] in KINDS]
        if not binds:
            continue
        fields = {}
        for alias, segs, _ in qparse.field_refs(q):
            got = fields.setdefault(alias.casefold(), [])
            f = ".".join(segs)
            if f.casefold() not in {x.casefold() for x in got}:
                got.append(f)
        places = OrderedDict()
        for alias, kind, name, sub, pos in binds:
            table = name + ("." + sub if sub else "")
            line = queries.line_of(strs, pos)
            key = (kind, table.casefold(), line)
            if key not in places:
                places[key] = (kind, name, {"path": ext.path(rel), "line": line, "where": where, "table": table,
                                            "fields": []})
            read = places[key][2]
            for f in fields.get(alias.casefold(), []) if alias else []:
                if f.casefold() not in {x.casefold() for x in read["fields"]}:
                    read["fields"].append(f)
        out.extend(places.values())
    return out


def analyze(ext_roots, cfg_old, cfg_new, only=None, skip=None):
    exts = metadata.find_extensions(ext_roots, only, skip)
    stats, cache, groups = Counter(), {}, OrderedDict()
    own, absent = set(), set()
    for ext in exts:
        for kind, name, read in reads_of(ext):
            mine = ext.object(kind, name)
            if mine is not None and not mine.adopted:
                own.add((ext.name, kind, name.casefold()))
                continue
            ck = (kind, name.casefold())
            if ck not in cache:
                cache[ck] = state(cfg_old, cfg_new, kind, name)
            if cache[ck] is None:
                absent.add(ck)
                continue
            groups.setdefault((ext.name, kind, name.casefold()), {"ext": ext.name, "kind": kind, "name": name,
                                                                  "reads": []})["reads"].append(read)
    findings = []
    for (_, kind, cf), g in groups.items():
        st = cache[(kind, cf)]
        if st["check"] == "UNCHANGED":
            continue
        findings.append(dict({"ext": g["ext"], "register": "%s.%s" % (kind, g["name"]), "check": st["check"],
                              "sev": SEV}, **{k: st[k] for k in LISTS}, reads=g["reads"], detail=detail(st)))
    findings.sort(key=lambda f: (f["register"].casefold(), f["ext"].casefold()))
    states = Counter(v["check"] for v in cache.values() if v is not None)
    stats["extensions"] = len(exts)
    stats["reads"] = sum(len(g["reads"]) for g in groups.values())
    stats["registers_read"] = sum(states.values())
    stats["registers_changed"] = states["CHANGED"]
    stats["registers_removed"] = states["REGISTER_REMOVED"]
    stats["registers_unreadable"] = states["XML_UNREADABLE"]
    stats["registers_unchanged"] = states["UNCHANGED"]
    stats["registers_own"] = len(own)
    stats["registers_not_in_old"] = len(absent)
    return exts, findings, stats


def _type_change(old, new):
    """Short type strings (borrowed._short) -> «what left → what came»: a composite type of twenty documents that got
    one more reads as that one document."""
    a, b = old.split(", "), new.split(", ")
    gone, came = [x for x in a if x not in b], [x for x in b if x not in a]
    return "`%s` → `%s`" % (", ".join(gone) or "—", ", ".join(came) or "—")


def _where(read, roots, ext):
    root = roots.get(ext)
    path = os.path.relpath(read["path"], root).replace("\\", "/") if root else read["path"]
    return path + (":%d" % read["line"] if read["line"] else "")


def render(args, exts, findings, stats, elapsed):
    md = ["# Чтение регистров, у которых обновление изменило состав или регистраторов", ""]
    md.append("new: `%s`, old: `%s`. Расширений: %d; мест чтения: %d; регистров вендора в запросах: %d — изменились: "
              "%d, удалены: %d, XML не разбирается: %d, без изменений: %d. Не сравнивались: собственные регистры "
              "расширений — %d, регистров нет в old — %d. Время: %.1f с." % (
                  args.config_new, args.config_old, len(exts), stats["reads"], stats["registers_read"],
                  stats["registers_changed"], stats["registers_removed"], stats["registers_unreadable"],
                  stats["registers_unchanged"], stats["registers_own"], stats["registers_not_in_old"], elapsed))
    md.append("")
    md.append("Проверка информационная: новое измерение, ресурс или регистратор не ломают текст запроса, и `queries` "
              "такой запрос не покажет, но результат может измениться — новое измерение дробит остатки и обороты, "
              "новый регистратор добавляет движения, сменившийся тип меняет соединения и суммы (поле, которого в new "
              "нет, `queries` покажет сам). Источники — тексты запросов (модули, ручной запрос динамического списка в "
              "Form.xml, наборы СКД); чтение через менеджер (РегистрыНакопления.X.Остатки()) не смотрится. Тип — что "
              "из него ушло → что пришло, полностью — в reads.json.")
    roots = {e.name: e.root for e in exts}
    sections = OrderedDict()
    for f in findings:
        sections.setdefault(f["register"].casefold(), []).append(f)
    if not sections:
        md.extend(["", "Нет."])
    for rows in sections.values():
        f = rows[0]
        md.extend(["", "## %s — %s" % (f["register"], f["check"]), ""])
        lines = [f["detail"]]  # dimensions, resources, attributes added / removed, renames, moves
        for t in f["type_changed"]:
            lines.append("тип %s %s%s: %s" % (PART_RU[t["part"]], t["name"],
                                              " (через %s)" % t["defined_type"] if t.get("defined_type") else "",
                                              _type_change(t["old"], t["new"])))
        for key, label in (("recorders_added", "новые регистраторы"), ("recorders_removed", "убраны регистраторы")):
            if f[key]:
                lines.append("%s (%d): %s" % (label, len(f[key]), ", ".join(f[key])))
        md.extend("- " + x for x in lines)
        md.extend(["", "Где читается:", ""])
        md.append(fileio.md_table(["Расширение", "Файл:строка", "Где", "Таблица", "Поля"],
                                  [(r["ext"], _where(x, roots, r["ext"]), x["where"], x["table"],
                                    ", ".join(x["fields"]) or "—") for r in rows for x in r["reads"]]))
    return "\n".join(md)


def main(argv=None):
    fileio.console_utf8()
    ap = argparse.ArgumentParser(description="Регистры вендора, которые расширения читают в текстах запросов и у "
                                             "которых новый релиз изменил состав или регистраторов (информационно)")
    ap.add_argument("--ext-root", action="extend", nargs="+", required=True,
                    help="папка выгрузок расширений (или одна выгрузка); можно несколько")
    ap.add_argument("--ext", action="extend", nargs="+", help="только эти расширения (имя)")
    ap.add_argument("--skip", action="extend", nargs="+", help="пропустить эти расширения (имя)")
    ap.add_argument("--config-old", required=True, help="выгрузка типовой релиза, под который написаны расширения")
    ap.add_argument("--config-new", required=True, help="выгрузка типовой нового релиза")
    ap.add_argument("--out", required=True, help="папка отчёта reads.json / reads.md, вне выгрузок")
    args = ap.parse_args(argv)
    for label, root in [("--ext-root", r) for r in args.ext_root] + [("--config-old", args.config_old),
                                                                     ("--config-new", args.config_new)]:
        if inside(args.out, root):
            sys.exit("--out %s внутри %s %s: reads в выгрузки не пишет" % (args.out, label, root))
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
    md_path, json_path = fileio.write_report(args.out, "reads", render(args, exts, findings, stats, elapsed), data)
    print("extensions: %d, reads: %d, registers read: %d (changed: %d, removed: %d, unreadable XML: %d, unchanged: %d),"
          " findings: %d, %.1fs" % (len(exts), stats["reads"], stats["registers_read"], stats["registers_changed"],
                                    stats["registers_removed"], stats["registers_unreadable"],
                                    stats["registers_unchanged"], len(findings), elapsed))
    print(md_path)
    print(json_path)


if __name__ == "__main__":
    main()
