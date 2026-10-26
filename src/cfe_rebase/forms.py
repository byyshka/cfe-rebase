# -*- coding: utf-8 -*-
"""Form.xml of managed forms: event and command bindings with callType and handler names.

An adopted (borrowed) form of an extension keeps a <BaseForm> snapshot of the vendor form; everything
outside it is the extension's view, where handlers of vendor events carry callType (Before/After/Override)
and handlers of the extension's own elements may carry it too. Own forms have no <BaseForm>.
The parser keeps line numbers (expat), so findings can point at Form.xml lines.
"""
import xml.parsers.expat

from . import fileio

FORM_EVENTS_RU = {
    "OnCreateAtServer": "ПриСозданииНаСервере", "OnOpen": "ПриОткрытии", "BeforeClose": "ПередЗакрытием",
    "OnClose": "ПриЗакрытии", "NotificationProcessing": "ОбработкаОповещения", "ChoiceProcessing": "ОбработкаВыбора",
    "OnReadAtServer": "ПриЧтенииНаСервере", "BeforeWrite": "ПередЗаписью", "BeforeWriteAtServer": "ПередЗаписьюНаСервере",
    "OnWriteAtServer": "ПриЗаписиНаСервере", "AfterWriteAtServer": "ПослеЗаписиНаСервере", "AfterWrite": "ПослеЗаписи",
    "FillCheckProcessingAtServer": "ОбработкаПроверкиЗаполненияНаСервере", "OnChange": "ПриИзменении",
    "StartChoice": "НачалоВыбора", "Selection": "Выбор", "OnActivateRow": "ПриАктивизацииСтроки",
    "BeforeAddRow": "ПередНачаломДобавления", "BeforeDeleteRow": "ПередУдалением", "Click": "Нажатие",
    "OnStartEdit": "ПриНачалеРедактирования", "OnEditEnd": "ПриОкончанииРедактирования",
}
CALLTYPE_RU = {"Before": "Перед", "After": "После", "Override": "Вместо"}


class Node(object):
    __slots__ = ("tag", "attrib", "line", "children", "text", "parent")

    def __init__(self, tag, attrib, line, parent):
        self.tag = tag
        self.attrib = attrib
        self.line = line
        self.children = []
        self.text = ""
        self.parent = parent

    def get(self, key, default=None):
        return self.attrib.get(key, default)

    def iter(self):
        stack = [self]
        while stack:
            n = stack.pop()
            yield n
            stack.extend(reversed(n.children))

    def find(self, tag):
        for ch in self.children:
            if ch.tag == tag:
                return ch
        return None


def _local(name):
    return name.rsplit("}", 1)[-1].rsplit(" ", 1)[-1]


def parse(path):
    data = fileio.read_bytes(path)
    parser = xml.parsers.expat.ParserCreate(namespace_separator=" ")
    root_holder = []
    stack = []

    def start(name, attrs):
        parent = stack[-1] if stack else None
        node = Node(_local(name), {_local(k): v for k, v in attrs.items()}, parser.CurrentLineNumber, parent)
        if parent is not None:
            parent.children.append(node)
        else:
            root_holder.append(node)
        stack.append(node)

    def end(name):
        stack.pop()

    def chars(text):
        if stack:
            stack[-1].text += text

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    parser.Parse(data, True)
    return root_holder[0]


def _owner_of_event(event_node, scope_root):
    events = event_node.parent
    owner = events.parent if events is not None else None
    if owner is None or owner is scope_root or owner.tag in ("Form", "BaseForm"):
        return "form", "<Форма>", "Form"
    return "item", owner.get("name"), owner.tag


def bindings(scope_root, skip_base=True):
    """Event/Action bindings under scope_root: [{owner_kind, owner, owner_tag, event, callType, handler, line}]."""
    res = []
    stack = [scope_root]
    while stack:
        node = stack.pop()
        for ch in reversed(node.children):
            if skip_base and ch.tag == "BaseForm":
                continue
            stack.append(ch)
        if node.tag == "Event" and node.parent is not None and node.parent.tag == "Events":
            kind, owner, tag = _owner_of_event(node, scope_root)
            res.append({"owner_kind": kind, "owner": owner, "owner_tag": tag, "event": node.get("name"),
                        "callType": node.get("callType"), "handler": node.text.strip(), "line": node.line})
        elif node.tag == "Action" and node.parent is not None and node.parent.tag == "Command":
            res.append({"owner_kind": "command", "owner": node.parent.get("name"), "owner_tag": "Command",
                        "event": "Action", "callType": node.get("callType"), "handler": node.text.strip(),
                        "line": node.line})
    res.sort(key=lambda b: b["line"])
    return res


class Form(object):
    def __init__(self, path):
        self.path = path
        self.root = parse(path)
        self.base = self.root.find("BaseForm")

    @property
    def borrowed(self):
        return self.base is not None

    def bindings(self):
        """Bindings of the extension's (or configuration's) own view, <BaseForm> excluded."""
        return bindings(self.root, skip_base=True)

    def base_bindings(self):
        return bindings(self.base, skip_base=False) if self.base is not None else []

    def names(self, scope=None):
        scope = scope or self.root
        out = set()
        for n in scope.iter():
            if n.tag == "BaseForm" and scope is self.root:
                continue
            nm = n.get("name")
            if nm and n.tag not in ("Event",):
                out.add(nm)
        return out


def handler_for(scope_root, owner_kind, owner, event):
    """(owner exists, handler name or None) for a binding without callType in a vendor form view."""
    if scope_root is None:
        return False, None
    if owner_kind == "form":
        for ev in (scope_root.find("Events").children if scope_root.find("Events") is not None else []):
            if ev.tag == "Event" and ev.get("name") == event and not ev.get("callType"):
                return True, ev.text.strip()
        return True, None
    if owner_kind == "command":
        for node in scope_root.iter():
            if node.tag == "Command" and node.get("name") == owner:
                act = node.find("Action")
                return True, (act.text.strip() if act is not None and not act.get("callType") else None)
        return False, None
    for node in scope_root.iter():
        if node.tag == "BaseForm" and node is not scope_root:
            continue
        if node.get("name") == owner and node.tag not in ("Event", "Command", "Attribute", "Parameter", "Column"):
            evs = node.find("Events")
            if evs is not None:
                for ev in evs.children:
                    if ev.get("name") == event and not ev.get("callType"):
                        return True, ev.text.strip()
            return True, None
    return False, None


def bound_to(scope_root, handler):
    """Sorted 'owner:event' list of bindings (without callType) that call the handler."""
    res = []
    if scope_root is None or not handler:
        return res
    h = handler.casefold()
    for b in bindings(scope_root, skip_base=True):
        if not b["callType"] and b["handler"].casefold() == h:
            res.append("%s:%s" % (b["owner"], b["event"]))
    return sorted(res)
