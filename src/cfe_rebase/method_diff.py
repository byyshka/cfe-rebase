# -*- coding: utf-8 -*-
"""What the vendor changed old -> new in every intercepted method, as compact hunks with line numbers.

    cfe-rebase method-diff --ext <extension dumps root> --old <vendor old dump> --new <vendor new dump> --out <dir>
                          [--only <extension name>] [--method <vendor method name>]

For every interceptor annotation (&Вместо, &Перед, &После, &ИзменениеИКонтроль) of the extension dumps, and every vendor
form handler an adopted form replaces by callType="Override" in Form.xml without ПродолжитьВызов (kind «Вместо
(callType)», a frozen copy like &Вместо: interceptors.override_copies): kind, target,
module, signature old/new and the changed blocks of the intercepted vendor method, or «тело не изменилось». Interceptors
of one method (several extensions or kinds) share one set of hunks. The body is compared from the line after the
parameter list to the end line: for &ИзменениеИКонтроль in the control view (lines trimmed, empty lines dropped,
comments kept - a vendor comment edit breaks the control), otherwise in the code view (bsl.body_code: whitespace
collapsed, comments and empty lines dropped). A worker reads these hunks instead of two whole vendor bodies; the line
numbers point into the vendor modules. A body the vendor moved out (new at most a quarter of old and calling a method
old did not) gets body_extracted (BODY_EXTRACTED of merge_copy). Writes <out>/report.md and <out>/report.json only.
"""
import argparse
import os
import sys
import time
from collections import Counter, OrderedDict

from . import bsl, fileio, interceptors as icp, merge_copy, metadata, refs
from .control_rebase import refuse_out_inside

CALLTYPE_KIND = "Вместо (callType)"
KIND_ORDER = ("Вместо", CALLTYPE_KIND, "Перед", "После", "ИзменениеИКонтроль")


def view(mod, m, control):
    """[(line, text)] of the body after the parameter list, end line included."""
    rows = bsl.body_control(mod, m) if control else bsl.body_code(mod, m)
    return [(k, t) for k, t in rows if k > m.decl_end_line]


def hunks(a, b, a_anchor, b_anchor):
    """Changed blocks of two [(line, text)] lists. An empty side gets the line it follows (at_old / at_new)."""
    out = []
    for tag, i1, i2, j1, j2 in bsl._matcher(a, b).get_opcodes():
        if tag == "equal":
            continue
        out.append({"tag": tag, "old": a[i1:i2], "new": b[j1:j2],
                    "at_old": a[i1 - 1][0] if i1 else a_anchor, "at_new": b[j1 - 1][0] if j1 else b_anchor})
    return out


def span(items, at):
    if not items:
        return "после %d" % at
    return "%d" % items[0][0] if len(items) == 1 else "%d-%d" % (items[0][0], items[-1][0])


def collect(exts, only_method, cfg_old=None, cfg_new=None):
    """Groups of interceptors by vendor method. With both releases given, the callType="Override" copies of adopted
    forms join too: their target is the vendor handler of the event (target_old - the old one, when the vendor
    re-bound the event)."""
    groups = OrderedDict()
    form = refs.form_cache()
    for ext in exts:
        for rel in ext.files(".bsl"):
            mod = ext.module(rel)
            if mod is None:
                continue
            for m in mod.methods:
                for kind, target, line in m.interceptors:
                    if not target or (only_method and target.casefold() != only_method.casefold()):
                        continue
                    key = (metadata.rel_key(rel), target.casefold())
                    g = groups.setdefault(key, {"module": rel, "module_title": metadata.module_title(rel),
                                                "target": target, "interceptors": []})
                    g["interceptors"].append({"ext": ext.name, "kind": kind, "method": m.name,
                                              "proceed": m.has_proceed, "line": line})
        if cfg_old is None or cfg_new is None:
            continue
        for o in icp.override_copies(ext, form, cfg_old, cfg_new):
            target = o["target_new"] or o["target_old"]
            if only_method and only_method.casefold() not in {(o["target_new"] or "").casefold(),
                                                               (o["target_old"] or "").casefold()}:
                continue
            key = (metadata.rel_key(o["module"]), target.casefold())
            g = groups.setdefault(key, {"module": o["module"], "module_title": metadata.module_title(o["module"]),
                                        "target": target, "interceptors": []})
            if not g["interceptors"] and o["target_old"] and o["target_old"] != target:
                g["target_old"] = o["target_old"]  # the vendor re-bound the event: the old body is under this name
            g["interceptors"].append({"ext": ext.name, "kind": CALLTYPE_KIND, "method": o["ext_method"],
                                      "proceed": False, "line": o["method"].decl_line, "form": o["form"],
                                      "xml_line": o["xml_line"], "event": "%s.%s" % (o["owner"], o["event"])})
    return list(groups.values())


