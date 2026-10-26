# -*- coding: utf-8 -*-
"""File I/O for 1C dumps and tool reports.

1C artifacts (.bsl, metadata XML, Form.xml) are UTF-8 with BOM and usually CRLF; the configurator
refuses some of them without BOM. read_* strips the BOM; write_like() keeps BOM and line endings of
the original file. Reports (.md, .json) are plain UTF-8 without BOM.
"""
import io
import json
import os

BOM = b"\xef\xbb\xbf"


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def detect_format(path):
    """{'bom': bool, 'newline': '\\r\\n' | '\\n'} of an existing text file."""
    data = read_bytes(path)
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    return {"bom": data.startswith(BOM), "newline": "\r\n" if crlf >= lf and crlf else "\n"}


def utf8_error(data):
    """None when data (a leading BOM aside) is valid UTF-8, else (line, byte, reason) of the first bad byte: read_*
    decode leniently, so a module in another encoding must be refused before it is rewritten."""
    body = data[len(BOM):] if data.startswith(BOM) else data
    try:
        body.decode("utf-8")
    except UnicodeDecodeError as exc:
        return body.count(b"\n", 0, exc.start) + 1, body[exc.start], exc.reason
    return None


def read_text(path):
    """File text without BOM, line endings normalized to '\\n'."""
    data = read_bytes(path)
    if data.startswith(BOM):
        data = data[len(BOM):]
    text = data.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_lines(path):
    """Lines without line terminators (1-based line N is result[N - 1])."""
    return read_text(path).split("\n")


def write_text(path, text, bom=True, newline="\r\n"):
    """Write text given with '\\n' line endings; default suits 1C artifacts (BOM + CRLF)."""
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    if newline != "\n":
        body = body.replace("\n", newline)
    data = body.encode("utf-8")
    if bom:
        data = BOM + data
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def write_like(path, text, original):
    """Write text keeping BOM and line endings of the original file."""
    fmt = detect_format(original)
    write_text(path, text, bom=fmt["bom"], newline=fmt["newline"])


def write_report(out_dir, name, markdown, data):
    """<out_dir>/<name>.md and <out_dir>/<name>.json (UTF-8 without BOM). Returns both paths."""
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, name + ".md")
    json_path = os.path.join(out_dir, name + ".json")
    with io.open(md_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(markdown if markdown.endswith("\n") else markdown + "\n")
    with io.open(json_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, default=str)
    return md_path, json_path


def md_cell(value):
    """Text safe for a Markdown table cell."""
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    return text.strip()


def md_table(headers, rows):
    lines = ["| " + " | ".join(md_cell(h) for h in headers) + " |",
             "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(md_cell(v) for v in row) + " |")
    return "\n".join(lines)


def console_utf8():
    """Make print() safe for Cyrillic on a Windows console."""
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
