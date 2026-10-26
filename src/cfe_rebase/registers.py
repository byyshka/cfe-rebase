# -*- coding: utf-8 -*-
"""Register writes in extension code against a register whose structure changed in the new release.

    cfe-rebase registers --ext-root <extension dumps> --config-old <old> --config-new <new> [--ext NAME] --out <dir>

A write site is live code (no comments, strings or #Удаление blocks) of the form
  РегистрыСведений|РегистрыНакопления|РегистрыБухгалтерии|РегистрыРасчета.<Имя>.СоздатьМенеджерЗаписи / СоздатьНаборЗаписей
  Движения.<Имя> (records of a document, the register found by name)
For the register, old -> new: dimensions added, dimensions removed, resources and attributes removed (a vendor rename to
«Удалить<Имя>» is one), resources or attributes that became dimensions, dimensions that became resources or attributes
(to_res). A finding is one method that writes the register and either does not mention a new dimension (`.Имя` anywhere
in its live code) - the record key changed, a record manager reads or replaces another record and a record set writes
records with an empty dimension - or still uses a field the new release no longer has (stale), or uses a dimension that
is now a resource or an attribute (key_lost: the field is still there, but not in the record key or the filter of a
record set, Набор.Отбор.<Имя> fails). A method that already fills every new dimension is not reported. The text test is
a heuristic: a dimension filled through ЗаполнитьЗначенияСвойств or a structure is not seen, read the method.
Field names are compared case-insensitively, as 1C does. Each finding has `check`:
  CHANGED           the structure changed as above
  REGISTER_REMOVED  the register is in old and not in new: the vendor deleted it (or renamed it «Удалить<Имя>»), the
                    write fails at run time; a register in neither release is the extension's own and is not reported
  XML_UNREADABLE    the register XML exists in old or new but does not parse: the write is not checked (this is
                    neither «no change» nor «removed»), the detail names the file and the parser error
The dumps are never written: --out inside --ext-root, --config-old or --config-new is refused.
"""
import argparse
import os
import re
import sys
import time
from collections import Counter

from . import bsl, fileio, metadata
from .control_rebase import inside

KINDS = ("РегистрСведений", "РегистрНакопления", "РегистрБухгалтерии", "РегистрРасчета")
COLLECTIONS = {"регистрысведений": "РегистрСведений", "informationregisters": "РегистрСведений",
               "регистрынакопления": "РегистрНакопления", "accumulationregisters": "РегистрНакопления",
               "регистрыбухгалтерии": "РегистрБухгалтерии", "accountingregisters": "РегистрБухгалтерии",
               "регистрырасчета": "РегистрРасчета", "calculationregisters": "РегистрРасчета"}
WRITE_RE = re.compile(r"(?<![\wЁё.])(%s)\s*\.\s*(%s)\s*\.\s*(СоздатьМенеджерЗаписи|СоздатьНаборЗаписей|CreateRecordManager|"
                      r"CreateRecordSet)\s*\(" % ("|".join(COLLECTIONS), bsl.IDENT), re.IGNORECASE)
MOVE_RE = re.compile(r"(?<![\wЁё])(Движения|RegisterRecords)\s*\.\s*(%s)(?![\wЁё])" % bsl.IDENT, re.IGNORECASE)
MEMBER_RE = re.compile(r"\.\s*(%s)" % bsl.IDENT)
WHAT = {"manager": "менеджер записи", "recordset": "набор записей", "movements": "движения документа"}


def live_lines(mod):
    """[(line number, code with strings blanked)] of live code: comments, strings and #Удаление blocks are not code."""
    out, deleting = [], False
    for n, (raw, t) in enumerate(zip(mod.lines, mod.lx), 1):
        if t.kind == "marker":
            if bsl.MARK_DEL.match(raw):
                deleting = True
            elif bsl.MARK_DEL_END.match(raw):
                deleting = False
            continue
        if not deleting and t.kind in ("code", "strcont"):
            out.append((n, t.blank))
    return out


def callers_of(mod, live, meth):
    """Methods of the same module that call `meth` (a helper that writes the register: its callers pass the key)."""
    if meth is None:
        return []
    rx = re.compile(r"(?<![\wЁё.])%s\s*\(" % re.escape(meth.name), re.IGNORECASE)
    out = []
    for n, code in live:
        if meth.decl_line <= n <= meth.end_line or not rx.search(code):
            continue
        m = mod.method_at_line(n)
        if m is not None and m.name not in out:
            out.append(m.name)
    return out


