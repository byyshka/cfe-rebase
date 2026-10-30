# -*- coding: utf-8 -*-
"""MCP server (cfe_rebase.mcp_server) on the synthetic corpus: response envelope, filtering against the full reports,
the tool that edits the work copy, input errors and one round trip over the stdio transport.

    python -m unittest discover -s tests/server -t .

Skipped without the MCP SDK (pip install -e ".[mcp]"); the install hint of `cfe-rebase mcp` is checked either way.
"""
import asyncio
import contextlib
import importlib.util
import inspect
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
from datetime import datetime
from unittest import mock

from cfe_rebase import __version__, control_rebase, fileio, mcp_server as srv, prepare as prep
from tests.unit import registry_fixture as fx

HAVE_MCP = importlib.util.find_spec("mcp") is not None
HAVE_GIT = shutil.which("git") is not None
HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(os.path.dirname(HERE), "corpus", "synthetic")
OLD = os.path.join(CORPUS, "old")
NEW = os.path.join(CORPUS, "new")
EXT = os.path.join(CORPUS, "ext")
WORK = os.path.join(CORPUS, "work")
EXT_MAIN = "Синт_Основное"
EXT_PRINT = "Синт_Печать"
COMMON = "CommonModules/ЗаказыСервер/Ext/Module.bsl"
OBJECT = "Documents/ЗаказПокупателя/Ext/ObjectModule.bsl"
BOM = b"\xef\xbb\xbf"
TOOL_NAMES = {"list_interceptors", "method_diff", "rebase_control", "check_control", "port_replace", "check_calls",
              "check_queries", "applied_text", "apply_candidate", "apply_borrow_edit", "prepare", "check_work",
              "check_borrowed", "check_registers", "check_reads", "check_copies", "check_names",
              "check_template_params"}
# tools whose answer on the synthetic corpus may have no items: a clean work copy, no borrowed or register findings
NO_FINDINGS = {"check_work", "check_borrowed", "check_registers", "check_reads", "check_names",
               "check_template_params"}
# report fields that carry method text: none of them may reach an answer unless asked for
TEXT_FIELDS = {"hunks", "conflict_blocks", "duplicated_unexplained", "src", "left_new", "added_to_new"}


def p(root, rel):
    return os.path.join(root, *rel.split("/"))


def keys_deep(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from keys_deep(v)
    elif isinstance(node, list):
        for v in node:
            yield from keys_deep(v)


def rmtree(path):
    """shutil.rmtree that also removes the read-only object files git leaves on Windows."""
    def retry(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onexc=retry)


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false"] +
                          list(args), cwd=cwd, capture_output=True, check=True)


