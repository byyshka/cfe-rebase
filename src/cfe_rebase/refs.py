# -*- coding: utf-8 -*-
"""Where method names are referenced inside a set of extension dumps (for dead-code and liveness checks)."""
import os
import re
from collections import defaultdict

from . import bsl, fileio, forms, metadata

SELF_QUALIFIERS = {"этотобъект", "thisobject", "этаформа", "thisform"}
XML_HANDLER_RE = re.compile(r"<(Handler|MethodName|ProcedureName)>([^<]+)</\1>")
XML_HANDLER_DIRS = ("eventsubscriptions/", "scheduledjobs/", "httpservices/", "webservices/", "commoncommands/")
EXTRA_EVENTS = {x.lower() for x in """
ПриКомпоновкеРезультата ПередЗагрузкойВариантаНаСервере ПриЗагрузкеВариантаНаСервере
ПриЗагрузкеПользовательскихНастроекНаСервере ПередЗагрузкойПользовательскихНастроекНаСервере
ПриСохраненииПользовательскихНастроекНаСервере ПриОбновленииСоставаПользовательскихНастроекНаСервере
""".split()}
IMPLICIT_KINDS = ("object", "manager", "recordset", "valuemanager", "command", "app", "session", "extconn")


class Refs(object):
    """local[module]  - bare calls, ЭтотОбъект./ЭтаФорма. calls, identifiers inside string literals of that module
                        (ОписаниеОповещения("Имя", ЭтотОбъект), ПодключитьОбработчикОжидания("Имя", ...)); the key is
                        the rel path, so adopted modules of different extensions share it
       remote         - qualified calls (X.Имя) and string identifiers anywhere: reach only exported methods
       form[module]   - handlers bound in Form.xml of the form that owns the module
       xml            - handlers named in other XML (subscriptions, scheduled jobs, HTTP/web services)"""

    def __init__(self):
        self.local = defaultdict(set)
        self.remote = set()
        self.form = defaultdict(set)
        self.xml = set()

    def referenced(self, rel, method):
        key = metadata.rel_key(rel)
        name = method.name.casefold()
        if name in self.local.get(key, ()) or name in self.form.get(key, ()):
            return True
        return method.export and (name in self.remote or name in self.xml)

    def liveness(self, rel, method):
        """Short answer to 'can this method run': interceptor, platform event, referenced, or no references."""
        if method is None:
            return "тело модуля"
        if method.interceptors:
            return "перехватчик &%s(\"%s\")" % method.interceptors[0][:2]
        if metadata.module_kind(rel) in IMPLICIT_KINDS and method.name.casefold() in (bsl.EVENTS | EXTRA_EVENTS):
            return "событие платформы"
        if self.referenced(rel, method):
            return "есть ссылки"
        return "ссылок в расширениях нет"


def build(exts, form_loader):
    """exts: [metadata.Dump]; form_loader(dump, rel) -> forms.Form or None (cached by the caller)."""
    refs = Refs()
    for ext in exts:
        for rel in ext.files(".bsl"):
            mod = ext.module(rel)
            if mod is None:
                continue
            key = metadata.rel_key(rel)
            toks = mod.toks
            for i, (k, v, _) in enumerate(toks):
                if k == "id" and i + 1 < len(toks) and toks[i + 1][0] == "op" and toks[i + 1][1] == "(":
                    prev = toks[i - 1] if i > 0 else None
                    if prev and prev[0] == "id" and prev[1].lower() in (bsl.KW_PROC | bsl.KW_FUNC):
                        continue
                    if prev and prev[0] == "op" and prev[1] == ".":
                        qual = toks[i - 2] if i > 1 else None
                        if qual and qual[0] == "id" and qual[1].casefold() in SELF_QUALIFIERS:
                            refs.local[key].add(v.casefold())
                        refs.remote.add(v.casefold())
                    else:
                        refs.local[key].add(v.casefold())
                elif k == "str":
                    for word in bsl.IDENT_RE.findall(v):
                        refs.local[key].add(word.casefold())
                        refs.remote.add(word.casefold())
        for rel in ext.files(".xml"):
            low = rel.lower()
            if low.endswith("/form.xml"):
                f = form_loader(ext, rel)
                if f is None:
                    continue
                mkey = metadata.rel_key(metadata.form_module_rel(rel))
                for b in f.bindings():
                    refs.form[mkey].add(b["handler"].casefold())
            elif low.startswith(XML_HANDLER_DIRS):
                text = fileio.read_text(ext.path(rel))
                for m in XML_HANDLER_RE.finditer(text):
                    refs.xml.add(m.group(2).strip().rsplit(".", 1)[-1].casefold())
    return refs


def form_cache():
    """A form loader with its own cache: loader(dump, rel) -> forms.Form or None. A Form.xml that is absent gives
    None; one that exists but does not parse gives None too and is listed in loader.unreadable as {dump, root, form,
    error}, so a report can say which forms it could not see instead of treating them as absent."""
    cache = {}
    unreadable = []

    def loader(dump, rel):
        if dump is None:
            return None
        key = (dump.root, metadata.rel_key(rel))
        if key not in cache:
            p = dump.path(rel)
            f = None
            try:
                f = forms.Form(p)
            except (OSError, Exception) as exc:  # noqa: BLE001 - a broken or absent XML must not stop the scan
                f = None
                if os.path.isfile(p):
                    unreadable.append({"dump": getattr(dump, "name", None) or dump.root, "root": dump.root,
                                       "form": metadata.norm_rel(rel), "error": "%s: %s" % (type(exc).__name__, exc)})
            cache[key] = f
        return cache[key]
    loader.unreadable = unreadable
    return loader
