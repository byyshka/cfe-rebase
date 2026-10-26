# -*- coding: utf-8 -*-
"""Difference of two ext_check.ps1 runs (/CheckConfig -ExtendedModulesCheck -HandlersExistence per extension),
e.g. the same extensions on the base "before" and "after", or before and after a fix.

A run folder holds NN.txt (platform output per extension) and index.json {"NN": "extension name"}; a legacy run
without index.json is read through the sibling log '<folder>.log' with lines '########## [NN] <name> ...'.
Extensions are matched by name, so the two runs may list them in a different order.
Messages are multisets of lines. Classes:
  noise   "Отсутствует обработчик" - a handler of the vendor form is invisible when one extension is checked alone;
          "Возможно ошибочное свойство/параметр" - typical on non-adopted objects and on verbatim vendor copies
  signal  everything else ("Возможно ошибочный метод", "Переменная не определена", compile errors ...)
With --config-after / --config-before (and --ext-root) noise is verified against the dumps: new messages against
the configuration of the "after" run, gone messages against the "before" one. A missing handler that exists in
neither the configuration form module nor an extension form module, and a metadata path ("Перечисление.X.Y",
"ДокументСсылка.X") that does not exist, become signal; a verified message is marked "шум (проверено)".
Messages present in both runs are not part of the difference; those that classify as signal (verified against
the "after" dumps) are listed per extension as "было и до обновления" - old defects the diff alone never shows.
"""
import argparse
import collections
import io
import json
import os
import re
import sys

from . import fileio, metadata

NOISE = [("Отсутствует обработчик", "обработчик базовой формы не виден при поштучной проверке"),
         ("Возможно ошибочное свойство", "свойство незаимствованного объекта или дословная копия текста вендора"),
         ("Возможно ошибочный параметр", "параметр незаимствованного объекта или дословная копия текста вендора")]
STATUS = ("Ошибок не обнаружено", "(no out file)")
LOG_HEAD = re.compile(r"^#+\s*\[(\d+)\]\s+(\S+)")
QUOTED = re.compile(r"\"([^\"]+)\"")
TYPE_STEMS = ("Ссылка", "Объект", "Менеджер", "Выборка", "Список", "НаборЗаписей", "МенеджерЗаписи", "КлючЗаписи")


class Verifier(object):
    """Checks form handlers and metadata paths named in platform messages against the dumps."""

    def __init__(self, config_new, ext_roots):
        self.cfg = metadata.Dump(config_new)
        self.exts = metadata.find_extensions(ext_roots or [])
        self.ext_names = {d.name.casefold() for d in self.exts}

    def has_dump(self, line):
        """The extension that produced the message is among the dumps: without it its own objects look missing."""
        return line.split(" ", 1)[0].casefold() in self.ext_names

    def dumps(self):
        return [self.cfg] + self.exts

    @staticmethod
    def form_module_rel(title):
        parts = title.split(".")
        if len(parts) == 3 and parts[0] == "ОбщаяФорма":
            return "CommonForms/%s/Ext/Form/Module.bsl" % parts[1]
        if len(parts) == 5 and parts[2] == "Форма" and parts[0] in metadata.KINDS:
            return "%s/%s/Forms/%s/Ext/Form/Module.bsl" % (metadata.KINDS[parts[0]][0], parts[1], parts[3])
        return None

    def handler(self, line):
        """True/False: a handler named in the message exists in some form module; None - cannot tell."""
        m = re.search(r"\s(\S+)\s+Отсутствует обработчик:\s+(\S+)(?:\s+\"([^\"]+)\")?", line)
        if not m:
            return None
        rel = self.form_module_rel(m.group(1))
        if rel is None:
            return None
        names = [x for x in (m.group(2), m.group(3)) if x]
        for d in self.dumps():
            mod = d.module(rel)
            if mod is not None and any(mod.find(n) is not None for n in names):
                return True
        return False

    def path(self, line):
        """True/False: the metadata object (and enum value) named in the message exists; None - not a path."""
        q = QUOTED.findall(line)
        if not q:
            return None
        parts = q[-1].split(".")
        if len(parts) < 2:
            return None
        kind = parts[0]
        for stem in TYPE_STEMS:
            if kind.endswith(stem) and kind[:-len(stem)] in metadata.KINDS:
                kind = kind[:-len(stem)]
                break
        if kind not in metadata.KINDS:
            return None
        exists = any(d.object_exists(kind, parts[1]) for d in self.dumps())
        if exists and kind == "Перечисление" and len(parts) >= 3:
            exists = any(parts[2] in (d.enum_values(parts[1]) or set()) for d in self.dumps())
        return exists


