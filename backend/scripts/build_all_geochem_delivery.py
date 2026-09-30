"""Build one current geochemical reconstruction result for every assay element.

Run from the delivery package's ``backend`` directory. Existing current-version
results with a readable scene manifest and PLY are reused. Newly generated
voxel CSV files are removed after a successful result because they are
intermediate data and each single-element PLY remains directly viewable.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.models import GeochemReconstructJob, get_session
from app.services.geochem_service import available_elements, start_geochem_job


ALGORITHM_VERSION = "geochem-anisotropic-idw-v1-trend-residual-full-field"
ALGORITHM_PROFILE = "algorithm_b_domain_grouped_anisotropic_idw_full_field"
CONSTRAINT_JOB_ID = "74909dae26924047ae7249d32d88c78f"
RULE_SET_ID = "105bc9c8999042999eac62ae0ff63c08"
VARIATION_JOB_ID = "6315b3449f1e4dd39f34b335a85becd9"


def job_element(job: GeochemReconstructJob) -> str:
    try:
        params = json.loads(job.params_json or "{}")
    except json.JSONDecodeError:
        return ""
    return str(params.get("preview_element") or "").strip()


def output_is_viewable(job: GeochemReconstructJob, element: str) -> bool:
    output_dir = Path(job.out_dir or "")
    return (
        (output_dir / "scene_manifest.json").is_file()
        and (output_dir / f"geochem_{element}_full_field.ply").is_file()
    )


def remove_intermediate_voxel_csv(job: GeochemReconstructJob) -> None:
    output_dir = Path(job.out_dir or "").resolve()
    storage_root = (Path.cwd() / "storage" / "geochem").resolve()
    if not output_dir.is_relative_to(storage_root):
        raise RuntimeError(f"Refusing to clean outside delivery storage: {output_dir}")
    for name in ("geochemical_voxels_15m.csv", "geochemical_voxels_20m.csv"):
        path = output_dir / name
        if path.is_file():
            path.unlink()


def start_element(element: str) -> str:
    db = get_session()
    try:
        return start_geochem_job(
            db=db,
            model_id=1,
            params={
                "elements": [element],
                "preview_element": element,
                "preview_mode": "concentration",
                "method": "anisotropic_idw_visual_v1",
                "nearest": 48,
                "power": 1.6,
                "chunk_size": 50000,
                "max_points_ply": 600000,
                "major_radius": 2400.0,
                "intermediate_radius": 1200.0,
                "minor_radius": 600.0,
                "azimuth": 80.1,
                "plunge": 7.7,
                "max_samples_per_hole": 8,
                "preferred_distinct_holes": 6,
                "compatible_lithology_weight": 0.7,
                "residual_full_radius": 0.65,
                "residual_fade_radius": 1.5,
                "composite_interval": 15.0,
                "transform": "log",
                "constraint_job_id": CONSTRAINT_JOB_ID,
                "lithology_grouping_enabled": True,
                "rule_set_id": RULE_SET_ID,
                "source_variation_job_id": VARIATION_JOB_ID,
                "algorithm_version": ALGORITHM_VERSION,
                "algorithm_profile": ALGORITHM_PROFILE,
            },
        )
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--keep-voxel-csv", action="store_true")
    args = parser.parse_args()
    if args.concurrency < 1 or args.concurrency > 3:
        parser.error("--concurrency must be between 1 and 3")

    db = get_session()
    try:
        elements = available_elements(db, 1)
        current_jobs = (
            db.query(GeochemReconstructJob)
            .filter(
                GeochemReconstructJob.model_id == 1,
                GeochemReconstructJob.status == "success",
                GeochemReconstructJob.algorithm_version == ALGORITHM_VERSION,
            )
            .order_by(GeochemReconstructJob.created_at.desc())
            .all()
        )
        reusable = {
            element
            for job in current_jobs
            if (element := job_element(job)) and output_is_viewable(job, element)
        }
    finally:
        db.close()

    pending = [element for element in elements if element not in reusable]
    print(f"elements={len(elements)} reusable={len(reusable)} pending={len(pending)}", flush=True)
    active: dict[str, str] = {}
    failures: dict[str, str] = {}

    while pending or active:
        while pending and len(active) < args.concurrency:
            element = pending.pop(0)
            job_id = start_element(element)
            active[job_id] = element
            print(f"START {element} {job_id}", flush=True)

        time.sleep(5)
        db = get_session()
        try:
            for job_id, element in list(active.items()):
                job = db.get(GeochemReconstructJob, job_id)
                if job is None or job.status not in {"success", "failed"}:
                    continue
                if job.status == "success":
                    if not args.keep_voxel_csv:
                        remove_intermediate_voxel_csv(job)
                    print(f"DONE {element} {job_id}", flush=True)
                else:
                    failures[element] = job.error or "unknown error"
                    print(f"FAILED {element} {job_id}: {failures[element]}", flush=True)
                del active[job_id]
        finally:
            db.close()

    if failures:
        print(json.dumps(failures, ensure_ascii=False, indent=2), flush=True)
        return 1
    print(f"COMPLETE all {len(elements)} elements", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
