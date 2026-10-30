# -*- coding: utf-8 -*-
"""Rebase every &ИзменениеИКонтроль of the extension dumps onto the new vendor release by a three-way merge and prove
the result passes the platform control (porting.md 4.4, &ИзменениеИКонтроль).

    cfe-rebase control-rebase --ext <extension dumps root> --old <vendor old dump> --new <vendor new dump> --out <dir>
                             [--work <work copy root>] [--only <extension name>]

Per intercepted method: base = the vendor body in the old release, ours = in the new release, theirs = the extension
body with its #Вставка/#Удаление markers; merge_copy.merge_bodies (git merge-file --diff3) carries the author's markers
onto the new body. The control view (bsl: #Вставка blocks dropped, #Удаление contents kept, lines trimmed, empty lines
dropped, comments kept) of the extension is checked against old before the merge and of the result against new after
it. A candidate is offered (<out>/<extension>/<method>.bsl, the whole method: annotations, header, body, end; BOM and
line endings of the extension module) only when the control passes, the signature fits new, no author line is lost and
no line is duplicated. Otherwise the file carries a suffix: .conflicts.bsl (diff3 markers new / old / copy - resolve
only these), .fail.bsl, .out-of-sync.bsl. --work compares with what was ported by hand. Dumps are never written.
UNCHANGED whose extension header does not fit new (sig_ext_vs_new: the vendor signature did not change, so it did not
fit old either) carries a note and problems: nothing to port in the body, but the method is not ready.
"""
import argparse
import os
import re
import sys
import time
from collections import Counter, OrderedDict

from . import bsl, fileio, metadata
from . import merge_copy
from .interceptors import sig_problems

KIND = "ИзменениеИКонтроль"
VERDICTS = OrderedDict([
    ("UNCHANGED", "тело вендора old→new в виде контроля не изменилось — переносить нечего; с problems (сигнатура) "
                  "заголовок метода расширения не подходит к new — метод не готов, заголовок править руками"),
    ("ALREADY_NEW", "текст расширения по контролю уже совпадает с new — переносить нечего"),
    ("REBASED", "перенесено слиянием: контроль против new PASS, сигнатура подходит, строк автора не потеряно и не "
                "задвоено — кандидат `<метод>.bsl`"),
    ("CONFLICTS", "конфликты слияния (diff3: new / old / copy) — `<метод>.conflicts.bsl`, решить только их"),
    ("FAIL", "слияние без конфликтов, но кандидат не прошёл проверку (контроль, сигнатура, потери, дубли) — "
             "`<метод>.fail.bsl`, разбирать вручную"),
    ("OUT_OF_SYNC", "расширение и до обновления не совпадало с old по контролю: основа слияния неверна — "
                    "`<метод>.out-of-sync.bsl` только как подсказка"),
    ("MISSING", "нет модуля или метода вендора в old или в new"),
])
SUFFIX = {"REBASED": "", "CONFLICTS": ".conflicts", "FAIL": ".fail", "OUT_OF_SYNC": ".out-of-sync"}
# nothing to port; ready only without problems (UNCHANGED with a header that does not fit new carries problems)
PASS_VERDICTS = ("UNCHANGED", "ALREADY_NEW")


def ready(row):
    """The method passes the platform control on new as it is: nothing to port and the signature fits."""
    return row["verdict"] in PASS_VERDICTS and not row.get("problems") and not row.get("sig_ext_vs_new")


def control_view(lines):
    """Platform control view of raw lines: #Вставка blocks dropped, #Удаление contents kept, trimmed, no empty lines."""
    out, state = [], None
    for raw in lines:
        if bsl.MARK_INS.match(raw):
            state = "ins"
            continue
        if bsl.MARK_DEL.match(raw):
            state = "del"
            continue
        if bsl.MARK_INS_END.match(raw) or bsl.MARK_DEL_END.match(raw):
            state = None
            continue
        if state == "ins":
            continue
        s = raw.strip(" \t﻿")
        if s:
            out.append(s)
    return out


def _is_marker(s):
    return bsl.MARK_INS.match(s) or bsl.MARK_DEL.match(s) or bsl.MARK_INS_END.match(s) or bsl.MARK_DEL_END.match(s)


