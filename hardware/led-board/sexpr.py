"""A small S-expression reader and writer for KiCad's file formats.

Atoms are kept exactly as written (strings keep their quotes), so anything read from a KiCad
library can be written back out unchanged.
"""
import re

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()]+')


def parse(text):
    """The first top-level list in `text`, as nested Python lists."""
    stack, cur = [], None
    for tok in _TOKEN.findall(text):
        if tok == "(":
            node = []
            if cur is not None:
                cur.append(node)
                stack.append(cur)
            cur = node
        elif tok == ")":
            if not stack:
                return cur
            cur = stack.pop()
        else:
            cur.append(tok)
    return cur


def q(text):
    """A KiCad string atom."""
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def num(value):
    """A KiCad number atom: no trailing zeros, no -0."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def dump(node, indent=0):
    """KiCad-style text: atoms stay on the opening line, child lists each get their own."""
    if not isinstance(node, list):
        return str(node)
    atoms, children = [], []
    for item in node:
        (children if isinstance(item, list) else atoms).append(item)
    pad = "\t" * indent
    head = "(" + " ".join(str(a) for a in atoms)
    if not children:
        return head + ")"
    # Short lists of all-atom children (xy pairs, font sizes) read better on one line.
    if all(not any(isinstance(c, list) for c in ch) for ch in children) and len(children) <= 3 and \
            sum(len(ch) for ch in children) <= 8 and node[0] in ("pts", "font", "at", "size"):
        return head + " " + " ".join(dump(c) for c in children) + ")"
    body = "\n".join(pad + "\t" + dump(c, indent + 1) for c in children)
    return f"{head}\n{body}\n{pad})"


def find(node, head):
    """The first child list whose head is `head`."""
    return next((c for c in node if isinstance(c, list) and c and c[0] == head), None)


def find_all(node, head):
    return [c for c in node if isinstance(c, list) and c and c[0] == head]
