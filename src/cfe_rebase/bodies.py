# -*- coding: utf-8 -*-
"""Frozen copies: every &Вместо without ПродолжитьВызов in the extensions, compared with the base method
of the old and the new configuration release.

For each copy: line ranges of the copy, base old and base new; line diffs old->copy (author edits and older
drift), old->new (what the vendor changed), copy->new (what porting has to reconcile); similarity ratios
copy~old, copy~new, old~new; a basis hint (rebased on new / old plus edits / older than old or rewritten);
the vendor hunks old->new classified against the copy (is the vendor change already in the copy or not).
Lines are compared with whitespace collapsed and empty lines dropped; --code-only also drops comments.
"""
import argparse
import os
import sys
import time
from collections import Counter

from . import bsl, fileio, metadata
from .control_rebase import refuse_out_inside

BASIS = {"new": "перенесена на new", "old": "копия old + правки", "older": "старше old или переписана",
         "same-old": "= old", "same-new": "= new"}


def body(module, method, code_only):
    """[(line, text)] from the declaration to the end, whitespace collapsed, empty lines dropped."""
    if code_only:
        return bsl.body_code(module, method)
    out = []
    for k in range(method.decl_line, method.end_line + 1):
        text = bsl.norm_ws(module.lines[k - 1])
        if text:
            out.append((k, text))
    return out


def three_way(old, copy, new):
    """Vendor hunks old->new classified against the copy."""
    sm_v = bsl._matcher(old, new)
    sm_c = bsl._matcher(old, copy)
    kept = set()
    for tag, i1, i2, j1, j2 in sm_c.get_opcodes():
        if tag == "equal":
            kept.update(range(i1, i2))
    copy_texts = {t for _, t in copy}
    res = []
    for tag, i1, i2, j1, j2 in sm_v.get_opcodes():
        if tag == "equal":
            continue
        new_part = new[j1:j2]
        new_in_copy = sum(1 for _, t in new_part if t in copy_texts)
        if i2 > i1:
            k = sum(1 for i in range(i1, i2) if i in kept)
            if new_part and new_in_copy == len(new_part) and k == 0:
                cls = "есть в копии"
            elif k == i2 - i1:
                cls = "ДРЕЙФ: копия держит текст old"
            elif k == 0:
                cls = "автор тоже менял это место"
            else:
                cls = "частично: часть строк old в копии"
        else:
            cls = "вставка new уже есть в копии" if new_part and new_in_copy == len(new_part) else \
                "ДРЕЙФ: вставки new нет в копии"
        res.append({"class": cls, "old": old[i1:i2], "new": new_part, "span": (i1, i2, j1, j2)})
    mark_moves(res, old, copy, new, kept)
    return res


MOVE_HINT = " (похоже на перемещение вендора: сверить порядок строк в копии)"
MOVE_CARRIED = "перемещение вендора: в копии порядок new"
MOVE_DRIFT = "ДРЕЙФ: перемещение вендора не перенесено, в копии порядок old"


def mark_moves(hunks, old, copy, new, kept):
    """A vendor move is a pure delete and a pure insert of the same lines. The two alignments (old->new and
    old->copy) resolve it independently, so a copy already rebased on new reads exactly like a stale one.
    The copy is judged by the order it keeps: an alignment preserves order, so the moved lines together with the
    lines they jumped over can all be matched only against the side whose order the copy follows. More of them
    matched old->copy - drift; more matched new->copy - the move is carried over; a tie (the author rewrote the
    jumped-over block) proves nothing, and the pair keeps its classes with a hint.
    Only blocks with a line that occurs once in old and once in new count: a lone КонецЕсли; proves nothing."""
    old_count = Counter(t for _, t in old)
    new_count = Counter(t for _, t in new)
    new_kept = None
    paired = set()
    for d in hunks:
        if d["new"] or not d["old"]:
            continue
        texts = [t for _, t in d["old"]]
        if not any(old_count[t] == 1 and new_count[t] == 1 for t in texts):
            continue
        ins = next((h for h in hunks if id(h) not in paired and not h["old"] and h["new"]
                    and [t for _, t in h["new"]] == texts), None)
        if ins is None:
            continue
        paired.add(id(ins))
        if new_kept is None:
            new_kept = set()
            for tag, a1, a2, _, _ in bsl._matcher(new, copy).get_opcodes():
                if tag == "equal":
                    new_kept.update(range(a1, a2))
        i1, i2 = d["span"][:2]
        at, _, j1, j2 = ins["span"]
        if at <= i1:
            jumped = i1 - at
            old_span, new_span = range(at, i2), range(j1, j2 + jumped)
        else:
            jumped = at - i2
            old_span, new_span = range(i1, at), range(j1 - jumped, j2)
        by_old = sum(1 for i in old_span if i in kept)
        by_new = sum(1 for j in new_span if j in new_kept)
        if by_new > by_old:
            d["class"] = ins["class"] = MOVE_CARRIED
        elif by_old > by_new:
            d["class"] = ins["class"] = MOVE_DRIFT
        else:
            d["class"] += MOVE_HINT
            ins["class"] += MOVE_HINT


