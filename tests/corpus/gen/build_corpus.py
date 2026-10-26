# -*- coding: utf-8 -*-
"""Generator of the synthetic corpus tests/corpus/synthetic: old/, new/, ext/ and work/ (expected/ and README.md are
not generated). work/ is assembled from the REBASED candidates of cfe_rebase control-rebase, run on the generated
dumps, plus the hand ports of bsl_texts.WORK_PORTS.

    python tests/corpus/gen/build_corpus.py --out <folder>   # write the corpus into <folder> (absent or empty)
    python tests/corpus/gen/build_corpus.py --check          # build into a temporary folder, compare byte by byte

The generator never writes into tests/corpus/synthetic: the committed corpus is the source of truth, and --check
proves that the generator reproduces it (every file except expected/ and README.md, the file list included).
Exit code of --check: 0 - identical; 1 - differences, each one printed.
"""
import argparse
import filecmp
import json
import os
import shutil
import sys
import tempfile

from cfe_rebase import control_rebase, fileio

if __package__:
    from . import bsl_texts as T
    from .xmlgen import LF_NS, child, el, md_doc, render, synonym, t_num, t_ref, t_str, uid
else:
    import bsl_texts as T
    from xmlgen import LF_NS, child, el, md_doc, render, synonym, t_num, t_ref, t_str, uid

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.abspath(os.path.join(HERE, "..", "synthetic"))
# hand-written parts of the committed corpus, outside the generator
NOT_GENERATED = ("expected", "README.md")

BOM = b"\xef\xbb\xbf"
VENDOR = "ТиповаяДемо"
EXT_MAIN = "Синт_Основное"
EXT_PRINT = "Синт_Печать"
WORK_MAIN = "Синт_Основное_11_5_27_01_10_2026_v_1_0"


# ------------------------------------------------------------------------------------------------------------------
# Bytes
# ------------------------------------------------------------------------------------------------------------------

