"""Every string literal in the Jenkinsfiles is closed on its own line.

Jenkins compiles a Jenkinsfile only when a build starts, so a broken quote (an apostrophe inside a
'...' string, as in "this site's", which stopped the model job on the VPS) passes every other test.
This is a small lexer, not a Groovy parser: it knows comments, '...', "...", '''...''', \"\"\"...\"\"\"
and backslash escapes, which is all these two files use."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def unclosed_strings(text: str) -> list[int]:
    """Line numbers where a one-line string literal is still open at the end of the line."""
    bad, i, n, line = [], 0, len(text), 1
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            line += text.count("\n", i, end)
            i = end + 2
        elif text.startswith("'''", i) or text.startswith('"""', i):
            q = text[i:i + 3]
            end = text.find(q, i + 3)
            if end < 0:
                return bad + [line]
            line += text.count("\n", i, end)
            i = end + 3
        elif c in "'\"":
            start, i = line, i + 1
            while i < n and text[i] != c and text[i] != "\n":
                i += 2 if text[i] == "\\" else 1
            if i >= n or text[i] == "\n":
                bad.append(start)
            else:
                i += 1
        else:
            i += 1
    return bad


@pytest.mark.parametrize("name", ["Jenkinsfile", "Jenkinsfile.model"])
def test_every_string_is_closed(name):
    assert unclosed_strings((ROOT / name).read_text()) == [], name


def test_the_lexer_finds_an_apostrophe_in_a_single_quoted_string():
    assert unclosed_strings("x(description: 'this site's model')\n") == [1]
    assert unclosed_strings('x(description: "this site\'s model")\n') == []
    assert unclosed_strings("sh '''\n echo 'a' \"b\"\n'''\n// it's a comment\n") == []