def change(old, new):
    """What the release changed in a register: {added, removed, to_dims, to_res, to_res_as, renamed} or None when
    nothing that writes depend on. to_res - dimensions of old that new keeps as a resource or an attribute (to_res_as:
    name -> «ресурс» / «реквизит»): the field is there, but no longer in the record key or the filter of a record set.
    Names are compared casefolded: a vendor rename that changes only the case changes nothing."""
    if old is None or new is None:
        return None
    new_fields = {x.casefold() for x in list(new.dims) + list(new.res) + list(new.attrs)}
    old_dims, new_dims = {x.casefold() for x in old.dims}, {x.casefold() for x in new.dims}
    # «Удалить<Имя>» is the vendor marking a field for removal: its own code stops filling it, so must the extension
    added = [d for d in new.dims if d.casefold() not in old_dims and not d.casefold().startswith("удалить")]
    removed = [f for f in list(old.dims) + list(old.res) + list(old.attrs) if f.casefold() not in new_fields]
    to_dims = [f for f in list(old.res) + list(old.attrs) if f.casefold() in new_dims]
    new_res = {x.casefold() for x in new.res}
    to_res_as = {d: "ресурс" if d.casefold() in new_res else "реквизит" for d in old.dims
                 if d.casefold() not in new_dims and d.casefold() in new_fields}
    renamed = {f: "Удалить" + f for f in removed if ("удалить" + f).casefold() in new_fields}
    if not added and not removed and not to_res_as:
        return None
    return {"added": added, "removed": removed, "to_dims": to_dims, "to_res": list(to_res_as), "to_res_as": to_res_as,
            "renamed": renamed}


def state(cfg_old, cfg_new, kind, name):
    """What a write to the register meets in new: a change() dict with `check` CHANGED, REGISTER_REMOVED (in old, not
    in new), XML_UNREADABLE (an XML of old or new that does not parse, with `files`), or None (nothing to report; a
    register in neither release is the extension's own)."""
    broken = [(where, d.object_path(kind, name), d.object_error(kind, name))
              for where, d in (("new", cfg_new), ("old", cfg_old)) if d is not None and d.object_error(kind, name)]
    if broken:
        return {"check": "XML_UNREADABLE", "files": broken}
    old = cfg_old.object(kind, name) if cfg_old is not None else None
    new = cfg_new.object(kind, name)
    if old is not None and new is None:
        return {"check": "REGISTER_REMOVED", "renamed": "Удалить" + name if cfg_new.object_exists(
            kind, "Удалить" + name) else None}
    ch = change(old, new)
    return dict(ch, check="CHANGED") if ch else None


def scan_module(ext, rel, cfg_old, cfg_new, cache):
    mod = ext.module(rel)
    if mod is None:
        return []
    live = live_lines(mod)
    sites = {}  # (method, kind, register) -> {first line, how}
    for n, code in live:
        found = [(COLLECTIONS[m.group(1).casefold()], m.group(2), "manager" if "менеджер" in m.group(3).casefold() or
                  "manager" in m.group(3).casefold() else "recordset") for m in WRITE_RE.finditer(code)]
        for m in MOVE_RE.finditer(code):
            for kind in KINDS:
                if cfg_new.object_exists(kind, m.group(2)) or (cfg_old is not None and cfg_old.object_exists(kind,
                                                                                                         m.group(2))):
                    found.append((kind, m.group(2), "movements"))
                    break
        for kind, name, how in found:
            meth = mod.method_at_line(n)
            key = (meth.name if meth else "", kind, name)
            sites.setdefault(key, {"line": n, "how": how, "method": meth})
    out = []
    for (mname, kind, name), site in sites.items():
        ck = (kind, name.casefold())
        if ck not in cache:
            cache[ck] = state(cfg_old, cfg_new, kind, name)
        ch = cache[ck]
        if ch is None:
            continue
        meth = site["method"]
        missing, stale, key_lost, parts = [], [], [], []
        if ch["check"] == "XML_UNREADABLE":
            parts.append("XML регистра не разбирается — %s; запись не проверена (это не «без изменений» и не "
                         "удаление)" % "; ".join("%s: %s — %s" % f for f in ch["files"]))
        elif ch["check"] == "REGISTER_REMOVED":
            parts.append("регистра в new нет (в old был)%s — обращение к нему упадёт при выполнении" % (
                ", вендор переименовал в %s — пометил к удалению" % ch["renamed"] if ch["renamed"] else ""))
        else:
            lines = [code for n, code in live if meth is None or meth.decl_line <= n <= meth.end_line]
            used = {m.group(1).casefold() for code in lines for m in MEMBER_RE.finditer(code)}
            missing = [d for d in ch["added"] if d.casefold() not in used]
            stale = [f for f in ch["removed"] if f.casefold() in used]
            key_lost = [d for d in ch["to_res"] if d.casefold() in used]
            if not missing and not stale and not key_lost:
                continue
            if ch["added"]:
                parts.append("в new новые измерения: %s%s" % (", ".join(ch["added"]), " (из них были ресурсами или "
                                                             "реквизитами: %s)" % ", ".join(ch["to_dims"])
                                                             if ch["to_dims"] else ""))
            if missing:
                parts.append("в методе не заполняются: %s" % ", ".join(missing))
            if stale:
                parts.append("метод использует поля, которых в new нет: %s" % ", ".join(
                    f + (" (вендор переименовал в %s)" % ch["renamed"][f] if f in ch["renamed"] else "")
                    for f in stale))
            if key_lost:
                parts.append("метод использует измерения, которые в new стали ресурсом или реквизитом — в ключе записи "
                             "и отборе набора их больше нет: %s" % ", ".join(
                                 "%s (%s)" % (d, ch["to_res_as"][d]) for d in key_lost))
        callers = callers_of(mod, live, meth)
        if callers:
            parts.append("метод вызывают (их вызовы, возможно, тоже придётся поправить): %s" % ", ".join(callers))
        out.append({"ext": ext.name, "module": rel, "module_title": metadata.module_title(rel), "method": mname,
                    "line": site["line"], "register": "%s.%s" % (kind, name), "how": site["how"],
                    "check": ch["check"], "added": ch.get("added", []), "removed": ch.get("removed", []),
                    "to_dims": ch.get("to_dims", []), "to_res": ch.get("to_res", []), "missing": missing,
                    "stale": stale, "key_lost": key_lost, "callers": callers,
                    "detail": "%s (%s): %s" % ("%s.%s" % (kind, name), WHAT[site["how"]], "; ".join(parts))})
    return out