def classify(line, verifier=None):
    for marker, why in NOISE:
        if marker in line:
            if verifier is None:
                return "noise", why
            if not verifier.has_dump(line):
                return "noise", "не сверялось, нет выгрузки расширения: " + why
            ok = verifier.handler(line) if marker == "Отсутствует обработчик" else verifier.path(line)
            if ok is False:
                return "signal", "проверено по выгрузкам: " + (
                    "обработчика нет ни в конфигурации, ни в расширениях" if marker == "Отсутствует обработчик"
                    else "объекта или значения нет ни в конфигурации, ни в расширениях")
            return "noise", ("шум (проверено): " if ok else "") + why
    return "signal", ""


def run_names(folder):
    idx = os.path.join(folder, "index.json")
    if os.path.isfile(idx):
        with io.open(idx, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return {str(k): v for k, v in data.items()}
    log = folder.rstrip("\\/") + ".log"
    names = {}
    if os.path.isfile(log):
        for line in fileio.read_lines(log):
            m = LOG_HEAD.match(line.strip())
            if m:
                names[m.group(1)] = m.group(2)
    if not names:
        sys.exit("no index.json and no %s with '########## [NN] name' lines" % log)
    return names


def read_run(folder):
    """{extension name: Counter(message line)}; None value when the output file is absent."""
    out = {}
    for key, name in run_names(folder).items():
        cand = [os.path.join(folder, "%s.txt" % key), os.path.join(folder, "%02d.txt" % int(key))]
        path = next((p for p in cand if os.path.isfile(p)), None)
        if path is None:
            out[name] = None
            continue
        lines = [l.strip() for l in fileio.read_lines(path) if l.strip()]
        out[name] = collections.Counter(l for l in lines if l not in STATUS)
    return out


def main(argv=None):
    fileio.console_utf8()
    ap = argparse.ArgumentParser(description="Difference of two extended module check runs")
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--out", help="folder for ext_check_diff.md / .json; without it Markdown goes to stdout")
    ap.add_argument("--noise-examples", type=int, default=3)
    ap.add_argument("--config-after", help="configuration dump of the base of the 'after' run: verify new messages")
    ap.add_argument("--config-before", help="configuration dump of the base of the 'before' run: verify gone messages")
    ap.add_argument("--ext-root", action="extend", nargs="+", help="extension dumps for the noise verification; repeatable")
    args = ap.parse_args(argv)
    before, after = read_run(args.before), read_run(args.after)
    verifier = Verifier(args.config_after, args.ext_root) if args.config_after else None
    verifier_before = Verifier(args.config_before, args.ext_root) if args.config_before else None
    rows = []
    only = {"before": sorted(set(before) - set(after)), "after": sorted(set(after) - set(before))}
    totals = collections.Counter()
    for name in sorted(set(before) & set(after), key=lambda s: s.casefold()):
        b, a = before.get(name), after.get(name)
        row = {"extension": name, "before": None if b is None else sum(b.values()),
               "after": None if a is None else sum(a.values())}
        if a is None or b is None:
            row["missing"] = "before" if b is None else "after"
            rows.append(row)
            continue
        new, gone = a - b, b - a
        for key, cnt in (("new", new), ("gone", gone)):
            items = []
            for line, n in sorted(cnt.items()):
                cls, why = classify(line, verifier if key == "new" else verifier_before)
                items.append({"line": line, "count": n, "class": cls, "why": why})
                totals["%s_%s" % (key, cls)] += n
            row[key] = items
        # Messages of both runs stay out of the difference; the old defects among them are listed separately.
        old = []
        for line, n in sorted((a & b).items()):
            cls, why = classify(line, verifier)
            if cls == "signal":
                old.append({"line": line, "count": n, "class": cls, "why": why})
                totals["old_signal"] += n
        row["old"] = old
        rows.append(row)
    md = ["# Расширенная проверка модулей: разница прогонов", ""]
    md.append("До: `%s`; после: `%s`. Новых сообщений: сигнал %d, шум %d; ушло: сигнал %d, шум %d; "
              "было и до обновления, похоже на ошибку: %d." % (
                  args.before, args.after, totals["new_signal"], totals["new_noise"], totals["gone_signal"],
                  totals["gone_noise"], totals["old_signal"]))
    md.append("")
    md.append("Шум: %s. %s" % ("; ".join("«%s» — %s" % n for n in NOISE),
                                "Новые сообщения сверены с выгрузкой «после»%s." % (
                                    ", ушедшие — с выгрузкой «до»" if verifier_before else "") if verifier else
                                "Шум не сверялся с выгрузками: под ним может быть реальная ошибка (передать "
                                "`--config-after`/`--config-before`)."))
    for side, label in (("before", "только в прогоне «до»"), ("after", "только в прогоне «после»")):
        if only[side]:
            md.append("")
            md.append("Расширения %s (не сравнивались): %d — %s." % (label, len(only[side]), ", ".join(only[side])))
    md.append("")
    md.append(fileio.md_table(
        ["Расширение", "До", "После", "Новый сигнал", "Новый шум", "Ушло", "Было и до, сигнал"],
        [(r["extension"], r["before"] if r["before"] is not None else "нет файла",
          r["after"] if r["after"] is not None else "нет файла",
          sum(i["count"] for i in r.get("new", []) if i["class"] == "signal"),
          sum(i["count"] for i in r.get("new", []) if i["class"] == "noise"),
          sum(i["count"] for i in r.get("gone", [])),
          sum(i["count"] for i in r.get("old", []))) for r in rows]))
    for r in rows:
        if not r.get("new") and not r.get("gone") and not r.get("old"):
            continue
        md.append("")
        md.append("## %s" % r["extension"])
        sig = [i for i in r.get("new", []) if i["class"] == "signal"]
        noise = [i for i in r.get("new", []) if i["class"] == "noise"]
        if sig:
            md.append("")
            md.append("Новый сигнал:")
            md.extend("- `%s`%s%s" % (i["line"], " ×%d" % i["count"] if i["count"] > 1 else "",
                                      " — " + i["why"] if i["why"] else "") for i in sig)
        if noise:
            md.append("")
            md.append("Новый шум — %d%s, примеры:" % (
                sum(i["count"] for i in noise),
                ", подтверждён по выгрузкам: %d" % sum(i["count"] for i in noise if i["why"].startswith("шум (проверено)"))
                if verifier else ""))
            md.extend("- `%s`%s" % (i["line"], " — проверено" if i["why"].startswith("шум (проверено)") else "")
                      for i in noise[:args.noise_examples])
        if r.get("gone"):
            md.append("")
            md.append("Ушло — %d:" % sum(i["count"] for i in r["gone"]))
            md.extend("- `%s` (%s)%s" % (i["line"], "сигнал" if i["class"] == "signal" else "шум",
                                         " ×%d" % i["count"] if i["count"] > 1 else "") for i in r["gone"])
        if r.get("old"):
            md.append("")
            md.append("Было и до обновления, похоже на ошибку — %d:" % sum(i["count"] for i in r["old"]))
            md.extend("- `%s`%s%s" % (i["line"], " ×%d" % i["count"] if i["count"] > 1 else "",
                                      " — " + i["why"] if i["why"] else "") for i in r["old"])
    text = "\n".join(md)
    if args.out:
        md_path, json_path = fileio.write_report(args.out, "ext_check_diff", text,
                                                 {"before": args.before, "after": args.after,
                                                  "verified_new": bool(verifier),
                                                  "verified_gone": bool(verifier_before), "only": only,
                                                  "totals": dict(totals), "extensions": rows})
        print("new: signal %d, noise %d; gone: signal %d, noise %d; in both runs, signal: %d" % (
            totals["new_signal"], totals["new_noise"], totals["gone_signal"], totals["gone_noise"],
            totals["old_signal"]))
        print(md_path)
        print(json_path)
    else:
        print(text)


if __name__ == "__main__":
    main()
