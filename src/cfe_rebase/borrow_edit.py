# -*- coding: utf-8 -*-
"""Structural edits of the adoptions of an extension in a work copy: remove an adoption, rename it to the vendor's name.

    cfe-rebase borrow-edit --work <work copy> [--ext NAME] [--remove Вид.Имя ...] [--rename Вид.Имя[=НовоеИмя] ...]
                           [--config-new <new release>] [--dry-run] [--out <dir>]

--remove: the object XML, its folder, its line in ChildObjects of Configuration.xml and every <Metadata name="Tag.Имя…">
entry of ConfigDumpInfo.xml (a nested block whole). Refused when code or XML of the extension still names the object
outside comments: «Документы.Имя», «Документ.Имя» in a query text, «ДокументСсылка.Имя», «cfg:DocumentRef.Имя»,
«Document.Имя.Attribute…», a call «Имя.Метод» of a common module, … (the references are listed). References from
other objects removed by the same call do not count.
--rename: the object XML and its folder under the new name, the object's own <Name> when it is still the old one, the
ChildObjects line of Configuration.xml, the entries of ConfigDumpInfo.xml and every English reference in XML of the
extension («cfg:DocumentRef.Имя», «Document.Имя.Attribute.X», «DocumentObject.Имя»). Russian references (BSL code,
query texts inside Form.xml and templates) are listed, not rewritten: a person decides on each. Without «=НовоеИмя»
the new name is the vendor's: the object of --config-new with the uuid of the adoption's ExtendedConfigurationObject.
Vendor renames keep the uuid, so after the rename the other tools match the object again.
Vid (Вид) is Russian («Документ»), the English element name («Document») or the folder («Documents/Имя»).

Every edit of one call is planned before anything is written: a refusal leaves the work copy as it is. Without
--dry-run every file that is changed, moved or deleted must be tracked by git (the rollback is git's), a file that is
rewritten must be UTF-8. 1C files are edited as text lines and written back byte for byte: BOM and line endings stay.
--dry-run lists exactly what would change and writes nothing to the work copy. --out - borrow_edit.json / .md there.
Exit code: 0 - applied (or the dry run planned without refusals), 1 - refused, 2 - bad input.
"""
import argparse
import os
import re
import subprocess
import sys

from . import fileio, metadata
from .control_rebase import inside

BOM = b"\xef\xbb\xbf"
NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё_][\wЁё]*$")
DUMP_INFO = "ConfigDumpInfo.xml"
CONFIG = "Configuration.xml"
METADATA_NAME_RE = re.compile(r'^\s*<Metadata name="([^"]+)"')
# English type names of other kinds that start like the tag of a kind: «DocumentJournal.X» is not a document
NOT_THIS_KIND = {"Document": "(?!Journal|Numerator)"}


class EditError(Exception):
    """Bad input: the edit is not planned at all."""


def parse_object(spec):
    """'Документ.Имя', 'Document.Имя' or 'Documents/Имя' -> (RU kind, name)."""
    s = (spec or "").strip().replace("\\", "/")
    if "/" in s:
        head, _, name = s.partition("/")
        if name.lower().endswith(".xml"):
            name = name[:-4]
        kind = metadata.FOLDER2KIND.get(head.lower())
    else:
        head, _, name = s.partition(".")
        kind = next((k for k in metadata.KINDS if k.casefold() == head.casefold()), None) or \
            metadata.TAG2KIND.get(head.casefold()) or metadata.FOLDER2KIND.get(head.lower())
    if kind is None or kind not in metadata.KIND_TAGS or not name or not NAME_RE.match(name):
        raise EditError("объект «%s»: ожидается Вид.Имя (Документ.Имя, Document.Имя или Documents/Имя)" % spec)
    return kind, name


