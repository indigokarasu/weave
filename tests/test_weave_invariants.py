"""Unit tests for Weave's pure logic: URL canonicalization and edge integrity.

No network, no Google API, no production DB. If one of these fails, the
canonical form or the integrity contract is broken and every write path that
touches URLs is suspect.
"""
import os
import sqlite3
import sys
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


class TestUrlNormalization(unittest.TestCase):
    def setUp(self):
        from url_norm import canonical_url, dedupe_key
        self.canonical_url = canonical_url
        self.dedupe_key = dedupe_key

    def test_scheme_and_www_are_normalized(self):
        for raw in ("http://www.github.com/ada",
                    "https://github.com/ada/",
                    "https://GitHub.com/ada"):
            self.assertEqual(self.dedupe_key(self.canonical_url(raw)),
                             self.dedupe_key(self.canonical_url("https://github.com/ada")),
                             raw)

    def test_trailing_slash_does_not_create_a_second_key(self):
        self.assertEqual(self.dedupe_key(self.canonical_url("https://example.com/x/")),
                         self.dedupe_key(self.canonical_url("https://example.com/x")))

    def test_two_different_paths_stay_distinct(self):
        self.assertNotEqual(self.dedupe_key(self.canonical_url("https://example.com/a")),
                            self.dedupe_key(self.canonical_url("https://example.com/b")))

    def test_tracking_params_are_stripped_but_meaningful_ones_survive(self):
        # utm_* carries no identity; a real query param is part of the link.
        self.assertEqual(self.dedupe_key(self.canonical_url("https://example.com/p?utm_source=x")),
                         self.dedupe_key(self.canonical_url("https://example.com/p")))
        self.assertNotEqual(self.dedupe_key(self.canonical_url("https://example.com/p?id=7")),
                            self.dedupe_key(self.canonical_url("https://example.com/p")))

    def test_fragments_are_kept_because_some_sites_route_identity_them(self):
        # rdio.com/#/people/<user> -- dropping the fragment merges real people.
        self.assertIn("#", self.canonical_url("https://rdio.com/#/people/ada"))

    def test_handle_paths_fold_but_generic_paths_do_not(self):
        # A GitHub handle is case-insensitive; a generic path may be
        # case-significant, so canonical_url must not lower it.
        self.assertEqual(self.canonical_url("https://github.com/AdaLovelace"),
                         "https://github.com/adalovelace")
        self.assertEqual(self.canonical_url("https://example.com/CaseSensitive"),
                         "https://example.com/CaseSensitive")

    def test_bare_email_is_not_a_url(self):
        # Prepending a scheme to a bare address made urlsplit read the local
        # part as userinfo, collapsing many people onto one key.
        self.assertIsNone(self.canonical_url("someone@gmail.com"))
        self.assertIsNone(self.canonical_url("mailto:someone@gmail.com"))

    def test_empty_and_none_are_falsy(self):
        self.assertFalse(self.canonical_url(""))
        self.assertFalse(self.canonical_url(None))


class TestEdgeIntegrity(unittest.TestCase):
    """`edges.target_id` is polymorphic — it may point at facts or preferences.

    A foreign key to persons(id) is wrong by construction and makes HasFact
    inserts fail. This builds the schema both ways and proves the difference.
    """

    def _db(self, with_fk):
        con = sqlite3.connect(":memory:")
        # SQLite does NOT enforce foreign keys unless asked. A test that omits
        # this proves nothing about the constraint it claims to be testing.
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("CREATE TABLE persons (id TEXT PRIMARY KEY)")
        con.execute("CREATE TABLE facts (id TEXT PRIMARY KEY)")
        con.execute("CREATE TABLE edges (source_id TEXT, target_id TEXT, rel_type TEXT)")
        if with_fk:
            con.execute("ALTER TABLE edges RENAME TO edges_old")
            con.execute("""CREATE TABLE edges (
                                source_id TEXT, target_id TEXT, rel_type TEXT,
                                FOREIGN KEY (target_id) REFERENCES persons(id))""")
            con.execute("DROP TABLE edges_old")
        con.execute("INSERT INTO persons VALUES ('p1')")
        con.execute("INSERT INTO facts VALUES ('f1')")
        return con

    def test_has_fact_insert_works_without_the_wrong_fk(self):
        con = self._db(with_fk=False)
        try:
            con.execute("INSERT INTO edges VALUES ('p1','f1','HasFact')")
            self.assertEqual(
                con.execute("SELECT count(*) FROM edges WHERE rel_type='HasFact'").fetchone()[0], 1)
        finally:
            con.close()

    def test_wrong_fk_makes_the_has_fact_insert_fail(self):
        con = self._db(with_fk=True)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("INSERT INTO edges VALUES ('p1','f1','HasFact')")
        finally:
            con.close()


class TestSkillPackageIntegrity(unittest.TestCase):
    """The package must not cite a reference that does not exist.

    A phantom `references/*.md` pointer makes a whole subsystem look available
    while the agent following it finds nothing.
    """

    def test_every_cited_reference_exists(self):
        import re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "SKILL.md"), encoding="utf-8") as fh:
            content = fh.read()
        cited = set(re.findall(r"references/[A-Za-z0-9_.-]+\.md", content))
        missing = [r for r in sorted(cited)
                   if not os.path.exists(os.path.join(root, r))]
        self.assertEqual(missing, [], "phantom references cited by SKILL.md")

    def test_frontmatter_parses(self):
        import yaml
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "SKILL.md"), encoding="utf-8") as fh:
            content = fh.read()
        fm = yaml.safe_load(content.split("---")[1])
        self.assertEqual(fm["name"], "ocas-weave")
        self.assertTrue(fm["description"])
        self.assertIn("category", fm["metadata"]["hermes"])


if __name__ == "__main__":
    unittest.main()
