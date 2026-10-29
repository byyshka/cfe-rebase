# -*- coding: utf-8 -*-
"""Check a ported work copy before it is loaded into the infobase.

    cfe-rebase lint --work <work copy root> [--ext <extension dumps the port started from>] [--only NAME] --out <dir>

Every text file of every extension in --work: git merge conflict markers. Every .bsl module: the structure checks of
structure.py (&ИзменениеИКонтроль markers, method boundaries, an annotation without a method, duplicates, #Если /
#Область, an unknown preprocessor instruction). A .bsl or .xml file that is not valid UTF-8 is one ENCODING error and
gets no other text check: a lenient decode would drop its Cyrillic markers and report it clean. With --ext (matched to
the work copy by folder name, extension name or the uuid of Configuration - it survives renaming): files of the source
extension missing from the work copy - an object that is also gone from the work copy's Configuration.xml is reported
once as a removal (OBJECT_REMOVED), not as lost files, and so is a form, template or command of an object that is gone
from the ChildObjects of that object's XML in the work copy (CHILD_REMOVED); an object whose XML is in the work copy
under another name with the same own uuid (a renamed adoption) is reported once (OBJECT_RENAMED, info) and its files
are compared under the new path - and .bsl modules that lost their BOM
(BOM_LOST) or whose line endings changed where the source had pure CRLF: pure LF (EOL_LF) or mixed CRLF and LF
(EOL_MIXED, at the line of the first bare LF). Mixed line endings in XML are the platform's own format and are not
reported. A nonexistent --ext, or one without extension dumps, is refused; an extension of the work copy without a pair
in --ext is named on the console and in stats (without_source, without_source_names). --only takes the name, folder
or Configuration uuid of the work copy's extension or of its source in --ext (the work copy is usually renamed).
The name of the extension (Name of its Configuration.xml) against the other files that carry it - a rename by
cf-edit modify-property Name changes only Configuration.xml: a role's Rights.xml with <name>Configuration.<other
name></name> is EXT_NAME_STALE (error: the platform refuses the load, «Неизвестный объект метаданных -
Configuration.<old name>» once per role, a real load), ConfigDumpInfo.xml with name="Configuration.<other name>"
(and its children Configuration.<other name>.ManagedApplicationModule, …) is EXT_NAME_STALE_DUMP_INFO (info: in the
same real load 20 files carried the old name and the 19 refusals were all roles; another real load passed the
check with a stale child entry, and the dump after the load wrote the new name there itself - to fix along, not
proven harmless for a partial load or a dump with -update over the work copy).
Lines are split on CR LF, LF and CR only, as the platform and fileio.read_lines count them. Nothing is written outside
--out, and --out may not be inside --work or --ext.

Severity: error - the platform will refuse the module or the port lost something; info - a removal to confirm.
Exit code 0: findings are data (lint.json, lint.md).
"""
import argparse
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter

from . import fileio, metadata, structure
from .control_rebase import cfg_uuid, inside, work_index

TEXT_EXT = {".bsl", ".xml", ".txt", ".html", ".htm", ".json", ".css", ".js"}
STRICT_EXT = {".bsl", ".xml"}  # the platform reads them as UTF-8: a byte that is not UTF-8 is an error
INFO = {"OBJECT_REMOVED", "CHILD_REMOVED", "OBJECT_RENAMED", "EXT_NAME_STALE_DUMP_INFO"}
# where the extension's own name is written besides Configuration.xml: «Configuration.<Имя>» and its children
RIGHTS_CFG_RE = re.compile(r"<name>\s*Configuration\.([^<.\s]+)[^<]*</name>")
DUMP_INFO_CFG_RE = re.compile(r'\bname="Configuration\.([^".\s]+)[^"]*"')
# sub-object folder of an object -> (ChildObjects bucket of metadata.ObjectInfo, what the detail says)
CHILD_DIRS = {"Forms": ("forms", "форма убрана"), "Templates": ("templates", "макет убран"),
              "Commands": ("commands", "команда убрана")}
BOM = b"\xef\xbb\xbf"
CHILD_RE = re.compile(r"<(\w+)>([^<]+)</\1>")
BARE_LF = re.compile(rb"(?<!\r)\n")


