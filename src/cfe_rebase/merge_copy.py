# -*- coding: utf-8 -*-
"""Port one frozen &Вместо copy onto the new-release body by a three-way merge (porting.md 4.4, &Вместо).

base = the method body in the old release, ours = in the new release, theirs = the copy in the extension.
Before merging, copy lines that differ from their aligned old line only by leading/trailing whitespace take the
old text: git merge-file compares lines exactly, and a re-indented untouched line would otherwise count as an
author edit and collide with every vendor change around it. Everything else of the copy is kept verbatim.

Output goes to --out only: the merged body (conflicts in diff3 style, BOM and line endings of the copy module)
and a report with the reverse-diff control of 4.4 step 4. The extension dump is never written. The merge does
not replace the line classes of 4.2 (1 / 1a / 2) or the vendor migration path of 4.3.
"""
import argparse
import difflib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter

from . import bsl, fileio

LABELS = ("new", "old", "copy")
CONFLICT_START = re.compile(r"^<{7}(?: |$)")


def _norm(text):
    return " ".join(text.split())


def align_whitespace(old, copy):
    """Copy lines equal to their aligned old line after strip() take the old text; the rest stays verbatim.
    Internal whitespace is compared as is: a changed space inside a string literal is still an author edit."""
    sm = difflib.SequenceMatcher(None, [s.strip() for s in old], [s.strip() for s in copy], autojunk=False)
    out = list(copy)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out[j1:j2] = old[i1:i2]
    return out


def conflict_blocks(merged):
    """diff3 conflicts of a merge result: [{'start', 'end' (indexes of the marker lines), 'new', 'old', 'copy'}]."""
    blocks, cur, state = [], None, None
    for k, s in enumerate(merged):
        if CONFLICT_START.match(s):
            cur, state = {"start": k, "new": [], "old": [], "copy": []}, "new"
        elif s.startswith("|||||||") and state:
            state = "old"
        elif s.startswith("=======") and state:
            state = "copy"
        elif s.startswith(">>>>>>>") and state:
            cur["end"] = k
            blocks.append(cur)
            state = None
        elif state:
            cur[state].append(s)
    return blocks


def _ws_key(line):
    """A line for the whitespace rules: whitespace runs collapsed as in _norm, but a string literal that opens and
    closes on the line is kept as written - "x  y" and "x y" are other values. A multi-line literal (a line that opens
    it without closing it, its |-continuations) is query text, where whitespace between words does not count: it is
    collapsed there too, except inside a ""…"" string of the query. A date literal '…' is kept as written."""
    s = line.strip()
    out, i, n = [], 0, len(s)
    in_str, query, quoted = s.startswith("|"), s.startswith("|"), False
    while i < n:
        c = s[i]
        if in_str and not query:
            # a literal on one line: verbatim up to the closing quote ("" is a quote inside it)
            if c == '"' and s[i + 1:i + 2] == '"':
                out.append('""')
                i += 2
                continue
            out.append(c)
            in_str = c != '"'
            i += 1
            continue
        if in_str:
            if c == '"' and s[i + 1:i + 2] == '"':
                out.append('""')
                quoted = not quoted
                i += 2
                continue
            if c == '"':
                in_str = query = quoted = False
                out.append(c)
                i += 1
                continue
            if c.isspace() and not quoted:
                j = i
                while j < n and s[j].isspace():
                    j += 1
                out.append(" ")
                i = j
                continue
            out.append(c)
            i += 1
            continue
        if c == '"':
            j = i + 1
            while j < n and not (s[j] == '"' and s[j + 1:j + 2] != '"'):
                j += 2 if s[j] == '"' else 1
            in_str, query = True, j >= n
            out.append(c)
            i += 1
            continue
        if c == "'":
            j = s.find("'", i + 1)
            j = n - 1 if j < 0 else j
            out.append(s[i:j + 1])
            i = j + 1
            continue
        if c == "/" and s[i + 1:i + 2] == "/":
            out.append(_norm(s[i:]))
            break
        if c.isspace():
            while i < n and s[i].isspace():
                i += 1
            out.append(" ")
            continue
        out.append(c)
        i += 1
    return "".join(out).strip()