class Text(object):
    """A text file of the work copy as lines that keep their own line ending: written back byte for byte."""

    def __init__(self, path):
        data = fileio.read_bytes(path)
        self.bad = fileio.utf8_error(data)
        self.bom = data.startswith(BOM)
        body = data[len(BOM):] if self.bom else data
        self.lines = body.decode("utf-8", "replace").split("\n")  # each line keeps its CR
        self.changed = False

    def data(self):
        return (BOM if self.bom else b"") + "\n".join(self.lines).encode("utf-8")


def _code(line, state):
    """The line without a «//» comment outside a string literal; state[0] - inside a string at the line start (BSL
    strings run over lines). Strings are kept: query texts name objects."""
    out, i, n = [], 0, len(line)
    while i < n:
        ch = line[i]
        if state[0]:
            if ch == '"':
                if line[i + 1:i + 2] == '"':
                    out.append('""')
                    i += 2
                    continue
                state[0] = False
            out.append(ch)
        elif ch == '"':
            state[0] = True
            out.append(ch)
        elif line.startswith("//", i):
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def ref_pattern(kind, name):
    """Names of the object in code and XML: Russian and English kind, manager collection and type names
    («ДокументСсылка», «DocumentRef») followed by .Имя, any case."""
    tag = metadata.KIND_TAGS[kind]
    heads = [re.escape(kind) + r"\w*", re.escape(tag) + NOT_THIS_KIND.get(tag, "") + r"[A-Za-z]*"]
    heads += [re.escape(c) for c in metadata.KINDS[kind][1:] if c]
    alts = r"(?<!\w)(?:%s)\.%s(?!\w)" % ("|".join(heads), re.escape(name))
    if kind == "ОбщийМодуль":  # a call of a common module names it alone: «Имя.Метод(»
        alts += r"|(?<![\w.])%s\s*\.\s*\w" % re.escape(name)
    return re.compile(alts, re.I)


def xml_type_pattern(kind, name):
    """English references in XML that a rename rewrites: «cfg:DocumentRef.Имя», «Document.Имя.Attribute.X»,
    name="DocumentObject.Имя" (case as written by the configurator)."""
    tag = metadata.KIND_TAGS[kind]
    return re.compile(r"(?<![\w])(%s%s[A-Za-z]*\.)%s(?![\w])" % (re.escape(tag), NOT_THIS_KIND.get(tag, ""),
                                                                 re.escape(name)))