@unittest.skipUnless(HAVE_MCP, "MCP SDK is not installed: pip install -e \".[mcp]\"")
@unittest.skipUnless(HAVE_GIT, "git is not on PATH: control_rebase merges with git merge-file")
class ServerTools(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cfe_mcp_")
        cls.out = os.path.join(cls.tmp, "out")
        cls.res = {
            "list_interceptors": srv.list_interceptors([EXT], OLD, NEW, cls.out),
            "list_interceptors_flagged": srv.list_interceptors([EXT], OLD, NEW, cls.out, only_flagged=True),
            "method_diff": srv.method_diff(EXT, OLD, NEW, cls.out),
            "rebase_control": srv.rebase_control(EXT, OLD, NEW, cls.out, work=WORK),
            "check_control": srv.check_control(EXT, OLD, NEW, cls.out),
            "check_control_work": srv.check_control(WORK, OLD, NEW, cls.out),
            "port_replace": srv.port_replace(p(OLD, COMMON), p(NEW, COMMON), p(p(EXT, EXT_PRINT), COMMON),
                                             "ПодготовитьДанныеПечати", cls.out),
            "check_calls": srv.check_calls([EXT], OLD, NEW, cls.out),
            "check_queries": srv.check_queries([EXT], OLD, NEW, cls.out),
            "applied_text": srv.applied_text(p(p(EXT, EXT_MAIN), COMMON), os.path.join(cls.tmp, "applied")),
            "check_work": srv.check_work(WORK, EXT, cls.out),
            "check_borrowed": srv.check_borrowed([EXT], OLD, NEW, cls.out),
            "check_registers": srv.check_registers([EXT], OLD, NEW, cls.out),
            "check_reads": srv.check_reads([EXT], OLD, NEW, cls.out),
            "check_copies": srv.check_copies([EXT], OLD, NEW, cls.out),
            "check_names": srv.check_names([EXT], OLD, NEW, cls.out),
            "check_template_params": srv.check_template_params([EXT], OLD, NEW, cls.out),
        }

    @classmethod
    def tearDownClass(cls):
        rmtree(cls.tmp)

    def full(self, name):
        with open(self.res[name]["report"]["json"], encoding="utf-8") as fh:
            return json.load(fh)

    def control_item(self, target, name="rebase_control"):
        rows = [it for it in self.res[name]["items"] if it["target"] == target]
        self.assertEqual(len(rows), 1, target)
        return rows[0]

    # -- envelope --------------------------------------------------------------------------------------------------
    def test_envelope(self):
        for name, res in self.res.items():
            with self.subTest(name):
                self.assertEqual(res["status"], "ok", res.get("error"))
                self.assertIn(res["tool"], TOOL_NAMES)
                self.assertEqual(res["version"], __version__)
                uuid.UUID(res["run_id"])
                self.assertIsNotNone(datetime.fromisoformat(res["started_at"]).tzinfo)
                self.assertIsInstance(res["elapsed_s"], float)
                self.assertIsInstance(res["inputs"], dict)
                self.assertTrue(res["summary"])
                if name not in NO_FINDINGS:  # an empty list is not sent
                    self.assertTrue(res["items"])
                self.assertNotIn("error", res)
                self.assertNotIn("console", res)
                json.dumps(res)  # what the SDK serializes
                if "report" in res:
                    self.assertTrue(os.path.isfile(res["report"]["json"]), res["report"])
                    self.assertTrue(os.path.isfile(res["report"]["md"]), res["report"])
                    # every call writes into its own folder <out>/<tool>-<run_id[:8]>
                    folder = os.path.dirname(res["report"]["json"])
                    self.assertEqual(os.path.basename(folder), "%s-%s" % (res["tool"], res["run_id"][:8]))
                    self.assertEqual(os.path.dirname(folder), self.out)

    def test_no_text_bodies_in_items(self):
        for name, res in self.res.items():
            with self.subTest(name):
                self.assertFalse(TEXT_FIELDS & set(keys_deep(res.get("items", []))))

    # -- list_interceptors -------------------------------------------------------------------------------------------
    def test_interceptors_rows_and_flag_filter(self):
        full = self.full("list_interceptors")["registry"]
        every, flagged = self.res["list_interceptors"], self.res["list_interceptors_flagged"]
        # 0.2.4: a long list is cut by size (mcp_server.LIST_MAX_CHARS), the flagged rows are kept first
        self.assertEqual(every.get("truncated", {}).get("items", {}).get("total", len(every["items"])), len(full))
        self.assertLessEqual({r["id"] for r in full if r["flags"]}, {it["id"] for it in every["items"]})
        self.assertEqual([it["id"] for it in flagged["items"]], [r["id"] for r in full if r["flags"]])
        self.assertTrue(all(it["flags"] for it in flagged["items"]))
        self.assertEqual(flagged["summary"], every["summary"])
        for it in every["items"]:
            self.assertTrue(os.path.isfile(it["path"]), it)
            self.assertNotIn("body_change", it)

    # -- method_diff -------------------------------------------------------------------------------------------------
    def test_method_diff_hunks_only_on_request(self):
        res = self.res["method_diff"]
        self.assertEqual({it["status"] for it in res["items"]} - {"changed", "unchanged", "other"}, set())
        changed = [it for it in res["items"] if it["status"] == "changed"]
        self.assertTrue(changed)
        self.assertTrue(all(it["hunk_lines"] > 0 for it in changed))
        with_hunks = srv.method_diff(EXT, OLD, NEW, self.out, with_hunks=True)
        self.assertTrue(all(it.get("hunks") for it in with_hunks["items"] if it["status"] == "changed"))
        one = srv.method_diff(EXT, OLD, NEW, self.out, method="ОпределитьСклад")
        self.assertEqual([it["target"] for it in one["items"]], ["ОпределитьСклад"])
        hunk = one["items"][0]["hunks"][0]
        new_lines = fileio.read_lines(one["items"][0]["new_path"])
        for line, text in hunk["new"]:  # line numbers of the real vendor module
            self.assertEqual(new_lines[line - 1].strip(), text)

    # -- rebase_control / check_control ------------------------------------------------------------------------------
    def test_rebase_control_summary_equals_direct_run(self):
        direct = os.path.join(self.tmp, "direct")
        with contextlib.redirect_stdout(io.StringIO()):
            control_rebase.main(["--ext", EXT, "--old", OLD, "--new", NEW, "--out", direct, "--work", WORK])
        with open(os.path.join(direct, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)
        res = self.res["rebase_control"]
        self.assertEqual(res["summary"], report["summary"])
        self.assertEqual([(it["target"], it["verdict"]) for it in res["items"]],
                         [(r["target"], r["verdict"]) for r in report["methods"]])

    def test_rebase_control_items(self):
        conflict = self.control_item("ОпределитьСклад")
        self.assertEqual((conflict["verdict"], conflict["conflicts"]), ("CONFLICTS", 1))
        cand = fileio.read_lines(conflict["candidate"])
        for line in conflict["conflict_lines"]:
            self.assertTrue(cand[line - 1].startswith("<<<<<<<"))
        missing = self.control_item("ПересчитатьСкидки")
        self.assertEqual(missing["note"], "old: есть, new: нет метода")
        self.assertNotIn("candidate", missing)
        for it in self.res["rebase_control"]["items"]:
            lines = fileio.read_lines(it["path"])
            start, end = it["ext_lines"]
            # the range starts at the first annotation line (a form method has &НаСервере above the interceptor)
            self.assertIn(lines[start - 1].split("(")[0], ("&ИзменениеИКонтроль", "&НаСервере"), it["target"])
            self.assertIn('&ИзменениеИКонтроль("%s")' % it["target"], lines[start - 1:start + 1], it["target"])
            self.assertTrue(lines[end - 1].startswith("Конец"), it["target"])
            if "lines" in it["work"]:
                wl = fileio.read_lines(it["work"]["path"])
                self.assertTrue(wl[it["work"]["lines"][0] - 1].startswith("&"), it["target"])

    def test_check_control_on_the_work_copy_passes(self):
        res = self.res["check_control_work"]
        self.assertEqual(res["summary"]["fail"], 0)
        self.assertEqual({it["result"] for it in res["items"]}, {"pass"})
        self.assertEqual(sum(1 for it in res["items"] if it["verdict"] == "ALREADY_NEW"), 13)

    def test_check_control_on_ext(self):
        res = self.res["check_control"]
        for it in res["items"]:
            self.assertEqual(it["result"], "pass" if it["verdict"] in ("UNCHANGED", "ALREADY_NEW") else "fail")
        self.assertEqual((res["summary"]["pass"], res["summary"]["fail"]), (1, 14))

    def test_control_selection(self):
        full = self.res["check_control"]
        fails = srv.check_control(EXT, OLD, NEW, self.out, result="fail")
        self.assertEqual(fails["status"], "ok", fails.get("error"))
        self.assertEqual({it["result"] for it in fails["items"]}, {"fail"})
        self.assertEqual(len(fails["items"]), full["summary"]["fail"])
        self.assertEqual((fails["summary"]["pass"], fails["summary"]["fail"]), (1, 14))  # counts everything
        self.assertEqual(fails["summary"]["select"], {"filter": {"result": "fail"}, "matched": 14})
        one = full["items"][0]
        by_module = srv.rebase_control(EXT, OLD, NEW, self.out, module=one["module_title"], verdict=[one["verdict"]])
        self.assertTrue(by_module["items"])
        self.assertTrue(all(it["module_title"] == one["module_title"] and it["verdict"] == one["verdict"]
                            for it in by_module["items"]))
        self.assertEqual(by_module["summary"]["select"]["matched"], len(by_module["items"]))
        self.assertNotIn("select", full["summary"])
        bad = srv.check_control(EXT, OLD, NEW, self.out, result="maybe")
        self.assertEqual(bad["error"]["code"], "bad_input")

    # -- port_replace ------------------------------------------------------------------------------------------------
    def test_port_replace(self):
        res = self.res["port_replace"]
        item = res["items"][0]
        self.assertEqual((item["method"], item["copy_method"]),
                         ("ПодготовитьДанныеПечати", "Синт_ПечатьПодготовитьДанныеПечати"))
        self.assertEqual(res["summary"]["conflicts"], item["conflicts"])
        self.assertTrue(os.path.isfile(item["merged"]))

    # -- check_copies ------------------------------------------------------------------------------------------------
    def test_check_copies(self):
        res = self.res["check_copies"]
        cases = self.full("check_copies")["cases"]
        self.assertEqual(len(res["items"]), len(cases))
        self.assertEqual(res["summary"]["copies"], len(cases))
        for it, c in zip(res["items"], cases):
            ported = c.get("basis") in ("new", "same-new") or (
                "vendor_hunks" in c and c["vendor_in_copy"] == c["vendor_hunks"])
            self.assertEqual(it["result"], "pass" if ported else "fail", c["copy_method"])
            if it["result"] == "fail":
                self.assertTrue(os.path.isfile(it["path"]))
                self.assertEqual([h["class"] for h in it.get("missing", [])],
                                 [h["class"] for h in c.get("three_way", []) if not h["in_copy"]])
        older = next(it for it in res["items"] if it["target"] == "СформироватьПредставлениеЗаказа")
        self.assertEqual((older["result"], older["basis"], older["missing"][0]["class"]),
                         ("fail", "older", "ДРЕЙФ: копия держит текст old"))
        self.assertEqual(res["summary"]["pass"] + res["summary"]["fail"], len(cases))

    # -- check_calls / check_queries ---------------------------------------------------------------------------------
    def test_check_calls_only_flagged(self):
        res = self.res["check_calls"]
        flagged = [f for f in self.full("check_calls")["findings"] if f["flags"]]
        self.assertEqual(len(res["items"]), len(flagged))
        self.assertEqual(res["summary"]["findings"], len(flagged))
        for it, f in zip(res["items"], flagged):
            self.assertTrue(it["flags"])
            self.assertEqual((it["line"], it["call"]), (f["line"], f["call"]))
            self.assertTrue(os.path.isfile(it["path"]))

    def test_check_queries_only_findings(self):
        res = self.res["check_queries"]
        full = self.full("check_queries")
        self.assertEqual(len(res["items"]), len(full["findings"]))
        self.assertEqual(res["summary"]["unjudged"], len(full["masked"]))
        self.assertEqual("unjudged" in res, bool(full["masked"]))
        for it in res["items"]:
            self.assertIn(it["part"], ("query", "dcs", "literal"))
            self.assertTrue(os.path.isfile(it["path"]))

    # -- applied_text ------------------------------------------------------------------------------------------------
    def test_applied_text(self):
        res = self.res["applied_text"]
        self.assertEqual(len(res["items"]), 1)
        item = res["items"][0]
        self.assertGreater(item["blanked"], 0)
        self.assertEqual(len(fileio.read_lines(item["dst"])), len(fileio.read_lines(item["path"])))

    # -- apply_candidate ---------------------------------------------------------------------------------------------
    def repo_with(self, rel, init=True):
        root = tempfile.mkdtemp(prefix="cfe_mcp_work_", dir=self.tmp)
        dst = p(root, rel)
        os.makedirs(os.path.dirname(dst))
        shutil.copyfile(p(p(EXT, EXT_MAIN), rel), dst)
        if init:
            git(root, "init", "-q")
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "base")
        return root, dst

    def check_apply(self, rel, target, eol):
        item = self.control_item(target)
        self.assertEqual(item["verdict"], "REBASED")
        root, path = self.repo_with(rel)
        before = fileio.read_lines(path)
        cand = fileio.read_lines(item["candidate"])[:-1]
        start, end = item["ext_lines"]
        res = srv.apply_candidate(root, rel, item["candidate"], start, end)
        self.assertEqual(res["status"], "ok", res.get("error"))
        got = res["items"][0]
        self.assertEqual(got["candidate_lines"], [start, start + len(cand) - 1])
        self.assertEqual(res["summary"]["lines_after"], len(cand))
        data = fileio.read_bytes(path)
        self.assertTrue(data.startswith(BOM))
        if eol == "crlf":
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        else:
            self.assertNotIn(b"\r", data)
        after = fileio.read_lines(path)
        self.assertEqual(after, before[:start - 1] + cand + before[end:])
        self.assertIn("git_numstat", got)
        return got

    def test_apply_candidate_keeps_bom_and_crlf(self):
        self.check_apply(COMMON, "РассчитатьСуммуЗаказа", "crlf")

    def test_apply_candidate_keeps_lf(self):
        self.check_apply(OBJECT, "ЗаполнитьСкладВСтроках", "lf")

    def test_apply_candidate_refuses(self):
        item = self.control_item("РассчитатьСуммуЗаказа")
        start, end = item["ext_lines"]
        root, path = self.repo_with(COMMON, init=False)
        before = fileio.read_bytes(path)
        res = srv.apply_candidate(root, COMMON, item["candidate"], start, end)
        self.assertEqual((res["status"], res["error"]["code"]), ("error", "bad_input"))
        self.assertIn("git", res["error"]["message"])
        git_root, _ = self.repo_with(COMMON)
        for args in ((COMMON, item["candidate"], start, 10 ** 6),
                     (COMMON, os.path.join(self.tmp, "nothing.bsl"), start, end),
                     (path, item["candidate"], start, end)):  # a module outside work_root
            res = srv.apply_candidate(git_root, *args)
            self.assertEqual((res["status"], res["error"]["code"]), ("error", "bad_input"), args)
        self.assertEqual(fileio.read_bytes(path), before)

    def test_apply_candidate_refuses_a_module_git_does_not_track(self):
        # rev-parse passes inside any repository: a work copy git-ignored by an outer one has nothing to roll back to
        item = self.control_item("РассчитатьСуммуЗаказа")
        start, end = item["ext_lines"]
        outer = tempfile.mkdtemp(prefix="cfe_mcp_outer_", dir=self.tmp)
        git(outer, "init", "-q")
        with open(os.path.join(outer, ".gitignore"), "w", encoding="utf-8") as fh:
            fh.write("work/\n")
        git(outer, "add", "-A")
        git(outer, "commit", "-q", "-m", "base")
        work = os.path.join(outer, "work")
        path = p(work, COMMON)
        os.makedirs(os.path.dirname(path))
        shutil.copyfile(p(p(EXT, EXT_MAIN), COMMON), path)
        own, untracked = self.repo_with(COMMON)
        os.remove(p(own, COMMON))
        git(own, "commit", "-q", "-a", "-m", "the module is gone from the index")
        shutil.copyfile(p(p(EXT, EXT_MAIN), COMMON), untracked)  # back on disk, never added
        for root, module in ((work, path), (own, untracked)):
            with self.subTest(root=root):
                before = fileio.read_bytes(module)
                res = srv.apply_candidate(root, COMMON, item["candidate"], start, end)
                self.assertEqual((res["status"], res["error"]["code"]), ("error", "bad_input"))
                self.assertIn("not tracked by git", res["error"]["message"])
                self.assertEqual(fileio.read_bytes(module), before)

    def test_check_work(self):
        res = self.res["check_work"]
        self.assertEqual((res["summary"]["errors"], res["summary"]["info"], res["summary"]["modules"]), (0, 0, 7))
        self.assertNotIn("items", res)
        broken = tempfile.mkdtemp(prefix="cfe_mcp_lint_", dir=self.tmp)
        shutil.copytree(WORK, os.path.join(broken, "w"))
        work_ext = os.listdir(os.path.join(broken, "w"))[0]
        with open(p(p(os.path.join(broken, "w"), work_ext), COMMON), "ab") as fh:
            fh.write(b"\r\n<<<<<<< new\r\n")
        res = srv.check_work(os.path.join(broken, "w"), EXT, self.out)
        self.assertEqual(res["summary"]["checks"], {"MERGE_MARKER": 1})
        self.assertEqual(res["items"][0]["file"], COMMON)
        self.assertTrue(os.path.isfile(res["items"][0]["path"]))
        res = srv.check_work(os.path.join(broken, "w"), EXT, os.path.join(broken, "w", "out"))
        self.assertEqual((res["status"], res["error"]["code"]), ("error", "bad_input"))
        out = os.path.join(EXT, "_mcp_lint_out")  # nor into the dumps the port started from
        try:
            res = srv.check_work(os.path.join(broken, "w"), EXT, out)
            self.assertEqual((res["status"], res["error"]["code"]), ("error", "bad_input"))
            self.assertIn("never writes into dumps", res["error"]["message"])
            self.assertFalse(os.path.exists(out))
        finally:
            shutil.rmtree(out, ignore_errors=True)

    # -- check_borrowed / check_registers ----------------------------------------------------------------------------
    def test_check_borrowed(self):
        res = self.res["check_borrowed"]
        full = self.full("check_borrowed")
        self.assertEqual(set(res["summary"]), {"findings", "checks", "extensions", "adopted", "lists_ported"})
        self.assertEqual(res["summary"]["findings"], len(full["findings"]))
        self.assertEqual(len(res.get("items", [])), len(full["findings"]))
        self.assertEqual(res["summary"]["extensions"], len(full["extensions"]))
        self.assertGreater(res["summary"]["adopted"], 0)
        for it in res.get("items", []):
            self.assertTrue(os.path.isfile(it["path"]), it)

    def test_check_registers(self):
        res = self.res["check_registers"]
        full = self.full("check_registers")
        self.assertEqual(set(res["summary"]), {"findings", "registers_written", "registers_changed",
                                               "registers_removed", "registers_unreadable", "extensions"})
        self.assertEqual(res["summary"]["findings"], len(full["findings"]))
        self.assertEqual(len(res.get("items", [])), len(full["findings"]))
        self.assertEqual(res["summary"]["extensions"], len(full["extensions"]))
        for it in res.get("items", []):
            self.assertTrue(os.path.isfile(it["path"]), it)

    def test_check_reads(self):
        res = self.res["check_reads"]
        full = self.full("check_reads")
        self.assertEqual(set(res["summary"]), {"extensions", "reads", "registers_read", "registers_changed",
                                               "registers_removed", "registers_unreadable", "registers_unchanged"})
        self.assertEqual(len(res.get("items", [])), len(full["findings"]))
        self.assertEqual(res["summary"]["extensions"], len(full["extensions"]))
        self.assertEqual(res["summary"]["registers_read"], sum(res["summary"][k] for k in (
            "registers_changed", "registers_removed", "registers_unreadable", "registers_unchanged")))
        # the corpus: a query of the extension reads a resource of ТоварыНаСкладах that the new release dropped
        item = next(it for it in res["items"] if it["register"] == "РегистрНакопления.ТоварыНаСкладах")
        self.assertEqual((item["check"], item["sev"], item["res_removed"]), ("CHANGED", "ПРОВЕРИТЬ",
                                                                            ["КоличествоВРезерве"]))
        self.assertNotIn("dims_added", item)  # an empty list is not sent
        # every field of reads.json reaches the MCP item: a field missing from READ_FIELDS would vanish silently
        for f in full["findings"]:
            self.assertEqual(set(f), set(srv.READ_FIELDS))
        for read in item["reads"]:
            self.assertTrue(os.path.isfile(read["path"]), read)
            self.assertEqual(read["table"], "ТоварыНаСкладах.Остатки")
            self.assertIn("КоличествоВРезервеОстаток", read["fields"])

    def test_borrowed_and_registers_never_write_into_dumps(self):
        for name, call in (("check_borrowed", srv.check_borrowed), ("check_registers", srv.check_registers),
                           ("check_reads", srv.check_reads)):
            for root in (EXT, NEW):
                out = os.path.join(root, "_mcp_out")
                with self.subTest(name, root=root):
                    try:
                        res = call([EXT], OLD, NEW, out)
                        self.assertEqual((res["status"], res["error"]["code"]), ("error", "bad_input"))
                        self.assertIn("never writes into dumps", res["error"]["message"])
                        self.assertFalse(os.path.exists(out))
                    finally:
                        shutil.rmtree(out, ignore_errors=True)

    def test_applied_text_never_writes_into_a_dump(self):
        dst = os.path.join(EXT, EXT_PRINT, "_applied")
        try:
            res = srv.applied_text(p(p(EXT, EXT_MAIN), COMMON), dst)
            self.assertEqual((res["status"], res["error"]["code"]), ("error", "bad_input"))
            self.assertIn("Configuration.xml", res["error"]["message"])
            self.assertFalse(os.path.exists(dst))
        finally:
            shutil.rmtree(dst, ignore_errors=True)

    def test_apply_candidate_refuses_unresolved_conflict(self):
        item = self.control_item("ОпределитьСклад")
        self.assertEqual(item["verdict"], "CONFLICTS")
        root, path = self.repo_with(COMMON)
        before = fileio.read_bytes(path)
        start, end = item["ext_lines"]
        res = srv.apply_candidate(root, COMMON, item["candidate"], start, end)
        self.assertEqual((res["status"], res["error"]["code"]), ("error", "bad_input"))
        self.assertIn("merge conflict markers", res["error"]["message"])
        self.assertEqual(fileio.read_bytes(path), before)

    def test_apply_candidate_refuses_text_not_in_utf8(self):
        # the whole module is rewritten: a lenient read would turn every Cyrillic byte outside the range into U+FFFD
        item = self.control_item("РассчитатьСуммуЗаказа")
        start, end = item["ext_lines"]
        root, path = self.repo_with(COMMON)
        utf8 = fileio.read_bytes(path)
        with open(path, "wb") as fh:
            fh.write(BOM + utf8[len(BOM):].decode("utf-8").encode("cp1251"))
        before = fileio.read_bytes(path)
        res = srv.apply_candidate(root, COMMON, item["candidate"], start, end)
        self.assertEqual((res["status"], res["error"]["code"]), ("error", "bad_input"))
        self.assertIn("module is not UTF-8", res["error"]["message"])
        self.assertEqual(fileio.read_bytes(path), before)
        with open(path, "wb") as fh:  # a module in UTF-8, the candidate in cp1251
            fh.write(utf8)
        cand = os.path.join(root, "candidate.bsl")
        with open(cand, "wb") as fh:
            fh.write(fileio.read_bytes(item["candidate"]).decode("utf-8-sig").encode("cp1251"))
        res = srv.apply_candidate(root, COMMON, cand, start, end)
        self.assertEqual((res["status"], res["error"]["code"]), ("error", "bad_input"))
        self.assertIn("candidate is not UTF-8", res["error"]["message"])
        self.assertEqual(fileio.read_bytes(path), utf8)

    def test_check_borrowed_checks_every_dump_path(self):
        for args in (([EXT], "old", NEW), ([EXT], OLD, "new"), (["ext"], OLD, NEW)):
            with self.subTest(args=args):
                res = srv.check_borrowed(*args, self.out)
                self.assertEqual((res["status"], res["error"]["code"]), ("error", "bad_input"))
                self.assertIn("path must be absolute", res["error"]["message"])

    def test_apply_candidate_refuses_a_wrong_range(self):
        # the end one line short leaves the old КонецПроцедуры behind; three lines long it eats the annotation and the
        # declaration of the next method; the start one line late leaves a second annotation on the method
        item = self.control_item("РассчитатьСуммуЗаказа")
        root, path = self.repo_with(COMMON)
        before = fileio.read_bytes(path)
        start, end = item["ext_lines"]
        for first, last in ((start, end - 1), (start, end + 3), (start + 1, end)):
            with self.subTest(range=(first, last)):
                res = srv.apply_candidate(root, COMMON, item["candidate"], first, last)
                self.assertEqual((res["status"], res["error"]["code"]), ("error", "bad_input"))
                self.assertIn("structure", res["error"]["message"])
                self.assertEqual(fileio.read_bytes(path), before)

    # -- input errors ------------------------------------------------------------------------------------------------
    def test_out_inside_the_extension_dump(self):
        out = os.path.join(EXT, "_mcp_out")
        try:
            res = srv.rebase_control(EXT, OLD, NEW, out)
            self.assertEqual((res["status"], res["error"]["code"]), ("error", "bad_input"))
            self.assertIn("control_rebase never writes into dumps", res["error"]["message"])
            self.assertNotIn("items", res)
            self.assertFalse(os.path.exists(out))
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_relative_path_is_refused(self):
        res = srv.check_calls(["ext"], OLD, NEW, self.out)
        self.assertEqual((res["status"], res["error"]["code"]), ("error", "bad_input"))


@unittest.skipUnless(HAVE_MCP, "MCP SDK is not installed: pip install -e \".[mcp]\"")
@unittest.skipUnless(HAVE_GIT, "git is not on PATH: control_rebase merges with git merge-file")
class ServerRegistry(unittest.TestCase):
    """db / to / from_release / registry instead of paths, and prepare."""
    maxDiff = None

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_mcp_reg_")
        self.addCleanup(rmtree, self.tmp)
        self.reg, self.task = fx.make(self.tmp)
        self.db = {"db": fx.DB, "to": fx.TO, "registry": self.reg}
        self.reports = os.path.join(self.task, "reports")
        self.ext = os.path.join(self.task, "before", "xml")

    def ok(self, res):
        self.assertEqual(res["status"], "ok", res.get("error"))
        self.assertNotIn(fx.SECRET, json.dumps(res, ensure_ascii=False))
        return res

    def bad(self, res, text, code="bad_input"):
        self.assertEqual((res["status"], res["error"]["code"]), ("error", code), res)
        self.assertIn(text, res["error"]["message"])
        self.assertNotIn(fx.SECRET, json.dumps(res, ensure_ascii=False))
        return res

    def test_tools_take_paths_from_the_registry(self):
        shutil.copytree(EXT, self.ext)
        res = self.ok(srv.rebase_control(**self.db))
        self.assertEqual({k: res["inputs"][k] for k in ("ext", "old", "new", "out", "work")},
                         {"ext": self.ext, "old": OLD, "new": NEW, "out": self.reports, "work": None})
        self.assertEqual(os.path.dirname(os.path.dirname(res["report"]["json"])), self.reports)
        datetime.fromisoformat(res["ext_newest"])
        direct = self.ok(srv.rebase_control(self.ext, OLD, NEW, os.path.join(self.tmp, "out")))
        self.assertNotIn("ext_newest", direct)
        self.assertEqual(res["summary"], direct["summary"])
        self.assertEqual(res["summary"]["REBASED"], 9)
        # every tool with out: the same answer as with explicit paths
        for name, call, explicit in (
                ("list_interceptors", srv.list_interceptors, ([self.ext], OLD, NEW)),
                ("method_diff", srv.method_diff, (self.ext, OLD, NEW)),
                ("check_control", srv.check_control, (self.ext, OLD, NEW)),
                ("check_calls", srv.check_calls, ([self.ext], OLD, NEW)),
                ("check_queries", srv.check_queries, ([self.ext], OLD, NEW))):
            with self.subTest(name):
                got = self.ok(call(**self.db))
                ref = self.ok(call(*explicit, os.path.join(self.tmp, "out")))
                self.assertEqual(got["summary"], ref["summary"])
                self.assertEqual(len(got["items"]), len(ref["items"]))
                self.assertIn("ext_newest", got)
        res = self.ok(srv.list_interceptors(**self.db))
        self.assertEqual(res["inputs"]["ext_root"], [self.ext])
        # the work copy of the registry joins rebase_control once it exists
        work = os.path.join(self.task, "work")
        shutil.copytree(WORK, work)
        res = self.ok(srv.rebase_control(**self.db))
        self.assertEqual(res["inputs"]["work"], work)
        self.assertTrue(all("work" in it for it in res["items"]))
        # explicit paths win over the registry
        res = self.ok(srv.check_control(WORK, **self.db))
        self.assertEqual(res["inputs"]["ext"], WORK)
        self.assertEqual(res["summary"]["fail"], 0)

    def test_port_replace_relative_modules(self):
        shutil.copytree(EXT, self.ext)
        res = self.ok(srv.port_replace(COMMON, COMMON, EXT_PRINT + "/" + COMMON, "ПодготовитьДанныеПечати",
                                       **self.db))
        self.assertEqual(res["inputs"]["old_module"], p(OLD, COMMON))
        self.assertEqual(res["inputs"]["new_module"], p(NEW, COMMON))
        self.assertEqual(res["inputs"]["copy_module"], p(p(self.ext, EXT_PRINT), COMMON))
        self.assertEqual(res["inputs"]["out"], self.reports)
        self.assertEqual(res["items"][0]["copy_method"], "Синт_ПечатьПодготовитьДанныеПечати")

    def test_bad_input(self):
        self.bad(srv.method_diff(), "нужен путь или db")
        self.bad(srv.check_calls(to=fx.TO), "только вместе с db")
        self.bad(srv.check_calls(db=fx.DB, registry=self.reg), "to:")
        self.bad(srv.check_calls(db="nope", to=fx.TO, registry=self.reg), "«nope»")
        self.bad(srv.rebase_control(db=fx.DB, to="11.5.99", registry=self.reg), "11.5.99")
        self.bad(srv.list_interceptors(db=fx.DB, to=fx.TO, registry=os.path.join(self.tmp, "нет.json")),
                 "реестр баз не найден")
        self.bad(srv.list_interceptors(**self.db), "path does not exist")  # nothing dumped yet

    def test_prepare(self):
        res = self.ok(srv.prepare(fx.DB, fx.TO, registry=self.reg))
        s = res["summary"]
        self.assertEqual((s["db"], s["from"], s["to"], s["ext"], s["dumped"]), (fx.DB, fx.FROM, fx.TO, self.ext, True))
        self.assertEqual(s["work"]["state"], "created")
        self.assertEqual(s["verdicts"]["REBASED"], 9)
        self.assertEqual(res["ext_newest"], s["ext_newest"])
        self.assertEqual(os.path.basename(os.path.dirname(res["report"]["json"])), "prepare-" + res["run_id"][:8])
        with open(res["report"]["json"], encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["run_id"], res["run_id"])
        self.assertEqual(set(res["report"]) - {"json", "plan"}, set(prep.TOOLS))
        for tool in prep.TOOLS + ("plan",):
            for kind in ("json", "md"):
                self.assertTrue(os.path.isfile(res["report"][tool][kind]), (tool, kind))
        self.assertEqual([os.path.basename(res["report"]["plan"][k]) for k in ("json", "md")], ["plan.json", "plan.md"])
        self.assertEqual((res["inputs"]["ext"], res["inputs"]["out"]), (self.ext, self.reports))
        again = self.ok(srv.prepare(fx.DB, fx.TO, registry=self.reg, dump=False))
        self.assertEqual(again["summary"]["work"]["state"], "existing")
        self.assertFalse(again["summary"]["dumped"])
        only = self.ok(srv.prepare(fx.DB, fx.TO, registry=self.reg, names=[fx.EXT_PRINT], only=fx.EXT_PRINT))
        self.assertEqual(only["summary"]["verdicts"]["REBASED"], 0)

    def test_prepare_out_of_perimeter(self):
        self.assertNotIn("out_of_perimeter", self.ok(srv.prepare(fx.DB, fx.TO, registry=self.reg))["summary"])
        base = os.path.join(self.tmp, "all")
        task = os.path.join(base, fx.TASK)
        _, ext_all = fx.make_ext_all(task)
        reg, _ = fx.make(base, db_cfg={"from": fx.FROM, "task_dir": task, "ext_dump": "before\\xml",
                                       "ext_all": "all\\xml"})
        res = self.ok(srv.prepare(fx.DB, fx.TO, registry=reg, dump=False))
        o = res["summary"]["out_of_perimeter"]
        self.assertEqual((o["ext_all"], o["perimeter"], o["extensions"], o["clean"]),
                         (ext_all, [fx.EXT_ALL_V2], 3, 1))
        self.assertEqual([(x["ext"], x["borrowed"]) for x in o["not_applied"]], [(fx.EXT_FOREIGN, 1)])
        self.assertTrue(o["not_applied"][0]["first"].startswith("COMPAT_MODE"))
        self.assertEqual([x["ext"] for x in o["control_only"]], [fx.EXT_MAIN])
        self.assertEqual(o["foreign"], {"error": 1, "risk": 0, "info": 1})
        datetime.fromisoformat(o["ext_all_newest"])
        self.assertEqual(set(res["report"]["out_of_perimeter"]), {"borrowed", "control_rebase"})
        # the outside extensions are not items; the perimeter's dependency on one that will not be applied is
        self.assertFalse({x["ext"] for x in res["items"]} & {fx.EXT_MAIN, fx.EXT_FOREIGN, fx.EXT_CLEAN})
        self.assertTrue(any("Синт_Дата" in x["what"][0] for x in res["items"]))

    def test_prepare_never_dumps_a_proof_corpus(self):
        reg, task = fx.make(os.path.join(self.tmp, "proof"), db_cfg={
            "from": fx.FROM, "task_dir": os.path.join(self.tmp, "proof", fx.TASK), "ext_dump": "before\\xml",
            "proof_corpus": True})
        res = self.bad(srv.prepare(fx.DB, fx.TO, registry=reg), "dump=False")
        self.assertIn(fx.DB, res["error"]["message"])
        self.assertIn("proof_corpus", res["error"]["message"])
        self.assertFalse(os.path.exists(os.path.join(task, "before")))  # the dump command did not run
        shutil.copytree(EXT, os.path.join(task, "before", "xml"))
        res = self.ok(srv.prepare(fx.DB, fx.TO, registry=reg, dump=False))
        self.assertFalse(res["summary"]["dumped"])

    def test_check_work_needs_no_release(self):
        reg, _ = fx.make(os.path.join(self.tmp, "lint"), releases={},
                         db_cfg={"task_dir": self.tmp, "ext_dump": EXT, "work": WORK})
        res = self.ok(srv.check_work(db=fx.DB, registry=reg))
        self.assertEqual((res["inputs"]["work"], res["inputs"]["ext"]), (WORK, EXT))
        self.assertEqual(res["summary"]["errors"], 0)

    def test_prepare_errors(self):
        self.bad(srv.prepare(fx.DB, fx.TO, registry=self.reg, dump=False), "нет выгрузок расширений")
        with mock.patch.dict(os.environ, {"CFE_FAKE_DUMP_FAIL": "1"}):
            res = self.bad(srv.prepare(fx.DB, fx.TO, registry=self.reg), "выгрузка завершилась с кодом 5",
                           code="external")
        self.assertIn("недоступна", res["console"])
        self.bad(srv.prepare("nope", fx.TO, registry=self.reg), "«nope»")


@unittest.skipUnless(HAVE_MCP, "MCP SDK is not installed: pip install -e \".[mcp]\"")
@unittest.skipUnless(HAVE_GIT, "git is not on PATH: merge-copy merges with git merge-file")
class PortReplaceKinds(unittest.TestCase):
    """port_replace tells same-point insertions apart, as rebase_control does (`same_point`)."""

    def test_same_point_insertion(self):
        body = "Процедура Тест() Экспорт\r\n\tА = 1;\r\n%s\tБ = 2;\r\nКонецПроцедуры\r\n"
        with tempfile.TemporaryDirectory() as d:
            paths = {}
            for name, text in (("old", body % ""), ("new", body % "\tВ = 3;\r\n"),
                               ("copy", "&Вместо(\"Тест\")\r\n" + body.replace("Тест()", "Расш_Тест()")
                                % "\tДобавкаАвтора = Истина;\r\n")):
                os.makedirs(os.path.join(d, name))
                paths[name] = os.path.join(d, name, "Module.bsl")
                with open(paths[name], "wb") as fh:
                    fh.write(BOM + text.encode("utf-8"))
            res = srv.port_replace(paths["old"], paths["new"], paths["copy"], "Тест", os.path.join(d, "out"))
            self.assertEqual(res["status"], "ok", res)
            item = res["items"][0]
            self.assertEqual((item["conflicts"], item["same_point"]), (1, 1))
            self.assertNotIn("backport", item)  # the two insertions are not alike

    def test_whitespace_only_conflict_is_auto_resolved(self):
        # the vendor only re-spaced a line next to the author's edit: git sees a conflict, the rules take the copy
        body = "Процедура Тест() Экспорт\r\n%s\r\n%s\r\nКонецПроцедуры\r\n"
        with tempfile.TemporaryDirectory() as d:
            paths = {}
            for name, text in (("old", body % ("\tА = 1;", "\tБ = 1;")), ("new", body % ("\tА  =  1;", "\tБ = 1;")),
                               ("copy", "&Вместо(\"Тест\")\r\n" + (body % ("\tА = 1;", "\tБ = 2;"))
                                .replace("Тест()", "Расш_Тест()"))):
                os.makedirs(os.path.join(d, name))
                paths[name] = os.path.join(d, name, "Module.bsl")
                with open(paths[name], "wb") as fh:
                    fh.write(BOM + text.encode("utf-8"))
            res = srv.port_replace(paths["old"], paths["new"], paths["copy"], "Тест", os.path.join(d, "out"))
            self.assertEqual(res["status"], "ok", res)
            item = res["items"][0]
            self.assertGreater(item.get("auto_resolved", 0), 0, item)
            self.assertEqual(item["conflicts"], 0)
            self.assertIn("\tБ = 2;", fileio.read_lines(item["merged"]))

    def write(self, d, texts):
        paths = {}
        for name, text in texts.items():
            os.makedirs(os.path.join(d, name))
            paths[name] = os.path.join(d, name, "Module.bsl")
            with open(paths[name], "wb") as fh:
                fh.write(BOM + text.replace("\n", "\r\n").encode("utf-8"))
        return paths

    def test_author_block_moved_and_copy_lines(self):
        from tests.unit.test_merge_copy import CHAIN_COPY, CHAIN_NEW, CHAIN_OLD
        with tempfile.TemporaryDirectory() as d:
            paths = self.write(d, {"old": "\n".join(CHAIN_OLD), "new": "\n".join(CHAIN_NEW),
                                   "copy": "&Вместо(\"П\")\n" + "\n".join(CHAIN_COPY) + "\n"})
            res = srv.port_replace(paths["old"], paths["new"], paths["copy"], "П", os.path.join(d, "out"))
            self.assertEqual(res["status"], "ok", res)
            item = res["items"][0]
            self.assertEqual(item["flags"], ["AUTHOR_BLOCK_MOVED"])
            self.assertEqual(item["copy_lines"], [2, len(CHAIN_COPY) + 1])
            self.assertEqual(item["author_moved"][0]["merged_line"], 14)
            self.assertEqual(res["summary"]["author_moved"], 1)
            self.assertGreater(item["duplicated_structural"], 0)
            self.assertNotIn("duplicated", item)

    def test_wrapper_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            paths = self.write(d, {"old": "Процедура П()\n\tА = 1;\nКонецПроцедуры",
                                   "new": "Процедура П()\n\tА = 2;\nКонецПроцедуры",
                                   "copy": "&Вместо(\"П\")\nПроцедура Расш_П()\n\tПродолжитьВызов();\nКонецПроцедуры"})
            res = srv.port_replace(paths["old"], paths["new"], paths["copy"], "П", os.path.join(d, "out"))
            self.assertEqual(res["error"]["code"], "bad_input", res)
            self.assertIn("ПродолжитьВызов", res["error"]["message"])


class ControlSignature(unittest.TestCase):
    """An &ИзменениеИКонтроль with nothing to port in the body but a header that does not fit new is not a pass."""

    def test_check_control_fails_and_rebase_control_says_not_ready(self):
        from tests.unit.test_control_signature import make_dumps
        with tempfile.TemporaryDirectory() as d:
            ext, old, new = make_dumps(d)
            res = srv.check_control(ext, old, new, os.path.join(d, "out"))
            self.assertEqual(res["status"], "ok", res.get("error"))
            item = res["items"][0]
            self.assertEqual((item["result"], item["verdict"]), ("fail", "UNCHANGED"))
            self.assertEqual(item["sig_ext_vs_new"], ["параметров 1 ≠ 2"])
            self.assertEqual(item["problems"], ["сигнатура против new: параметров 1 ≠ 2"])
            self.assertIn("не подходит к new", item["note"])
            self.assertEqual((res["summary"]["pass"], res["summary"]["fail"]), (0, 1))
            item = srv.rebase_control(ext, old, new, os.path.join(d, "out"))["items"][0]
            self.assertEqual(item["verdict"], "UNCHANGED")
            self.assertTrue(item["problems"] and item["note"])
            ext, old, new = make_dumps(os.path.join(d, "fits"), ext_header="Процедура ЕГ_П(А, Б)")
            res = srv.check_control(ext, old, new, os.path.join(d, "out"))
            self.assertEqual(res["items"][0]["result"], "pass")


class RegisterFields(unittest.TestCase):
    """check_registers passes to_res and key_lost: a dimension the vendor turned into a resource or an attribute."""

    def test_key_lost_reaches_the_item(self):
        from tests.unit.test_borrowed import HEAD, child, obj, write
        with tempfile.TemporaryDirectory() as d:
            for root, children in (("old", [child("Dimension", "Склад"), child("Dimension", "Товар")]),
                                   ("new", [child("Dimension", "Товар"), child("Resource", "Склад")])):
                write(os.path.join(d, root, "InformationRegisters", "Цены.xml"), obj("InformationRegister", "Цены",
                                                                                      children))
                write(os.path.join(d, root, "Configuration.xml"), HEAD + '<Configuration uuid="c-%s"><Properties>'
                      '<Name>Типовая</Name></Properties></Configuration></MetaDataObject>' % root)
            ext = os.path.join(d, "ext", "Расш")
            write(os.path.join(ext, "Configuration.xml"), HEAD + '<Configuration uuid="c-ext"><Properties><Name>Расш'
                  '</Name></Properties></Configuration></MetaDataObject>')
            write(os.path.join(ext, "CommonModules", "Расш_Цены", "Ext", "Module.bsl"), "\n".join([
                "Процедура Очистить(Склад) Экспорт", "\tНабор = РегистрыСведений.Цены.СоздатьНаборЗаписей();",
                "\tНабор.Отбор.Склад.Установить(Склад);", "\tНабор.Записать();", "КонецПроцедуры"]))
            res = srv.check_registers([os.path.join(d, "ext")], os.path.join(d, "old"), os.path.join(d, "new"),
                                      os.path.join(d, "out"))
            self.assertEqual(res["status"], "ok", res.get("error"))
            item = next(it for it in res["items"] if it["method"] == "Очистить")
            self.assertEqual((item["to_res"], item["key_lost"]), (["Склад"], ["Склад"]))
            self.assertTrue(os.path.isfile(item["path"]))


class Selection(unittest.TestCase):
    """module / object / flag / severity, baseline and accepted of the check tools on the synthetic corpus:
    the work copy (its extension renamed) against the dumps it was ported from."""
    CHECKS = (srv.list_interceptors, srv.check_calls, srv.check_queries, srv.check_borrowed, srv.check_registers,
              srv.check_reads, srv.check_work, srv.check_names)

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cfe_sel_")
        cls.out = os.path.join(cls.tmp, "out")
        cls.calls = srv.check_calls([EXT], OLD, NEW, cls.out)

    @classmethod
    def tearDownClass(cls):
        rmtree(cls.tmp)

    def test_parameters_are_optional(self):
        for fn in self.CHECKS:
            params = inspect.signature(fn).parameters
            for name in srv.SELECT_ARGS:
                self.assertIsNone(params[name].default, (fn.__name__, name))
            self.assertIn("baseline", fn.__doc__)

    def test_filter_applies_before_the_cap_and_summary_counts_all(self):
        full = self.calls
        res = srv.check_calls([EXT], OLD, NEW, self.out, module="Синт_ЗаказыСервер")
        self.assertEqual(res["status"], "ok", res.get("error"))
        self.assertEqual(res["summary"]["findings"], full["summary"]["findings"])
        self.assertEqual(res["summary"]["select"]["filter"], {"module": "Синт_ЗаказыСервер"})
        self.assertEqual(res["summary"]["select"]["matched"], len(res["items"]))
        self.assertTrue(res["items"])
        self.assertTrue(all(it["module_title"] == "ОбщийМодуль.Синт_ЗаказыСервер" for it in res["items"]))
        self.assertLess(len(res["items"]), len(full["items"]))
        with open(res["report"]["selection"]["json"], encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)["findings"]), len(res["items"]))
        self.assertNotIn("select", full["summary"])
        self.assertNotIn("selection", full["report"])
        self.assertNotIn("key", full["items"][0])
        flagged = srv.check_calls([EXT], OLD, NEW, self.out, flag=["TOO_FEW_ARGS"], object="Синт_ЗаказыСервер")
        self.assertEqual([it["flags"] for it in flagged["items"]], [["TOO_FEW_ARGS"]])

    def test_baseline_of_dumps_and_of_a_report_agree(self):
        by_dump = srv.check_calls([WORK], OLD, NEW, self.out, baseline=EXT)
        by_report = srv.check_calls([WORK], OLD, NEW, self.out,
                                    baseline=os.path.dirname(self.calls["report"]["json"]))
        for res in (by_dump, by_report):
            self.assertEqual(res["status"], "ok", res.get("error"))
            sel = res["summary"]["select"]
            d = sel["delta"]
            self.assertEqual(d["new"] + d["kept"], res["summary"]["findings"])
            self.assertEqual(d["kept"] + d["gone"], self.calls["summary"]["findings"])
            self.assertEqual(sel["renamed_ext"], {EXT_MAIN: "Синт_Основное_11_5_27_01_10_2026_v_1_0"})
            self.assertTrue(all(it["delta"] in ("new", "kept", "gone") and it["key"] for it in res["items"]))
            gone = [it for it in res["items"] if it["delta"] == "gone"]
            self.assertTrue(gone)
            self.assertEqual(res["items"][-len(gone):], gone)
        self.assertEqual(by_dump["summary"]["select"]["delta"], by_report["summary"]["select"]["delta"])
        # a gone row of a run on dumps has its path; of a report - the module relative to the extension
        self.assertTrue(all(os.path.isfile(it["path"]) for it in by_dump["items"]))
        self.assertTrue(all("module" in it for it in by_report["items"] if it["delta"] == "gone"))
        self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(by_dump["report"]["json"]), "baseline",
                                                    "calls.json")))

    def test_baseline_is_the_root_of_a_prepare_report(self):
        # prepare writes <folder>/<tool>/<report>.json: its root folder is a baseline of every check it ran
        root = os.path.join(self.tmp, "prepare-root")
        os.makedirs(os.path.join(root, "calls"), exist_ok=True)
        shutil.copy(self.calls["report"]["json"], os.path.join(root, "calls", "calls.json"))
        res = srv.check_calls([WORK], OLD, NEW, self.out, baseline=root)
        self.assertEqual(res["status"], "ok", res.get("error"))
        by_folder = srv.check_calls([WORK], OLD, NEW, self.out, baseline=os.path.join(root, "calls"))
        self.assertEqual(res["summary"]["select"]["delta"], by_folder["summary"]["select"]["delta"])
        self.assertFalse(os.path.isdir(os.path.join(os.path.dirname(res["report"]["json"]), "baseline")))

    def test_the_same_dumps_are_all_kept(self):
        for fn in (srv.check_queries, srv.check_borrowed, srv.list_interceptors):
            res = fn([EXT], OLD, NEW, self.out, baseline=EXT)
            self.assertEqual(res["status"], "ok", (fn.__name__, res.get("error")))
            d = res["summary"]["select"]["delta"]
            self.assertEqual((d["new"], d["gone"]), (0, 0), fn.__name__)

    def test_accepted(self):
        first = srv.check_calls([WORK], OLD, NEW, self.out, baseline=EXT)
        target = next(it for it in first["items"] if it["delta"] != "gone")
        path = os.path.join(self.tmp, "accepted.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump([{"key": target["key"], "reason": "вызов уходит вместе с формой"},
                       {"key": "нет|такого|ключа", "reason": "устарело"}], fh, ensure_ascii=False)
        res = srv.check_calls([WORK], OLD, NEW, self.out, baseline=EXT, accepted=path)
        sel = res["summary"]["select"]
        self.assertEqual((sel["accepted"], sel["accepted_unused"]), (1, 1))
        self.assertEqual(sel["open"], sel["delta"]["new"] + sel["delta"]["kept"] - 1)
        hit = [it for it in res["items"] if it.get("accepted")]
        self.assertEqual([(it["key"], it["accepted"]) for it in hit], [(target["key"], "вызов уходит вместе с формой")])

    def test_bad_selection_input(self):
        bad_json = os.path.join(self.tmp, "bad.json")
        with open(bad_json, "w", encoding="utf-8") as fh:
            fh.write("[1, 2")
        queries = srv.check_queries([EXT], OLD, NEW, self.out)
        cases = [(srv.check_calls, {"severity": "fatal"}, "severity"),
                 (srv.check_calls, {"baseline": "relative/calls.json"}, "must be absolute"),
                 (srv.check_calls, {"baseline": os.path.join(self.tmp, "nope.json")}, "does not exist"),
                 (srv.check_calls, {"baseline": queries["report"]["json"]}, "нужен calls"),
                 (srv.check_calls, {"baseline": self.tmp}, "ни отчёта calls.json, ни выгрузок"),
                 (srv.check_calls, {"accepted": bad_json}, "accepted")]
        for fn, kw, words in cases:
            res = fn([EXT], OLD, NEW, self.out, **kw)
            self.assertEqual((res["status"], res["error"]["code"]), ("error", "bad_input"), kw)
            self.assertIn(words, res["error"]["message"], kw)
        res = srv.check_work(WORK, EXT, self.out, baseline=EXT)
        self.assertEqual(res["error"]["code"], "bad_input")
        self.assertIn("только отчёт", res["error"]["message"])


