# -*- coding: utf-8 -*-
"""Normalization of tool reports (the JSON a command writes next to its Markdown) for comparing runs.

Two runs of the same tool on the same input differ only by where the input and the output lie and by timings.
normalize() replaces every occurrence of the given absolute paths with a placeholder (the rest of such a path gets
forward slashes) and drops volatile keys. It serves the golden tests of the synthetic corpus and the comparison of two
runs on real dumps alike:

    paths = {"": corpus_root, "<out>": out_dir}            # "" - relative to that root
    paths = {"<old>": old_dump, "<new>": new_dump, "<ext>": ext_root, "<out>": out_dir}
    data = normalize(json.load(fh), paths)

Paths are matched case-insensitively with either separator, as Windows reports them.
"""
import json
import os
import re

VOLATILE_KEYS = re.compile(r"^(elapsed|elapsed_s|time|timestamp|duration|generated|generated_at|started|started_at|"
                           r"finished|finished_at)$", re.IGNORECASE)
_TAIL = r"(?P<tail>(?:[\\/][^\\/\r\n\t\"'`<>|*?:;]*)*)"


def _pattern(path):
    parts = [p for p in re.split(r"[\\/]+", os.path.abspath(path)) if p]
    # the root must end at a separator or at the end of the path: "...\synthetic" never matches "...\synthetic2"
    return re.compile(r"[\\/]+".join(re.escape(p) for p in parts) + r"(?![\w.\-])" + _TAIL, re.IGNORECASE)


def _compile(paths):
    """[(pattern, placeholder)], longest path first so that a root nested in another wins."""
    pairs = []
    for placeholder, value in paths.items():
        for p in ([value] if isinstance(value, str) else list(value)):
            if p:
                pairs.append((os.path.abspath(p), placeholder))
    pairs.sort(key=lambda x: len(x[0]), reverse=True)
    return [(_pattern(p), placeholder) for p, placeholder in pairs]


def _replace(text, compiled):
    for pattern, placeholder in compiled:
        def repl(m, placeholder=placeholder):
            tail = m.group("tail").replace("\\", "/")
            if placeholder:
                return placeholder + tail
            return tail.lstrip("/") or "."
        text = pattern.sub(repl, text)
    return text


def normalize(data, paths, drop=VOLATILE_KEYS):
    """A copy of the JSON-like data: paths replaced in every string, volatile keys removed.

    paths: {placeholder: path or [paths]}; the placeholder "" makes the path relative to that root ("." for the root
    itself). drop: a compiled regex of key names to remove, or None to keep every key."""
    compiled = _compile(paths)

    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items() if not (drop and isinstance(k, str) and drop.match(k))}
        if isinstance(node, (list, tuple)):
            return [walk(v) for v in node]
        if isinstance(node, str):
            return _replace(node, compiled)
        return node

    return walk(data)


def load(json_path, paths, drop=VOLATILE_KEYS):
    with open(json_path, encoding="utf-8") as fh:
        return normalize(json.load(fh), paths, drop)


def dumps(data):
    """Stable text form for expected files and diffs: key order as the tool wrote it, UTF-8, one-space indent."""
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"