class Editor(object):
    def __init__(self, ext):
        self.ext = ext
        self.files = ext.files()
        self.texts = {}
        self.deleted = []   # rel
        self.moved = []     # (rel from, rel to)
        self.refs = []      # {op, object, file, line, text, kind}
        self.refusals = []
        self.ops = []

    def text(self, rel):
        if rel not in self.texts:
            self.texts[rel] = Text(self.ext.path(rel))
        return self.texts[rel]

    def object_files(self, kind, name):
        folder = metadata.KINDS[kind][0]
        xml, sub = ("%s/%s.xml" % (folder, name)).casefold(), ("%s/%s/" % (folder, name)).casefold()
        return [f for f in self.files if f.casefold() == xml or f.casefold().startswith(sub)]

    def scan(self, kind, name, skip, rewrite=False):
        """References to the object in the extension outside the files in skip: [{file, line, text, kind}]. With
        rewrite, English references in XML are counted apart (they are rewritten, not reported)."""
        rx = ref_pattern(kind, name)
        typed = xml_type_pattern(kind, name)
        found = []
        skip = {f.casefold() for f in skip}
        for rel in self.files:
            low = rel.casefold()
            if low in skip or low in (CONFIG.casefold(), DUMP_INFO.casefold()):
                continue
            ext = os.path.splitext(low)[1]
            if ext not in (".bsl", ".xml"):
                continue
            t = self.text(rel)
            state = [False]
            for n, raw in enumerate(t.lines, 1):
                line = raw.rstrip("\r")
                code = _code(line, state) if ext == ".bsl" else line
                for m in rx.finditer(code):
                    if rewrite and ext == ".xml" and typed.fullmatch(m.group(0)):
                        continue
                    found.append({"file": rel, "line": n, "text": line.strip()[:200],
                                  "kind": "bsl" if ext == ".bsl" else "xml"})
                    break
        return found

    def drop_child(self, kind, name):
        """Remove the line <Tag>Имя</Tag> from ChildObjects of Configuration.xml; how many lines went."""
        if not self.ext.exists(CONFIG):
            return 0
        t = self.text(CONFIG)
        rx = re.compile(r"^\s*<%s>%s</%s>\s*$" % (metadata.KIND_TAGS[kind], re.escape(name), metadata.KIND_TAGS[kind]),
                        re.I)
        keep = [s for s in t.lines if not rx.match(s.rstrip("\r"))]
        gone = len(t.lines) - len(keep)
        if gone:
            t.lines, t.changed = keep, True
        return gone

    def drop_dump_info(self, kind, name):
        """Remove every <Metadata name="Tag.Имя…"> entry of ConfigDumpInfo.xml, a nested block whole; lines removed."""
        if not self.ext.exists(DUMP_INFO):
            return 0
        t = self.text(DUMP_INFO)
        full = ("%s.%s" % (metadata.KIND_TAGS[kind], name)).casefold()
        out, i, gone = [], 0, 0
        while i < len(t.lines):
            line = t.lines[i]
            m = METADATA_NAME_RE.match(line)
            nm = m.group(1).casefold() if m else ""
            if m and (nm == full or nm.startswith(full + ".")):
                depth = 0 if line.rstrip("\r").rstrip().endswith("/>") else 1
                i += 1
                gone += 1
                while depth and i < len(t.lines):
                    s = t.lines[i].strip()
                    if s.startswith("</Metadata>"):
                        depth -= 1
                    elif s.startswith("<Metadata ") and not s.endswith("/>"):
                        depth += 1
                    i += 1
                    gone += 1
                continue
            out.append(line)
            i += 1
        if gone:
            t.lines, t.changed = out, True
        return gone

    def remove(self, kind, name, together):
        """Plan the removal of an adoption; together - files of every object removed by this call."""
        title = "%s.%s" % (kind, name)
        files = self.object_files(kind, name)
        op = {"op": "remove", "object": title, "delete": files, "edits": []}
        self.ops.append(op)
        o = self.ext.object(kind, name)
        if not files or o is None:
            self.refusals.append("%s: в расширении нет объекта (%s/%s.xml)" % (title, metadata.KINDS[kind][0], name)
                                 if not files else "%s: XML объекта не разбирается" % title)
            return
        if not o.adopted:
            self.refusals.append("%s: объект собственный, не заимствованный — borrow-edit снимает только "
                                 "заимствования" % title)
            return
        refs = self.scan(kind, name, together)
        for r in refs:
            r.update(op="remove", object=title)
        self.refs.extend(refs)
        if refs:
            self.refusals.append("%s: на объект ссылаются код или XML расширения вне комментариев — %d мест (refs)"
                                 % (title, len(refs)))
        op["edits"].append({"file": CONFIG, "lines_removed": self.drop_child(kind, name)})
        op["edits"].append({"file": DUMP_INFO, "lines_removed": self.drop_dump_info(kind, name)})
        self.deleted.extend(files)

    def rename(self, kind, name, new):
        title = "%s.%s" % (kind, name)
        folder, tag = metadata.KINDS[kind][0], metadata.KIND_TAGS[kind]
        files = self.object_files(kind, name)
        op = {"op": "rename", "object": title, "new_name": new, "move": [], "edits": []}
        self.ops.append(op)
        o = self.ext.object(kind, name) if files else None
        if not files or o is None:
            self.refusals.append("%s: в расширении нет объекта (%s/%s.xml)" % (title, folder, name) if not files
                                 else "%s: XML объекта не разбирается" % title)
            return
        if not o.adopted:
            self.refusals.append("%s: объект собственный, не заимствованный — borrow-edit переименовывает только "
                                 "заимствования" % title)
            return
        if not NAME_RE.match(new):
            self.refusals.append("%s: новое имя «%s» не годится в имя объекта 1С" % (title, new))
            return
        clash = [f for f in self.object_files(kind, new) if f not in files]
        if clash:
            self.refusals.append("%s: в расширении уже есть %s.%s (%s)" % (title, kind, new, clash[0]))
            return
        xml = "%s/%s.xml" % (folder, name)
        xml = next(f for f in files if f.casefold() == xml.casefold())
        for f in files:
            to = "%s/%s%s" % (folder, new, f[len(folder) + 1 + len(name):])
            if to != f:
                op["move"].append([f, to])
                self.moved.append((f, to))
        # the object's own <Name>: the first one in its XML (InternalInfo has none, children come after Properties)
        t = self.text(xml)
        for k, line in enumerate(t.lines):
            m = re.search(r"<Name>([^<]*)</Name>", line)
            if m:
                if m.group(1) == name:
                    t.lines[k] = line[:m.start(1)] + new + line[m.end(1):]
                    t.changed = True
                    op["edits"].append({"file": xml, "what": "<Name>", "count": 1})
                break
        if self.ext.exists(CONFIG):
            c = self.text(CONFIG)
            rx = re.compile(r"(<%s>)%s(</%s>)" % (tag, re.escape(name), tag))
            count = 0
            for k, line in enumerate(c.lines):
                line2, n = rx.subn(lambda m: m.group(1) + new + m.group(2), line)
                if n:
                    c.lines[k], c.changed, count = line2, True, count + n
            op["edits"].append({"file": CONFIG, "what": "ChildObjects", "count": count})
        # English references in every XML of the extension, ConfigDumpInfo.xml included
        typed = xml_type_pattern(kind, name)
        for rel in self.files:
            if not rel.lower().endswith(".xml") or rel.casefold() == CONFIG.casefold():
                continue
            t = self.text(rel)
            count = 0
            for k, line in enumerate(t.lines):
                line2, n = typed.subn(lambda m: m.group(1) + new, line)
                if n:
                    t.lines[k], t.changed, count = line2, True, count + n
            if count:
                op["edits"].append({"file": rel, "what": "ссылки на тип", "count": count})
        refs = self.scan(kind, name, [], rewrite=True)
        for r in refs:
            r.update(op="rename", object=title)
        self.refs.extend(refs)
        op["refs_to_fix"] = len(refs)

    def changed(self):
        gone = {f.casefold() for f in self.deleted}
        return sorted(rel for rel, t in self.texts.items() if t.changed and rel.casefold() not in gone)


