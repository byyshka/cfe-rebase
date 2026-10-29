# -*- coding: utf-8 -*-
"""COMPAT_MODE (borrowed) and EXT_NAME_STALE / EXT_NAME_STALE_DUMP_INFO (lint): the two refusals of a real load
(УТ 11.5.17.172 -> 11.5.27.79). Fragments of the XML follow that extension."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from cfe_rebase import borrowed, lint, mcp_server, metadata, plan, selection

HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" '
        'xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:xr="http://v8.1c.ru/8.3/xcf/readable" version="2.20">\n')
OLD_NAME = "МоёРасширение_v_43"
NEW_NAME = "МоёРасширение_11_5_27_79_v_44"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))


def ext_config(name, compat=None, state="Notify", ecm="Version8_3_21"):
    """Configuration.xml of an extension as a real one has it: CompatibilityMode on control in InternalInfo."""
    internal = ("<InternalInfo>\n\t\t\t<xr:PropertyState>\n\t\t\t\t<xr:Property>CompatibilityMode</xr:Property>\n"
                "\t\t\t\t<xr:State>%s</xr:State>\n\t\t\t</xr:PropertyState>\n\t\t</InternalInfo>\n" % state
                if compat and state else "")
    return (HEAD + '\t<Configuration uuid="00000000-0000-4000-8000-000000000001">\n\t\t' + internal +
            "\t\t<Properties>\n\t\t\t<ObjectBelonging>Adopted</ObjectBelonging>\n\t\t\t<Name>%s</Name>\n"
            "\t\t\t<ConfigurationExtensionPurpose>Customization</ConfigurationExtensionPurpose>\n"
            "\t\t\t<NamePrefix>мр_</NamePrefix>\n"
            "\t\t\t<ConfigurationExtensionCompatibilityMode>%s</ConfigurationExtensionCompatibilityMode>\n"
            "%s\t\t</Properties>\n\t\t<ChildObjects>\n\t\t\t<Language>Русский</Language>\n\t\t</ChildObjects>\n"
            "\t</Configuration>\n</MetaDataObject>" % (
                name, ecm, "\t\t\t<CompatibilityMode>%s</CompatibilityMode>\n" % compat if compat else ""))


def vendor_config(compat):
    return (HEAD + '\t<Configuration uuid="c-vendor">\n\t\t<Properties>\n\t\t\t<Name>УправлениеТорговлей</Name>\n'
            "\t\t\t<ConfigurationExtensionCompatibilityMode>Version8_3_27</ConfigurationExtensionCompatibilityMode>\n"
            "\t\t\t<CompatibilityMode>%s</CompatibilityMode>\n\t\t</Properties>\n\t</Configuration>\n"
            "</MetaDataObject>" % compat)


def vendor_dump(root, compat):
    write(os.path.join(root, "Configuration.xml"), vendor_config(compat))
    write(os.path.join(root, "Catalogs", "Склады.xml"),
          HEAD + '<Catalog uuid="o-1"><Properties><Name>Склады</Name></Properties></Catalog></MetaDataObject>')


def rights(cfg_name):
    """Rights.xml of a role of a real extension (БазовыеПраваУТ): rights on its own objects and on the extension itself."""
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<Rights xmlns="http://v8.1c.ru/8.2/roles" version="2.20">\n'
            "\t<setForNewObjects>false</setForNewObjects>\n\t<object>\n\t\t<name>Report.мр_ПоМенеджерам</name>\n"
            "\t\t<right>\n\t\t\t<name>Use</name>\n\t\t\t<value>true</value>\n\t\t</right>\n\t</object>\n"
            "\t<object>\n\t\t<name>Configuration.%s</name>\n\t\t<right>\n\t\t\t<name>MainWindowModeNormal</name>\n"
            "\t\t\t<value>true</value>\n\t\t</right>\n\t</object>\n</Rights>" % cfg_name)


def dump_info(cfg_name, child=False):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<ConfigDumpInfo xmlns="http://v8.1c.ru/8.3/xcf/dumpinfo" '
            'format="Hierarchical" version="2.20">\n\t<ConfigVersions>\n'
            '\t\t<Metadata name="CommonPicture.ЭлектронноеПисьмо" id="c5448e53" configVersion="fde2"/>\n'
            '\t\t<Metadata name="Configuration.%s" id="00000000-0000-4000-8000-000000000001" configVersion="36d5"/>\n'
            '%s\t</ConfigVersions>\n</ConfigDumpInfo>' % (
                cfg_name, '\t\t<Metadata name="Configuration.%s.ManagedApplicationModule" id="x.1" configVersion="a"/>'
                          '\n' % cfg_name if child else ""))


class CompatMode(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_compat_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        vendor_dump(os.path.join(self.tmp, "old"), "Version8_3_24")
        vendor_dump(os.path.join(self.tmp, "new"), "Version8_3_27")
        self.ext = os.path.join(self.tmp, "ext", OLD_NAME)
        self.out = os.path.join(self.tmp, "out")

    def run_tool(self):
        with contextlib.redirect_stdout(io.StringIO()):
            borrowed.main(["--ext-root", os.path.join(self.tmp, "ext"), "--config-old", os.path.join(self.tmp, "old"),
                           "--config-new", os.path.join(self.tmp, "new"), "--out", self.out])
        with open(os.path.join(self.out, "borrowed.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_dump_reads_the_mode_not_the_extension_mode(self):
        write(os.path.join(self.ext, "Configuration.xml"), ext_config(OLD_NAME, "Version8_3_24"))
        d = metadata.Dump(self.ext)
        self.assertEqual((d.compat, d.compat_state), ("Version8_3_24", "Notify"))
        self.assertEqual(metadata.Dump(os.path.join(self.tmp, "new")).compat, "Version8_3_27")

    def test_real_mode_of_the_old_release_is_an_error_in_the_plan(self):
        write(os.path.join(self.ext, "Configuration.xml"), ext_config(OLD_NAME, "Version8_3_24"))
        data = self.run_tool()
        found = [f for f in data["findings"] if f["check"] == "COMPAT_MODE"]
        self.assertEqual(len(found), 1)
        f = found[0]
        self.assertEqual((f["ext"], f["file"], f["ext_value"], f["new_value"]),
                         (OLD_NAME, "Configuration.xml", "Version8_3_24", "Version8_3_27"))
        self.assertIn("Notify", f["detail"])
        self.assertIn("ConfigurationExtensionCompatibilityMode не трогать", f["detail"])
        self.assertEqual(selection.level("borrowed", f), "error")
        p = plan.build({}, {}, {}, {}, {}, data)
        item = next(x for x in p["items"] if x["module"] == "Configuration.xml")
        self.assertEqual(item["group"], "MUST_FIX")
        self.assertTrue(item["what"][0].startswith("расширение: режим совместимости расширения Version8_3_24"))
        self.assertIn("borrowed:COMPAT_MODE", item["sources"])

    def test_same_mode_absent_mode_and_extension_mode_are_not_findings(self):
        # the extension mode (8_3_21 under 8_3_27) was accepted by the platform in a real load: never compared
        for cfg in (ext_config(OLD_NAME, "Version8_3_27", ecm="Version8_3_14"),  # other real extensions: already 8_3_27
                    ext_config(OLD_NAME, None),  # most extensions have no CompatibilityMode at all
                    ext_config(OLD_NAME, "Version8_3_27", state=None)):
            write(os.path.join(self.ext, "Configuration.xml"), cfg)
            shutil.rmtree(self.out, True)
            self.assertEqual([f for f in self.run_tool()["findings"] if f["check"] == "COMPAT_MODE"], [])

    def test_check_borrowed_answers_with_both_values(self):
        write(os.path.join(self.ext, "Configuration.xml"), ext_config(OLD_NAME, "Version8_3_24"))
        res = mcp_server.check_borrowed([os.path.join(self.tmp, "ext")], os.path.join(self.tmp, "old"),
                                        os.path.join(self.tmp, "new"), self.out, flag=["COMPAT_MODE"])
        self.assertEqual(res["status"], "ok", res)
        self.assertEqual([(x["check"], x["ext_value"], x["new_value"], x["path"]) for x in res["items"]],
                         [("COMPAT_MODE", "Version8_3_24", "Version8_3_27", os.path.join(self.ext, "Configuration.xml"))])

    def test_mode_without_control_state_is_still_compared(self):
        # not proven that the platform skips a mode without <xr:PropertyState>: reported, the detail has no state
        write(os.path.join(self.ext, "Configuration.xml"), ext_config(OLD_NAME, "Version8_3_24", state=None))
        f = next(f for f in self.run_tool()["findings"] if f["check"] == "COMPAT_MODE")
        self.assertNotIn("на контроле", f["detail"])


class ExtNameStale(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_extname_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # the folder keeps the old name, as in a real work copy: cf-edit changed only Name
        self.root = os.path.join(self.tmp, "work", OLD_NAME)
        write(os.path.join(self.root, "Configuration.xml"), ext_config(NEW_NAME, "Version8_3_27"))
        for role in ("БазовыеПраваУТ", "Доп_РазрешитьИзменениеРО"):
            write(os.path.join(self.root, "Roles", role, "Ext", "Rights.xml"), rights(OLD_NAME))
        write(os.path.join(self.root, "ConfigDumpInfo.xml"), dump_info(OLD_NAME, child=True))

    def issues(self):
        issues, _ = lint.lint(os.path.join(self.tmp, "work"))
        return [x for x in issues if x["check"].startswith("EXT_NAME_STALE")]

    def test_refused_load_roles_and_dump_info_keep_the_old_name(self):
        got = self.issues()
        self.assertEqual([(x["file"], x["check"], x["severity"], x["line"]) for x in got], [
            ("Roles/БазовыеПраваУТ/Ext/Rights.xml", "EXT_NAME_STALE", "error", 12),
            ("Roles/Доп_РазрешитьИзменениеРО/Ext/Rights.xml", "EXT_NAME_STALE", "error", 12),
            ("ConfigDumpInfo.xml", "EXT_NAME_STALE_DUMP_INFO", "info", 5)])
        role = got[0]
        self.assertEqual((role["old_name"], role["new_name"]), (OLD_NAME, NEW_NAME))
        self.assertIn("Неизвестный объект метаданных - Configuration.%s" % OLD_NAME, role["detail"])
        self.assertIn("заменить Configuration.%s на Configuration.%s" % (OLD_NAME, NEW_NAME), role["detail"])
        self.assertIn("ссылок: 2", got[2]["detail"])  # Configuration.X and Configuration.X.ManagedApplicationModule
        self.assertEqual(selection.level("lint", role), "error")
        self.assertEqual(selection.level("lint", got[2]), "info")

    def test_hand_fix_fixed_files_are_clean(self):
        for role in ("БазовыеПраваУТ", "Доп_РазрешитьИзменениеРО"):
            write(os.path.join(self.root, "Roles", role, "Ext", "Rights.xml"), rights(NEW_NAME))
        write(os.path.join(self.root, "ConfigDumpInfo.xml"), dump_info(NEW_NAME, child=True))
        self.assertEqual(self.issues(), [])

    def test_check_work_answers_with_both_names_and_selects_by_flag(self):
        res = mcp_server.check_work(os.path.join(self.tmp, "work"), out=os.path.join(self.tmp, "out"),
                                    flag=["EXT_NAME_STALE"])
        self.assertEqual(res["status"], "ok", res)
        self.assertEqual(res["summary"]["checks"], {"EXT_NAME_STALE": 2, "EXT_NAME_STALE_DUMP_INFO": 1})
        self.assertEqual((res["summary"]["errors"], res["summary"]["info"]), (2, 1))
        self.assertEqual({(x["check"], x["old_name"], x["new_name"]) for x in res["items"]},
                         {("EXT_NAME_STALE", OLD_NAME, NEW_NAME)})
        self.assertTrue(all(os.path.isfile(x["path"]) for x in res["items"]))

    def test_case_only_difference_and_other_files_are_not_findings(self):
        for role in ("БазовыеПраваУТ", "Доп_РазрешитьИзменениеРО"):
            write(os.path.join(self.root, "Roles", role, "Ext", "Rights.xml"), rights(NEW_NAME.upper()))
        write(os.path.join(self.root, "ConfigDumpInfo.xml"), dump_info(NEW_NAME))
        # a Rights.xml-like text outside Roles/<role>/Ext is not a role
        write(os.path.join(self.root, "Templates", "Макет", "Ext", "Rights.xml"), rights(OLD_NAME))
        self.assertEqual(self.issues(), [])


if __name__ == "__main__":
    unittest.main()