def write(rel_root, rel, text, newline="crlf", bom=True):
    """text uses '\\n'; newline: crlf | lf; a line ending with '¶' always gets a bare LF."""
    lines = text.split("\n")
    trailing = lines and lines[-1] == ""
    if trailing:
        lines = lines[:-1]
    chunks = []
    for line in lines:
        if line.endswith("¶"):
            chunks.append(line[:-1].encode("utf-8") + b"\n")
        else:
            chunks.append(line.encode("utf-8") + (b"\r\n" if newline == "crlf" else b"\n"))
    data = b"".join(chunks)
    if not trailing and data:
        data = data[:-2] if data.endswith(b"\r\n") else data[:-1]
    if bom:
        data = BOM + data
    path = os.path.join(rel_root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


# ------------------------------------------------------------------------------------------------------------------
# Metadata XML
# ------------------------------------------------------------------------------------------------------------------

def configuration(name, version, key, children, purpose=None, synonym_text=""):
    props = []
    if purpose:
        props.append(el("ObjectBelonging", text="Adopted"))
    props += [el("Name", text=name), synonym(synonym_text), el("Comment")]
    if purpose:
        props += [el("ConfigurationExtensionPurpose", text=purpose),
                  el("KeepMappingToExtendedConfigurationObjectsByIDs", text="true"),
                  el("NamePrefix", text="Синт_"),
                  el("ConfigurationExtensionCompatibilityMode", text="Version8_3_27")]
    else:
        props.append(el("NamePrefix"))
    props += [el("DefaultRunMode", text="ManagedApplication"), el("ScriptVariant", text="Russian"),
              el("Vendor", text="" if purpose else "Синтетика"), el("Version", text=version),
              el("DefaultLanguage", text="Language.Русский")]
    if not purpose:
        props.append(el("CompatibilityMode", text="Version8_3_27"))
    co = el("ChildObjects", *[el(tag, text=n) for tag, n in children])
    return md_doc(el("Configuration", el("Properties", *props), co, attrs={"uuid": uid(key)}))


def language(adopted_by=None):
    key = "ext:%s:Language.Русский" % adopted_by if adopted_by else "Language.Русский"
    props = []
    if adopted_by:
        props += [el("ObjectBelonging", text="Adopted"), el("Name", text="Русский"), el("Comment"),
                  el("ExtendedConfigurationObject", text=uid("Language.Русский")), el("LanguageCode", text="ru")]
    else:
        props += [el("Name", text="Русский"), synonym("Русский"), el("Comment"), el("LanguageCode", text="ru")]
    return md_doc(el("Language", el("Properties", *props), attrs={"uuid": uid(key)}))


def common_module(name, server=True, client=False, adopted_by=None):
    key = "ext:%s:CommonModule.%s" % (adopted_by, name) if adopted_by else "CommonModule." + name
    if adopted_by:
        props = [el("ObjectBelonging", text="Adopted"), el("Name", text=name), el("Comment"),
                 el("ExtendedConfigurationObject", text=uid("CommonModule." + name))]
    else:
        props = [el("Name", text=name), el("Synonym"), el("Comment"), el("Global", text="false"),
                 el("ClientManagedApplication", text="true" if client else "false"),
                 el("Server", text="true" if server else "false"),
                 el("ExternalConnection", text="true" if server else "false"),
                 el("ClientOrdinaryApplication", text="true" if client else "false"),
                 el("ServerCall", text="false"), el("Privileged", text="false"),
                 el("ReturnValuesReuse", text="DontUse")]
    return md_doc(el("CommonModule", el("Properties", *props), attrs={"uuid": uid(key)}))


def catalog(name, attrs):
    key = "Catalog." + name
    kids = [child("Attribute", "%s.Attribute.%s" % (key, a), a, typ) for a, typ in attrs]
    return md_doc(el("Catalog", el("Properties", el("Name", text=name), el("Synonym"), el("Comment"),
                                   el("Hierarchical", text="false"), el("CodeLength", text="9"),
                                   el("DescriptionLength", text="100")),
                     el("ChildObjects", *kids) if kids else el("ChildObjects"), attrs={"uuid": uid(key)}))


def enum(name, values):
    key = "Enum." + name
    kids = [child("EnumValue", "%s.EnumValue.%s" % (key, v), v) for v in values]
    return md_doc(el("Enum", el("Properties", el("Name", text=name), el("Synonym"), el("Comment")),
                     el("ChildObjects", *kids), attrs={"uuid": uid(key)}))


DOC = "Document.ЗаказПокупателя"


def document(new):
    attrs = [("Контрагент", t_ref("cfg:CatalogRef.Контрагенты")), ("Склад", t_ref("cfg:CatalogRef.Склады"))]
    if new:
        attrs.append(("СкладОтгрузки", t_ref("cfg:CatalogRef.Склады")))
    attrs += [("СуммаДокумента", t_num()), ("Статус", t_ref("cfg:EnumRef.СтатусыЗаказов")),
              ("Комментарий", t_str()), ("Основание", t_ref("cfg:DocumentRef.ЗаказПокупателя"))]
    cols = [("Номенклатура", t_ref("cfg:CatalogRef.Номенклатура")), ("Количество", t_num(15, 3)),
            ("Цена", t_num()), ("Сумма", t_num()), ("СуммаСкидки", t_num()),
            ("Склад", t_ref("cfg:CatalogRef.Склады")), ("Коэффициент", t_num(10, 3))]
    ts = el("TabularSection",
            el("Properties", el("Name", text="Товары"), el("Synonym"), el("Comment")),
            el("ChildObjects", *[child("Attribute", "%s.Товары.%s" % (DOC, c), c, typ) for c, typ in cols]),
            attrs={"uuid": uid(DOC + ".Товары")})
    kids = [child("Attribute", "%s.Attribute.%s" % (DOC, a), a, typ) for a, typ in attrs]
    kids += [ts, el("Form", text="ФормаДокумента"), el("Form", text="ФормаСписка")]
    props = [el("Name", text="ЗаказПокупателя"), synonym("Заказ покупателя"), el("Comment"),
             el("Posting", text="Allow"),
             el("RegisterRecords", el("xr:Item", text="AccumulationRegister.ТоварыНаСкладах",
                                      attrs={"xsi:type": "xr:MDObjectRef"})),
             el("DefaultObjectForm", text="Document.ЗаказПокупателя.Form.ФормаДокумента"),
             el("DefaultListForm", text="Document.ЗаказПокупателя.Form.ФормаСписка")]
    return md_doc(el("Document", el("Properties", *props), el("ChildObjects", *kids), attrs={"uuid": uid(DOC)}))


def document_adopted(ext):
    props = [el("ObjectBelonging", text="Adopted"), el("Name", text="ЗаказПокупателя"), el("Comment"),
             el("ExtendedConfigurationObject", text=uid(DOC))]
    return md_doc(el("Document", el("Properties", *props),
                     el("ChildObjects", el("Form", text="ФормаДокумента"), el("Form", text="ФормаСписка")),
                     attrs={"uuid": uid("ext:%s:%s" % (ext, DOC))}))


def form_meta(form, adopted_by=None):
    key = "%s.Form.%s" % (DOC, form)
    if adopted_by:
        props = [el("ObjectBelonging", text="Adopted"), el("Name", text=form), el("Comment"),
                 el("ExtendedConfigurationObject", text=uid(key)), el("FormType", text="Managed")]
        return md_doc(el("Form", el("Properties", *props), attrs={"uuid": uid("ext:%s:%s" % (adopted_by, key))}))
    props = [el("Name", text=form), el("Synonym"), el("Comment"), el("FormType", text="Managed")]
    return md_doc(el("Form", el("Properties", *props), attrs={"uuid": uid(key)}))


def register(new):
    key = "AccumulationRegister.ТоварыНаСкладах"
    kids = [child("Resource", key + ".Resource.Количество", "Количество", t_num(15, 3))]
    if not new:
        kids.append(child("Resource", key + ".Resource.КоличествоВРезерве", "КоличествоВРезерве", t_num(15, 3)))
    kids += [child("Dimension", key + ".Dimension.Номенклатура", "Номенклатура",
                   t_ref("cfg:CatalogRef.Номенклатура")),
             child("Dimension", key + ".Dimension.Склад", "Склад", t_ref("cfg:CatalogRef.Склады"))]
    return md_doc(el("AccumulationRegister",
                     el("Properties", el("Name", text="ТоварыНаСкладах"), synonym("Товары на складах"),
                        el("Comment"), el("RegisterType", text="Balance")),
                     el("ChildObjects", *kids), attrs={"uuid": uid(key)}))


# -- Form.xml ------------------------------------------------------------------------------------------------------

def event(name, handler, call_type=None):
    attrs = {"name": name}
    if call_type:
        attrs["callType"] = call_type
    return el("Event", attrs=attrs, text=handler)


def input_field(name, ident, path, events=()):
    kids = [el("DataPath", text=path),
            el("ContextMenu", attrs={"name": name + "КонтекстноеМеню", "id": str(ident + 1)}),
            el("ExtendedTooltip", attrs={"name": name + "РасширеннаяПодсказка", "id": str(ident + 2)})]
    if events:
        kids.append(el("Events", *[event(*e) for e in events]))
    return el("InputField", *kids, attrs={"name": name, "id": str(ident)})


def form_doc_items(with_events, ext=False):
    """Header fields of ФормаДокумента. Vendor: OnChange of ПокупательЗаказа; extension: its callType handler."""
    if not with_events:
        ev = ()
    elif ext:
        ev = (("OnChange", "Синт_ПокупательЗаказаПриИзмененииПосле", "After"),)
    else:
        ev = (("OnChange", "ПокупательЗаказаПриИзменении"),)
    return el("ChildItems", input_field("ПокупательЗаказа", 1, "Объект.Контрагент", ev),
              input_field("Склад", 4, "Объект.Склад"), input_field("Комментарий", 7, "Объект.Комментарий"))


def main_attr(ident):
    return el("Attributes", el("Attribute", el("Type", el("v8:Type", text="cfg:DocumentObject.ЗаказПокупателя")),
                               el("MainAttribute", text="true"), el("SavedData", text="true"),
                               attrs={"name": "Объект", "id": str(ident)}))


def form_xml(*kids):
    body = "\n".join(render(k, 1) for k in kids)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<Form %s>\n%s\n</Form>\n' % (LF_NS, body)


def form_doc_vendor():
    return form_xml(el("AutoCommandBar", attrs={"name": "ФормаКоманднаяПанель", "id": "-1"}),
                    el("Events", event("OnOpen", "ПриОткрытии"), event("BeforeWrite", "ПередЗаписью"),
                       event("OnCreateAtServer", "ПриСозданииНаСервере")),
                    form_doc_items(True), main_attr(1))


def form_doc_ext():
    bar = el("AutoCommandBar", el("Autofill", text="false"), attrs={"name": "ФормаКоманднаяПанель", "id": "-1"})
    base = el("BaseForm", bar, form_doc_items(False), main_attr(1000001), attrs={"version": "2.20"})
    return form_xml(bar,
                    el("Events", event("OnOpen", "Синт_ПриОткрытииПосле", "After"),
                       event("BeforeWrite", "Синт_ФормаПередЗаписьюПеред", "Before")),
                    form_doc_items(True, ext=True), main_attr(1000001), base)


def list_items():
    return el("ChildItems", el("Table", el("DataPath", text="Список"),
                               el("ContextMenu", attrs={"name": "СписокКонтекстноеМеню", "id": "2"}),
                               el("ExtendedTooltip", attrs={"name": "СписокРасширеннаяПодсказка", "id": "3"}),
                               attrs={"name": "Список", "id": "1"}))


def form_list_vendor():
    attrs = el("Attributes", el("Attribute", el("Type", el("v8:Type", text="cfg:DynamicList")),
                                el("MainAttribute", text="true"),
                                el("Settings", el("ManualQuery", text="false"),
                                   el("MainTable", text="Document.ЗаказПокупателя"),
                                   attrs={"xsi:type": "DynamicList"}),
                                attrs={"name": "Список", "id": "1"}))
    return form_xml(el("AutoCommandBar", attrs={"name": "ФормаКоманднаяПанель", "id": "-1"}),
                    el("Events", event("OnCreateAtServer", "ПриСозданииНаСервере")), list_items(), attrs)


def form_list_ext():
    bar = el("AutoCommandBar", el("Autofill", text="false"), attrs={"name": "ФормаКоманднаяПанель", "id": "-1"})
    base = el("BaseForm", bar, list_items(), el("Attributes"), attrs={"version": "2.20"})
    return form_xml(bar, list_items(), el("Attributes"), base)


# ------------------------------------------------------------------------------------------------------------------
# Trees
# ------------------------------------------------------------------------------------------------------------------

VENDOR_CHILDREN = [("Language", "Русский"), ("CommonModule", "ЗаказыКлиентСервер"), ("CommonModule", "ЗаказыСервер"),
                   ("Catalog", "Контрагенты"), ("Catalog", "Номенклатура"), ("Catalog", "Склады"),
                   ("Document", "ЗаказПокупателя"), ("Enum", "СтатусыЗаказов"),
                   ("AccumulationRegister", "ТоварыНаСкладах")]


def build_vendor(root, new):
    version = "11.5.27.1" if new else "11.5.22.1"
    write(root, "Configuration.xml", configuration(VENDOR, version, "Configuration." + VENDOR, VENDOR_CHILDREN,
                                                   synonym_text="Типовая демо"))
    write(root, "Languages/Русский.xml", language())
    write(root, "CommonModules/ЗаказыСервер.xml", common_module("ЗаказыСервер"))
    write(root, "CommonModules/ЗаказыСервер/Ext/Module.bsl", T.vendor_zakazy_server(new))
    write(root, "CommonModules/ЗаказыКлиентСервер.xml", common_module("ЗаказыКлиентСервер", client=True))
    write(root, "CommonModules/ЗаказыКлиентСервер/Ext/Module.bsl", T.vendor_zakazy_ks(new))
    write(root, "Catalogs/Контрагенты.xml", catalog("Контрагенты", [("ИНН", t_str(12))]))
    write(root, "Catalogs/Номенклатура.xml", catalog("Номенклатура", [("Артикул", t_str(25))]))
    write(root, "Catalogs/Склады.xml", catalog("Склады", []))
    write(root, "Enums/СтатусыЗаказов.xml", enum("СтатусыЗаказов", ["Черновик", "КОтгрузке", "Отменен"]))
    write(root, "AccumulationRegisters/ТоварыНаСкладах.xml", register(new))
    d = "Documents/ЗаказПокупателя"
    write(root, d + ".xml", document(new))
    write(root, d + "/Ext/ObjectModule.bsl", T.vendor_object(new))
    write(root, d + "/Ext/ManagerModule.bsl", T.vendor_manager(new))
    write(root, d + "/Forms/ФормаДокумента.xml", form_meta("ФормаДокумента"))
    write(root, d + "/Forms/ФормаДокумента/Ext/Form.xml", form_doc_vendor())
    write(root, d + "/Forms/ФормаДокумента/Ext/Form/Module.bsl", T.vendor_form_doc(new))
    write(root, d + "/Forms/ФормаСписка.xml", form_meta("ФормаСписка"))
    write(root, d + "/Forms/ФормаСписка/Ext/Form.xml", form_list_vendor())
    write(root, d + "/Forms/ФормаСписка/Ext/Form/Module.bsl", T.vendor_form_list(new))


MAIN_CHILDREN = [("Language", "Русский"), ("CommonModule", "ЗаказыКлиентСервер"), ("CommonModule", "ЗаказыСервер"),
                 ("CommonModule", "Синт_ЗаказыСервер"), ("Document", "ЗаказПокупателя")]


def build_ext_main(root, name, version, modules):
    """modules: {'zs', 'ks', 'manager', 'object', 'form_doc', 'form_list'} -> text."""
    write(root, "Configuration.xml", configuration(name, version, "ExtConfiguration." + EXT_MAIN, MAIN_CHILDREN,
                                                   purpose="Customization", synonym_text="Синт: основное"))
    write(root, "Languages/Русский.xml", language(EXT_MAIN))
    write(root, "CommonModules/ЗаказыСервер.xml", common_module("ЗаказыСервер", adopted_by=EXT_MAIN))
    write(root, "CommonModules/ЗаказыСервер/Ext/Module.bsl", modules["zs"])
    write(root, "CommonModules/ЗаказыКлиентСервер.xml", common_module("ЗаказыКлиентСервер", adopted_by=EXT_MAIN))
    write(root, "CommonModules/ЗаказыКлиентСервер/Ext/Module.bsl", modules["ks"])
    write(root, "CommonModules/Синт_ЗаказыСервер.xml", common_module("Синт_ЗаказыСервер"))
    write(root, "CommonModules/Синт_ЗаказыСервер/Ext/Module.bsl", T.ext_main_own_module())
    d = "Documents/ЗаказПокупателя"
    write(root, d + ".xml", document_adopted(EXT_MAIN))
    write(root, d + "/Ext/ObjectModule.bsl", modules["object"], newline=modules.get("object_newline", "lf"))
    write(root, d + "/Ext/ManagerModule.bsl", modules["manager"])
    write(root, d + "/Forms/ФормаДокумента.xml", form_meta("ФормаДокумента", EXT_MAIN))
    write(root, d + "/Forms/ФормаДокумента/Ext/Form.xml", form_doc_ext())
    write(root, d + "/Forms/ФормаДокумента/Ext/Form/Module.bsl", modules["form_doc"])
    write(root, d + "/Forms/ФормаСписка.xml", form_meta("ФормаСписка", EXT_MAIN))
    write(root, d + "/Forms/ФормаСписка/Ext/Form.xml", form_list_ext())
    write(root, d + "/Forms/ФормаСписка/Ext/Form/Module.bsl", modules["form_list"])


def build_ext_print(root):
    write(root, "Configuration.xml", configuration(EXT_PRINT, "1.0", "ExtConfiguration." + EXT_PRINT,
                                                   [("Language", "Русский"), ("CommonModule", "ЗаказыСервер")],
                                                   purpose="Customization", synonym_text="Синт: печать"))
    write(root, "Languages/Русский.xml", language(EXT_PRINT))
    write(root, "CommonModules/ЗаказыСервер.xml", common_module("ЗаказыСервер", adopted_by=EXT_PRINT))
    write(root, "CommonModules/ЗаказыСервер/Ext/Module.bsl", T.ext_print_zakazy_server())


def ext_modules():
    return {"zs": T.ext_main_zakazy_server(), "ks": T.ext_main_zakazy_ks(), "manager": T.ext_main_manager(),
            "object": T.ext_main_object(), "form_doc": T.ext_main_form_doc(), "form_list": T.ext_main_form_list()}


# ------------------------------------------------------------------------------------------------------------------
# Work copy
# ------------------------------------------------------------------------------------------------------------------

def run_rebase(root, out):
    """control_rebase of the package on the generated dumps under root; its report and candidates go to out."""
    control_rebase.main(["--ext", os.path.join(root, "ext"), "--old", os.path.join(root, "old"),
                         "--new", os.path.join(root, "new"), "--out", out, "--only", EXT_MAIN])
    with open(os.path.join(out, "report.json"), encoding="utf-8") as fh:
        return json.load(fh)


def candidate(out, row):
    with open(os.path.join(out, *row["file"].split("/")), "rb") as fh:
        data = fh.read()
    return data[len(BOM):].decode("utf-8").replace("\r\n", "\n")


def build_work(root, rebase_out):
    rep = run_rebase(root, rebase_out)
    by = {}
    for r in rep["methods"]:
        by[(r["module"], r["target"])] = r
    zs = "CommonModules/ЗаказыСервер/Ext/Module.bsl"
    ks = "CommonModules/ЗаказыКлиентСервер/Ext/Module.bsl"
    mgr = "Documents/ЗаказПокупателя/Ext/ManagerModule.bsl"
    obj = "Documents/ЗаказПокупателя/Ext/ObjectModule.bsl"
    fdoc = "Documents/ЗаказПокупателя/Forms/ФормаДокумента/Ext/Form/Module.bsl"
    flist = "Documents/ЗаказПокупателя/Forms/ФормаСписка/Ext/Form/Module.bsl"

    def ported(rel, methods):
        res = {}
        for target, text in methods.items():
            r = by.get((rel, target))
            if r is None:
                res[target] = text
            elif r["verdict"] == "REBASED":
                res[target] = candidate(rebase_out, r)
            elif r["verdict"] == "MISSING":
                continue
            elif target in T.WORK_PORTS:
                res[target] = T.WORK_PORTS[target]
            else:
                res[target] = text
        return res

    zs_methods = ported(zs, T.EXT_ZS_METHODS)
    # a hand port differs from the candidate only by an empty line: still SAME
    zs_methods["РассчитатьСуммуЗаказа"] = zs_methods["РассчитатьСуммуЗаказа"].replace(") Экспорт\n\t\n", ") Экспорт\n", 1)
    modules = {"zs": T.ext_main_zakazy_server(zs_methods),
               "ks": T.ext_main_zakazy_ks(ported(ks, T.EXT_KS_METHODS)),
               "manager": T.ext_main_manager(ported(mgr, T.EXT_MANAGER_METHODS)),
               "object": T.ext_main_object(ported(obj, T.EXT_OBJECT_METHODS)),
               "form_doc": T.ext_main_form_doc(ported(fdoc, T.EXT_FORM_DOC_METHODS)),
               "form_list": T.ext_main_form_list(ported(flist, T.EXT_FORM_LIST_METHODS))}
    build_ext_main(os.path.join(root, "work", WORK_MAIN), WORK_MAIN, "1.0", modules)
    for r in rep["methods"]:
        print("  %-12s %s %s" % (r["verdict"], r["module"], r["target"]))


def build(root, rebase_out):
    """Write old/, new/, ext/ and work/ into root; the control_rebase run behind work/ goes to rebase_out."""
    build_vendor(os.path.join(root, "old"), new=False)
    build_vendor(os.path.join(root, "new"), new=True)
    build_ext_main(os.path.join(root, "ext", EXT_MAIN), EXT_MAIN, "1.0", ext_modules())
    build_ext_print(os.path.join(root, "ext", EXT_PRINT))
    build_work(root, rebase_out)


# ------------------------------------------------------------------------------------------------------------------
# Comparison with the committed corpus
# ------------------------------------------------------------------------------------------------------------------

def tree_files(root, skip=()):
    """Relative '/'-paths of every file under root; top-level names in skip are left out."""
    res = set()
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        if rel_dir == ".":
            dirnames[:] = [d for d in dirnames if d not in skip]
            filenames = [f for f in filenames if f not in skip]
        for f in filenames:
            res.add(f if rel_dir == "." else rel_dir + "/" + f)
    return res


def first_difference(want, got):
    """Where two differing byte strings part: offset, line and a few bytes of each side."""
    n = next((i for i in range(min(len(want), len(got))) if want[i] != got[i]), min(len(want), len(got)))
    return "byte %d (line %d): corpus %r, generated %r; sizes %d / %d" % (
        n, want[:n].count(b"\n") + 1, want[n:n + 24], got[n:n + 24], len(want), len(got))


def compare(built, corpus=CORPUS):
    """Differences of a generated tree from the committed corpus, expected/ and README.md excluded."""
    want, got = tree_files(corpus, NOT_GENERATED), tree_files(built, NOT_GENERATED)
    diffs = ["only in corpus: " + p for p in sorted(want - got)]
    diffs += ["only in generated: " + p for p in sorted(got - want)]
    for rel in sorted(want & got):
        want_path, got_path = os.path.join(corpus, *rel.split("/")), os.path.join(built, *rel.split("/"))
        if filecmp.cmp(want_path, got_path, shallow=False):
            continue
        with open(want_path, "rb") as fw, open(got_path, "rb") as fg:
            diffs.append("differs: %s: %s" % (rel, first_difference(fw.read(), fg.read())))
    return diffs


# ------------------------------------------------------------------------------------------------------------------
# Command line
# ------------------------------------------------------------------------------------------------------------------

def inside(path, root):
    path, root = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(root))
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # different drives
        return False