@unittest.skipUnless(HAVE_MCP, "MCP SDK is not installed: pip install -e \".[mcp]\"")
@unittest.skipUnless(HAVE_GIT, "git is not on PATH: control_rebase merges with git merge-file")
class StdioTransport(unittest.TestCase):
    def test_round_trip(self):
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        tmp = tempfile.mkdtemp(prefix="cfe_mcp_stdio_")
        self.addCleanup(shutil.rmtree, tmp, True)

        async def session():
            # the package the tests import, not whatever copy is installed (the default environment of the stdio
            # client drops PYTHONPATH); the rest of this process's environment kept
            src = os.path.dirname(os.path.dirname(os.path.abspath(srv.__file__)))
            params = StdioServerParameters(command=sys.executable, args=["-m", "cfe_rebase", "mcp"], cwd=tmp,
                                           env=dict(os.environ, PYTHONPATH=src))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as s:
                    await s.initialize()
                    tools = await s.list_tools()
                    res = await s.call_tool("rebase_control", {"ext": EXT, "old": OLD, "new": NEW,
                                                               "out": os.path.join(tmp, "out")})
                    return tools, res

        tools, res = asyncio.run(asyncio.wait_for(session(), 120))
        self.assertEqual({t.name for t in tools.tools}, TOOL_NAMES)
        data = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
        if data is None:
            data = json.loads(res.content[0].text)
        self.assertEqual(data["status"], "ok", data.get("error"))
        self.assertEqual(data["summary"]["REBASED"], 9)