def analyse(g, cfg_old, cfg_new, ext=None):
    """ext - the extension dump of the group: a vendor rename keeps the uuid, so the module of a renamed object is
    looked up under its name in each release."""
    rel_old = metadata.counterpart_rel(ext, g["module"], cfg_old)[0] if ext is not None else g["module"]
    rel_new = metadata.counterpart_rel(ext, g["module"], cfg_new)[0] if ext is not None else g["module"]
    vo, vn = cfg_old.module(rel_old), cfg_new.module(rel_new)
    mo = vo.find(g.get("target_old") or g["target"]) if vo else None
    mn = vn.find(g["target"]) if vn else None
    g["old_lines"] = "%d-%d" % (mo.decl_line, mo.end_line) if mo else None
    g["new_lines"] = "%d-%d" % (mn.decl_line, mn.end_line) if mn else None
    g["sig_old"] = mo.signature() if mo else None
    g["sig_new"] = mn.signature() if mn else None
    g["sig_changed"] = bool(mo and mn and mo.sig_key() != mn.sig_key())
    if mo is None or mn is None:
        if mn is None and g["target"].casefold() in bsl.EVENTS and vn is not None:
            g["status"] = "нет типового обработчика события в new" + (" (в old был)" if mo else "")
        elif mn is None:
            g["status"] = "метода нет в new" + (" (в old был)" if mo else "") if vn else "модуля нет в new"
        else:
            g["status"] = "метода не было в old — появился в new" if vo else "модуля не было в old"
        g["hunks"] = []
        return g
    control = any(i["kind"] == "ИзменениеИКонтроль" for i in g["interceptors"])
    g["view"] = "контроль" if control else "код"
    a, b = view(vo, mo, control), view(vn, mn, control)
    g["hunks"] = hunks(a, b, mo.decl_end_line, mn.decl_end_line)
    st = bsl.diff_stats(a, b)
    g["change"] = bsl.change_label(st)
    if not g["hunks"]:
        g["status"] = "тело не изменилось"
    else:
        g["status"] = "тело изменилось: %s, блоков %d" % (g["change"], st["hunks"])
        if control and bsl.diff_stats(view(vo, mo, False), view(vn, mn, False))["hunks"] == 0:
            g["status"] += " (только комментарии — контроль `&ИзменениеИКонтроль` всё равно ломается)"
        extracted = merge_copy.body_extracted(vo, mo, vn, mn)
        if extracted:
            g["body_extracted"] = extracted
            g["status"] += "; BODY_EXTRACTED — вендор вынес тело (строк кода %d → %d, новые вызовы: %s): %s" % (
                extracted["old_lines"], extracted["new_lines"], ", ".join(extracted["new_calls"]),
                merge_copy.EXTRACT_ADVICE)
    return g


def kind_text(kind):
    return kind if kind == CALLTYPE_KIND else "&" + kind


def who(g):
    return "; ".join("%s %s%s → %s" % (i["ext"], kind_text(i["kind"]), " +ПВ" if i["proceed"] else "", i["method"])
                     for i in g["interceptors"])