def files(root):
    """{rel path with '/': absolute path} of a dump."""
    out = {}
    for dp, _, names in os.walk(root):
        for n in names:
            p = os.path.join(dp, n)
            out[os.path.relpath(p, root).replace("\\", "/")] = p
    return out


def text_of(data):
    return (data[len(BOM):] if data.startswith(BOM) else data).decode("utf-8", "replace")


def text_lines(data):
    """Lines split on CR LF, LF and CR only, as fileio.read_lines and the platform count them: str.splitlines also
    breaks on U+2028, \\x0c, \\x1c-\\x1e and \\x85 and shifts every line number after one. A final line terminator does
    not make an empty last line."""
    lines = text_of(data).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def file_issues(rel, data):
    if os.path.splitext(rel)[1].lower() in STRICT_EXT:
        bad = fileio.utf8_error(data)
        if bad:
            return [{"file": rel, "check": "ENCODING", "line": bad[0],
                     "detail": "не UTF-8: байт 0x%02X (%s); остальные проверки текста для файла не делались" % bad[1:]}]
    lines = text_lines(data)
    issues = structure.module_issues(lines) if rel.lower().endswith(".bsl") else structure.merge_markers(lines)
    return [{"file": rel, "check": c, "line": n, "detail": d} for c, n, d in issues]


def name_issues(rel, data, name):
    """EXT_NAME_STALE (a role's Rights.xml) / EXT_NAME_STALE_DUMP_INFO (ConfigDumpInfo.xml): «Configuration.X» where X
    is not the extension's name - one finding per file, at the line of the first such reference; [] for other files."""
    parts = rel.split("/")
    if len(parts) == 4 and parts[0] == "Roles" and parts[2] == "Ext" and parts[3] == "Rights.xml":
        check, rx, where = "EXT_NAME_STALE", RIGHTS_CFG_RE, "права роли на Configuration.%s"
    elif rel == "ConfigDumpInfo.xml":
        check, rx, where = "EXT_NAME_STALE_DUMP_INFO", DUMP_INFO_CFG_RE, "записи Configuration.%s"
    else:
        return []
    text = text_of(data)
    stale = [m for m in rx.finditer(text) if m.group(1).casefold() != name.casefold()]
    if not stale:
        return []
    olds = sorted({m.group(1) for m in stale})
    line = text.count("\n", 0, stale[0].start()) + 1
    detail = "%s (ссылок: %d), а расширение называется %s (Name в Configuration.xml) — заменить Configuration.%s на " \
             "Configuration.%s в этом файле" % (where % ", ".join(olds), len(stale), name, "/".join(olds), name)
    if check == "EXT_NAME_STALE":
        detail += "; иначе платформа откажет в загрузке: «Неизвестный объект метаданных - Configuration.%s»" % olds[0]
    else:
        detail += "; полная загрузка по ConfigDumpInfo.xml не отказывает, а выгрузка после неё пишет новое имя сама " \
                  "(проверено на реальных загрузках) — поправить вместе с ролями, чтобы рабочая копия совпала с базой"
    return [{"file": rel, "check": check, "line": line, "detail": detail, "old_name": olds[0], "new_name": name}]


def eol(data):
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    return "CRLF" if crlf and not lf else "LF" if lf and not crlf else "MIXED" if crlf else "NONE"


def first_lf(data):
    """Line (from 1) that ends with the first bare LF, 0 when there is none."""
    m = BARE_LF.search(data)
    return data.count(b"\n", 0, m.start()) + 1 if m else 0


def children(work_root):
    """Names listed in ChildObjects of the work copy's Configuration.xml."""
    p = os.path.join(work_root, "Configuration.xml")
    if not os.path.isfile(p):
        return set()
    text = text_of(fileio.read_bytes(p))
    start, end = text.find("<ChildObjects>"), text.find("</ChildObjects>")
    return {m.group(2).strip() for m in CHILD_RE.finditer(text[start:end])} if start >= 0 else set()


def child_of(parts):
    """(sub-object key «Folder/Object/Forms/Name», owner XML rel, bucket, label) of a file of a form, template or
    command of an object, else None."""
    if len(parts) < 4 or parts[2] not in CHILD_DIRS:
        return None
    name = parts[3][:-4] if len(parts) == 4 and parts[3].lower().endswith(".xml") else parts[3]
    bucket, label = CHILD_DIRS[parts[2]]
    return "/".join(parts[:3] + [name]), "%s/%s.xml" % (parts[0], parts[1]), bucket, label


