import os
import sys
import unittest


def _add_pipelines_to_syspath():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    p = os.path.join(repo_root, "scripts", "pipelines")
    if p not in sys.path:
        sys.path.insert(0, p)


_add_pipelines_to_syspath()

from db_migrate import split_sql_statements  # noqa: E402


class SqlSplitterTests(unittest.TestCase):
    def test_splits_basic_statements(self):
        sql = "CREATE TABLE a(x int); CREATE TABLE b(y int);"
        stmts = split_sql_statements(sql)
        self.assertEqual(len(stmts), 2)
        self.assertTrue(stmts[0].startswith("CREATE TABLE a"))
        self.assertTrue(stmts[1].startswith("CREATE TABLE b"))

    def test_ignores_semicolon_in_single_quotes(self):
        sql = "INSERT INTO t VALUES('a;b');\nSELECT 1;"
        stmts = split_sql_statements(sql)
        self.assertEqual(len(stmts), 2)
        self.assertIn("'a;b'", stmts[0])

    def test_ignores_semicolon_in_dollar_quoted_blocks(self):
        sql = "DO $$ BEGIN RAISE NOTICE 'a;b'; END $$;\nSELECT 1;"
        stmts = split_sql_statements(sql)
        self.assertEqual(len(stmts), 2)
        self.assertTrue(stmts[0].startswith("DO $$"))
        self.assertEqual(stmts[1].strip(), "SELECT 1")

    def test_ignores_comments(self):
        sql = "-- comment;\nSELECT 1; /* block;comment; */ SELECT 2;"
        stmts = split_sql_statements(sql)
        self.assertEqual([s.strip() for s in stmts], ["SELECT 1", "SELECT 2"])


if __name__ == "__main__":
    unittest.main()
