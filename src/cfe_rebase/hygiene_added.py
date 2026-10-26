# -*- coding: utf-8 -*-
"""Code hygiene heuristics over the lines ADDED to a module (or to every .bsl of a folder), not the whole file:
  EM_DASH        long dash in a comment
  BSLLS          //BSLLS or @skip-check suppression marks
  LATIN_ID       Latin-only identifier in code (platform format names like XML, TXT, CRC32 are allowed)
  LATIN_ALIAS    Latin alias after КАК / AS (query text)
This is the step before a full code hygiene check: it narrows the review to what the edit brought in.
"""
import argparse
import difflib
import os
import re
import sys

from . import bsl, fileio

LATIN_ID = re.compile(r"(?<![A-Za-zА-Яа-яЁё0-9_])([A-Za-z][A-Za-z0-9_]*)(?![A-Za-zА-Яа-яЁё0-9_])")
ALIAS = re.compile(r"(?<![A-Za-zА-Яа-яЁё0-9_])(?:КАК|AS)\s+([A-Za-z][A-Za-z0-9_]*)(?![A-Za-zА-Яа-яЁё0-9_])",
                   re.IGNORECASE)
SUPPRESS = re.compile(r"(BSLLS|@skip-check)", re.IGNORECASE)
ALLOWED_LATIN = {x.lower() for x in """
XML HTML JSON TXT PDF MXL XLS XLSX DOCX ODS ODT CSV ZIP CRC32 MD5 SHA1 SHA256 UTF8 UTF16 ANSI OEM UUID
HTTP HTTPS FTP FTPS URL URI COM DOM XDTO XPath XSL XSLT XSD ID GUID SMTP POP3 IMAP TCP IP SQL
""".split()}
EXPLAIN = {"EM_DASH": "длинное тире в комментарии", "BSLLS": "подавление //BSLLS или @skip-check",
           "LATIN_ID": "латинский идентификатор в коде", "LATIN_ALIAS": "латинский псевдоним после КАК/AS"}


def comment_of(raw, line):
    if line.kind in ("comment", "strcomment"):
        return raw.strip()
    if line.kind in ("code", "strcont"):
        rest = raw[len(line.code):].lstrip()
        return rest if rest.startswith("//") else ""
    return ""


def added_lines(old_lines, new_lines):
    out = []
    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("insert", "replace"):
            out.extend(range(j1, j2))
    return out


def check_file(original, changed):
    new_lines = fileio.read_lines(changed)
    old_lines = fileio.read_lines(original) if original and os.path.isfile(original) else []
    lx = bsl.lex_lines(new_lines)
    idx = added_lines(old_lines, new_lines)
    hits = []
    for i in idx:
        raw = new_lines[i]
        ln = lx[i]
        flags = []
        comment = comment_of(raw, ln)
        if "—" in comment:
            flags.append("EM_DASH")
        if comment and SUPPRESS.search(comment):
            flags.append("BSLLS")
        latin = []
        if ln.kind == "code":
            for m in LATIN_ID.finditer(ln.blank):
                if m.group(1).lower() not in ALLOWED_LATIN:
                    latin.append(m.group(1))
        if latin:
            flags.append("LATIN_ID")
        aliases = []
        if ln.kind in ("code", "strcont"):
            aliases = [a for a in ALIAS.findall(ln.code) if a.lower() not in ALLOWED_LATIN]
        if aliases:
            flags.append("LATIN_ALIAS")
        if flags:
            hits.append({"line": i + 1, "text": raw.strip()[:200], "flags": flags, "latin": latin,
                         "aliases": aliases})
    return {"original": original, "changed": changed, "added": len(idx), "hits": hits}


def pairs(original, changed):
    if os.path.isfile(changed):
        return [(original, changed)]
    out = []
    for dp, _, fn in os.walk(changed):
        for f in sorted(fn):
            if f.lower().endswith(".bsl"):
                full = os.path.join(dp, f)
                rel = os.path.relpath(full, changed)
                out.append((os.path.join(original, rel) if original else None, full))
    return out


def render(results):
    md = ["# Гигиена добавленных строк", ""]
    total_added = sum(r["added"] for r in results)
    total_hits = sum(len(r["hits"]) for r in results)
    md.append("Файлов: %d, добавлено строк: %d, строк с замечаниями: %d." % (len(results), total_added, total_hits))
    md.append("")
    md.append(fileio.md_table(["Метка", "Что"], sorted(EXPLAIN.items())))
    for r in results:
        if not r["added"]:
            continue
        md.append("")
        md.append("## %s" % r["changed"])
        md.append("")
        md.append("Добавлено строк: %d%s." % (r["added"], "" if r["original"] and os.path.isfile(r["original"])
                                                 else " (оригинала нет — новый файл)"))
        if r["hits"]:
            md.append("")
            md.append(fileio.md_table(["Строка", "Метки", "Текст"],
                                      [(h["line"], ", ".join(h["flags"]) + (
                                          " (%s)" % ", ".join(h["latin"] + h["aliases"]) if h["latin"] or h["aliases"]
                                          else ""), h["text"]) for h in r["hits"]]))
    return "\n".join(md)


def main(argv=None):
    fileio.console_utf8()
    ap = argparse.ArgumentParser(description="Hygiene heuristics over added lines only")
    ap.add_argument("--original", required=True, help="module before the edit, or a folder of modules")
    ap.add_argument("--changed", required=True, help="module after the edit, or a folder (paired by relative path)")
    ap.add_argument("--out", help="folder for hygiene_added.md / .json; without it Markdown goes to stdout")
    args = ap.parse_args(argv)
    results = [check_file(o, c) for o, c in pairs(args.original, args.changed)]
    md = render(results)
    if args.out:
        md_path, json_path = fileio.write_report(args.out, "hygiene_added", md, {"results": results})
        print("files: %d, added: %d, lines with remarks: %d" % (
            len(results), sum(r["added"] for r in results), sum(len(r["hits"]) for r in results)))
        print(md_path)
        print(json_path)
    else:
        print(md)


if __name__ == "__main__":
    main()