def basis(s_co, s_cn, s_on, len_old):
    if s_co["hunks"] == 0:
        return "same-old"
    if s_cn["hunks"] == 0:
        return "same-new"
    if s_cn["ratio"] > s_co["ratio"] + 0.01:
        return "new"
    removed_share = s_co["removed"] / float(len_old or 1)
    return "older" if removed_share > 0.05 else "old"


def hunks_md(hunks, la, lb, limit):
    out = []
    shown = 0
    for h in hunks:
        if shown >= limit:
            out.append("… ещё %d блоков" % (len(hunks) - shown))
            break
        out.append("@@ %s" % h["tag"])
        for ln, t in h["a"]:
            out.append("- %s:%d  %s" % (la, ln, t))
        for ln, t in h["b"]:
            out.append("+ %s:%d  %s" % (lb, ln, t))
        shown += 1
    return out


def analyze(args):
    metadata.check_config(args.config_new, "--config-new")
    metadata.check_config(args.config_old, "--config-old")
    cfg_new = metadata.Dump(args.config_new, "new")
    cfg_old = metadata.Dump(args.config_old, "old")
    exts = metadata.find_extensions(args.ext_root, args.ext, args.skip)
    if not exts:
        sys.exit("no extension dumps found under --ext-root")
    cases = []
    for ext in exts:
        for rel in ext.files(".bsl"):
            mod = ext.module(rel)
            if mod is None:
                continue
            for m in mod.methods:
                for kind, target, ann_line in m.interceptors:
                    if kind != "Вместо" or m.has_proceed:
                        continue
                    new_mod, old_mod = cfg_new.module(rel), cfg_old.module(rel)
                    t_new = new_mod.find(target) if new_mod else None
                    t_old = old_mod.find(target) if old_mod else None
                    case = {"ext": ext.name, "module": rel, "module_title": metadata.module_title(rel),
                            "target": target, "copy_method": m.name, "copy_lines": [m.decl_line, m.end_line],
                            "old_lines": [t_old.decl_line, t_old.end_line] if t_old else None,
                            "new_lines": [t_new.decl_line, t_new.end_line] if t_new else None}
                    if t_new is None or t_old is None:
                        case["note"] = "нет базового метода в %s" % " и ".join(
                            x for x, t in (("old", t_old), ("new", t_new)) if t is None)
                        cases.append(case)
                        continue
                    c = body(mod, m, args.code_only)
                    o = body(old_mod, t_old, args.code_only)
                    n = body(new_mod, t_new, args.code_only)
                    # ratios and basis on the body without the declaration line: the copy always has another name
                    s_co, s_cn, s_on = bsl.diff_stats(o[1:], c[1:]), bsl.diff_stats(n[1:], c[1:]), \
                        bsl.diff_stats(o[1:], n[1:])
                    tw = three_way(o, c, n)
                    case.update({
                        "len": {"copy": len(c), "old": len(o), "new": len(n)},
                        "ratio": {"copy~old": s_co["ratio"], "copy~new": s_cn["ratio"], "old~new": s_on["ratio"]},
                        "change": {"old->copy": bsl.change_label(s_co), "old->new": bsl.change_label(s_on),
                                   "copy->new": bsl.change_label(bsl.diff_stats(c[1:], n[1:]))},
                        "basis": basis(s_co, s_cn, s_on, len(o) - 1),
                        "vendor_hunks": len(tw),
                        "drift_hunks": sum(1 for h in tw if h["class"].startswith("ДРЕЙФ")),
                        "diff_old_copy": bsl.diff_hunks(o, c), "diff_old_new": bsl.diff_hunks(o, n),
                        "diff_copy_new": bsl.diff_hunks(c, n), "three_way": tw})
                    cases.append(case)
    return cfg_new, cfg_old, exts, cases