def analyze(ext_roots, cfg_old, cfg_new, only=None, skip=None):
    exts = metadata.find_extensions(ext_roots, only, skip)
    findings, stats, cache = [], Counter(), {}
    for ext in exts:
        for rel in ext.files(".bsl"):
            stats["modules"] += 1
            findings.extend(scan_module(ext, rel, cfg_old, cfg_new, cache))
    states = Counter(v["check"] for v in cache.values() if v is not None)
    stats["registers_changed"] = states["CHANGED"]
    stats["registers_removed"] = states["REGISTER_REMOVED"]
    stats["registers_unreadable"] = states["XML_UNREADABLE"]
    stats["registers_written"] = len(cache)
    return exts, findings, stats


def render(args, exts, findings, stats, elapsed):
    md = ["# Запись в регистры, у которых обновление изменило состав", ""]
    md.append("new: `%s`, old: `%s`. Расширений: %d, модулей: %d; регистров, в которые пишет код: %d, из них в new "
              "изменились: %d, удалены: %d, XML не разбирается: %d. Время: %.1f с." % (
                  args.config_new, args.config_old, len(exts), stats["modules"], stats["registers_written"],
                  stats["registers_changed"], stats["registers_removed"], stats["registers_unreadable"], elapsed))
    md.append("")
    md.append("Новое измерение меняет ключ записи: менеджер записи без него читает или заменяет другую запись, набор "
              "записей пишет записи с пустым измерением. Измерение, ставшее ресурсом или реквизитом, из ключа ушло: "
              "отбор набора по нему и чтение менеджера записи по нему больше не работают. Проверка — по тексту метода "
              "(`.Имя`): измерение, заполняемое через ЗаполнитьЗначенияСвойств или структуру, она не видит.")
    md.append("")
    md.append(fileio.md_table(["Проверка", "Расширение", "Модуль:строка", "Метод", "Регистр", "Как пишет", "Что"],
                              [(f["check"], f["ext"], "%s:%d" % (f["module_title"], f["line"]), f["method"] or "—",
                                f["register"], WHAT[f["how"]], f["detail"].split(": ", 1)[1]) for f in findings])
              if findings else "Нет.")
    return "\n".join(md)


def main(argv=None):
    fileio.console_utf8()
    ap = argparse.ArgumentParser(description="Запись в регистры, у которых новый релиз изменил состав измерений и "
                                             "полей: новые измерения, которые код не заполняет, и удалённые поля")
    ap.add_argument("--ext-root", action="extend", nargs="+", required=True,
                    help="папка выгрузок расширений (или одна выгрузка); можно несколько")
    ap.add_argument("--ext", action="extend", nargs="+", help="только эти расширения (имя)")
    ap.add_argument("--skip", action="extend", nargs="+", help="пропустить эти расширения (имя)")
    ap.add_argument("--config-old", required=True, help="выгрузка типовой релиза, под который написаны расширения")
    ap.add_argument("--config-new", required=True, help="выгрузка типовой нового релиза")
    ap.add_argument("--out", required=True, help="папка отчёта registers.json / registers.md, вне выгрузок")
    args = ap.parse_args(argv)
    for label, root in [("--ext-root", r) for r in args.ext_root] + [("--config-old", args.config_old),
                                                                     ("--config-new", args.config_new)]:
        if inside(args.out, root):
            sys.exit("--out %s внутри %s %s: registers в выгрузки не пишет" % (args.out, label, root))
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
    md_path, json_path = fileio.write_report(args.out, "registers", render(args, exts, findings, stats, elapsed), data)
    print("extensions: %d, modules: %d, registers written: %d (changed in new: %d, removed: %d, unreadable XML: %d), "
          "findings: %d, %.1fs" % (len(exts), stats["modules"], stats["registers_written"], stats["registers_changed"],
                                   stats["registers_removed"], stats["registers_unreadable"], len(findings), elapsed))
    print(md_path)
    print(json_path)


if __name__ == "__main__":
    main()
