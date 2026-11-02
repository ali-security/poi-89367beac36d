#!/usr/bin/env python3
"""Summarise Ant JUnit XML reports (TEST-*.xml) for the seal-build workflow.

Prints per-report-dir and overall run/failed/errored/skipped counts, and names
every skipped test with its reason: the <skipped message> when JUnit recorded
one, otherwise the @Ignore("...") / Assume text found in the test source.
"""
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

SRC_ROOTS = ["src"]


def find_source(classname):
    rel = classname.split("$")[0].replace(".", "/") + ".java"
    for root in SRC_ROOTS:
        for path in glob.glob(os.path.join(root, "**", rel), recursive=True):
            return path
    simple = rel.rsplit("/", 1)[-1]
    for root in SRC_ROOTS:
        for path in glob.glob(os.path.join(root, "**", simple), recursive=True):
            return path
    return None


def ignore_reason(classname, method, depth=0):
    path = find_source(classname)
    if not path:
        return "no reason recorded"
    with open(path, encoding="latin-1") as fh:
        text = fh.read()
    m = re.search(r"void\s+" + re.escape(method) + r"\s*\(", text)
    if m:
        head = text[max(0, m.start() - 600):m.start()]
        ig = list(re.finditer(r"@Ignore(\(\s*\"((?:[^\"\\]|\\.)*)\"\s*\))?", head))
        if ig:
            return "upstream @Ignore" + (": " + ig[-1].group(2) if ig[-1].group(2) else " annotation in source (no message given)")
        body = text[m.end():m.end() + 3000]
        if "Assume" in body or "assume" in body:
            return "JUnit assumption not met (upstream Assume.* in test body)"
    cls = re.search(r"@Ignore(\(\s*\"((?:[^\"\\]|\\.)*)\"\s*\))?\s*(public\s+)?(abstract\s+)?(final\s+)?class", text)
    if cls:
        return "upstream class-level @Ignore" + (": " + cls.group(2) if cls.group(2) else "")
    # method may be inherited: follow the extends chain
    ext = re.search(r"class\s+\w+(?:<[^>]*>)?\s+extends\s+([\w.]+)", text)
    if ext and depth < 5:
        parent = ext.group(1)
        if "." not in parent:
            imp = re.search(r"import\s+([\w.]*\." + re.escape(parent) + r")\s*;", text)
            pkg = re.search(r"package\s+([\w.]+)\s*;", text)
            parent = imp.group(1) if imp else ((pkg.group(1) + ".") if pkg else "") + parent
        return ignore_reason(parent, method, depth + 1)
    return "no reason recorded"


def assume_calls(classname, method, depth=0):
    """The Assume/assume* statements in the test method body (follows extends)."""
    path = find_source(classname)
    if not path:
        return []
    with open(path, encoding="latin-1") as fh:
        text = fh.read()
    m = re.search(r"void\s+" + re.escape(method) + r"\s*\(", text)
    if m:
        body = text[m.end():m.end() + 3000]
        return [c.strip() for c in re.findall(r"(?:Assume\.)?assume\w+\([^;]*\);", body)]
    ext = re.search(r"class\s+\w+(?:<[^>]*>)?\s+extends\s+([\w.]+)", text)
    if ext and depth < 5:
        return assume_calls(ext.group(1), method, depth + 1)
    return []


def describe_assumption(classname, method, message):
    # Hamcrest-style assumption messages ("got: <false>, expected: is <true>") carry no
    # cause; quote the upstream assume* statements that produced them.
    if message and message.startswith("got: "):
        calls = assume_calls(classname, method)
        if calls:
            return ("upstream data-driven JUnit assumption not met (%s): this parameterized case's test "
                    "input does not satisfy the precondition the test source checks with: %s" % (message, " ".join(calls)))
    return message


def main(dirs):
    total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    problems, skipped = [], {}
    for d in dirs:
        files = sorted(glob.glob(os.path.join(d, "TEST-*.xml")))
        sub = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
        for f in files:
            root = ET.parse(f).getroot()
            for k in sub:
                sub[k] += int(root.get(k, 0) or 0)
            for tc in root.iter("testcase"):
                name = "%s.%s" % (tc.get("classname"), tc.get("name"))
                for tag in ("failure", "error"):
                    el = tc.find(tag)
                    if el is not None:
                        problems.append("%s %s: %s" % (tag.upper(), name, el.get("message")))
                sk = tc.find("skipped")
                if sk is not None:
                    # parameterized runs are named "method[i: param]"; group them per method
                    cname, base = tc.get("classname"), re.sub(r"\[.*$", "", tc.get("name") or "")
                    if cname.startswith("junit.framework.JUnit4TestCaseFacade"):
                        # a whole class ignored at class level is reported under the facade
                        cname, base = base, "<class>"
                    reason = describe_assumption(cname, base, sk.get("message")) or ignore_reason(cname, base)
                    key = (cname, base, reason)
                    skipped[key] = skipped.get(key, 0) + 1
        print("[%s] suites=%d tests=%d failures=%d errors=%d skipped=%d passed=%d" % (
            d, len(files), sub["tests"], sub["failures"], sub["errors"], sub["skipped"],
            sub["tests"] - sub["failures"] - sub["errors"] - sub["skipped"]))
        for k in total:
            total[k] += sub[k]
    passed = total["tests"] - total["failures"] - total["errors"] - total["skipped"]
    print("TOTAL: tests=%d passed=%d failures=%d errors=%d skipped=%d" % (
        total["tests"], passed, total["failures"], total["errors"], total["skipped"]))
    for line in problems:
        print(line)
    for (cls, meth, reason), n in sorted(skipped.items()):
        print("SKIPPED %s.%s%s -- %s" % (cls, meth, " (x%d parameterized runs)" % n if n > 1 else "", reason))


if __name__ == "__main__":
    main(sys.argv[1:])
