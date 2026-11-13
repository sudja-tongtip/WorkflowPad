# -*- coding: utf-8 -*-
import sys
p = sys.argv[1]
raw = open(p, "rb").read().decode("utf-8")
crlf = "\r\n" in raw
t = raw.replace("\r\n", "\n")


def sub(old, new, n=1):
    global t
    assert t.count(old) == n, (t.count(old), old[:90])
    t = t.replace(old, new)


sub('''def _norm_key(v: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (v or "").strip().lower()).strip("_")
''', '''def _norm_key(v: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (v or "").strip().lower()).strip("_")


_ASPECT_START = (("opening", "opening"), ("central", "theme"), ("theme", "theme"), ("section", "sections"), ("song_journey", "sections"), ("emotional", "development"), ("narrative", "development"),
                 ("development", "development"), ("hook", "hook"), ("memorab", "hook"), ("imagery", "imagery"), ("metaphor", "imagery"), ("repetition", "repetition"), ("contrast", "repetition"),
                 ("ending", "ending"), ("payoff", "ending"))


def aspect_key(v: str) -> str:
    """The key of an aspect from what the model wrote: the key itself, a display name (`central idea`, `section purpose and journey`) or the start of one. Only the NAME is read: nothing else is guessed;
    a name that starts like none of the eight is returned as it is (and refused by the caller)."""
    k = _norm_key(v)
    k = _ASPECT_ALIAS.get(k, k)
    if k in ASPECTS:
        return k
    for start, key in _ASPECT_START:
        if k.startswith(start):
            return key
    return k
''')
sub('''        key = _norm_key(f.get("aspect"))
        key = _ASPECT_ALIAS.get(key, key)
        if key not in ASPECTS:
            diag.append(f"[ASPECT.{n}] was not used: aspect''', '''        key = aspect_key(f.get("aspect"))
        if key not in ASPECTS:
            diag.append(f"[ASPECT.{n}] was not used: aspect''')
sub('''        key = _norm_key(f.get("aspect"))
        key = _ASPECT_ALIAS.get(key, key)
        if key and key not in have:''', '''        key = aspect_key(f.get("aspect"))
        if key and key not in have:''')
open(p, "wb").write((t.replace("\n", "\r\n") if crlf else t).encode("utf-8"))
print("ok")