def child_names(work_root, owner_rel, bucket, cache):
    """Casefolded names of one ChildObjects bucket of an object XML of the work copy; None when it does not parse."""
    if owner_rel not in cache:
        try:
            cache[owner_rel] = metadata.ObjectInfo(None, owner_rel, os.path.join(work_root, *owner_rel.split("/")))
        except (ET.ParseError, ValueError, OSError):
            cache[owner_rel] = None
    info = cache[owner_rel]
    return None if info is None else {n.casefold() for n in getattr(info, bucket)}


def renamed_objects(src_files, work_files):
    """{(folder, source name): work name} of the objects whose XML is gone from the work copy under the source name
    but is there under another one with the same own uuid: a renamed adoption (borrow-edit --rename) keeps it."""
    out, by_uuid = {}, {}
    for rel in sorted(src_files):
        parts = rel.split("/")
        if len(parts) != 2 or not parts[1].lower().endswith(".xml") or rel in work_files or \
                parts[0].lower() not in metadata.FOLDER2KIND:
            continue
        uid = metadata.head_uuid(src_files[rel])
        if not uid:
            continue
        if parts[0] not in by_uuid:
            by_uuid[parts[0]] = {}
            for w in work_files:
                wp = w.split("/")
                if len(wp) == 2 and wp[0] == parts[0] and wp[1].lower().endswith(".xml"):
                    wu = metadata.head_uuid(work_files[w])
                    if wu:
                        by_uuid[parts[0]].setdefault(wu.lower(), wp[1][:-4])
        new = by_uuid[parts[0]].get(uid.lower())
        if new is not None and "%s/%s.xml" % (parts[0], new) not in src_files:
            out[(parts[0], parts[1][:-4])] = new
    return out


def moved(rel, renames):
    """rel of a source file under the new name of its renamed object."""
    parts = rel.split("/")
    if len(parts) == 2 and parts[1].lower().endswith(".xml") and (parts[0], parts[1][:-4]) in renames:
        return "%s/%s.xml" % (parts[0], renames[(parts[0], parts[1][:-4])])
    if len(parts) > 2 and (parts[0], parts[1]) in renames:
        return "/".join([parts[0], renames[(parts[0], parts[1])]] + parts[2:])
    return rel


