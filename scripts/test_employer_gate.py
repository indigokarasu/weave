import os
"""Cases from this session's real data, both directions."""
import sys
sys.path.insert(0, os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "skills/ocas-weave/scripts"))
from employer_gate import corroborate, is_free_mail, tokens, candidates

SUPPORTED = [
    # the ones that were RIGHT and must keep flowing through
    ("Example Health", "someone@examplehealth.test", (), ()),
    ("Facebook AI", "sample.person@meta.example", (), ()),            # alias: facebook=meta
    ("Meta", "sample.person@meta.example", (), ()),
    ("Examplecorp", "user@examplecorp.test", (), ()),
    ("Berkeley Lab", "x@lbl.gov", (), ()),                 # alias group
    ("Recurse Center", "", ("https://recurse.com/about",), ()),
    ("Wikimedia Foundation", "", ("https://wikimedia.org/wiki/User:X",), ()),
    ("Exampleauto", "", ("https://exampleauto.test/team",), ()),
]

UNSUPPORTED = [
    # the fabrications and the junk
    ("Heriot-Watt University", "kim@example.com", ("https://linkedin.com/in/kima",), ()),
    ("University at Buffalo", "", (), ()),
    ("Carnegie Mellon University", "frosty@example.com", (), ()),
    ("Saltlaketoastmastersclub", "", (), ()),
    ("Dedham", "", (), ()),
    ("American", "", (), ()),                              # United Airlines' "org"
    ("Engineering", "shahyar@example.com", (), ()),
    ("Angel Investor / Individual", "mossglade@me.com", (), ()),
    ("Independent / Freelance", "jjonesered@example.com", (), ()),
    ("Gauteng Provincial Government", "kathleen@example.com", (), ()),
    # a page anyone can have is not employment
    ("GitHub", "", ("https://github.com/brainwane",), ()),
    ("Google", "", ("https://developers.google.com/profile/u/ankita",), ()),
    ("Medium", "", ("https://medium.com/@someone",), ()),
    # a generic word matching a generic domain is not corroboration
    ("The Design Studio", "x@designco.com", (), ()),
]

fails = 0
for org, em, urls, other in SUPPORTED:
    if not corroborate(org, em, urls, other):
        print("  MISS  %-28r should be supported" % org)
        fails += 1
for org, em, urls, other in UNSUPPORTED:
    why = corroborate(org, em, urls, other)
    if why:
        print("  FALSE %-28r wrongly supported by %s" % (org, why))
        fails += 1

assert is_free_mail("gmail.com") and is_free_mail("me.com")
assert not is_free_mail("meta.com"), "prefix match would eat meta.com"
assert not is_free_mail("examplecorp.test")
assert tokens("The Design Studio") == []
assert "example" in tokens("Example Health")
assert "meta" in candidates("Facebook AI")
# a short alias must not substring-match: 'lbl' is inside 'quibbler.com'
assert not corroborate("Berkeley Lab", "x@quibbler.com", (), ())
# an org must not corroborate itself
assert corroborate("Acme Corp", "", (), ("Acme Corp",)) is None

n = len(SUPPORTED) + len(UNSUPPORTED)
print("  %d/%d cases pass" % (n - fails, n))
raise SystemExit(1 if fails else 0)