def _git(args, cwd):
    return subprocess.run(["git", "-c", "core.quotepath=off"] + list(args), cwd=cwd, capture_output=True)


def tracked(root):
    """rel paths (from root, «/») git tracks under root; None when root is not in a git work tree."""
    if _git(["rev-parse", "--show-toplevel"], root).returncode:
        return None
    res = _git(["ls-files", "-z", "--", "."], root)
    return {p.replace("\\", "/") for p in res.stdout.decode("utf-8", "replace").split("\0") if p}


def run(work, ext_name=None, remove=(), rename=(), config_new=None, dry_run=False):
    """Plan the edits and, without dry_run and refusals, write them. Returns {ext, root, dry_run, applied, refused,
    operations, deleted, moved, changed, refs}; EditError on bad input (nothing planned)."""
    if not remove and not rename:
        raise EditError("нечего делать: нужен --remove или --rename")
    if not os.path.isdir(work):
        raise EditError("--work %s не существует" % work)
    exts = [e for e in metadata.find_extensions([work], [ext_name] if ext_name else None) if e.is_extension]
    if len(exts) != 1:
        raise EditError("в %s %s" % (work, "нет расширения %s" % ext_name if ext_name and not exts else
                                     "нет выгрузок расширений" if not exts else
                                     "несколько расширений (%s): выбрать одно --ext" % ", ".join(e.name for e in exts)))
    ext = exts[0]
    removes = [parse_object(s) for s in remove]
    renames = []
    cfg_new = None
    for spec in rename:
        obj, sep, new = spec.partition("=")
        kind, name = parse_object(obj)
        new = new.strip()
        if not sep:
            if not config_new:
                raise EditError("--rename %s без нового имени: нужен --config-new (имя вендора по uuid)" % spec)
            if cfg_new is None:
                problem = metadata.config_problem(config_new)
                if problem:
                    raise EditError("--config-new %s: %s" % (config_new, problem))
                cfg_new = metadata.Dump(config_new, "new")
            new = metadata.counterpart_name(ext, kind, name, cfg_new)
            if new is None:
                raise EditError("%s.%s: в --config-new нет объекта с uuid заимствования (ExtendedConfigurationObject)"
                                % (kind, name))
            if new.casefold() == name.casefold():
                raise EditError("%s.%s: в --config-new имя то же — переименовывать нечего" % (kind, name))
        renames.append((kind, name, new))
    seen = [(k, n.casefold()) for k, n in removes] + [(k, n.casefold()) for k, n, _ in renames]
    if len(seen) != len(set(seen)):
        raise EditError("один объект назван дважды")
    ed = Editor(ext)
    together = [f for k, n in removes for f in ed.object_files(k, n)]
    for kind, name in removes:
        ed.remove(kind, name, together)
    for kind, name, new in renames:
        ed.rename(kind, name, new)
    changed = ed.changed()
    for rel in changed:
        if ed.text(rel).bad:
            ed.refusals.append("%s не в UTF-8 (строка %d: байт 0x%02X, %s): переписать его нельзя" % (
                (rel,) + ed.text(rel).bad))
    if not dry_run:
        known = tracked(ext.root)
        if known is None:
            ed.refusals.append("рабочая копия %s не в git-репозитории: откатить правку было бы нечем" % ext.root)
        else:
            touched = changed + ed.deleted + [f for f, _ in ed.moved]
            loose = [f for f in touched if f not in known]
            if loose:
                ed.refusals.append("git не отслеживает %d файлов, которые правка меняет (откатить было бы нечем): %s"
                                   % (len(loose), ", ".join(loose[:5])))
    applied = False
    if not dry_run and not ed.refusals:
        for rel in changed:
            with open(ext.path(rel), "wb") as fh:
                fh.write(ed.text(rel).data())
        for rel in ed.deleted:
            os.remove(ext.path(rel))
        for src, dst in ed.moved:
            os.makedirs(os.path.dirname(ext.path(dst)), exist_ok=True)
            os.rename(ext.path(src), ext.path(dst))
        _prune(ext.root, [f for f in ed.deleted] + [f for f, _ in ed.moved])
        applied = True
    return {"ext": ext.name, "root": ext.root, "dry_run": dry_run, "applied": applied, "refused": ed.refusals,
            "operations": ed.ops, "deleted": ed.deleted, "moved": [list(m) for m in ed.moved], "changed": changed,
            "refs": ed.refs}