def check():
    tmp = tempfile.mkdtemp(prefix="cfe_corpus_gen_")
    try:
        build(os.path.join(tmp, "corpus"), os.path.join(tmp, "rebase"))
        diffs = compare(os.path.join(tmp, "corpus"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for d in diffs:
        print(d)
    print("%d difference(s) with %s" % (len(diffs), CORPUS))
    return 1 if diffs else 0


def main(argv=None):
    fileio.console_utf8()
    ap = argparse.ArgumentParser(description="Generate the synthetic corpus (old, new, ext, work)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--out", help="folder to write the corpus into: absent or empty, never the committed corpus")
    mode.add_argument("--check", action="store_true",
                      help="build into a temporary folder and compare byte by byte with tests/corpus/synthetic")
    args = ap.parse_args(argv)
    if args.check:
        return check()
    if inside(args.out, CORPUS) or inside(CORPUS, args.out):
        ap.error("--out %s overlaps the committed corpus %s: the generator never writes there" % (args.out, CORPUS))
    if os.path.isdir(args.out) and os.listdir(args.out):
        ap.error("--out %s is not empty" % args.out)
    rebase_out = tempfile.mkdtemp(prefix="cfe_corpus_gen_rebase_")
    build(args.out, rebase_out)
    print("corpus: %s" % os.path.abspath(args.out))
    print("control_rebase report behind work/ (review it): %s" % os.path.join(rebase_out, "report.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
