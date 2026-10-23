#!/usr/bin/env python3
"""Unit tests for fetch_arcade_benchmark.py."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.datasets.fetch_arcade_benchmark import (
    extract_input_files,
    generate_mock_arcade_records,
    load_from_local_file,
    parse_arcade_episode,
    save_records_to_jsonl,
)


class TestFetchArcadeBenchmark(unittest.TestCase):

    def test_extract_input_files(self):
        code1 = "df = pd.read_csv('train_data.csv')\nconn = sqlite3.connect('metrics.sqlite3')"
        code2 = "table = pd.read_parquet('features.parquet')"
        files = extract_input_files(code1, code2)
        self.assertIn("train_data.csv", files)
        self.assertIn("metrics.sqlite3", files)
        self.assertIn("features.parquet", files)

    def test_parse_arcade_episode_single_turn(self):
        episode = {
            "notebook_name": "single_task.ipynb",
            "turns": [
                {
                    "input": "Load dataset and print head",
                    "turn": {
                        "intent": {"value": "Load 'data.csv' and print first 5 rows", "output_variables": ["df"]},
                        "code": {"value": "df = pd.read_csv('data.csv')\nprint(df.head())"},
                    },
                }
            ],
        }
        records = parse_arcade_episode(episode, episode_idx=0, source_split="test")
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["turn_type"], "single-turn")
        self.assertEqual(rec["intent"], "Load 'data.csv' and print first 5 rows")
        self.assertIn("data.csv", rec["input_files"])

    def test_parse_arcade_episode_multi_turn(self):
        episode = {
            "notebook_name": "multi_task.ipynb",
            "turns": [
                {
                    "input": "Step 1",
                    "turn": {
                        "intent": {"value": "Connect to 'db.sqlite'"},
                        "code": {"value": "conn = sqlite3.connect('db.sqlite')"},
                    },
                },
                {
                    "input": "Step 2",
                    "turn": {
                        "intent": {"value": "Query results"},
                        "code": {"value": "res = conn.execute('SELECT 1').fetchall()"},
                    },
                },
            ],
        }
        records = parse_arcade_episode(episode, episode_idx=1, source_split="test")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["turn_type"], "multi-turn")
        self.assertEqual(records[1]["turn_type"], "multi-turn")
        self.assertIn("db.sqlite", records[0]["input_files"])
        self.assertIn("db.sqlite", records[1]["input_files"])

    def test_save_and_load_records(self):
        records = generate_mock_arcade_records()
        self.assertGreater(len(records), 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "output.jsonl"
            save_records_to_jsonl(records, out_path)
            self.assertTrue(out_path.exists())

            # Read back and verify json lines
            with open(out_path, "r", encoding="utf-8") as f:
                lines = [json.loads(line) for line in f]
            self.assertEqual(len(lines), len(records))
            self.assertIn("intent", lines[0])
            self.assertIn("code", lines[0])
            self.assertIn("turn_type", lines[0])
            self.assertIn("input_files", lines[0])


if __name__ == "__main__":
    unittest.main()
