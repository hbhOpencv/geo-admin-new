from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


BACKEND_DIR = Path(__file__).resolve().parents[1]
ALGORITHMS_DIR = BACKEND_DIR / "app" / "algorithms"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(ALGORITHMS_DIR))

from app.algorithms.geochem_mining.correlation import (  # noqa: E402
    CorrelationConfig,
    build_candidate_combination_cards,
    build_target_pair_coanomaly,
)
from app.algorithms.geochem_mining.data_pipeline import DEFAULT_DB_PATH, load_geochem_dataset  # noqa: E402
from app.algorithms.geochem_mining.professional_rules import (  # noqa: E402
    build_threshold_profile,
    classify_mining_level,
)
from app.algorithms.geochem_mining.variation import (  # noqa: E402
    VariationConfig,
    robust_log_mad_background,
    run_variation_algorithm,
)
from geochem_geology_constrained import (  # noqa: E402
    attach_region_sample_support,
    interpolate_constrained_csv,
    leave_one_hole_out_validation,
)
from geochem_stl_interpolate_fast import interpolate_csv as interpolate_baseline_csv  # noqa: E402


class GeochemEvidenceV5Tests(unittest.TestCase):
    def test_senior_baseline_uses_log_idw_and_exposes_spatial_support(self):
        samples = pd.DataFrame(
            [
                {"hole_id": "H1", "x": 0.0, "y": 0.0, "z": 0.0, "Cu": 10.0},
                {"hole_id": "H2", "x": 10.0, "y": 0.0, "z": 0.0, "Cu": 100.0},
                {"hole_id": "H3", "x": 20.0, "y": 0.0, "z": 0.0, "Cu": 1000.0},
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            voxel_csv = root / "voxels.csv"
            output_csv = root / "result.csv"
            pd.DataFrame(
                [{"x": 10.0, "y": 0.0, "z": 0.0}, {"x": 5000.0, "y": 0.0, "z": 0.0}]
            ).to_csv(voxel_csv, index=False)
            interpolate_baseline_csv(
                voxel_csv,
                samples,
                ["Cu"],
                output_csv,
                nearest=3,
                power=2.0,
                transform="log",
                search_radius=100.0,
            )
            result = pd.read_csv(output_csv)
        self.assertAlmostEqual(float(result.iloc[0]["Cu"]), 100.0)
        self.assertEqual(int(result.iloc[0]["Cu_supporting_holes"]), 3)
        self.assertEqual(result.iloc[0]["confidence_level"], "high")
        self.assertTrue(pd.isna(result.iloc[1]["Cu"]))
        self.assertEqual(result.iloc[1]["confidence_level"], "no_data")

    def test_relative_colours_have_one_project_wide_meaning(self):
        copper = build_threshold_profile(
            "Cu",
            pd.Series([10, 20, 30]),
            100,
            200,
            relative_ratio_cutoffs=[1.25, 1.5, 2, 4],
        )
        zinc = build_threshold_profile(
            "Zn",
            pd.Series([1000, 2000, 3000]),
            1000,
            2000,
            relative_ratio_cutoffs=[1.25, 1.5, 2, 4],
        )
        self.assertEqual(
            classify_mining_level(180, copper)["mining_level"],
            classify_mining_level(1800, zinc)["mining_level"],
        )
        self.assertEqual(copper["relative_band_method"], "project_wide_fixed_background_ratios")

    def test_log_mad_is_not_dragged_to_extreme_raw_value(self):
        values = pd.Series([100.0] * 40 + [1_000_000.0])
        result = robust_log_mad_background(values, 30)
        self.assertAlmostEqual(float(result["background_mean"]), 100.0)
        self.assertAlmostEqual(float(result["threshold_T_auto"]), 100.0)
        self.assertEqual(int(result["removed_high_count"]), 1)

    def test_model_background_is_frozen_when_hole_filter_changes(self):
        dataset = load_geochem_dataset(DEFAULT_DB_PATH, model_id=1, selected_elements=["Cu"])
        hole_id = str(dataset.assays.iloc[0]["hole_id"])
        config = VariationConfig(
            minimum_positive_count=30,
            background_method="log_mad",
            background_scope="model",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_variation_algorithm(
                DEFAULT_DB_PATH,
                1,
                root / "all",
                config,
                {"selected_elements": ["Cu"]},
            )
            run_variation_algorithm(
                DEFAULT_DB_PATH,
                1,
                root / "one-hole",
                config,
                {"selected_elements": ["Cu"], "hole_ids": [hole_id]},
            )
            all_stats = pd.read_csv(root / "all" / "element_background_statistics.csv")
            filtered_stats = pd.read_csv(root / "one-hole" / "element_background_statistics.csv")
        self.assertAlmostEqual(
            float(all_stats.iloc[0]["background_mean"]),
            float(filtered_stats.iloc[0]["background_mean"]),
        )

    def test_target_pair_cards_use_explicit_denominator_and_spatial_evidence(self):
        assays = pd.DataFrame(
            {
                "assay_id": [1, 2, 3, 4, 5],
                "Cu": [1, 1, 1, 1, 1],
                "Zn": [1, 1, 1, 1, 1],
            }
        )
        anomalies = pd.DataFrame(
            [
                {"assay_id": 1, "element": "Cu", "hole_id": "H1", "section_geobody_key": "G", "meets_statistical_threshold": True, "meets_boundary_grade": False, "meets_industrial_grade": False},
                {"assay_id": 2, "element": "Cu", "hole_id": "H1", "section_geobody_key": "G", "meets_statistical_threshold": True, "meets_boundary_grade": False, "meets_industrial_grade": False},
                {"assay_id": 1, "element": "Zn", "hole_id": "H1", "section_geobody_key": "G", "meets_statistical_threshold": True, "meets_boundary_grade": False, "meets_industrial_grade": False},
                {"assay_id": 2, "element": "Zn", "hole_id": "H1", "section_geobody_key": "G", "meets_statistical_threshold": True, "meets_boundary_grade": False, "meets_industrial_grade": False},
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            anomalies.to_csv(root / "anomaly_intervals.csv", index=False)
            config = CorrelationConfig(
                focus_elements=("Cu",),
                minimum_lift=1.2,
                strong_correlation_threshold=0.5,
            )
            evidence = build_target_pair_coanomaly(assays, ["Cu", "Zn"], root, config)
        row = evidence.iloc[0]
        self.assertEqual(int(row["n_common_positive"]), 5)
        self.assertEqual(int(row["co_anomaly_count"]), 2)
        self.assertAlmostEqual(float(row["lift"]), 2.5)
        self.assertEqual(int(row["n11_both_anomalous"]), 2)
        self.assertEqual(int(row["n10_target_only"]), 0)
        self.assertEqual(int(row["n01_candidate_only"]), 0)
        self.assertEqual(int(row["n00_neither"]), 3)
        self.assertTrue(np.isfinite(float(row["fisher_p"])))
        self.assertTrue(np.isfinite(float(row["bh_q"])))

        pairs = pd.DataFrame(
            [{
                "element_a": "Cu", "element_b": "Zn", "pearson_log_r": 0.8,
                "pearson_raw_r": 0.6, "pearson_r": 0.8, "spearman_r": 0.75,
                "n_common": 5, "pair_status": "ok",
            }]
        )
        same_hole = pd.DataFrame(
            [{"target_element": "Cu", "candidate_element": "Zn", "hole_id": "H1"}]
        )
        cards = build_candidate_combination_cards(
            pairs, evidence, same_hole, pd.DataFrame(), config
        )
        self.assertEqual(cards.iloc[0]["evidence_tier"], "B")
        self.assertFalse(bool(cards.iloc[0]["formal_candidate"]))
        evidence_high = evidence.copy()
        evidence_high.loc[:, "co_anomaly_count"] = 3
        no_cross_hole_cards = build_candidate_combination_cards(
            pairs, evidence_high, same_hole, pd.DataFrame(), config
        )
        self.assertEqual(no_cross_hole_cards.iloc[0]["evidence_tier"], "B")
        self.assertIn("缺少跨孔重复", no_cross_hole_cards.iloc[0]["evidence_reason"])
        self.assertEqual(no_cross_hole_cards.iloc[0]["spatial_grade"], "same_hole")
        self.assertFalse(bool(no_cross_hole_cards.iloc[0]["spatial_action_allowed"]))

    def test_log_interpolation_outputs_threshold_support_and_loho(self):
        samples = pd.DataFrame(
            [
                {"hole_id": "H1", "x": 0.0, "y": 0.0, "z": 0.0, "Cu": 10.0, "lithology_code": 1},
                {"hole_id": "H2", "x": 10.0, "y": 0.0, "z": 0.0, "Cu": 100.0, "lithology_code": 1},
                {"hole_id": "H3", "x": 20.0, "y": 0.0, "z": 0.0, "Cu": 1000.0, "lithology_code": 1},
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            voxel_csv = root / "voxels.csv"
            output_csv = root / "result.csv"
            pd.DataFrame(
                [
                    {"X": 0.0, "Y": 0.0, "Z": 0.0, "pred_code_final": 1, "dist_fault": 100.0},
                    {"X": 10.0, "Y": 0.0, "Z": 0.0, "pred_code_final": 1, "dist_fault": 100.0},
                ]
            ).to_csv(voxel_csv, index=False)
            interpolate_constrained_csv(
                voxel_csv,
                samples,
                "Cu",
                output_csv,
                nearest=3,
                search_radius=100,
                transform="log",
                statistical_threshold=50,
                boundary_grade=500,
                validation_quality="high",
            )
            result = pd.read_csv(output_csv)
        self.assertTrue(np.isclose(result.iloc[0]["Cu"], 10.0))
        self.assertEqual(float(result.iloc[0]["Cu_support_T"]), 0.0)
        self.assertEqual(float(result.iloc[1]["Cu_support_T"]), 1.0)
        details, summary = leave_one_hole_out_validation(
            samples, "Cu", nearest=2, search_radius=100
        )
        self.assertEqual(set(summary["transform"]), {"raw", "log"})
        self.assertEqual(len(details), 6)
        self.assertIn("log_mae", summary.columns)
        self.assertIn("spearman_r", summary.columns)
        self.assertIn("absolute_log_error", details.columns)
        regions = pd.DataFrame(
            [{
                "region_id": "T-1", "evidence_type": "T",
                "centroid_x": 10.0, "centroid_y": 0.0, "centroid_z": 0.0,
                "min_x": 0.0, "max_x": 20.0,
                "min_y": 0.0, "max_y": 0.0,
                "min_z": 0.0, "max_z": 0.0,
            }]
        )
        traced = attach_region_sample_support(
            regions, samples, "Cu", 10.0, {"T": 50.0}
        )
        self.assertEqual(int(traced.iloc[0]["supporting_hole_count"]), 3)
        self.assertEqual(int(traced.iloc[0]["raw_exceedance_count"]), 2)


if __name__ == "__main__":
    unittest.main()