def _prune(root, rels):
    """Remove the folders the deleted and moved files left empty (git does not keep empty folders either)."""
    dirs = sorted({os.path.dirname(os.path.join(root, *r.split("/"))) for r in rels}, key=len, reverse=True)
    for d in dirs:
        while inside(d, root) and os.path.normcase(os.path.abspath(d)) != os.path.normcase(os.path.abspath(root)):
            try:
                os.rmdir(d)
            except OSError:
                break
            d = os.path.dirname(d)


def render(res):
    md = ["# Правка заимствований — %s" % res["ext"], ""]
    md.append("Расширение: `%s`. %s." % (res["root"], "Пробный прогон: рабочая копия не менялась" if res["dry_run"]
                                         else "Применено" if res["applied"] else "Отказ: рабочая копия не менялась"))
    if res["refused"]:
        md.extend(["", "## Отказ", ""] + ["- " + r for r in res["refused"]])
    md.extend(["", "## Операции", ""])
    for op in res["operations"]:
        if op["op"] == "remove":
            md.append("- снять заимствование %s: удалить файлов %d; %s" % (op["object"], len(op["delete"]), "; ".join(
                "%s: строк убрать %d" % (e["file"], e["lines_removed"]) for e in op["edits"])))
        else:
            md.append("- переименовать %s → %s: переместить файлов %d; %s; ссылок в коде поправить руками: %d" % (
                op["object"], op["new_name"], len(op["move"]), "; ".join(
                    "%s (%s): %d" % (e["file"], e["what"], e["count"]) for e in op["edits"]) or "правок XML нет",
                op.get("refs_to_fix", 0)))
    md.extend(["", "## Файлы", ""])
    md.append(fileio.md_table(["Действие", "Файл"], [("удалить", f) for f in res["deleted"]] +
                              [("переместить", "%s → %s" % tuple(m)) for m in res["moved"]] +
                              [("изменить", f) for f in res["changed"]]) if res["deleted"] or res["moved"] or
              res["changed"] else "Нет.")
    md.extend(["", "## Ссылки на объекты в расширении", ""])
    md.append(fileio.md_table(["Операция", "Объект", "Файл:строка", "Текст"],
                              [(r["op"], r["object"], "%s:%d" % (r["file"], r["line"]), r["text"])
                               for r in res["refs"]]) if res["refs"] else "Нет.")
    return "\n".join(md)


