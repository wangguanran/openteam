import unittest

import openteam_yaml


class OpenTeamYamlTests(unittest.TestCase):
    def test_safe_dump_preserves_empty_nested_lists(self):
        payload = {
            "requirements": [
                {
                    "constraints": [],
                    "acceptance": [],
                    "supersedes": [],
                    "conflicts_with": [],
                    "decision_log_refs": [],
                }
            ]
        }

        dumped = openteam_yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)

        self.assertIn("constraints: []", dumped)
        self.assertIn("acceptance: []", dumped)
        self.assertIn("supersedes: []", dumped)
        self.assertIn("conflicts_with: []", dumped)
        self.assertIn("decision_log_refs: []", dumped)
        self.assertEqual(openteam_yaml.safe_load(dumped), payload)


if __name__ == "__main__":
    unittest.main()