class Instructions(unittest.TestCase):
    def test_every_writer_is_named(self):
        # not «only apply_candidate writes»: prepare runs the dump into ext and makes the work copy, applied_text too
        text = srv.INSTRUCTIONS
        self.assertNotIn("только apply_candidate", text)
        for words in ("apply_candidate — модуль рабочей копии", "applied_text — копию модулей в dst",
                      "prepare — запускает команду выгрузки расширений в ext", "dump=False", "создаёт рабочую копию"):
            self.assertIn(words, text)

    def test_prepare_names_what_the_plan_does_not_cover(self):
        # the same gaps as plan.NOT_COVERED: own copies of DCS / templates and register writes through a structure
        doc = " ".join(srv.prepare.__doc__.split())
        for words in ("копии СКД и макетов вендора", "ЗаполнитьЗначенияСвойств или структуру", "proof_corpus",
                      "CFE_REBASE_DUMP_TIMEOUT"):
            self.assertIn(words, doc)


class InstallHint(unittest.TestCase):
    def test_without_the_sdk(self):
        code = "import sys; sys.modules['mcp'] = None; from cfe_rebase.cli import main; main(['mcp'])"
        cwd = tempfile.mkdtemp(prefix="cfe_mcp_hint_")  # an empty folder: nothing on sys.path[0] shadows the stdlib
        self.addCleanup(shutil.rmtree, cwd, True)
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, cwd=cwd)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("cfe-rebase[mcp]", proc.stderr.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
