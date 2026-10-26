# -*- coding: utf-8 -*-
"""Tiny XML helpers for the synthetic corpus generator (build_corpus.py)."""
import uuid
from xml.sax.saxutils import escape

MD_NS = ('xmlns="http://v8.1c.ru/8.3/MDClasses" xmlns:app="http://v8.1c.ru/8.2/managed-application/core" '
         'xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config" '
         'xmlns:cmi="http://v8.1c.ru/8.2/managed-application/cmi" xmlns:ent="http://v8.1c.ru/8.1/data/enterprise" '
         'xmlns:lf="http://v8.1c.ru/8.2/managed-application/logform" xmlns:style="http://v8.1c.ru/8.1/data/ui/style" '
         'xmlns:sys="http://v8.1c.ru/8.1/data/ui/fonts/system" xmlns:v8="http://v8.1c.ru/8.1/data/core" '
         'xmlns:v8ui="http://v8.1c.ru/8.1/data/ui" xmlns:web="http://v8.1c.ru/8.1/data/ui/colors/web" '
         'xmlns:win="http://v8.1c.ru/8.1/data/ui/colors/windows" xmlns:xen="http://v8.1c.ru/8.3/xcf/enums" '
         'xmlns:xpr="http://v8.1c.ru/8.3/xcf/predef" xmlns:xr="http://v8.1c.ru/8.3/xcf/readable" '
         'xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
         'version="2.20"')
LF_NS = ('xmlns="http://v8.1c.ru/8.3/xcf/logform" xmlns:app="http://v8.1c.ru/8.2/managed-application/core" '
         'xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config" '
         'xmlns:dcscor="http://v8.1c.ru/8.1/data-composition-system/core" '
         'xmlns:dcssch="http://v8.1c.ru/8.1/data-composition-system/schema" '
         'xmlns:dcsset="http://v8.1c.ru/8.1/data-composition-system/settings" '
         'xmlns:ent="http://v8.1c.ru/8.1/data/enterprise" xmlns:lf="http://v8.1c.ru/8.2/managed-application/logform" '
         'xmlns:style="http://v8.1c.ru/8.1/data/ui/style" xmlns:sys="http://v8.1c.ru/8.1/data/ui/fonts/system" '
         'xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:v8ui="http://v8.1c.ru/8.1/data/ui" '
         'xmlns:web="http://v8.1c.ru/8.1/data/ui/colors/web" xmlns:win="http://v8.1c.ru/8.1/data/ui/colors/windows" '
         'xmlns:xr="http://v8.1c.ru/8.3/xcf/readable" xmlns:xs="http://www.w3.org/2001/XMLSchema" '
         'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" version="2.20"')

_NS = uuid.UUID("a3f1c2d4-5b6e-4f70-8a91-b2c3d4e5f607")


def uid(key):
    """Deterministic synthetic uuid: the same key gives the same uuid in old, new and ext."""
    return str(uuid.uuid5(_NS, key))


def el(tag, *children, attrs=None, text=None):
    return (tag, attrs or {}, [c for c in children if c is not None], text)


def render(node, level=0):
    tag, attrs, children, text = node
    a = "".join(' %s="%s"' % (k, escape(v, {'"': "&quot;"})) for k, v in attrs.items())
    ind = "\t" * level
    if children:
        inner = "\n".join(render(c, level + 1) for c in children)
        return "%s<%s%s>\n%s\n%s</%s>" % (ind, tag, a, inner, ind, tag)
    if text is None or text == "":
        return "%s<%s%s/>" % (ind, tag, a)
    return "%s<%s%s>%s</%s>" % (ind, tag, a, escape(text), tag)


def md_doc(node):
    return '<?xml version="1.0" encoding="UTF-8"?>\n<MetaDataObject %s>\n%s\n</MetaDataObject>\n' % (
        MD_NS, render(node, 1))


def synonym(text):
    if not text:
        return el("Synonym")
    return el("Synonym", el("v8:item", el("v8:lang", text="ru"), el("v8:content", text=text)))


def t_ref(t):
    return el("Type", el("v8:Type", text=t))


def t_num(digits=15, frac=2):
    return el("Type", el("v8:Type", text="xs:decimal"),
              el("v8:NumberQualifiers", el("v8:Digits", text=str(digits)), el("v8:FractionDigits", text=str(frac)),
                 el("v8:AllowedSign", text="Any")))


def t_str(length=0):
    return el("Type", el("v8:Type", text="xs:string"),
              el("v8:StringQualifiers", el("v8:Length", text=str(length)), el("v8:AllowedLength", text="Variable")))


def child(tag, key, name, typ=None, adopted_from=None):
    """Attribute / Dimension / Resource / EnumValue with uuid uid(key). adopted_from - uid key of the vendor child:
    the extension borrows it (ObjectBelonging=Adopted, ExtendedConfigurationObject)."""
    if adopted_from:
        props = [el("ObjectBelonging", text="Adopted"), el("Name", text=name), el("Comment"),
                 el("ExtendedConfigurationObject", text=uid(adopted_from))]
    else:
        props = [el("Name", text=name), el("Synonym"), el("Comment")]
    if typ is not None:
        props.append(typ)
    return el(tag, el("Properties", *props), attrs={"uuid": uid(key)})
