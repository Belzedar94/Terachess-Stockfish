import argparse
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lmp_shadow_harness as harness


def sample_record():
    return {
        "schema": harness.SCHEMA_TRACE,
        "sequence": 1,
        "root_fen": "root",
        "fen": "node",
        "probe_mode": "baseline",
        "depth": 4,
        "improving": False,
        "probe_trigger_rank": 9,
        "baseline_trigger_rank": 9,
        "baseline_trigger_depth": 4,
        "legal_move_count": 13,
        "tail_quiets": 4,
        "baseline_skipped_moves": ["m10", "m11", "m12", "m13"],
        "baseline_cutoff": False,
        "best_after": 0,
        "tail": [
            {"rank": 10, "move": "m10", "pruned_by_rest": False, "value": -1, "nodes": 2},
            {"rank": 11, "move": "m11", "pruned_by_rest": True, "value": None, "nodes": 0},
            {"rank": 12, "move": "m12", "pruned_by_rest": False, "value": 3, "nodes": 5},
            {"rank": 13, "move": "m13", "pruned_by_rest": False, "value": 4, "nodes": 7},
        ],
    }


class LmpShadowHarnessTests(unittest.TestCase):

    def test_frozen_root_manifest_identity(self):
        roots = Path(__file__).resolve().parent / "lmp_shadow_roots_v1.json"
        self.assertEqual(harness.sha256_file(roots), harness.ROOTS_SHA256)

    def test_analysis_gate_parameters_cannot_be_relaxed(self):
        args = argparse.Namespace(
            limit=None,
            min_exposed=harness.FROZEN_MIN_EXPOSED,
            min_per_stratum=harness.FROZEN_MIN_PER_STRATUM,
            oracle_sample=harness.FROZEN_ORACLE_SAMPLE,
        )
        self.assertTrue(
            harness.analysis_parameters_frozen(args, harness.FROZEN_ROOTS_PER_SPLIT)
        )
        args.min_exposed -= 1
        self.assertFalse(
            harness.analysis_parameters_frozen(args, harness.FROZEN_ROOTS_PER_SPLIT)
        )
        args.min_exposed = harness.FROZEN_MIN_EXPOSED
        args.limit = 1
        self.assertFalse(
            harness.analysis_parameters_frozen(args, harness.FROZEN_ROOTS_PER_SPLIT)
        )

    def test_uci_script_waits_for_the_last_asynchronous_search(self):
        script = harness.uci_script(
            [{"fen": "root-a"}, {"fen": "root-b"}], Path("net.tnn"), 100000
        ).splitlines()
        self.assertEqual(script[-3:], ["go nodes 100000", "ucinewgame", "quit"])
        self.assertEqual(script.count("ucinewgame"), 3)

    def test_exact_live_skip_record_is_structurally_valid(self):
        self.assertEqual(harness.validate_trace_structure([sample_record()]), [])

    def test_rank_model_mismatch_fails_closed(self):
        record = sample_record()
        record["baseline_skipped_moves"] = ["m13"]
        errors = harness.validate_trace_structure([record])
        self.assertTrue(any("rank simulation" in error for error in errors))

    def test_frozen_thresholds(self):
        thresholds = harness.policy_thresholds(sample_record())
        self.assertEqual(thresholds["baseline"], 9)
        self.assertEqual(thresholds["U2"], 18)
        self.assertIsNone(thresholds["D4"])
        self.assertNotIn("U3/4", thresholds)
        self.assertEqual(thresholds["k1.5"], 28)
        self.assertEqual(thresholds["legal-half"], 9)
        self.assertIsNone(thresholds["no-LMP"])

    def test_first_non_loss_move_delays_but_never_advances_a_trigger(self):
        record = sample_record()
        record.update(
            {
                "depth": 1,
                "probe_trigger_rank": 5,
                "baseline_trigger_rank": 5,
                "baseline_trigger_depth": 1,
                "tail": [],
                "tail_quiets": 0,
                "baseline_skipped_moves": [],
            }
        )
        thresholds = harness.policy_thresholds(record)
        self.assertEqual(thresholds["baseline"], 5)
        self.assertEqual(thresholds["U2"], 5)

    def test_baseline_and_no_lmp_classification(self):
        record = sample_record()
        thresholds = harness.policy_thresholds(record)
        skipped = set(record["baseline_skipped_moves"])
        self.assertFalse(harness.retained("baseline", record["tail"][0], thresholds, skipped))
        self.assertFalse(harness.retained("baseline", record["tail"][2], thresholds, skipped))
        self.assertTrue(harness.retained("no-LMP", record["tail"][2], thresholds, skipped))
        self.assertTrue(harness.retained("U2", record["tail"][0], thresholds, skipped))

    def test_u34_direction_control_is_separate(self):
        record = sample_record()
        record.update(
            {
                "probe_mode": "u34",
                "probe_trigger_rank": 6,
                "legal_move_count": 11,
                "baseline_skipped_moves": ["m10", "m11"],
                "tail": [
                    {"rank": 7, "move": "m7", "pruned_by_rest": False, "value": -1, "nodes": 2},
                    {"rank": 8, "move": "m8", "pruned_by_rest": True, "value": None, "nodes": 0},
                    {"rank": 10, "move": "m10", "pruned_by_rest": False, "value": 3, "nodes": 5},
                    {"rank": 11, "move": "m11", "pruned_by_rest": False, "value": 4, "nodes": 7},
                ],
            }
        )
        self.assertEqual(harness.validate_trace_structure([record]), [])
        self.assertEqual(set(harness.policy_thresholds(record)), {"baseline", "U3/4"})

    def test_downstream_pruned_move_with_value_is_rejected(self):
        record = copy.deepcopy(sample_record())
        record["tail"][1]["value"] = 0
        errors = harness.validate_trace_structure([record])
        self.assertTrue(any("downstream-pruned" in error for error in errors))

    def test_uci_search_parser_uses_last_info_before_bestmove(self):
        output = "\n".join(
            [
                "info depth 2 score cp 1 nodes 10 time 1",
                "info depth 3 seldepth 5 score cp 7 nodes 20 time 2",
                "bestmove a1a2",
                "info depth 1 score mate 3 nodes 4 time 1",
                "bestmove b1b2",
            ]
        )
        self.assertEqual(
            harness.parse_searches(output),
            [
                {
                    "depth": 3,
                    "seldepth": 5,
                    "nodes": 20,
                    "time_ms": 2,
                    "score_kind": "cp",
                    "score": 7,
                    "bestmove": "a1a2",
                },
                {
                    "depth": 1,
                    "nodes": 4,
                    "time_ms": 1,
                    "score_kind": "mate",
                    "score": 3,
                    "bestmove": "b1b2",
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