def render(cfg_new, cfg_old, exts, cases, args, elapsed):
    md = ["# Копии тел `&Вместо` без `ПродолжитьВызов`", ""]
    md.append("new: `%s` (%s), old: `%s` (%s). Расширений: %d, копий: %d. Сравнение: %s. Время: %.1f с." % (
        cfg_new.root, cfg_new.version, cfg_old.root, cfg_old.version, len(exts), len(cases),
        "только код (без комментариев)" if args.code_only else "строки с комментариями, пробелы схлопнуты",
        elapsed))
    md.append("")
    md.append("Основа копии (`basis`): **перенесена на new** — copy~new выше copy~old; **копия old + правки** — из old "
              "удалено не больше 5 % строк; **старше old или переписана** — удалено больше: копия снята с более "
              "раннего релиза, либо автор переписал тело (эвристика, различить их по трём версиям нельзя). "
              "Коэффициенты и основа считаются по телу без строки объявления (у копии всегда другое имя), "
              "диффы — с ней. ДРЕЙФ — изменения вендора old→new, которых нет в копии.")
    md.append("")
    md.append(fileio.md_table(
        ["Расширение", "Модуль", "Цель", "Копия / old / new (строки)", "copy~old", "copy~new", "old~new",
         "old→copy", "old→new", "Основа", "ДРЕЙФ"],
        [(c["ext"], c["module_title"], c["target"],
          "%s / %s / %s" % tuple("%d–%d" % tuple(x) if x else "—" for x in (c["copy_lines"], c["old_lines"],
                                                                          c["new_lines"])),
          c.get("ratio", {}).get("copy~old", "—"), c.get("ratio", {}).get("copy~new", "—"),
          c.get("ratio", {}).get("old~new", "—"), c.get("change", {}).get("old->copy", "—"),
          c.get("change", {}).get("old->new", "—"), BASIS.get(c.get("basis"), c.get("note", "—")),
          "%d из %d" % (c["drift_hunks"], c["vendor_hunks"]) if "drift_hunks" in c else "—") for c in cases]))
    for c in cases:
        if "diff_old_copy" not in c:
            continue
        md.append("")
        md.append("## %s — %s → %s" % (c["ext"], c["module_title"], c["target"]))
        md.append("")
        md.append("Копия `%s` %s:%d–%d; old %d–%d; new %d–%d." % (
            c["copy_method"], c["module"], c["copy_lines"][0], c["copy_lines"][1], c["old_lines"][0],
            c["old_lines"][1], c["new_lines"][0], c["new_lines"][1]))
        for title, key, la, lb in (("old → копия (правки автора и более старая основа)", "diff_old_copy", "old", "copy"),
                                   ("old → new (изменения вендора)", "diff_old_new", "old", "new"),
                                   ("копия → new (что сводить при переносе)", "diff_copy_new", "copy", "new")):
            md.append("")
            md.append("### %s: %d блоков" % (title, len(c[key])))
            md.append("")
            md.append("```")
            md.extend(hunks_md(c[key], la, lb, args.max_hunks) or ["(нет различий)"])
            md.append("```")
        md.append("")
        md.append("### Изменения вендора против копии")
        md.append("")
        if not c["three_way"]:
            md.append("Вендор тело не менял.")
        for h in c["three_way"]:
            md.append("- **%s**: old %s → new %s" % (
                h["class"], ", ".join(str(ln) for ln, _ in h["old"][:6]) or "—",
                ", ".join(str(ln) for ln, _ in h["new"][:6]) or "—"))
            for ln, t in h["new"][:3]:
                md.append("  - new:%d `%s`" % (ln, t[:150].replace("`", "'")))
    return "\n".join(md)


def main(argv=None):
    fileio.console_utf8()
    ap = argparse.ArgumentParser(description="Frozen &Вместо copies against base old and new")
    ap.add_argument("--ext-root", action="extend", nargs="+", required=True)
    ap.add_argument("--ext", action="extend", nargs="+")
    ap.add_argument("--skip", action="extend", nargs="+")
    ap.add_argument("--config-old", required=True)
    ap.add_argument("--config-new", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--code-only", action="store_true", help="drop comments before comparing")
    ap.add_argument("--max-hunks", type=int, default=60, help="diff blocks per section in Markdown (JSON has all)")
    args = ap.parse_args(argv)
    refuse_out_inside(args.out, [("--ext-root", r) for r in args.ext_root] +
                      [("--config-old", args.config_old), ("--config-new", args.config_new)], "bodies")
    t0 = time.time()
    cfg_new, cfg_old, exts, cases = analyze(args)
    elapsed = time.time() - t0
    md = render(cfg_new, cfg_old, exts, cases, args, elapsed)
    md_path, json_path = fileio.write_report(args.out, "bodies", md, {
        "config_new": cfg_new.root, "config_old": cfg_old.root, "code_only": args.code_only, "cases": cases})
    print("extensions: %d, copies: %d, %.1fs" % (len(exts), len(cases), elapsed))
    for c in cases:
        print("  %-40s %-45s %s" % (c["ext"][:40], c["target"][:45], BASIS.get(c.get("basis"), c.get("note"))))
    print(md_path)
    print(json_path)


if __name__ == "__main__":
    main()