def render(args, groups, elapsed):
    kinds = Counter(i["kind"] for g in groups for i in g["interceptors"])
    changed = [g for g in groups if g["hunks"]]
    md = ["# Изменения вендора в перехваченных методах", ""]
    md.append("Расширения: `%s`%s%s. Вендор old: `%s`, new: `%s`. Время: %.1f с." % (
        args.ext, " (только `%s`)" % args.only if args.only else "",
        ", метод `%s`" % args.method if args.method else "", args.old, args.new, elapsed))
    md.append("")
    md.append("Перехватов: %d (%s), методов вендора: %d; тело изменилось — %d, не изменилось — %d, прочее (нет "
              "метода, новый метод) — %d." % (
                  sum(kinds.values()), ", ".join("%s — %d" % (kind_text(k), kinds[k]) for k in KIND_ORDER
                                                 if kinds.get(k)),
                  len(groups), len(changed), sum(1 for g in groups if g["status"] == "тело не изменилось"),
                  sum(1 for g in groups if "hunks" in g and not g["hunks"] and g["status"] != "тело не изменилось")))
    md.append("")
    md.append("Сравнение тела — со строки после списка параметров до конца метода. Вид «код»: пробелы схлопнуты, "
              "комментарии и пустые строки выброшены; вид «контроль» (есть `&ИзменениеИКонтроль`): края строк "
              "обрезаны, пустые строки выброшены, комментарии входят. Номера строк — в модулях вендора.")
    md.append("")
    md.append(fileio.md_table(
        ["№", "Модуль", "Метод вендора", "Перехваты", "Сигнатура old→new", "Строки old / new", "Тело old→new"],
        [(n, g["module_title"], g["target"], who(g),
          ("изменилась" if g["sig_changed"] else "та же") if g["sig_old"] and g["sig_new"] else "—",
          "%s / %s" % (g["old_lines"] or "—", g["new_lines"] or "—"), g["status"]) for n, g in enumerate(groups, 1)]))
    for n, g in enumerate(groups, 1):
        if not g["hunks"] and not g["sig_changed"]:
            continue
        md.append("")
        md.append("## %d. %s → %s" % (n, g["module_title"], g["target"]))
        md.append("")
        md.append("- Перехваты: %s." % who(g))
        md.append("- Модуль: `%s`, строки old %s, new %s." % (g["module"], g["old_lines"], g["new_lines"]))
        if g.get("target_old"):
            md.append("- Вендор перепривязал событие: в old обработчик `%s`." % g["target_old"])
        if g["sig_changed"]:
            md.append("- Сигнатура old: `%s` → new: `%s`." % (g["sig_old"], g["sig_new"]))
        if g["hunks"]:
            md.append("- Вид сравнения: %s; %s." % (g["view"], g["status"]))
            md.append("")
            md.append("```diff")
            for h in g["hunks"]:
                md.append("@@ old %s → new %s @@" % (span(h["old"], h["at_old"]), span(h["new"], h["at_new"])))
                md.extend("-%d: %s" % (k, t) for k, t in h["old"])
                md.extend("+%d: %s" % (k, t) for k, t in h["new"])
            md.append("```")
    return "\n".join(md)


def main(argv=None):
    fileio.console_utf8()
    ap = argparse.ArgumentParser(description="Vendor hunks old -> new of every intercepted method")
    ap.add_argument("--ext", required=True, help="extension dumps root: one folder per extension (or one dump)")
    ap.add_argument("--old", required=True, help="vendor dump of the old release")
    ap.add_argument("--new", required=True, help="vendor dump of the new release")
    ap.add_argument("--out", required=True, help="report folder")
    ap.add_argument("--only", help="only this extension (name from Configuration.xml or folder name)")
    ap.add_argument("--method", help="only this vendor method (interception target)")
    args = ap.parse_args(argv)
    refuse_out_inside(args.out, (("--ext", args.ext), ("--old", args.old), ("--new", args.new)), "method_diff")
    t0 = time.time()
    exts = metadata.find_extensions([args.ext], [args.only] if args.only else None)
    if not exts:
        sys.exit("no extension dumps found under --ext" + (" for --only %s" % args.only if args.only else ""))
    metadata.check_config(args.old, "--old")
    metadata.check_config(args.new, "--new")
    cfg_old, cfg_new = metadata.Dump(args.old, "old"), metadata.Dump(args.new, "new")
    by_name = {e.name: e for e in exts}
    groups = [analyse(g, cfg_old, cfg_new, by_name.get(g["interceptors"][0]["ext"]))
              for g in collect(exts, args.method, cfg_old, cfg_new)]
    elapsed = time.time() - t0
    md_path, _ = fileio.write_report(args.out, "report", render(args, groups, elapsed), {
        "ext": args.ext, "old": args.old, "new": args.new, "only": args.only, "method": args.method,
        "methods": groups})
    n_int = sum(len(g["interceptors"]) for g in groups)
    print("interceptors: %d, vendor methods: %d; body changed %d, unchanged %d, other %d; hunk lines %d; report: %s" % (
        n_int, len(groups), sum(1 for g in groups if g["hunks"]),
        sum(1 for g in groups if g["status"] == "тело не изменилось"),
        sum(1 for g in groups if not g["hunks"] and g["status"] != "тело не изменилось"),
        sum(len(h["old"]) + len(h["new"]) + 1 for g in groups for h in g["hunks"]), md_path))


if __name__ == "__main__":
    main()
