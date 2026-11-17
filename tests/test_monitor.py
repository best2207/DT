import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main

class MonitorTests(unittest.TestCase):
    def test_china_policy_gets_priority(self):
        event = {"title": "EU anti-dumping investigation on China steel", "url": "https://example.test/1", "source": "fixture"}
        result = main.annotate(event, [], {"china_keywords": ["China"], "risk_keywords": {"P1": [], "P2": ["anti-dumping"]}})
        self.assertTrue(result["china_related"])
        self.assertEqual(result["risk_level"], "P2")

    def test_company_alias_match(self):
        event = {"title": "Alpha Corp insolvency filing", "url": "https://example.test/2", "source": "fixture"}
        result = main.annotate(event, [{"name": "Alpha Holdings", "aliases": ["Alpha Corp"]}], {"risk_keywords": {"P1": [], "P2": ["insolvency"]}})
        self.assertEqual(result["matched_companies"][0]["name"], "Alpha Holdings")
        self.assertEqual(result["risk_level"], "P2")

    def test_merge_deduplicates_urls(self):
        raw = {"title": "Example event", "url": "https://example.test/3", "source": "fixture"}
        settings = {"risk_keywords": {"P1": [], "P2": []}, "china_keywords": [], "max_events": 10}
        items, fresh = main.merge([raw, raw], [], [], settings)
        self.assertEqual(len(items), 1)
        self.assertEqual(len(fresh), 1)

if __name__ == "__main__":
    unittest.main()