def _code(lines):
    """Lines of one side of a conflict for the whitespace rules (_ws_key), empty ones dropped."""
    return [k for k in (_ws_key(s) for s in lines) if k]


def auto_resolve(merged):
    """Conflicts that whitespace alone made, resolved: both sides equal up to whitespace -> new; one side differs from
    old only by whitespace -> the other side. Whitespace inside a string literal is not whitespace (see _ws_key): such
    a conflict stays. (lines, [{'rule', 'line'}]) - line: the 1-based line of the resolved block in the result. Only
    for &Вместо copies: the platform control of &ИзменениеИКонтроль compares texts."""
    out, resolved, last = [], [], 0
    for b in conflict_blocks(merged):
        out.extend(merged[last:b["start"]])
        new, old, copy = _code(b["new"]), _code(b["old"]), _code(b["copy"])
        rule, take = None, None
        if new == copy:
            rule, take = "ws_identical", b["new"]
        elif new == old:
            rule, take = "vendor_ws_only", b["copy"]
        elif copy == old:
            rule, take = "author_ws_only", b["new"]
        if rule is None:
            out.extend(merged[b["start"]:b["end"] + 1])
        else:
            resolved.append({"rule": rule, "line": len(out) + 1})
            out.extend(take)
        last = b["end"] + 1
    out.extend(merged[last:])
    return out, resolved


BACKPORT_RATIO = 0.75


def conflict_kinds(merged):
    """Per remaining conflict: {'kind': 'same_point' | 'overlap', 'backport': bool}. same_point - old has no code
    there and both sides only inserted: the order is the human's call (in the hand ports a fixed order matched half
    the time). backport - the two insertions are alike (ratio >= BACKPORT_RATIO, or one holds all lines of the other):
    the author likely back-ported what the vendor now ships, so new alone is the likely answer."""
    out = []
    for b in conflict_blocks(merged):
        new, old, copy = _code(b["new"]), _code(b["old"]), _code(b["copy"])
        same_point = not old and bool(new) and bool(copy)
        back = False
        if same_point:
            ratio = difflib.SequenceMatcher(None, "\n".join(new), "\n".join(copy), autojunk=False).ratio()
            back = ratio >= BACKPORT_RATIO or set(new) <= set(copy) or set(copy) <= set(new)
        out.append({"kind": "same_point" if same_point else "overlap", "backport": back})
    return out


def kind_label(k, control=False):
    if k["kind"] != "same_point":
        return ""
    if k["backport"]:
        return " — вставки в одной точке, похоже на бэкпорт: скорее взять new"
    return " — вставки в одной точке: порядок решает человек" + (", контроль проходит при любом" if control else "")


