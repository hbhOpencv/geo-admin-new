from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.algorithms.geochem_mining.correlation import CorrelationConfig, build_correlation_quality  # noqa: E402
from app.algorithms.geochem_mining.professional_rules import (  # noqa: E402
    BOUNDARY_GRADES_PPM,
    MINING_DISPLAY_LEVEL_COLORS,
    MINING_LEVEL_COLORS,
    build_threshold_profile,
    classify_mining_level,
    professional_display_label,
    relative_enrichment_ratio,
)
from app.services.geochem_service import (  # noqa: E402
    generate_element_ply,
    normalize_scene_display_labels,
    prepare_interpolation_assay_csv,
)
from app.algorithms.geochem_mining.variation import build_anomaly_intervals  # noqa: E402


class ProfessionalRuleTests(unittest.TestCase):
    def test_professional_display_labels_do_not_change_internal_level_keys(self):
        self.assertEqual(professional_display_label("最低边界品位"), "边界品位")
        self.assertEqual(professional_display_label("工业品位"), "最低工业品位")
        self.assertEqual(professional_display_label("明显正异常"), "明显异常")
        self.assertEqual(professional_display_label("强正异常"), "强正异常")
        self.assertIn("最低边界品位", MINING_DISPLAY_LEVEL_COLORS)
        self.assertIn("工业品位", MINING_DISPLAY_LEVEL_COLORS)

    def test_old_scene_manifest_is_normalized_only_at_legend_display_boundary(self):
        payload = {
            "legend": [
                {"level": "最低边界品位", "color": "#A50F15", "measured_interval_count": 2},
                {"level": "工业品位", "color": "#7A0177", "measured_interval_count": 1},
            ],
            "level_counts": {"最低边界品位": 2, "工业品位": 1},
        }
        normalized = normalize_scene_display_labels(payload)
        self.assertEqual([item["level"] for item in normalized["legend"]], ["边界品位", "最低工业品位"])
        self.assertEqual(normalized["level_counts"], {"最低边界品位": 2, "工业品位": 1})

    def test_relative_enrichment_uses_each_elements_own_background(self):
        self.assertAlmostEqual(relative_enrichment_ratio(6000.0, 5000.0), 1.2)
        self.assertAlmostEqual(relative_enrichment_ratio(100.0, 50.0), 2.0)
        self.assertGreater(
            relative_enrichment_ratio(100.0, 50.0),
            relative_enrichment_ratio(6000.0, 5000.0),
        )

    def test_legacy_fourth_relative_cutoff_is_not_an_active_v2_band(self):
        profile = build_threshold_profile(
            "Zn",
            pd.Series([100.0] * 40),
            background_mean=100.0,
            statistical_threshold=300.0,
            relative_ratio_cutoffs=[1.25, 1.5, 2.0, 4.0],
        )
        self.assertEqual(profile["relative_ratio_cutoffs"], [1.25, 1.5, 2.0])
        self.assertNotIn("relative_band_cut_4", profile)

    def test_boundary_and_industrial_grade_override_statistical_bands(self):
        values = pd.Series([100.0] * 40 + [180.0, 400.0, 2500.0, 6000.0])
        profile = build_threshold_profile(
            "Cu",
            values,
            background_mean=100.0,
            statistical_threshold=150.0,
            industrial_grades_ppm={"Cu": 5000.0},
        )
        self.assertEqual(profile["boundary_grade_ppm"], BOUNDARY_GRADES_PPM["Cu"])
        self.assertEqual(classify_mining_level(400.0, profile)["mining_level"], "统计异常中带")
        self.assertEqual(classify_mining_level(2500.0, profile)["mining_level"], "最低边界品位")
        self.assertEqual(classify_mining_level(6000.0, profile)["mining_level"], "工业品位")

    def test_v2_levels_use_professional_then_statistical_then_relative_priority(self):
        profile = build_threshold_profile(
            "Cu",
            pd.Series([100.0] * 40),
            background_mean=100.0,
            statistical_threshold=300.0,
            boundary_grades_ppm={"Cu": 2000.0},
            industrial_grades_ppm={"Cu": 5000.0},
        )
        cases = {
            100.0: "背景范围",
            130.0: "轻微相对富集",
            170.0: "相对富集",
            220.0: "强相对富集",
            300.0: "统计异常外带",
            600.0: "统计异常中带",
            1200.0: "统计异常内带",
            2500.0: "最低边界品位",
            6000.0: "工业品位",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                result = classify_mining_level(value, profile)
                self.assertEqual(result["mining_level"], expected)
                self.assertEqual(result["display_color_hex"], MINING_LEVEL_COLORS[expected])

    def test_public_map_uses_exactly_six_distinct_display_classes(self):
        self.assertEqual(len(MINING_DISPLAY_LEVEL_COLORS), 6)
        self.assertEqual(len(set(MINING_DISPLAY_LEVEL_COLORS.values())), 6)
        profile = build_threshold_profile(
            "Cu",
            pd.Series([100.0] * 40),
            background_mean=100.0,
            statistical_threshold=300.0,
            industrial_grades_ppm={"Cu": 5000.0},
        )
        self.assertEqual(classify_mining_level(130.0, profile)["display_level"], "相对富集")
        self.assertEqual(classify_mining_level(2500.0, profile)["display_level"], "最低边界品位")
        self.assertEqual(classify_mining_level(6000.0, profile)["display_level"], "工业品位")

    def test_statistical_threshold_is_inclusive_and_matches_display_level(self):
        profile = build_threshold_profile(
            "Zn",
            pd.Series([100.0] * 40),
            background_mean=100.0,
            statistical_threshold=200.0,
            boundary_grades_ppm={"Zn": 5000.0},
        )
        result = classify_mining_level(200.0, profile)
        self.assertEqual(result["mining_level"], "统计异常外带")
        self.assertTrue(result["meets_statistical_threshold"])

    def test_variation_interval_selection_includes_value_equal_to_threshold(self):
        profile = build_threshold_profile(
            "Zn",
            pd.Series([100.0] * 40),
            background_mean=100.0,
            statistical_threshold=200.0,
            boundary_grades_ppm={"Zn": 5000.0},
        )
        statistics = pd.DataFrame([{**profile, "element": "Zn", "threshold_source": "test"}])
        assays = pd.DataFrame(
            [
                {
                    "assay_id": 1,
                    "hole_id": "H1",
                    "from_depth": 0.0,
                    "to_depth": 1.0,
                    "mid_depth": 0.5,
                    "Zn": 200.0,
                    "x_approx": 1.0,
                    "y_approx": 2.0,
                    "z_approx": 99.5,
                    "coordinate_quality": "approximate_vertical",
                    "section_match_status": "unique_match",
                    "section_geobody_key": "G1",
                    "stratum_code": "",
                    "lithology": "",
                    "weathering": "",
                    "engineering_grade": "",
                    "geobody_property_status": "exact",
                    "data_quality_flags": "",
                }
            ]
        )
        result = build_anomaly_intervals(assays, statistics)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["anomaly_level"], "统计异常外带")
        self.assertEqual(result.iloc[0]["mining_level"], "统计异常外带")

    def test_confirmed_industrial_grade_emits_industrial_colour(self):
        profile = build_threshold_profile(
            "Cu",
            pd.Series([100.0] * 40),
            background_mean=100.0,
            statistical_threshold=300.0,
        )
        result = classify_mining_level(1_000_000.0, profile)
        self.assertEqual(profile["industrial_grade_ppm"], 4000.0)
        self.assertEqual(result["mining_level"], "工业品位")
        self.assertTrue(result["meets_industrial_grade"])

    def test_boundary_grade_can_override_a_higher_statistical_threshold(self):
        profile = build_threshold_profile(
            "Rb",
            pd.Series([224.0] * 40),
            background_mean=224.0,
            statistical_threshold=2179.2,
        )
        result = classify_mining_level(914.0, profile)
        self.assertEqual(result["mining_level"], "最低边界品位")
        self.assertFalse(result["meets_statistical_threshold"])
        self.assertTrue(result["meets_boundary_grade"])

    def test_rubidium_boundary_grade_is_the_confirmed_914_ppm(self):
        profile = build_threshold_profile(
            "Rb",
            pd.Series([200.0] * 40 + [300.0, 365.0, 500.0]),
            background_mean=200.0,
            statistical_threshold=280.0,
        )
        result = classify_mining_level(914.0, profile)
        self.assertEqual(profile["boundary_grade_ppm"], 914.0)
        self.assertEqual(result["mining_level"], "最低边界品位")
        self.assertTrue(result["meets_boundary_grade"])

    def test_correlation_quality_marks_focus_metals_without_excluding_other_elements(self):
        assays = pd.DataFrame({"Cu": [1.0, 2.0, 3.0], "Zn": [2.0, 3.0, 4.0], "Ce": [4.0, 5.0, 6.0]})
        config = CorrelationConfig(minimum_positive_count=3, minimum_pair_common_count=3)
        quality = build_correlation_quality(assays, ["Cu", "Zn", "Ce"], config)
        flags = quality.set_index("element")["is_focus_metal"].to_dict()
        self.assertTrue(flags["Cu"])
        self.assertTrue(flags["Zn"])
        self.assertFalse(flags["Ce"])
        self.assertTrue(quality["candidate_for_pairwise_correlation"].all())

    def test_full_field_preview_reports_professional_levels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "voxels.csv"
            ply_path = root / "preview.ply"
            pd.DataFrame(
                {
                    "x": [0.0, 1.0, 2.0, 3.0, 4.0],
                    "y": [0.0, 0.0, 0.0, 0.0, 0.0],
                    "z": [0.0, 0.0, 0.0, 0.0, 0.0],
                    "Cu": [100.0, 400.0, 2500.0, 6000.0, None],
                    "Cu_support_T": [0.0, 0.25, 0.75, 1.0, 0.5],
                    "confidence_level": ["high", "low", "high", "high", "no_data"],
                }
            ).to_csv(csv_path, index=False)
            profile = build_threshold_profile(
                "Cu",
                pd.Series([100.0] * 40 + [400.0, 2500.0, 6000.0]),
                background_mean=100.0,
                statistical_threshold=150.0,
                industrial_grades_ppm={"Cu": 5000.0},
            )
            info = generate_element_ply(
                csv_path,
                ply_path,
                "Cu",
                max_points=0,
                display_mode="concentration",
                threshold_profile=profile,
            )
            self.assertEqual(info["display_mode"], "concentration")
            self.assertEqual(info["level_counts"]["强正异常"], 1)
            self.assertEqual(info["confidence_counts"]["low"], 1)
            self.assertEqual(info["level_counts"]["最低边界品位"], 1)
            self.assertEqual(info["level_counts"]["工业品位"], 1)
            self.assertEqual(len(info["legend"]), 6)
            self.assertNotIn("无可靠估计", info["level_counts"])
            self.assertEqual(info["ply_points"], 4)
            self.assertTrue(ply_path.exists())
            support_info = generate_element_ply(
                csv_path,
                root / "support.ply",
                "Cu",
                max_points=0,
                display_mode="support_T",
                threshold_profile=profile,
            )
            confidence_info = generate_element_ply(
                csv_path,
                root / "confidence.ply",
                "Cu",
                max_points=0,
                display_mode="confidence",
                threshold_profile=profile,
            )
            self.assertEqual(support_info["scalar_column"], "Cu_support_T")
            self.assertEqual(support_info["ply_points"], 5)
            self.assertEqual(confidence_info["level_counts"]["low"], 1)

    def test_preview_metadata_remains_strict_json_while_industrial_grade_is_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "voxels.csv"
            pd.DataFrame({"x": [0], "y": [0], "z": [0], "Rb": [365], "confidence_level": ["high"]}).to_csv(csv_path, index=False)
            profile = build_threshold_profile(
                "Rb", pd.Series([200.0] * 40 + [365.0]), background_mean=200.0, statistical_threshold=280.0
            )
            info = generate_element_ply(
                csv_path, root / "preview.ply", "Rb", max_points=0,
                display_mode="concentration", threshold_profile=profile,
            )
            json.dumps(info, allow_nan=False)
            self.assertIsNone(info["threshold_profile"]["industrial_grade_ppm"])

    def test_statistical_anomaly_preview_clips_voxels_at_method_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "voxels.csv"
            pd.DataFrame({
                "x": [0, 1, 2, 3],
                "y": [0, 0, 0, 0],
                "z": [0, 0, 0, 0],
                "Cu": [100.0, 200.0, 400.0, 800.0],
                "confidence_level": ["high", "high", "medium", "fault_mask"],
            }).to_csv(csv_path, index=False)
            profile = build_threshold_profile(
                "Cu", pd.Series([100.0] * 40 + [200.0, 400.0, 800.0]),
                background_mean=100.0, statistical_threshold=300.0,
            )
            info = generate_element_ply(
                csv_path, root / "anomaly.ply", "Cu", max_points=0,
                display_mode="statistical_anomaly", threshold_profile=profile,
            )
            self.assertEqual(info["display_mode"], "statistical_anomaly")
            self.assertEqual(info["ply_points"], 1)
            self.assertEqual(info["level_counts"], {"明显正异常": 1})

    def test_expert_formula_filters_interpolation_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "assays.csv"
            target = root / "filtered.csv"
            pd.DataFrame({"hole_id": ["H1", "H2", "H3"], "Fe": [10.0, 12.0, 1000.0]}).to_csv(source, index=False)
            profile = {
                "background_method": "expert_2sigma",
                "outlier_cutoff": 100.0,
                "outlier_cutoff_scale": "normal",
            }
            result = prepare_interpolation_assay_csv(source, target, "Fe", profile, {})
            self.assertEqual(result["removed_sample_count"], 1)
            self.assertEqual(len(pd.read_csv(target)), 2)

    def test_expert_profile_preserves_outlier_cutoff(self):
        from app.services.geochem_service import compute_threshold_profiles
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "assays.csv"
            pd.DataFrame({"Fe": [10.0] * 40 + [1000.0]}).to_csv(source, index=False)
            profile = compute_threshold_profiles(
                source, ["Fe"], rule_config={"background_method": "expert_2sigma", "minimum_positive_count": 30}
            )["Fe"]
            self.assertIsNotNone(profile["outlier_cutoff"])
            self.assertEqual(profile["outlier_cutoff_scale"], "normal")


if __name__ == "__main__":
    unittest.main()
