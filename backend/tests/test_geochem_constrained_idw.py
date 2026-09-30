from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd


ALGORITHMS = Path(__file__).resolve().parents[1] / "app" / "algorithms"
if str(ALGORITHMS) not in sys.path:
    sys.path.insert(0, str(ALGORITHMS))

from geochem_geology_constrained import (  # noqa: E402
    _build_hole_trees,
    _query_hole_balanced_neighbours,
    apply_lithology_groups,
    assign_sample_lithology,
    interpolate_constrained_csv,
    load_lithology_group_mapping,
)


class GeochemConstrainedIdwTests(unittest.TestCase):
    def test_vectorized_neighbourhood_caps_each_hole(self):
        points = np.array([[0, 0, z] for z in range(5)] + [[10, 0, z] for z in range(5)], dtype=float)
        holes = np.array([0] * 5 + [1] * 5, dtype=np.int32)
        source = {
            "values": np.arange(10, dtype=float),
            "group_codes": np.zeros(10, dtype=np.int32),
            "hole_trees": _build_hole_trees(points, holes),
        }
        distances, indexes = _query_hole_balanced_neighbours(
            np.array([[5.0, 0.0, 2.0]]), source, 0, nearest=4,
            max_samples_per_hole=2, compatible_lithology_weight=0.7,
        )
        selected_holes = holes[indexes[0]]
        self.assertEqual(distances.shape, (1, 4))
        self.assertEqual(int(np.sum(selected_holes == 0)), 2)
        self.assertEqual(int(np.sum(selected_holes == 1)), 2)

    def _write_group_inputs(self, root, groups):
        lithology_map = root / "lithology_map.csv"
        pd.DataFrame(
            [
                {"岩性编码": 1, "岩性名称": "二长花岗岩"},
                {"岩性编码": 2, "岩性名称": "黑云母二长花岗岩"},
                {"岩性编码": 3, "岩性名称": "乱码岩性"},
            ]
        ).to_csv(lithology_map, index=False, encoding="utf-8-sig")
        config = root / "groups.json"
        config.write_text(
            __import__("json").dumps(
                {
                    "schema_version": 1,
                    "mapping_version": "test-v1",
                    "status": "confirmed",
                    "source_constraint_job_id": "B-NEW",
                    "source_lithology_map_sha256": __import__("hashlib").sha256(
                        lithology_map.read_bytes()
                    ).hexdigest().upper(),
                    "groups": groups,
                    "unmapped_policy": "keep_original",
                    "fault_policy": "never_merge",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return config, lithology_map

    def test_confirmed_group_shares_samples_and_preserves_original_codes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config, lithology_map = self._write_group_inputs(
                root,
                [{
                    "group_id": -1001,
                    "group_name": "花岗岩归并组",
                    "member_names": ["二长花岗岩", "黑云母二长花岗岩"],
                    "allow_cross_member_interpolation": True,
                }],
            )
            mapping, group_names, metadata = load_lithology_group_mapping(
                config, lithology_map, "B-NEW", fault_code=34
            )
            samples = pd.DataFrame([
                {"hole_id": "H1", "x": 0.0, "y": 0.0, "z": 0.0, "Cu": 100.0, "lithology_code": 1},
                {"hole_id": "H2", "x": 10.0, "y": 0.0, "z": 0.0, "Cu": 10.0, "lithology_code": 2},
            ])
            samples = apply_lithology_groups(samples, mapping, {1: "二长花岗岩", 2: "黑云母二长花岗岩"}, group_names)
            voxel_csv = root / "voxels.csv"
            pd.DataFrame([
                {"X": 1.0, "Y": 0.0, "Z": 0.0, "pred_code_final": 1, "dist_fault": 100.0},
                {"X": 9.0, "Y": 0.0, "Z": 0.0, "pred_code_final": 2, "dist_fault": 100.0},
                {"X": 5.0, "Y": 0.0, "Z": 0.0, "pred_code_final": 2, "dist_fault": 5.0},
            ]).to_csv(voxel_csv, index=False)
            output = root / "result.csv"
            stats = interpolate_constrained_csv(
                voxel_csv, samples, "Cu", output,
                lithology_names={1: "二长花岗岩", 2: "黑云母二长花岗岩"},
                lithology_group_mapping=mapping,
                group_names=group_names,
                mapping_metadata=metadata,
                nearest=2, search_radius=100.0, fault_thickness=30.0,
            )
            result = pd.read_csv(output)
            self.assertEqual(result["original_lithology_code"].tolist(), [1, 2, 2])
            self.assertEqual(result.loc[:1, "geochem_group_code"].tolist(), [-1001, -1001])
            self.assertTrue(result.loc[:1, "Cu"].notna().all())
            self.assertEqual(result.loc[2, "confidence_level"], "fault_mask")
            self.assertTrue(pd.isna(result.loc[2, "Cu"]))
            self.assertEqual(stats["merged_group_voxels"], 2)
            self.assertEqual(stats["fault_masked"], 1)
            self.assertEqual(stats["mapping_version"], "test-v1")
            self.assertEqual(stats["sample_lithology_counts"], {"1": 1, "2": 1})
            self.assertEqual(stats["sample_group_counts"], {"-1001": 2})
            self.assertEqual(
                stats["rows"],
                stats["fault_masked"]
                + stats["merged_group_voxels"]
                + stats["raw_code_voxels"]
                + stats["unmapped_lithology_voxels"],
            )

    def test_unmapped_code_stays_independent_and_disabled_mapping_is_equivalent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            samples = pd.DataFrame([
                {"hole_id": "H1", "x": 0.0, "y": 0.0, "z": 0.0, "Cu": 20.0, "lithology_code": 1},
                {"hole_id": "H3", "x": 20.0, "y": 0.0, "z": 0.0, "Cu": 200.0, "lithology_code": 3},
            ])
            voxel_csv = root / "voxels.csv"
            pd.DataFrame([
                {"X": 19.0, "Y": 0.0, "Z": 0.0, "pred_code_final": 3, "dist_fault": 100.0}
            ]).to_csv(voxel_csv, index=False)
            plain = root / "plain.csv"
            explicit = root / "explicit.csv"
            interpolate_constrained_csv(voxel_csv, samples, "Cu", plain, nearest=2, search_radius=100.0)
            grouped_samples = apply_lithology_groups(samples, {1: -1001}, {1: "A", 3: "乱码岩性"}, {-1001: "A组"})
            interpolate_constrained_csv(
                voxel_csv, grouped_samples, "Cu", explicit,
                lithology_group_mapping={1: -1001}, group_names={-1001: "A组"},
                nearest=2, search_radius=100.0,
            )
            self.assertTrue(np.isfinite(pd.read_csv(plain).loc[0, "Cu"]))
            result = pd.read_csv(explicit)
            self.assertTrue(np.isfinite(result.loc[0, "Cu"]))
            self.assertEqual(int(result.loc[0, "geochem_group_code"]), 3)

    def test_group_config_rejects_duplicate_member_and_fault_code(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            duplicate, lithology_map = self._write_group_inputs(
                root,
                [
                    {"group_id": -1001, "group_name": "G1", "member_names": ["二长花岗岩"]},
                    {"group_id": -1002, "group_name": "G2", "member_names": ["二长花岗岩"]},
                ],
            )
            with self.assertRaisesRegex(ValueError, "多个归并组"):
                load_lithology_group_mapping(duplicate, lithology_map, "B-NEW", fault_code=34)

            fault_config, lithology_map = self._write_group_inputs(
                root,
                [{"group_id": -1001, "group_name": "G", "member_names": ["二长花岗岩"]}],
            )
            data = __import__("json").loads(fault_config.read_text(encoding="utf-8"))
            data["groups"][0]["member_names"] = ["断层"]
            frame = pd.read_csv(lithology_map, encoding="utf-8-sig")
            frame.loc[len(frame)] = [34, "断层"]
            frame.to_csv(lithology_map, index=False, encoding="utf-8-sig")
            data["source_lithology_map_sha256"] = __import__("hashlib").sha256(lithology_map.read_bytes()).hexdigest().upper()
            fault_config.write_text(__import__("json").dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "断层编码"):
                load_lithology_group_mapping(fault_config, lithology_map, "B-NEW", fault_code=34)

    def test_group_config_rejects_unconfirmed_wrong_job_and_wrong_map_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config, lithology_map = self._write_group_inputs(
                root,
                [{"group_id": -1001, "group_name": "G", "member_names": ["二长花岗岩"]}],
            )
            data = __import__("json").loads(config.read_text(encoding="utf-8"))
            data["status"] = "draft"
            config.write_text(__import__("json").dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "未确认"):
                load_lithology_group_mapping(config, lithology_map, "B-NEW", fault_code=34)

            data["status"] = "confirmed"
            config.write_text(__import__("json").dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "任务不匹配"):
                load_lithology_group_mapping(config, lithology_map, "B-OTHER", fault_code=34)

            data["source_lithology_map_sha256"] = "0" * 64
            config.write_text(__import__("json").dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "哈希不匹配"):
                load_lithology_group_mapping(config, lithology_map, "B-NEW", fault_code=34)

    def test_assign_sample_lithology_and_prevent_cross_lithology(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            layers = pd.DataFrame(
                [
                    {"钻孔ID": "H1", "岩性段顶深Z(m)": 100.0, "岩性段底深Z(m)": 50.0, "岩性段中点Z(m)": 75.0, "岩性编码": 1},
                    {"钻孔ID": "H2", "岩性段顶深Z(m)": 100.0, "岩性段底深Z(m)": 50.0, "岩性段中点Z(m)": 75.0, "岩性编码": 2},
                ]
            )
            layers_csv = tmp_path / "layers.csv"
            layers.to_csv(layers_csv, index=False, encoding="utf-8-sig")
            samples = pd.DataFrame(
                [
                    {"hole_id": "H1", "from_depth": 0.0, "to_depth": 10.0, "x": 0.0, "y": 0.0, "z": 75.0, "Cu": 100.0},
                    {"hole_id": "H2", "from_depth": 0.0, "to_depth": 10.0, "x": 10.0, "y": 0.0, "z": 75.0, "Cu": 10.0},
                ]
            )
            samples = assign_sample_lithology(samples, layers_csv)
            self.assertEqual(samples["lithology_code"].tolist(), [1.0, 2.0])

            voxels = pd.DataFrame(
                [
                    {"X": 1.0, "Y": 0.0, "Z": 75.0, "pred_code_final": 1, "dist_fault": 100.0},
                    {"X": 9.0, "Y": 0.0, "Z": 75.0, "pred_code_final": 2, "dist_fault": 100.0},
                    {"X": 5.0, "Y": 0.0, "Z": 75.0, "pred_code_final": 1, "dist_fault": 5.0},
                ]
            )
            voxel_csv = tmp_path / "voxels.csv"
            output_csv = tmp_path / "result.csv"
            voxels.to_csv(voxel_csv, index=False)
            stats = interpolate_constrained_csv(
                voxel_csv,
                samples,
                "Cu",
                output_csv,
                lithology_names={1: "A", 2: "B"},
                nearest=2,
                power=2.0,
                search_radius=100.0,
                fault_code=99,
                fault_thickness=30.0,
                chunk_size=2,
            )
            result = pd.read_csv(output_csv)
            self.assertTrue(np.isfinite(result.loc[0, "Cu"]))
            self.assertTrue(np.isfinite(result.loc[1, "Cu"]))
            self.assertGreater(result.loc[0, "Cu"], result.loc[1, "Cu"])
            self.assertTrue(pd.isna(result.loc[2, "Cu"]))
            self.assertEqual(result.loc[2, "confidence_level"], "fault_mask")
            self.assertEqual(stats["fault_masked"], 1)

    def test_selected_element_is_independent_of_other_columns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            samples = pd.DataFrame(
                [
                    {"hole_id": "H1", "x": 0.0, "y": 0.0, "z": 0.0, "Cu": 20.0, "Zn": 999.0, "lithology_code": 1.0},
                    {"hole_id": "H2", "x": 10.0, "y": 0.0, "z": 0.0, "Cu": 40.0, "Zn": 1.0, "lithology_code": 1.0},
                ]
            )
            voxels = pd.DataFrame([{"X": 5.0, "Y": 0.0, "Z": 0.0, "pred_code_final": 1, "dist_fault": 100.0}])
            voxel_csv = tmp_path / "voxels.csv"
            voxels.to_csv(voxel_csv, index=False)
            values = []
            for index, zinc in enumerate(([999.0, 1.0], [0.0, 100000.0])):
                changed = samples.copy()
                changed["Zn"] = zinc
                output = tmp_path / f"result-{index}.csv"
                interpolate_constrained_csv(voxel_csv, changed, "Cu", output, nearest=2, search_radius=100.0)
                values.append(pd.read_csv(output).loc[0, "Cu"])
            self.assertTrue(np.isclose(values[0], values[1]))


if __name__ == "__main__":
    unittest.main()
