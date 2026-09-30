import json
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.algorithms.deep_mlp import _load_source_color_table
from app.models import Base, GeologicalModel, ReconstructJob
from app.services.deep_service import _resolve_source_b, available_source_b_jobs


class DeepSourceBindingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, future=True)()
        self.session.add(GeologicalModel(id=1, name="test-model"))
        self.session.commit()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def add_b_job(self, job_id: str, finished_at: datetime) -> ReconstructJob:
        out_dir = self.root / job_id / "outputs"
        out_dir.mkdir(parents=True)
        (out_dir / "voxels_ikrig.csv").write_text("X,Y,Z,p_0\n", encoding="utf-8")
        (out_dir / "voxels_final.csv").write_text("X,Y,Z,pred_code\n", encoding="utf-8")
        (out_dir / "lithology_color_map.json").write_text(
            json.dumps([{"岩性编码": 0, "岩性名称": "花岗岩", "R": 31, "G": 119, "B": 180, "A": 255}], ensure_ascii=False),
            encoding="utf-8",
        )
        job = ReconstructJob(
            id=job_id,
            model_id=1,
            status="success",
            params_json=json.dumps({"method": "B", "voxel_size": 15}),
            out_dir=str(out_dir),
            created_at=finished_at - timedelta(minutes=1),
            finished_at=finished_at,
        )
        self.session.add(job)
        self.session.commit()
        return job

    def test_selected_b_job_is_resolved_exactly_instead_of_latest(self):
        old = self.add_b_job("old-b", datetime(2026, 7, 1, 10, 0, 0))
        self.add_b_job("new-b", datetime(2026, 7, 2, 10, 0, 0))

        _, _, color_map, source_id = _resolve_source_b(self.session, 1, old.id)

        self.assertEqual(source_id, "old-b")
        self.assertEqual(color_map.parent, Path(old.out_dir))
        self.assertEqual([item["id"] for item in available_source_b_jobs(self.session, 1)], ["new-b", "old-b"])

    def test_source_is_required_and_color_values_are_inherited_exactly(self):
        job = self.add_b_job("chosen-b", datetime(2026, 7, 1, 10, 0, 0))
        with self.assertRaisesRegex(ValueError, "请选择"):
            _resolve_source_b(self.session, 1, "")

        colors, rows = _load_source_color_table(Path(job.out_dir) / "lithology_color_map.json")
        self.assertEqual(colors[0], (31, 119, 180))
        self.assertEqual(rows[0]["岩性名称"], "花岗岩")


if __name__ == "__main__":
    unittest.main()
