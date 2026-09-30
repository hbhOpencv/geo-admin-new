from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.models import (  # noqa: E402
    Base,
    GeochemBackgroundProfile,
    GeochemCandidateClue,
    GeochemElementRule,
    GeochemMiningJob,
    GeochemRuleSet,
    GeochemWorkflowRun,
    GeologicalModel,
)
from app.services.geochem_workflow_service import (  # noqa: E402
    _available_reconstruction_elements,
    create_workflow,
    run_correlation,
    sync_workflow,
)


class GeochemWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.temp_dir.name) / 'test.db'}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.model = GeologicalModel(name="workflow-test")
        self.db.add(self.model)
        self.db.flush()
        self.rules = GeochemRuleSet(
            id="rules-1",
            model_id=self.model.id,
            name="test rules",
            version=1,
            status="draft",
            background_method="log_mad",
            background_scope="model",
            relative_ratio_cutoffs_json="[1.25, 1.5, 2.0]",
            support_probability_cutoff=0.5,
            minimum_positive_count=30,
            merge_gap_m=0.5,
            minimum_segment_length_m=0.0,
        )
        self.rules.element_rules = [
            GeochemElementRule(
                element="Cu",
                role="metal",
                boundary_grade_ppm=2000,
                boundary_status="confirmed",
            ),
            GeochemElementRule(element="As", role="indicator"),
        ]
        self.db.add(self.rules)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _workflow(self) -> GeochemWorkflowRun:
        with patch(
            "app.services.geochem_workflow_service._dataset_hash",
            return_value="frozen-data-hash",
        ):
            return create_workflow(
                self.db,
                model_id=self.model.id,
                rule_set_id=self.rules.id,
                created_by=None,
                client_request_id="once",
            )

    def test_create_is_idempotent_and_freezes_provenance(self):
        first = self._workflow()
        second = self._workflow()
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.dataset_hash, "frozen-data-hash")
        self.assertEqual(first.rule_set_id, self.rules.id)
        self.assertIn("不等同于发现矿体", first.limitations_json)

    def test_successful_variation_persists_every_segment_and_ranks_metal_first(self):
        workflow = self._workflow()
        output = Path(self.temp_dir.name) / "variation"
        variation_dir = output / "element_variation"
        variation_dir.mkdir(parents=True)
        pd.DataFrame(
            [
                {
                    "hole_id": "H1",
                    "element": "As",
                    "segment_from_depth": 10.0,
                    "segment_to_depth": 12.0,
                    "segment_length_m": 2.0,
                    "max_value_to_background_ratio": 20.0,
                    "highest_mining_level": "统计异常内带",
                },
                {
                    "hole_id": "H2",
                    "element": "Cu",
                    "segment_from_depth": 20.0,
                    "segment_to_depth": 21.0,
                    "segment_length_m": 1.0,
                    "max_value_to_background_ratio": 3.0,
                    "highest_mining_level": "最低边界品位",
                    "boundary_grade_ppm": 2000.0,
                    "meets_boundary_grade": True,
                },
            ]
        ).to_csv(variation_dir / "merged_anomaly_segments.csv", index=False)
        job = GeochemMiningJob(
            id="variation-1",
            model_id=self.model.id,
            algorithm_mode="variation",
            status="success",
            progress=100,
            stage="done",
            out_dir=str(output),
            rule_set_id=self.rules.id,
            workflow_id=workflow.id,
            dataset_hash=workflow.dataset_hash,
        )
        self.db.add(job)
        workflow.variation_job_id = job.id
        self.db.commit()

        sync_workflow(self.db, workflow)
        clues = (
            self.db.query(GeochemCandidateClue)
            .filter(GeochemCandidateClue.workflow_id == workflow.id)
            .order_by(GeochemCandidateClue.rank.asc())
            .all()
        )
        self.assertEqual(len(clues), 2)
        self.assertEqual(clues[0].main_element, "Cu")
        self.assertEqual(clues[1].main_element, "As")
        self.assertEqual(workflow.stage, "clue_selection")

    def test_correlation_is_bound_to_selected_clue_and_full_data_scope(self):
        workflow = self._workflow()
        variation = GeochemMiningJob(
            id="variation-1",
            model_id=self.model.id,
            algorithm_mode="variation",
            status="success",
            progress=100,
            stage="done",
            out_dir=str(Path(self.temp_dir.name) / "missing"),
            rule_set_id=self.rules.id,
            workflow_id=workflow.id,
            dataset_hash=workflow.dataset_hash,
        )
        clue = GeochemCandidateClue(
            id="clue-1",
            workflow_id=workflow.id,
            source_job_id=variation.id,
            source_key="H1|Cu|1|2",
            kind="variation",
            main_element="Cu",
            members_json='["Cu"]',
            hole_ids_json='["H1"]',
            status="candidate",
            rank=1,
        )
        self.db.add_all([variation, clue])
        workflow.variation_job_id = variation.id
        self.db.commit()
        captured = {}

        def fake_start(_db, _model_id, params):
            captured.update(params)
            self.db.add(
                GeochemMiningJob(
                    id="correlation-1",
                    model_id=self.model.id,
                    algorithm_mode="correlation",
                    status="queued",
                    progress=0,
                    stage="queued",
                    out_dir=str(Path(self.temp_dir.name) / "correlation"),
                    rule_set_id=self.rules.id,
                    source_variation_job_id=variation.id,
                    workflow_id=workflow.id,
                    dataset_hash=workflow.dataset_hash,
                    params_json=json.dumps(params),
                )
            )
            self.db.commit()
            return "correlation-1"

        with (
            patch(
                "app.services.geochem_workflow_service._dataset_hash",
                return_value=workflow.dataset_hash,
            ),
            patch(
                "app.services.geochem_workflow_service.start_job",
                side_effect=fake_start,
            ),
        ):
            job_id = run_correlation(self.db, workflow, clue.id)

        self.assertEqual(job_id, "correlation-1")
        self.assertEqual(captured["source_variation_job_id"], variation.id)
        self.assertEqual(captured["correlation"]["focus_elements"][0], "Cu")
        self.assertIn("Au", captured["correlation"]["focus_elements"])
        self.assertIn("Zn", captured["correlation"]["focus_elements"])
        self.assertEqual(captured["correlation"]["target_elements"], ["As", "Cu"])
        self.assertIsNone(captured["filters"]["selected_elements"])
        self.assertIsNone(captured["filters"]["hole_ids"])

    def test_reconstruction_elements_include_every_element_with_valid_background_data(self):
        workflow = self._workflow()
        variation = GeochemMiningJob(
            id="variation-all-elements",
            model_id=self.model.id,
            algorithm_mode="variation",
            status="success",
            progress=100,
            stage="done",
            out_dir=str(Path(self.temp_dir.name) / "variation-all-elements"),
            rule_set_id=self.rules.id,
            workflow_id=workflow.id,
            dataset_hash=workflow.dataset_hash,
        )
        self.db.add(variation)
        self.db.flush()
        self.db.add_all([
            GeochemBackgroundProfile(
                model_id=self.model.id,
                rule_set_id=self.rules.id,
                variation_job_id=variation.id,
                element="Cu",
                method="log_mad",
                scope_signature='{"scope":"model"}',
                background_value=10.0,
                statistical_threshold=20.0,
            ),
            GeochemBackgroundProfile(
                model_id=self.model.id,
                rule_set_id=self.rules.id,
                variation_job_id=variation.id,
                element="As",
                method="log_mad",
                scope_signature='{"scope":"model"}',
                background_value=5.0,
                statistical_threshold=12.0,
            ),
        ])
        workflow.variation_job_id = variation.id
        self.db.commit()

        self.assertEqual(
            _available_reconstruction_elements(self.db, workflow),
            ["As", "Cu"],
        )


if __name__ == "__main__":
    unittest.main()