def merge_bodies(old, new, copy, auto=False):
    """{'lines': merged lines (with conflict markers), 'conflicts': int, 'aligned': whitespace-aligned copy,
    'auto': conflicts resolved by auto_resolve (only with auto=True, for &Вместо copies), 'kinds': conflict_kinds}."""
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is not on PATH: merge_copy needs `git merge-file`")
    aligned = align_whitespace(old, copy)
    tmp = tempfile.mkdtemp(prefix="merge_copy_")
    try:
        paths = []
        for label, lines in zip(LABELS, (new, old, aligned)):
            path = os.path.join(tmp, label + ".bsl")
            with open(path, "wb") as fh:
                fh.write(("\n".join(lines) + "\n").encode("utf-8"))
            paths.append(path)
        cmd = [git, "merge-file", "-p", "--diff3"]
        for label in LABELS:
            cmd += ["-L", label]
        proc = subprocess.run(cmd + paths, capture_output=True)
    finally:
        shutil.rmtree(tmp, True)
    if proc.returncode < 0 or proc.returncode > 127:
        raise RuntimeError("git merge-file failed: %s" % proc.stderr.decode("utf-8", "replace").strip())
    text = proc.stdout.decode("utf-8").replace("\r\n", "\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    resolved = []
    if auto:
        lines, resolved = auto_resolve(lines)
    conflicts = sum(1 for s in lines if CONFLICT_START.match(s))
    return {"lines": lines, "conflicts": conflicts, "aligned": aligned, "auto": resolved,
            "kinds": conflict_kinds(lines)}


def _removed(a, b):
    """Lines of a that are not in b by a whitespace-insensitive alignment."""
    sm = difflib.SequenceMatcher(None, [_norm(s) for s in a], [_norm(s) for s in b], autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("delete", "replace"):
            out.extend(a[i1:i2])
    return out


def lost_lines(old, copy, merged):
    """Copy lines missing from the result that do not exist in the old body either: possibly lost author work.
    A dropped line found in old is vendor text the new release replaced - a legitimate loss (porting.md 4.2)."""
    old_norm = {_norm(s) for s in old}
    return [s for s in _removed(copy, merged) if _norm(s) and _norm(s) not in old_norm]


def resolve(merged, side):
    """The merged text with every diff3 conflict resolved to one side: 'new' or 'copy'."""
    out, state = [], None
    for s in merged:
        if s.startswith("<<<<<<<"):
            state = "new"
        elif s.startswith("|||||||") and state:
            state = "old"
        elif s.startswith("=======") and state:
            state = "copy"
        elif s.startswith(">>>>>>>") and state:
            state = None
        elif state is None or state == side:
            out.append(s)
    return out


def duplicated(new, copy, merged):
    """{'new': [...], 'copy': [...]}: lines that resolving every conflict to that side leaves more times than
    both new and the copy have them. git may read a vendor move next to an author block as "the block went
    down" and insert the vendor lines once more outside the conflict; presence checks do not see it."""
    limit = Counter(_norm(s) for s in new)
    for t, c in Counter(_norm(s) for s in copy).items():
        limit[t] = max(limit[t], c)
    out = {}
    for side in ("new", "copy"):
        seen, extra = Counter(), []
        for s in resolve(merged, side):
            t = _norm(s)
            seen[t] += 1
            if t and seen[t] == limit[t] + 1:
                extra.append(s)
        out[side] = extra
    return out


PAIR_RATIO = 0.5


def _whole(a, b):
    return ("[-%s-]" % a if a else "") + ("{+%s+}" % b if b else "")


def inline_diff(a, b):
    """One-line markup of a (new) against b (copy): [-…-] only in new, {+…+} only in the copy.
    As in KDiff3's fine diff: without a shared run of 4+ characters the whole line is marked, and a shared run
    shorter than 3 characters between two changes is absorbed into them - a lone common letter is noise."""
    ops = difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    if a != b and not any(t == "equal" and i2 - i1 >= 4 for t, i1, i2, _, _ in ops):
        return _whole(a, b)
    segs = []
    for k, (tag, i1, i2, j1, j2) in enumerate(ops):
        equal = tag == "equal" and not (i2 - i1 < 3 and 0 < k < len(ops) - 1)
        if segs and not equal and not segs[-1][0]:
            segs[-1][1] += a[i1:i2]
            segs[-1][2] += b[j1:j2]
        else:
            segs.append([equal, a[i1:i2], b[j1:j2]])
    return "".join(x if equal else _whole(x, y) for equal, x, y in segs)


def conflict_diffs(merged):
    """Per diff3 conflict: its new-side lines against its copy-side lines. Where both sides replaced lines, each
    new line is paired with the most similar copy line (ratio >= PAIR_RATIO) and marked up character by character;
    the rest is shown whole - a character diff of unrelated lines is noise."""
    blocks, cur, state = [], None, None
    for s in merged:
        if s.startswith("<<<<<<<"):
            cur, state = {"new": [], "copy": []}, "new"
        elif s.startswith("|||||||") and state:
            state = "old"
        elif s.startswith("=======") and state:
            state = "copy"
        elif s.startswith(">>>>>>>") and state:
            blocks.append(cur)
            state = None
        elif state in ("new", "copy"):
            cur[state].append(s.strip())
    out = []
    for b in blocks:
        sm = difflib.SequenceMatcher(None, b["new"], b["copy"], autojunk=False)
        lines = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            copies, used = b["copy"][j1:j2], set()
            for s in b["new"][i1:i2]:
                best, score = None, PAIR_RATIO
                for k, c in enumerate(copies):
                    r = difflib.SequenceMatcher(None, s, c, autojunk=False).ratio()
                    if k not in used and r >= score:
                        best, score = k, r
                if best is None:
                    lines.append(_whole(s, ""))
                else:
                    used.add(best)
                    lines.append(inline_diff(s, copies[best]))
            lines.extend(_whole("", c) for k, c in enumerate(copies) if k not in used)
        out.append(lines)
    return out


def control(old, new, copy, merged):
    """Reverse diff of 4.4 step 4. left_new - lines of the new body missing from the result; the ones found in old
    are old text the author replaced, the rest (dropped_vendor_new) is new vendor code the merge threw away and
    must be zero. added_to_new - author lines and conflict markers. lost - see lost_lines()."""
    old_norm = {_norm(s) for s in old}
    left = [s for s in _removed(new, merged) if _norm(s)]
    return {"left_new": left,
            "dropped_vendor_new": [s for s in left if _norm(s) not in old_norm],
            "added_to_new": [s for s in _removed(merged, new) if _norm(s)],
            "lost": lost_lines(old, copy, merged),
            "duplicated": duplicated(new, copy, merged)}


def _method(path, name, what):
    mod = bsl.load_module(path)
    m = mod.find(name)
    if m is None:
        sys.exit("method %s is not found in %s module %s" % (name, what, path))
    return mod, m, mod.lines[m.decl_line - 1:m.end_line]


def _copy_method(path, target, name):
    mod = bsl.load_module(path)
    if name:
        m = mod.find(name)
    else:
        found = [m for m in mod.methods for kind, t, _ in m.interceptors
                 if kind == "Вместо" and t.casefold() == target.casefold()]
        m = found[0] if len(found) == 1 else None
        if len(found) > 1:
            sys.exit("several &Вместо(\"%s\") in %s: pass --copy-method" % (target, path))
    if m is None:
        sys.exit("no &Вместо(\"%s\") copy in %s" % (target, path))
    return m, mod.lines[m.decl_line - 1:m.end_line]


def _dump_root(path):
    """Nearest parent directory with Configuration.xml, or None."""
    d = os.path.dirname(os.path.abspath(path))
    while True:
        if os.path.isfile(os.path.join(d, "Configuration.xml")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _inside(path, root):
    path, root = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
    return path == root or path.startswith(root.rstrip("\\/") + os.sep)


def render(args, copy_name, res, ctl):
    md = ["# Перенос копии `&Вместо` трёхсторонним слиянием", ""]
    md.append("Метод `%s`, копия `%s`. old: `%s`, new: `%s`, копия: `%s`." % (
        args.method, copy_name, args.old, args.new, args.copy))
    md.append("")
    md.append("| Показатель | Значение |")
    md.append("|---|---|")
    md.append("| Конфликтов (разрешить вручную, стиль diff3: new / old / copy) | %d |" % res["conflicts"])
    md.append("| Из них вставок в одной точке (порядок решает человек) | %d |"
              % sum(1 for k in res.get("kinds", []) if k["kind"] == "same_point"))
    md.append("| Разрешено правилами пробелов (стороны равны с точностью до пробелов / одна сторона меняла только "
              "пробелы) | %d |" % len(res.get("auto", [])))
    md.append("| Строк ушло из тела new (объявление и текст old, который автор заменил) | %d |" % len(ctl["left_new"]))
    md.append("| Из них нового кода вендора, выпавшего при слиянии (должно быть 0) | %d |"
              % len(ctl["dropped_vendor_new"]))
    md.append("| Строк добавлено к телу new (правки автора и маркеры конфликтов) | %d |" % len(ctl["added_to_new"]))
    md.append("| Строк копии нет ни в результате, ни в old (возможная потеря работы автора) | %d |" % len(ctl["lost"]))
    md.append("| Строк задвоится, если все конфликты решить в пользу new / копии (должно быть 0) | %d / %d |"
              % (len(ctl["duplicated"]["new"]), len(ctl["duplicated"]["copy"])))
    sections = [("Выпал новый код вендора", ctl["dropped_vendor_new"]),
                ("Возможно потеряны (строки автора)", ctl["lost"]),
                ("Задвоится при выборе стороны new", ctl["duplicated"]["new"]),
                ("Задвоится при выборе стороны копии", ctl["duplicated"]["copy"]),
                ("Ушли из тела new", ctl["left_new"])]
    for title, items in sections:
        md.append("")
        md.append("## %s" % title)
        md.append("")
        md.extend(["- `%s`" % s.strip().replace("`", "'") for s in items] or ["(нет)"])
    md.append("")
    md.append("## Разрешено правилами пробелов")
    md.append("")
    rule_ru = {"ws_identical": "обе стороны одинаковы с точностью до пробелов — взята new",
               "vendor_ws_only": "вендор менял только пробелы — взята копия",
               "author_ws_only": "автор менял только пробелы — взята new"}
    md.extend(["- строка %d результата: %s" % (r["line"], rule_ru[r["rule"]]) for r in res.get("auto", [])]
              or ["(нет)"])
    md.append("")
    md.append("## Конфликты: что отличается в строках")
    md.append("")
    md.append("`[-…-]` есть только в new, `{+…+}` только в копии; строка new сравнивается с самой похожей строкой "
              "копии, непохожие показаны целиком.")
    kinds = res.get("kinds", [])
    for n, lines in enumerate(conflict_diffs(res["lines"]), 1):
        md.append("")
        md.append("Конфликт %d%s:" % (n, kind_label(kinds[n - 1]) if n <= len(kinds) else ""))
        md.append("")
        md.append("```")
        md.extend(lines or ["(стороны отличаются только пробелами)"])
        md.append("```")
    md.append("")
    md.append("Слияние не заменяет классы строк §4.2 и путь миграции вендора §4.3. Результат — черновик: в модуль "
              "расширения он переносится вручную после разбора конфликтов и сверки чисел выше.")
    return "\n".join(md)


def main(argv=None):
    fileio.console_utf8()
    ap = argparse.ArgumentParser(description="Port a frozen &Вместо copy onto the new-release body (git merge-file)")
    ap.add_argument("--old", required=True, help="module file of the old release")
    ap.add_argument("--new", required=True, help="module file of the new release")
    ap.add_argument("--copy", required=True, help="module file of the extension holding the copy")
    ap.add_argument("--method", required=True, help="configuration method the copy replaces")
    ap.add_argument("--copy-method", help="name of the copy in the extension (default: found by &Вместо)")
    ap.add_argument("--out", required=True, help="report folder; must be outside the extension dump")
    args = ap.parse_args(argv)

    root = _dump_root(args.copy)
    if root and _inside(args.out, root):
        sys.exit("--out %s is inside the extension dump %s: merge_copy never writes there" % (args.out, root))
    _, _, old = _method(args.old, args.method, "old")
    _, _, new = _method(args.new, args.method, "new")
    m, copy = _copy_method(args.copy, args.method, args.copy_method)
    res = merge_bodies(old, new, copy, auto=True)
    ctl = control(old, new, copy, res["lines"])

    name = "merge_copy_" + re.sub(r"[^\w.-]", "_", m.name)
    body_path = os.path.join(args.out, name + ".bsl")
    os.makedirs(args.out, exist_ok=True)
    fileio.write_like(body_path, "\n".join(res["lines"]) + "\n", args.copy)
    md_path, json_path = fileio.write_report(args.out, name, render(args, m.name, res, ctl), {
        "method": args.method, "copy_method": m.name, "old": args.old, "new": args.new, "copy": args.copy,
        "conflicts": res["conflicts"], "auto_resolved": res["auto"], "conflict_kinds": res["kinds"],
        "control": ctl})
    print("conflicts: %d (resolved by whitespace rules: %d, same-point insertions: %d), left new: %d (vendor-new "
          "dropped: %d), added: %d, possibly lost: %d, duplicated new/copy: %d/%d" % (
              res["conflicts"], len(res["auto"]), sum(1 for k in res["kinds"] if k["kind"] == "same_point"),
              len(ctl["left_new"]), len(ctl["dropped_vendor_new"]), len(ctl["added_to_new"]),
              len(ctl["lost"]), len(ctl["duplicated"]["new"]), len(ctl["duplicated"]["copy"])))
    for p in (body_path, md_path, json_path):
        print(p)


if __name__ == "__main__":
    main()