def compare(src_root, work_root, src_files, work_files):
    """Issues of the work copy against the source extension dump: lost files, BOM and line endings of modules (a pure
    CRLF source module that became pure LF - EOL_LF, mixed - EOL_MIXED). An object gone from Configuration.xml, or a
    form / template / command gone from the ChildObjects of its object's XML, is one item to confirm, not lost files.
    An object renamed in the work copy (the same own uuid under another name, OBJECT_RENAMED) has its files compared
    under the new path."""
    issues = []
    renames = renamed_objects(src_files, work_files)
    for (folder, old), new in sorted(renames.items()):
        issues.append({"file": "%s/%s" % (folder, new), "check": "OBJECT_RENAMED", "line": 0,
                       "detail": "объект переименован: %s/%s → %s/%s (uuid объекта тот же); файлы сверены с исходными "
                                 "под новым путём" % (folder, old, folder, new)})
    if renames:
        src_files = {moved(rel, renames): p for rel, p in src_files.items()}
    listed = children(work_root)
    removed, removed_children, owners = {}, {}, {}
    for rel in sorted(set(src_files) - set(work_files)):
        parts = rel.split("/")
        obj = parts[1][:-4] if len(parts) == 2 and parts[1].endswith(".xml") else (parts[1] if len(parts) > 2 else None)
        if obj and "%s/%s.xml" % (parts[0], obj) not in work_files and obj not in listed:
            removed.setdefault("%s/%s" % (parts[0], obj), []).append(rel)
            continue
        child = child_of(parts)
        if child is not None:
            key, owner, bucket, label = child
            if owner in work_files and key + ".xml" not in work_files:
                names = child_names(work_root, owner, bucket, owners)
                if names is not None and key.rsplit("/", 1)[1].casefold() not in names:
                    removed_children.setdefault(key, [owner, label, []])[2].append(rel)
                    continue
        issues.append({"file": rel, "check": "FILE_MISSING", "line": 0, "detail": "файл есть в исходном расширении, "
                                                                                   "в рабочей копии его нет"})
    for obj, rels in removed.items():
        issues.append({"file": obj, "check": "OBJECT_REMOVED", "line": 0,
                       "detail": "объект убран из рабочей копии вместе с Configuration.xml (файлов: %d) — подтвердить, "
                                 "что намеренно" % len(rels)})
    for key, (owner, label, rels) in removed_children.items():
        issues.append({"file": key, "check": "CHILD_REMOVED", "line": 0,
                       "detail": "%s из рабочей копии вместе с записью в ChildObjects %s (файлов: %d) — подтвердить, "
                                 "что намеренно" % (label, owner, len(rels))})
    for rel in sorted(set(src_files) & set(work_files)):
        if not rel.lower().endswith(".bsl"):
            continue
        a, b = fileio.read_bytes(src_files[rel]), fileio.read_bytes(work_files[rel])
        if a.startswith(BOM) and not b.startswith(BOM):
            issues.append({"file": rel, "check": "BOM_LOST", "line": 0, "detail": "в исходном модуле BOM был, в "
                                                                                  "рабочей копии его нет"})
        if eol(a) == "CRLF" and eol(b) == "LF":
            issues.append({"file": rel, "check": "EOL_LF", "line": 0, "detail": "исходный модуль в CRLF, рабочая "
                                                                                "копия — в LF"})
        elif eol(a) == "CRLF" and eol(b) == "MIXED":
            issues.append({"file": rel, "check": "EOL_MIXED", "line": first_lf(b),
                           "detail": "исходный модуль в CRLF, в рабочей копии концы строк смешаны: CRLF и LF"})
    return issues


def keys_of(dump):
    """Names an extension dump answers to: folder, name from Configuration.xml, Configuration uuid (casefolded)."""
    return [k for k in (os.path.basename(dump.root).casefold(), dump.name.casefold(), cfg_uuid(dump)) if k]


def lint(work, ext=None, only=None):
    """(issues, stats): issues - [{ext, source, file, line, check, severity, detail}] errors first. only matches the
    work copy's extension or its source in ext (by folder, name or uuid); stats.without_source_names - the work
    copy's extensions without a source in ext."""
    sources = work_index(ext) if ext else {}
    issues, stats, unmatched = [], Counter(), []
    for w in metadata.find_extensions([work]):
        src = next((sources[k] for k in keys_of(w) if k in sources), None) if ext else None
        if only and only.strip().casefold() not in keys_of(w) + (keys_of(src) if src is not None else []):
            continue
        stats["extensions"] += 1
        if ext and src is None:
            stats["without_source"] += 1
            unmatched.append(w.name)
        wf = files(w.root)
        found = []
        for rel, p in sorted(wf.items()):
            if os.path.splitext(rel)[1].lower() not in TEXT_EXT:
                continue
            stats["files"] += 1
            stats["modules"] += rel.lower().endswith(".bsl")
            data = fileio.read_bytes(p)
            issues_of_file = file_issues(rel, data)
            found.extend(issues_of_file)
            if not any(x["check"] == "ENCODING" for x in issues_of_file):
                found.extend(name_issues(rel, data, w.name))
        if src is not None:
            found.extend(compare(src.root, w.root, files(src.root), wf))
        for it in found:
            it.update(ext=w.name, source=src.name if src is not None else None,
                      severity="info" if it["check"] in INFO else "error",
                      path=os.path.join(w.root, *it["file"].split("/")))
            issues.append(it)
    if unmatched:
        stats["without_source_names"] = unmatched
    issues.sort(key=lambda x: (x["severity"] != "error", x["ext"], x["file"], x["line"]))
    return issues, stats