def main(argv=None):
    fileio.console_utf8()
    ap = argparse.ArgumentParser(description="Правка заимствований в рабочей копии: снять заимствование удалённого "
                                             "вендором объекта или переименовать его под новое имя вендора")
    ap.add_argument("--work", required=True, help="рабочая копия: папка расширений или одно расширение")
    ap.add_argument("--ext", help="расширение (имя из Configuration.xml или папка), если в --work их несколько")
    ap.add_argument("--remove", action="extend", nargs="+", default=[], metavar="ВИД.ИМЯ",
                    help="снять заимствование: Документ.Имя, Document.Имя или Documents/Имя; можно несколько")
    ap.add_argument("--rename", action="extend", nargs="+", default=[], metavar="ВИД.ИМЯ[=НОВОЕ]",
                    help="переименовать заимствование; без =НОВОЕ — имя вендора по uuid из --config-new")
    ap.add_argument("--config-new", help="выгрузка нового релиза: новое имя для --rename без =НОВОЕ")
    ap.add_argument("--dry-run", action="store_true", help="только показать, что изменится; рабочая копия не меняется")
    ap.add_argument("--out", help="папка отчёта borrow_edit.json / borrow_edit.md, вне рабочей копии")
    args = ap.parse_args(argv)
    if args.out and inside(args.out, args.work):
        sys.exit("--out %s внутри рабочей копии %s: borrow-edit туда отчёт не пишет" % (args.out, args.work))
    try:
        res = run(args.work, args.ext, args.remove, args.rename, args.config_new, args.dry_run)
    except EditError as exc:
        sys.exit(str(exc))
    print("%s: %s; удалить файлов %d, переместить %d, изменить %d; ссылок %d" % (
        res["ext"], ("ОТКАЗ, " if res["refused"] else "") + "пробный прогон" if res["dry_run"] else
        "применено" if res["applied"] else "ОТКАЗ, рабочая копия не менялась",
        len(res["deleted"]), len(res["moved"]), len(res["changed"]), len(res["refs"])))
    for r in res["refused"]:
        print("  отказ: " + r)
    for f in res["deleted"]:
        print("  удалить     " + f)
    for src, dst in res["moved"]:
        print("  переместить %s -> %s" % (src, dst))
    for f in res["changed"]:
        print("  изменить    " + f)
    for r in res["refs"]:
        print("  ссылка (%s %s) %s:%d: %s" % (r["op"], r["object"], r["file"], r["line"], r["text"]))
    if args.out:
        md_path, json_path = fileio.write_report(args.out, "borrow_edit", render(res), res)
        print(md_path)
        print(json_path)
    return 1 if res["refused"] else 0


if __name__ == "__main__":
    sys.exit(main())
