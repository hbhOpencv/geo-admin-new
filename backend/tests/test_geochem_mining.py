from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.algorithms.geochem_mining.correlation import (  # noqa: E402
    _pairwise_correlations,
    build_cluster_geology_summary,
    build_threshold_sensitivity,
    build_cluster_overlap,
    build_correlation_matrices,
    enrich_cluster_statistics,
    r_type_cluster,
)
from app.algorithms.geochem_mining.data_pipeline import DEFAULT_DB_PATH, load_geochem_dataset  # noqa: E402
from app.algorithms.geochem_mining.variation import expert_2sigma_background, iterative_upper_background, merge_anomaly_segments  # noqa: E402
from app.models import get_session  # noqa: E402
from app.services.geochem_mining_service import available_options, conflicting_algorithm_modes  # noqa: E402


class MiningAlgorithmTests(unittest.TestCase):
    def test_model_scoped_dataset_and_project_counts(self):
        dataset = load_geochem_dataset(DEFAULT_DB_PATH, model_id=1)
        self.assertEqual(len(dataset.assays), 3351)
        self.assertEqual(len(dataset.elements), 41)
        self.assertTrue((dataset.assays["model_id"] == 1).all())
        self.assertEqual(len(dataset.invalid_intervals), 6)

    def test_filters_are_applied_without_changing_database(self):
        full = load_geochem_dataset(DEFAULT_DB_PATH, model_id=1)
        hole = str(full.assays.iloc[0]["hole_id"])
        element = full.elements[0]
        filtered = load_geochem_dataset(
            DEFAULT_DB_PATH, model_id=1, selected_elements=[element], hole_ids=[hole]
        )
        self.assertEqual(filtered.elements, [element])
        self.assertTrue((filtered.assays["hole_id"] == hole).all())
        self.assertLess(len(filtered.assays), len(full.assays))

    def test_unknown_model_and_element_are_rejected(self):
        with self.assertRaises(ValueError):
            load_geochem_dataset(DEFAULT_DB_PATH, model_id=999999)
        with self.assertRaises(ValueError):
            load_geochem_dataset(DEFAULT_DB_PATH, model_id=1, selected_elements=["NotAnElement"])

    def test_iterative_background_and_segment_merge(self):
        result = iterative_upper_background(pd.Series([10.0] * 40 + [1000.0]), 30)
        self.assertEqual(result["removed_high_count"], 1)
        anomalies = pd.DataFrame([
            {"assay_id": 1, "hole_id": "H1", "from_depth": 0.0, "to_depth": 2.0, "element": "Cu", "value": 10.0, "threshold_T_auto": 2.0, "value_to_threshold_ratio": 5.0, "anomaly_level": "内带异常", "x_approx": 1.0, "y_approx": 2.0, "z_approx": 99.0, "coordinate_quality": "approximate_vertical", "section_geobody_key": "A", "geobody_property_status": "exact"},
            {"assay_id": 2, "hole_id": "H1", "from_depth": 2.0, "to_depth": 9.0, "element": "Cu", "value": 6.0, "threshold_T_auto": 2.0, "value_to_threshold_ratio": 3.0, "anomaly_level": "中带异常", "x_approx": 1.0, "y_approx": 2.0, "z_approx": 94.5, "coordinate_quality": "approximate_vertical", "section_geobody_key": "A", "geobody_property_status": "exact"},
        ])
        merged = merge_anomaly_segments(anomalies)
        self.assertEqual(len(merged), 1)
        self.assertAlmostEqual(float(merged.iloc[0]["z_approx_mid"]), 95.5)

    def test_expert_2sigma_background_removes_outliers_and_records_formula(self):
        result = expert_2sigma_background(pd.Series([10.0] * 40 + [1000.0]), 30)
        self.assertEqual(result["background_method"], "expert_2sigma")
        self.assertEqual(result["removed_high_count"], 1)
        self.assertEqual(result["threshold_formula"], "X + 2σ")
        self.assertAlmostEqual(float(result["background_mean"]), 10.0)

    def test_correlation_matrix_works_with_read_only_pandas_arrays(self):
        assays = pd.DataFrame({"A": [1, 2, 3, 4], "B": [2, 4, 6, 8], "C": [8, 3, 5, 1]})
        pairs = _pairwise_correlations(assays, ["A", "B", "C"], 3)
        pearson, _, _ = build_correlation_matrices(pairs, ["A", "B", "C"], 0.5)
        groups, merges, linkage = r_type_cluster(pearson, 0.5)
        self.assertEqual(float(pearson.loc["A", "A"]), 1.0)
        self.assertEqual(len(merges), 2)
        self.assertEqual(len(linkage), 2)
        self.assertFalse(groups.empty)

    def test_negative_correlation_strength_and_stability_are_reported(self):
        assays = pd.DataFrame({"A": [1, 2, 3, 4], "B": [8, 6, 4, 2]})
        pairwise = _pairwise_correlations(assays, ["A", "B"], 3)
        _, _, pairs = build_correlation_matrices(pairwise, ["A", "B"], 0.5)
        self.assertEqual(pairs.iloc[0]["correlation_level"], "强负相关")
        self.assertEqual(pairs.iloc[0]["stability_note"], "Pearson与Spearman均较强且方向一致")

    def test_correlation_only_mode_has_typed_overlap_placeholders(self):
        clusters = pd.DataFrame([
            {"cluster_id": 1, "elements": "A,B", "is_multielement_combination": True}
        ])
        overlap = build_cluster_overlap(clusters, Path("missing-variation-output"))
        self.assertEqual(overlap.iloc[0]["variation_status"], "variation_algorithm_not_run")
        self.assertEqual(int(overlap.iloc[0]["cluster_id"]), 1)
        self.assertEqual(overlap.iloc[0]["elements"], "A,B")

    def test_overlap_location_uses_only_true_co_anomaly_intervals(self):
        clusters = pd.DataFrame([{
            "cluster_id": 1, "elements": "A,B", "is_multielement_combination": True,
        }])
        anomalies = pd.DataFrame([
            {"assay_id": 1, "element": "A", "hole_id": "H1", "section_geobody_key": "G1"},
            {"assay_id": 1, "element": "B", "hole_id": "H1", "section_geobody_key": "G1"},
            {"assay_id": 2, "element": "A", "hole_id": "H2", "section_geobody_key": "G2"},
            {"assay_id": 3, "element": "A", "hole_id": "H2", "section_geobody_key": "G2"},
            {"assay_id": 4, "element": "A", "hole_id": "H2", "section_geobody_key": "G2"},
        ])
        with tempfile.TemporaryDirectory() as directory:
            anomaly_dir = Path(directory)
            anomalies.to_csv(anomaly_dir / "anomaly_intervals.csv", index=False)
            overlap = build_cluster_overlap(clusters, anomaly_dir)
        self.assertEqual(int(overlap.iloc[0]["assays_with_two_or_more_members_anomalous"]), 1)
        self.assertEqual(overlap.iloc[0]["dominant_holes"], "H1")
        self.assertEqual(overlap.iloc[0]["dominant_section_geobody_keys"], "G1")

    def test_cluster_stability_and_geology_summary_are_explainable(self):
        matrix = pd.DataFrame(
            [[1.0, 0.8], [0.8, 1.0]], index=["A", "B"], columns=["A", "B"]
        )
        clusters = pd.DataFrame([{
            "cluster_id": 1, "elements": "A,B", "element_count": 2,
            "is_multielement_combination": True, "mean_internal_pearson_r": 0.8,
        }])
        enriched = enrich_cluster_statistics(clusters, matrix, matrix, 0.5)
        self.assertEqual(enriched.iloc[0]["confidence_level"], "高")
        self.assertAlmostEqual(float(enriched.iloc[0]["stable_pair_ratio"]), 1.0)

        assays = pd.DataFrame({
            "A": [1.0, 2.0, 9.0, 10.0], "B": [1.0, 3.0, 8.0, 11.0],
            "hole_id": ["H1", "H1", "H2", "H2"],
            "from_depth": [0.0, 1.0, 2.0, 3.0], "to_depth": [1.0, 2.0, 3.0, 4.0],
            "section_geobody_key": ["G1", "G1", "G2", "G2"],
            "stratum_code": ["S1", "S1", "S2", "S2"],
            "lithology": ["L1", "L1", "L2", "L2"],
            "weathering": ["W1", "W1", "W2", "W2"],
        })
        geology = build_cluster_geology_summary(enriched, assays)
        self.assertEqual(int(geology.iloc[0]["joint_high_interval_count"]), 1)
        self.assertEqual(geology.iloc[0]["dominant_holes"], "H2")

        sensitivity = build_threshold_sensitivity(matrix, (0.5, 0.9))
        self.assertEqual(int(sensitivity.iloc[0]["multielement_cluster_count"]), 1)
        self.assertEqual(int(sensitivity.iloc[1]["multielement_cluster_count"]), 0)


class MiningServiceTests(unittest.TestCase):
    def test_standalone_algorithms_have_independent_execution_slots(self):
        self.assertNotIn("correlation", conflicting_algorithm_modes("variation"))
        self.assertNotIn("variation", conflicting_algorithm_modes("correlation"))
        self.assertIn("both", conflicting_algorithm_modes("variation"))
        self.assertIn("both", conflicting_algorithm_modes("correlation"))
        self.assertEqual(
            set(conflicting_algorithm_modes("both")),
            {"variation", "correlation", "both"},
        )

    def test_options_are_model_scoped(self):
        db = get_session()
        try:
            options = available_options(db, 1)
        finally:
            db.close()
        self.assertEqual(options["assay_count"], 3351)
        self.assertEqual(len(options["elements"]), 41)
        self.assertEqual(len(options["holes"]), 7)
        self.assertEqual(options["geological_borehole_count"], 16)
        self.assertEqual(options["chemical_borehole_count"], 7)
        self.assertEqual(options["missing_chemical_borehole_count"], 9)


if __name__ == "__main__":
    unittest.main()