def render(args, issues, stats, elapsed):
    md = ["# Проверка рабочей копии перед загрузкой", ""]
    md.append("Рабочая копия: `%s`; исходные выгрузки: %s. Расширений: %d, текстовых файлов: %d, модулей: %d. "
              "Время: %.1f с." % (args.work, "`%s`" % args.ext if args.ext else "не заданы (сверка файлов не "
                                  "делалась)", stats["extensions"], stats["files"], stats["modules"], elapsed))
    if stats.get("without_source"):
        md.append("")
        md.append("Без пары в исходных выгрузках: %d расширений — для них только проверка структуры: %s." % (
            stats["without_source"], ", ".join("`%s`" % n for n in stats.get("without_source_names", []))))
    counts = Counter(x["check"] for x in issues)
    md.append("")
    md.append("Ошибок: %d, к подтверждению: %d." % (sum(1 for x in issues if x["severity"] == "error"),
                                                    sum(1 for x in issues if x["severity"] == "info")))
    if counts:
        md.append("")
        md.append(fileio.md_table(["Проверка", "Находок"], sorted(counts.items())))
    md.append("")
    md.append("## Находки")
    md.append("")
    md.append(fileio.md_table(["Важность", "Расширение", "Файл:строка", "Проверка", "Что"],
                              [(x["severity"], x["ext"], "%s:%d" % (x["file"], x["line"]) if x["line"] else x["file"],
                                x["check"], x["detail"]) for x in issues]) if issues else "Нет.")
    return "\n".join(md)


def main(argv=None):
    fileio.console_utf8()
    ap = argparse.ArgumentParser(description="Проверка рабочей копии расширений перед загрузкой в базу: маркеры "
                                             "слияния, структура модулей, потерянные файлы, BOM и концы строк")
    ap.add_argument("--work", required=True, help="рабочая копия: папка расширений (или одно расширение)")
    ap.add_argument("--ext", help="выгрузки расширений, с которых начинался перенос: сверка файлов, BOM, концов строк")
    ap.add_argument("--only", help="только это расширение: имя из Configuration.xml, имя папки или uuid Configuration "
                                   "расширения рабочей копии или его пары в --ext")
    ap.add_argument("--out", required=True, help="папка отчёта lint.json / lint.md, вне рабочей копии и --ext")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.work):
        sys.exit("--work %s не существует" % args.work)
    if inside(args.out, args.work):
        sys.exit("--out %s внутри рабочей копии %s: lint туда не пишет" % (args.out, args.work))
    if args.ext and inside(args.out, args.ext):
        sys.exit("--out %s внутри исходных выгрузок %s: lint туда не пишет" % (args.out, args.ext))
    # a mistyped --ext must not turn into «no source, structure only» with errors: 0
    if args.ext and not os.path.isdir(args.ext):
        sys.exit("--ext %s не существует" % args.ext)
    if args.ext and not any(d.is_extension for d in metadata.find_extensions([args.ext])):
        sys.exit("в --ext %s нет выгрузок расширений (Configuration.xml с ConfigurationExtensionPurpose; выгрузка "
                 "типовой — не они)" % args.ext)
    t0 = time.time()
    issues, stats = lint(args.work, args.ext, args.only)
    if not stats["extensions"]:
        if args.only and metadata.find_extensions([args.work]):
            sys.exit("в %s нет расширения %s: не совпало ни имя, ни папка, ни uuid Configuration%s" % (
                args.work, args.only, " — ни у рабочей копии, ни у её пары в --ext" if args.ext else ""))
        sys.exit("в %s нет выгрузок расширений (папок с Configuration.xml)" % args.work)
    elapsed = time.time() - t0
    data = {"work": args.work, "ext": args.ext, "only": args.only, "stats": dict(stats), "issues": issues}
    md_path, json_path = fileio.write_report(args.out, "lint", render(args, issues, stats, elapsed), data)
    errors = sum(1 for x in issues if x["severity"] == "error")
    print("extensions: %d, files: %d, modules: %d, errors: %d, to confirm: %d, %.1fs" % (
        stats["extensions"], stats["files"], stats["modules"], errors, len(issues) - errors, elapsed))
    counts = Counter(x["check"] for x in issues)
    if counts:
        print("checks: " + ", ".join("%s=%d" % kv for kv in sorted(counts.items())))
    if stats.get("without_source"):
        print("without source in --ext (structure only, files not compared): %d: %s" % (
            stats["without_source"], ", ".join(stats["without_source_names"])))
    print(md_path)
    print(json_path)


if __name__ == "__main__":
    main()
