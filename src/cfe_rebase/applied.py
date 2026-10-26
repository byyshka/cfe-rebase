# -*- coding: utf-8 -*-
"""Applied copy of &ИзменениеИКонтроль modules: what the platform compiles, with the line numbers of the real file.

    cfe-rebase applied --src <module file or folder> --dst <folder>

#Удаление … #КонецУдаления blocks and every marker line (#Вставка, #КонецВставки, #Удаление, #КонецУдаления, a trailing
«// …» comment allowed) become empty lines, the contents of #Вставка stay. Lines are blanked, never dropped, so a
syntax check of the copy reports the line numbers of the real file; the raw module reads deleted and inserted code
together and gives false parse errors. A marker is a line whose first character after spaces and tabs is «#», inside a
query string literal too. Every other byte is kept: BOM, the line ending of every line (a blanked line keeps its CR),
a last line without a newline. A folder is walked for .bsl files and relative paths are kept under --dst; the sources
are never written, and neither is any dump: when a result file would land in a folder with Configuration.xml (--dst
itself, a parent of it, or a dump below --dst laid out like --src) nothing is written.
"""
import argparse
import os
import re
import sys

from . import fileio
from .merge_copy import _dump_root

BOM = b"\xef\xbb\xbf"

# a marker may carry a trailing line comment: «#Вставка // ЕГ 13.04.2026 - марка»
MARK_TAIL = r"\s*(?://.*)?$"
MARK_INS = re.compile(r"^#\s*(Вставка|Insert|КонецВставки|EndInsert)" + MARK_TAIL, re.IGNORECASE)
MARK_DEL = re.compile(r"^#\s*(Удаление|Delete)" + MARK_TAIL, re.IGNORECASE)
MARK_DEL_END = re.compile(r"^#\s*(КонецУдаления|EndDelete)" + MARK_TAIL, re.IGNORECASE)


def key(line):
    return line.decode("utf-8", "replace").lstrip("\ufeff").strip(" \t\r")


def applied(data):
    """(bytes, blanked lines): #Удаление blocks and #Вставка/#КонецВставки marker lines turned into empty lines, so the
    line numbers of a syntaxcheck report are the numbers of the real file; other bytes kept."""
    bom = data.startswith(BOM)
    out, dropped, deleting = [], 0, False
    for line in (data[len(BOM):] if bom else data).split(b"\n"):
        s = key(line)
        if MARK_DEL.match(s):
            deleting = True
        elif MARK_DEL_END.match(s):
            deleting = False
        elif not deleting and not MARK_INS.match(s):
            out.append(line)
            continue
        out.append(b"\r" if line.endswith(b"\r") else b"")
        dropped += 1
    return (BOM if bom else b"") + b"\n".join(out), dropped


def applied_bytes(raw):
    """The applied copy of a module given as raw bytes."""
    return applied(raw)[0]


def inside(path, root):
    path = os.path.normcase(os.path.abspath(path))
    root = os.path.normcase(os.path.abspath(root))
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def sources(src):
    """[(path, path relative to --dst)]: the file itself, or every .bsl under the folder."""
    if os.path.isfile(src):
        return [(src, os.path.basename(src))]
    out = []
    for root, dirs, names in os.walk(src):
        dirs.sort()
        for name in sorted(names):
            if name.lower().endswith(".bsl"):
                path = os.path.join(root, name)
                out.append((path, os.path.relpath(path, src)))
    return out


def main(argv=None):
    fileio.console_utf8()
    ap = argparse.ArgumentParser(description="Применённая копия модулей с &ИзменениеИКонтроль: блоки #Удаление и "
                                             "строки маркеров опустошены, номера строк как в исходном файле")
    ap.add_argument("--src", required=True, help="файл модуля или папка (все .bsl в ней)")
    ap.add_argument("--dst", required=True, help="папка результата: относительные пути от --src сохраняются")
    args = ap.parse_args(argv)
    if not os.path.exists(args.src):
        sys.exit("--src %s не существует" % args.src)
    root = args.src if os.path.isdir(args.src) else os.path.dirname(os.path.abspath(args.src))
    if inside(args.dst, root):
        sys.exit("--dst %s внутри папки --src %s: исходники не перезаписываются" % (args.dst, root))
    files = sources(args.src)
    for _, rel in files:
        dump = _dump_root(os.path.join(args.dst, rel))
        if dump and inside(args.dst, dump):
            sys.exit("--dst %s внутри выгрузки %s (папка с Configuration.xml): applied в выгрузки не пишет"
                     % (args.dst, dump))
        if dump:
            sys.exit("--dst %s: %s лёг бы в выгрузку %s (папка с Configuration.xml): applied в выгрузки не пишет"
                     % (args.dst, rel, dump))
    total = 0
    for path, rel in files:
        data, blanked = applied(fileio.read_bytes(path))
        dst = os.path.join(args.dst, rel)
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(data)
        total += blanked
        print("%s: опустошено строк %d" % (rel.replace("\\", "/"), blanked))
    print("файлов: %d, опустошено строк: %d" % (len(files), total))


if __name__ == "__main__":
    main()