def effective_view(lines):
    """What runs: #Удаление blocks and markers dropped, trimmed, no empty lines (comments kept)."""
    out, deleting = [], False
    for raw in lines:
        if bsl.MARK_DEL.match(raw):
            deleting = True
            continue
        if bsl.MARK_DEL_END.match(raw):
            deleting = False
            continue
        if deleting or _is_marker(raw):
            continue
        s = raw.strip(" \t﻿")
        if s:
            out.append(s)
    return out


def trimmed(lines):
    return [s.strip(" \t﻿") for s in lines if s.strip(" \t﻿")]


def first_diff(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return "строка %d: «%s» | «%s»" % (i + 1, x[:70], y[:70])
    return "длина %d против %d" % (len(a), len(b))


def parts(mod, m):
    """Annotation lines, header lines (declaration .. closing parenthesis), body lines (after it .. end line)."""
    return (mod.lines[m.start_line - 1:m.decl_line - 1], mod.lines[m.decl_line - 1:m.decl_end_line],
            mod.lines[m.decl_end_line:m.end_line])


def rename(lines, old, new):
    """Method name in the declaration line replaced."""
    if not lines:
        return lines
    pat = re.compile(r"(?<![\wЁё])" + re.escape(old) + r"(?=\s*\()", re.IGNORECASE)
    return [pat.sub(new, lines[0], count=1)] + list(lines[1:])


def rebase_header(old_h, new_h, ext_h, vendor_name, ext_name):
    """(header lines, how, conflicts). The extension keeps its header while the vendor header is unchanged; a vendor
    change is taken from new with the extension name when the extension header is the old one renamed."""
    if trimmed(old_h) == trimmed(new_h):
        return list(ext_h), "из расширения", 0
    if [bsl.norm_ws(s) for s in trimmed(rename(old_h, vendor_name, ext_name))] == \
            [bsl.norm_ws(s) for s in trimmed(ext_h)]:
        return rename(new_h, vendor_name, ext_name), "из new с именем расширения", 0
    res = merge_copy.merge_bodies(old_h, new_h, ext_h)
    return res["lines"], "слит (заголовок расширения отличался от old)", res["conflicts"]


def unexplained_duplicates(old, new, copy, merged, dup):
    """merge_copy.duplicated caps a line at max(new, copy) occurrences, so a `КонецЕсли;` the author inserted into a
    body where the vendor also added one reads as a duplicate. Here the cap is new + what the author added on top of
    old; only lines above it stay."""
    cnt = lambda lines: Counter(merge_copy._norm(s) for s in lines)  # noqa: E731
    c_old, c_new, c_copy = cnt(old), cnt(new), cnt(copy)
    out = {}
    for side in ("new", "copy"):
        c_res = cnt(merge_copy.resolve(merged, side))
        out[side] = [s for s in dup[side] if c_res[merge_copy._norm(s)] >
                     c_new[merge_copy._norm(s)] + max(0, c_copy[merge_copy._norm(s)] - c_old[merge_copy._norm(s)])]
    return out


def conflict_blocks(lines):
    """[(first line number in the body, [diff3 lines])]"""
    out, cur = [], None
    for n, s in enumerate(lines, 1):
        if s.startswith("<<<<<<<"):
            cur = (n, [s])
        elif cur is not None:
            cur[1].append(s)
            if s.startswith(">>>>>>>"):
                out.append(cur)
                cur = None
    return out


def find_ick(mod, target, prefer=None):
    """&ИзменениеИКонтроль method of a module for the target; prefer - the method name to take among several."""
    found = [m for m in mod.methods for k, t, _ in m.interceptors
             if k == KIND and (t or "").casefold() == target.casefold()]
    for m in found:
        if prefer and m.name.casefold() == prefer.casefold():
            return m
    return found[0] if found else None


def other_interceptors(mod, target):
    return sorted({"&%s %s" % (k, m.name) for m in mod.methods for k, t, _ in m.interceptors
                   if k != KIND and (t or "").casefold() == target.casefold()})


def compare_work(work_dump, rel, target, ext_method, expected, new_body):
    """Hand-made port in the work copy against the expected (header, body): the candidate, or the extension as is.
    SAME - equal up to indentation and empty lines; SAME_EFFECTIVE - the same control view, the same running code
    and header, differences only in marker placement or comments inside #Удаление/#Вставка."""
    wmod = work_dump.module(rel) if work_dump else None
    if wmod is None:
        return {"status": "нет модуля в work"}
    wm = find_ick(wmod, target, ext_method)
    if wm is None:
        other = other_interceptors(wmod, target)
        return {"status": "в work нет &ИзменениеИКонтроль на метод" + (" (есть: %s)" % ", ".join(other) if other
                                                                         else "")}
    _, wh, wb = parts(wmod, wm)
    res = {"method": wm.name, "lines": "%d-%d" % (wm.start_line, wm.end_line)}
    if new_body is not None:
        res["control"] = "PASS" if control_view(wb) == control_view(new_body) else \
            "FAIL " + first_diff(control_view(wb), control_view(new_body))
    if expected is None:
        res["status"] = "контроль work против new: " + res.get("control", "—")
        return res
    eh, eb = expected
    if trimmed(wh + wb) == trimmed(eh + eb):
        res["status"] = "SAME"
    elif [bsl.norm_ws(s) for s in trimmed(wh)] == [bsl.norm_ws(s) for s in trimmed(eh)] and \
            control_view(wb) == control_view(eb) and effective_view(wb) == effective_view(eb):
        res["status"] = "SAME_EFFECTIVE"
    else:
        res["status"] = "DIFFERS " + first_diff(trimmed(eh + eb), trimmed(wh + wb))
    res["status"] += "; контроль work против new: " + res.get("control", "—")
    return res


def process(args, ext, cfg_old, cfg_new, work_dump):
    items = []
    for rel in ext.files(".bsl"):
        mod = ext.module(rel)
        if mod is None:
            continue
        for m in mod.methods:
            for kind, target, _ in m.interceptors:
                if kind == KIND and target:
                    items.append((rel, mod, m, target))
    # one candidate file per target name; a name met in several modules of the extension gets the module title
    twice = {t for t, c in Counter(t.casefold() for _, _, _, t in items).items() if c > 1}
    return [one(args, ext, rel, mod, m, target, cfg_old, cfg_new, work_dump, target.casefold() in twice)
            for rel, mod, m, target in items]


def one(args, ext, rel, mod, m, target, cfg_old, cfg_new, work_dump, qualify):
    row = OrderedDict([("ext", ext.name), ("module", rel), ("module_title", metadata.module_title(rel)),
                       ("target", target), ("ext_method", m.name), ("ext_lines", "%d-%d" % (m.start_line, m.end_line))])
    # a vendor rename keeps the uuid: the module of a renamed object is under its name in each release
    vo = cfg_old.module(metadata.counterpart_rel(ext, rel, cfg_old)[0])
    vn = cfg_new.module(metadata.counterpart_rel(ext, rel, cfg_new)[0])
    # the work copy keeps the own uuid of the object when its adoption is renamed
    wrel = metadata.counterpart_rel(ext, rel, work_dump)[0] if work_dump is not None else rel
    mo = vo.find(target) if vo else None
    mn = vn.find(target) if vn else None
    ann, ext_h, ext_body = parts(mod, m)
    if mo is None or mn is None:
        row["verdict"] = "MISSING"
        row["note"] = "old: %s, new: %s" % ("есть" if mo else ("нет метода" if vo else "нет модуля"),
                                           "есть" if mn else ("нет метода" if vn else "нет модуля"))
        if work_dump is not None:
            row["work"] = compare_work(work_dump, wrel, target, m.name, None, parts(vn, mn)[2] if mn else None)
            row["work"]["ext"] = work_dump.name
        return row
    _, old_h, old_body = parts(vo, mo)
    _, new_h, new_body = parts(vn, mn)
    row["sig_old"], row["sig_new"] = mo.signature(), mn.signature()
    row["sig_changed"] = mo.sig_key() != mn.sig_key()
    row["sig_ext_vs_new"] = sig_problems(m, mn)
    c_ext, c_old, c_new = control_view(ext_body), control_view(old_body), control_view(new_body)
    row["pre"] = "ok" if c_ext == c_old else first_diff(c_ext, c_old)
    st = bsl.diff_stats([(0, s) for s in c_old], [(0, s) for s in c_new])
    row["vendor"] = bsl.change_label(st)
    code_old = [t for k, t in bsl.body_code(vo, mo) if k > mo.decl_end_line]
    code_new = [t for k, t in bsl.body_code(vn, mn) if k > mn.decl_end_line]
    row["vendor_code_only"] = bsl.change_label(bsl.diff_stats([(0, s) for s in code_old], [(0, s) for s in code_new]))
    expected = None
    if c_ext == c_new and not row["sig_ext_vs_new"]:
        row["verdict"] = "UNCHANGED" if st["hunks"] == 0 else "ALREADY_NEW"
        expected = (ext_h, ext_body)
    elif st["hunks"] == 0 and row["pre"] == "ok" and not row["sig_changed"]:
        # nothing to port in the body, but the extension header does not fit new (nor old: the vendor signature is
        # the same): the platform will not bind the interceptor - not ready, check_control fails it
        row["verdict"] = "UNCHANGED"
        row["note"] = "тело переносить не нужно, но заголовок метода расширения не подходит к new"
        row["problems"] = ["сигнатура против new: " + "; ".join(row["sig_ext_vs_new"])]
        expected = (ext_h, ext_body)
    else:
        header, how, h_conf = rebase_header(old_h, new_h, ext_h, mo.name, m.name)
        res = merge_copy.merge_bodies(old_body, new_body, ext_body)
        merged = res["lines"]
        cand = ann + header + merged
        row["header"] = how
        row["conflicts"] = res["conflicts"] + h_conf
        row["conflict_blocks"] = conflict_blocks(cand) if row["conflicts"] else []
        if res["kinds"]:
            row["conflict_kinds"] = res["kinds"]  # body conflicts only: they follow the header ones in the file
        row["lost"] = merge_copy.lost_lines(old_body, ext_body, merge_copy.resolve(merged, "copy"))
        row["duplicated"] = merge_copy.duplicated(new_body, ext_body, merged)
        row["duplicated_unexplained"] = unexplained_duplicates(old_body, new_body, ext_body, merged, row["duplicated"])
        problems = []
        if not row["conflicts"]:
            post = control_view(merged)
            row["post"] = "PASS" if post == c_new else "FAIL " + first_diff(post, c_new)
            cand_mod = bsl.Module("\n".join(ann + header + merged))
            cm = cand_mod.methods[0] if cand_mod.methods else None
            sig = ["заголовок не разобран"] if cm is None else sig_problems(cm, mn)
            row["sig_candidate_vs_new"] = sig
            if row["post"] != "PASS":
                problems.append("контроль против new: " + row["post"])
            if sig:
                problems.append("сигнатура против new: " + "; ".join(sig))
        if row["lost"]:
            problems.append("потеряно строк автора: %d" % len(row["lost"]))
        dup = row["duplicated_unexplained"]
        if dup["new"] or dup["copy"]:
            problems.append("задвоится new/copy: %d/%d" % (len(dup["new"]), len(dup["copy"])))
        row["problems"] = problems
        if row["pre"] != "ok":
            row["verdict"] = "OUT_OF_SYNC"
        elif row["conflicts"]:
            row["verdict"] = "CONFLICTS"
        elif problems:
            row["verdict"] = "FAIL"
        else:
            row["verdict"] = "REBASED"
            expected = (header, merged)
        base = "%s@%s" % (target, row["module_title"]) if qualify else target
        fname = re.sub(r'[<>:"/\\|?*]', "_", base) + SUFFIX[row["verdict"]] + ".bsl"
        path = os.path.join(args.out, re.sub(r'[<>:"/\\|?*]', "_", ext.name), fname)
        fileio.write_like(path, "\n".join(cand) + "\n", ext.path(rel))
        row["file"] = os.path.relpath(path, args.out).replace("\\", "/")
        row["replaces"] = "строки %d-%d модуля расширения" % (m.start_line, m.end_line)
    if work_dump is not None:
        row["work"] = compare_work(work_dump, wrel, target, m.name, expected, new_body)
        row["work"]["ext"] = work_dump.name
    return row


CFG_UUID_RE = re.compile(r"<Configuration\s+uuid=\"([0-9a-fA-F-]+)\"")


def cfg_uuid(dump):
    """uuid of the extension's Configuration object: it survives renaming the extension (name + date + version)."""
    p = os.path.join(dump.root, "Configuration.xml")
    if not os.path.isfile(p):
        return None
    with open(p, "rb") as fh:
        m = CFG_UUID_RE.search(fh.read(4000).decode("utf-8", "replace"))
    return m.group(1).lower() if m else None


def work_index(root):
    """Extension dumps of the work copy by folder name, by name and by Configuration uuid."""
    idx = {}
    for d in metadata.find_extensions([root]):
        for k in (os.path.basename(d.root).casefold(), d.name.casefold(), cfg_uuid(d)):
            if k:
                idx.setdefault(k, d)
    return idx


def inside(path, root):
    path, root = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
    return path == root or path.startswith(root.rstrip("\\/") + os.sep)


def refuse_out_inside(out, roots, tool):
    """Exit when --out is inside one of roots ([(flag, path or None)]): a report never goes into a dump."""
    for label, root in roots:
        if root and inside(out, root):
            sys.exit("--out %s is inside %s %s: %s never writes into dumps" % (out, label, root, tool))


def render(args, rows, counts, elapsed):
    md = ["# Перенос `&ИзменениеИКонтроль` на новый релиз трёхсторонним слиянием", ""]
    md.append("Расширения: `%s`%s. Вендор old: `%s`, new: `%s`.%s Время: %.1f с." % (
        args.ext, " (только `%s`)" % args.only if args.only else "", args.old, args.new,
        " Сравнение с ручным переносом: `%s`." % args.work if args.work else "", elapsed))
    md.append("")
    md.append(fileio.md_table(["Вердикт", "Методов", "Смысл"], [(v, counts.get(v, 0), t) for v, t in VERDICTS.items()]))
    md.append("")
    md.append("Контроль — построчное сравнение без `#Вставка`, с содержимым `#Удаление`, края строк обрезаны, пустые "
              "строки выброшены, комментарии входят. Кандидат заменяет метод целиком (аннотации, заголовок, тело, "
              "конец) — строки, указанные в отчёте; BOM и концы строк как у модуля расширения.")
    md.append("")
    headers = ["№", "Расширение", "Модуль", "Метод вендора", "Метод расширения", "Сигнатура old→new",
               "Контроль до (расш. ↔ old)", "Вендор old→new (контроль / код)", "Вердикт", "Конфликтов",
               "Потеряно строк автора", "Задвоится new/copy", "Файл"]
    if args.work:
        headers.append("Ручной перенос (work)")
    table = []
    for n, r in enumerate(rows, 1):
        cells = [n, r["ext"], r["module_title"], r["target"], "%s (стр. %s)" % (r["ext_method"], r["ext_lines"]),
                 ("изменилась" if r.get("sig_changed") else "та же") if "sig_old" in r else "—",
                 r.get("pre", "—"), "%s / %s" % (r["vendor"], r["vendor_code_only"]) if "vendor" in r else "—",
                 r["verdict"] + (" (%s)" % r["note"] if r.get("note") else ""),
                 r.get("conflicts", "—"), len(r["lost"]) if "lost" in r else "—",
                 "%d/%d" % (len(r["duplicated_unexplained"]["new"]), len(r["duplicated_unexplained"]["copy"]))
                 if "duplicated" in r else "—",
                 r.get("file", "—")]
        if args.work:
            cells.append(r.get("work", {}).get("status", "—"))
        table.append(cells)
    md.append(fileio.md_table(headers, table))
    for n, r in enumerate(rows, 1):
        if r["verdict"] in ("UNCHANGED", "ALREADY_NEW", "MISSING") and not r.get("sig_ext_vs_new"):
            continue
        md.append("")
        md.append("## %d. %s → %s (%s)" % (n, r["module_title"], r["target"], r["ext"]))
        md.append("")
        if r["verdict"] in PASS_VERDICTS and r.get("problems"):
            md.append("- Вердикт: **%s**, но метод не готов: %s." % (r["verdict"], r["note"]))
        else:
            md.append("- Вердикт: **%s**. %s" % (r["verdict"], VERDICTS[r["verdict"]]))
        if r.get("file"):
            md.append("- Файл: `%s`, заменяет %s; заголовок — %s." % (r["file"], r["replaces"], r["header"]))
        if r.get("sig_changed"):
            md.append("- Сигнатура old: `%s` → new: `%s`." % (r["sig_old"], r["sig_new"]))
        if r.get("sig_ext_vs_new"):
            md.append("- Метод расширения против new: %s." % "; ".join(r["sig_ext_vs_new"]))
        if r.get("pre") not in (None, "ok"):
            md.append("- Контроль до обновления (расширение против old) не сходится: %s." % r["pre"])
        for p in r.get("problems", []):
            md.append("- %s" % p)
        dup, dup_all = r.get("duplicated_unexplained") or {}, r.get("duplicated") or {}
        for title, items in (("Возможно потеряны строки автора", r.get("lost") or []),
                             ("Задвоится при выборе стороны new", dup.get("new") or []),
                             ("Задвоится при выборе стороны копии", dup.get("copy") or [])):
            if items:
                md.append("- %s:" % title)
                md.extend("  - `%s`" % s.strip().replace("`", "'") for s in items)
        explained = len(dup_all.get("new") or []) + len(dup_all.get("copy") or []) - \
            len(dup.get("new") or []) - len(dup.get("copy") or [])
        if explained:
            md.append("- Повторов, объяснённых строками, которые добавил автор (не дубли): %d." % explained)
        blocks = r.get("conflict_blocks") or []
        kinds = r.get("conflict_kinds") or []
        first_body = len(blocks) - len(kinds)
        for k, (line, block) in enumerate(blocks, 1):
            label = merge_copy.kind_label(kinds[k - 1 - first_body], control=True) if k > first_body else ""
            md.append("")
            md.append("Конфликт %d (строка %d файла)%s:" % (k, line, label))
            md.append("")
            md.append("```")
            md.extend(block)
            md.append("```")
    return "\n".join(md)


def main(argv=None):
    fileio.console_utf8()
    ap = argparse.ArgumentParser(description="Rebase &ИзменениеИКонтроль onto the new vendor release (git merge-file)")
    ap.add_argument("--ext", required=True, help="extension dumps root: one folder per extension (or one dump)")
    ap.add_argument("--old", required=True, help="vendor dump of the old release")
    ap.add_argument("--new", required=True, help="vendor dump of the new release")
    ap.add_argument("--out", required=True, help="report and candidates folder, outside every dump")
    ap.add_argument("--work", help="work copy root (same layout as --ext): compare with the hand-made port")
    ap.add_argument("--only", help="only this extension (name from Configuration.xml or folder name)")
    ap.add_argument("--skip", action="extend", nargs="+", help="skip these extensions (name); repeatable")
    args = ap.parse_args(argv)
    refuse_out_inside(args.out, (("--ext", args.ext), ("--old", args.old), ("--new", args.new), ("--work", args.work)),
                      "control_rebase")
    t0 = time.time()
    exts = metadata.find_extensions([args.ext], [args.only] if args.only else None, args.skip)
    if not exts:
        sys.exit("no extension dumps found under --ext" + (" for --only %s" % args.only if args.only else ""))
    metadata.check_config(args.old, "--old")
    metadata.check_config(args.new, "--new")
    cfg_old, cfg_new = metadata.Dump(args.old, "old"), metadata.Dump(args.new, "new")
    works = work_index(args.work) if args.work else {}
    rows = []
    for ext in exts:
        wd = None
        if args.work:
            wd = works.get(os.path.basename(ext.root).casefold()) or works.get(ext.name.casefold()) or \
                works.get(cfg_uuid(ext)) or metadata.Dump(os.path.join(args.work, os.path.basename(ext.root)))
        rows.extend(process(args, ext, cfg_old, cfg_new, wd))
    elapsed = time.time() - t0
    counts = Counter(r["verdict"] for r in rows)
    md = render(args, rows, counts, elapsed)
    os.makedirs(args.out, exist_ok=True)
    md_path, json_path = fileio.write_report(args.out, "report", md, {
        "ext": args.ext, "old": args.old, "new": args.new, "work": args.work, "only": args.only,
        "summary": {v: counts.get(v, 0) for v in VERDICTS}, "methods": rows})
    not_ready = sum(1 for r in rows if r["verdict"] in PASS_VERDICTS and not ready(r))
    print("methods: %d; %s%s; report: %s" % (
        len(rows), ", ".join("%s %d" % (v, counts.get(v, 0)) for v in VERDICTS),
        "; not ready (signature does not fit new): %d" % not_ready if not_ready else "", md_path))


if __name__ == "__main__":
    main()
