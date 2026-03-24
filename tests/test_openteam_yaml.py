import unittest

import yaml


class OpenTeamYamlTests(unittest.TestCase):
    def test_safe_load_supports_top_level_scalars(self) -> None:
        self.assertIs(yaml.safe_load("false"), False)
        self.assertEqual(yaml.safe_load("2"), 2)

    def test_safe_load_supports_block_scalars_inside_sequences(self) -> None:
        doc = yaml.safe_load(
            """
items:
  - name: one
    body: |
      line 1

      line 2
"""
        )

        self.assertEqual(doc["items"][0]["name"], "one")
        self.assertEqual(doc["items"][0]["body"], "line 1\n\nline 2")

    def test_safe_dump_writes_nested_data(self) -> None:
        payload = {"enabled": True, "items": [{"name": "demo", "count": 2}]}
        dumped = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        loaded = yaml.safe_load(dumped)

        self.assertEqual(loaded, payload)


if __name__ == "__main__":
    unittest.main()
