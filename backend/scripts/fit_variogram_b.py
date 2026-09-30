# -*- coding: utf-8 -*-
r"""Fit spherical indicator variogram parameters for Algorithm B.

Supports two data sources:
1) `--input_csv`: an existing CSV such as `real_plus_virtual.csv`
2) `--model_id`: export real borehole-layer samples from DB

Examples:
  cd backend
  python scripts\fit_variogram_b.py --input_csv ..\backend\storage\reconstruct\0509...\outputs\real_plus_virtual.csv

  python scripts\fit_variogram_b.py --model_id 1 --out_dir .\storage\variograms_main --real_only 1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import curve_fit


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import get_session  # noqa: E402
from app.services.reconstruct_service import export_model_sections_to_layer_csv  # noqa: E402


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Fit spherical indicator variogram parameters for Algorithm B.")
    ap.add_argument("--input_csv", default="", help="Existing sample CSV, e.g. real_plus_virtual.csv")
    ap.add_argument("--model_id", type=int, default=0, help="Export real borehole-layer CSV from DB when input_csv is not provided")
    ap.add_argument("--out_dir", default="", help="Output directory for PNG/CSV/JSON")

    ap.add_argument("--x_col", default="X坐标(m)")
    ap.add_argument("--y_col", default="Y坐标(m)")
    ap.add_argument("--z_col", default="岩性段中点Z(m)")
    ap.add_argument("--code_col", default="岩性编码")
    ap.add_argument("--src_col", default="来源")
    ap.add_argument("--real_tag", default="实测")
    ap.add_argument("--real_only", type=int, default=1, help="1=only real samples if src_col exists")

    ap.add_argument("--min_pos", type=int, default=6)
    ap.add_argument("--min_bins_with_pairs", type=int, default=4)
    ap.add_argument("--min_pairs_per_bin", type=int, default=5)

    ap.add_argument("--max_lag", type=float, default=2000.0)
    ap.add_argument("--n_bins", type=int, default=12)

    ap.add_argument("--p0_a", type=float, default=800.0)
    ap.add_argument("--p0_c0", type=float, default=0.0)
    ap.add_argument("--p0_c", type=float, default=0.2)
    ap.add_argument("--bound_a_min", type=float, default=1.0)
    ap.add_argument("--bound_c0_min", type=float, default=0.0)
    ap.add_argument("--bound_c_min", type=float, default=0.0)
    ap.add_argument("--bound_a_max", type=float, default=5000.0)
    ap.add_argument("--bound_c0_max", type=float, default=1.0)
    ap.add_argument("--bound_c_max", type=float, default=2.0)

    ap.add_argument(
        "--shared_mode",
        choices=["weighted_average", "largest_class", "median"],
        default="weighted_average",
        help="How to derive one shared parameter triplet from main lithologies",
    )
    return ap.parse_args()


def gamma_spherical(h: np.ndarray | float, a: float, c0: float, c: float) -> np.ndarray:
    h = np.asarray(h, dtype=float)
    hr = h / np.maximum(a, 1e-12)
    g = np.empty_like(hr, dtype=float)
    inside = hr <= 1.0
    g[inside] = c0 + c * (1.5 * hr[inside] - 0.5 * (hr[inside] ** 3))
    g[~inside] = c0 + c
    return g


def experimental_variogram_indicator(
    xyz: np.ndarray,
    indicator: np.ndarray,
    max_lag: float,
    n_bins: int,
) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    n = xyz.shape[0]
    if n < 2:
        return None

    iu, ju = np.triu_indices(n, k=1)
    diff = xyz[iu] - xyz[ju]
    h = np.sqrt((diff * diff).sum(axis=1))
    g = 0.5 * (indicator[iu] - indicator[ju]) ** 2

    edges = np.linspace(0.0, float(max_lag), int(n_bins) + 1)
    centers = 0.5 * (edges[:-1] + edges[1:])
    gamma = np.full(int(n_bins), np.nan, dtype=float)
    cnt = np.zeros(int(n_bins), dtype=int)

    for idx in range(int(n_bins)):
        mask = (h >= edges[idx]) & (h < edges[idx + 1])
        cnt[idx] = int(mask.sum())
        if cnt[idx] > 0:
            gamma[idx] = float(np.mean(g[mask]))

    return centers, gamma, cnt


def plot_variogram(
    centers: np.ndarray,
    gamma_exp: np.ndarray,
    cnt: np.ndarray,
    code: int,
    fit_params: Tuple[float, float, float],
    out_png: Path,
) -> None:
    valid = (cnt > 0) & np.isfinite(gamma_exp)
    if not np.any(valid):
        return

    plt.figure(figsize=(7.5, 5), dpi=180)
    sizes = 25 + 90 * (cnt[valid] / max(cnt[valid].max(), 1))
    plt.scatter(centers[valid], gamma_exp[valid], s=sizes, alpha=0.85, label="Experimental")

    xs = np.linspace(0, np.nanmax(centers[valid]), 200)
    a, c0, c = fit_params
    plt.plot(xs, gamma_spherical(xs, a, c0, c), linewidth=2.0, label=f"Spherical fit: a={a:.1f}, C0={c0:.3f}, C={c:.3f}")

    plt.xlabel("Lag distance h (m)")
    plt.ylabel("Semi-variance γ(h)")
    plt.title(f"Experimental variogram (indicator) - code={code}")
    plt.grid(True, alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_png, dpi=240)
    plt.close()


def resolve_out_dir(args: argparse.Namespace) -> Path:
    if args.out_dir:
        out_dir = Path(args.out_dir).resolve()
    else:
        out_dir = BACKEND_DIR / "storage" / "variograms_main"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def load_input_csv(args: argparse.Namespace, out_dir: Path) -> Tuple[pd.DataFrame, Path]:
    if args.input_csv:
        csv_path = Path(args.input_csv).resolve()
        if not csv_path.exists():
            raise FileNotFoundError(f"input_csv not found: {csv_path}")
        return pd.read_csv(csv_path, encoding="utf-8-sig"), csv_path

    if not args.model_id:
        raise ValueError("Provide either --input_csv or --model_id")

    tmp_dir = Path(tempfile.mkdtemp(prefix="fit_variogram_b_", dir=str(out_dir)))
    csv_path = tmp_dir / "borehole_layers_real.csv"
    with get_session() as db:
        export_model_sections_to_layer_csv(db, int(args.model_id), csv_path)
    return pd.read_csv(csv_path, encoding="utf-8-sig"), csv_path


def prepare_dataframe(df: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    required = [args.x_col, args.y_col, args.z_col, args.code_col]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}; available columns: {df.columns.tolist()}")

    if int(args.real_only) == 1 and args.src_col in df.columns:
        df = df[df[args.src_col].astype(str) == args.real_tag].copy()

    df = df.dropna(subset=required).copy()
    df[args.code_col] = df[args.code_col].astype(int)
    return df


def choose_shared_params(fits: List[Dict[str, Any]], mode: str) -> Dict[str, Any]:
    if not fits:
        return {"a": 800.0, "C0": 0.0, "C": 0.2, "from": "fallback_default"}

    if mode == "largest_class":
        top = max(fits, key=lambda item: item["n_pos"])
        return {"a": top["a"], "C0": top["C0"], "C": top["C"], "from": "largest_class", "from_code": top["code"]}

    if mode == "median":
        return {
            "a": float(np.median([item["a"] for item in fits])),
            "C0": float(np.median([item["C0"] for item in fits])),
            "C": float(np.median([item["C"] for item in fits])),
            "from": "median_main_codes",
        }

    total = sum(int(item["n_pos"]) for item in fits)
    return {
        "a": float(sum(item["a"] * item["n_pos"] for item in fits) / max(total, 1)),
        "C0": float(sum(item["C0"] * item["n_pos"] for item in fits) / max(total, 1)),
        "C": float(sum(item["C"] * item["n_pos"] for item in fits) / max(total, 1)),
        "from": "weighted_average_main_codes",
    }


def main() -> None:
    args = parse_args()
    out_dir = resolve_out_dir(args)
    df_raw, source_csv = load_input_csv(args, out_dir)
    df = prepare_dataframe(df_raw, args)

    xyz = df[[args.x_col, args.y_col, args.z_col]].to_numpy(dtype=float)
    codes = df[args.code_col].to_numpy(dtype=int)

    vc = pd.Series(codes).value_counts().sort_values(ascending=False)
    print("=== Lithology sample counts ===")
    print(vc.to_string())

    main_codes = vc[vc >= int(args.min_pos)].index.tolist()
    print(f"\nmain codes (min_pos={args.min_pos}) = {main_codes}")

    p0 = (float(args.p0_a), float(args.p0_c0), float(args.p0_c))
    bounds = (
        [float(args.bound_a_min), float(args.bound_c0_min), float(args.bound_c_min)],
        [float(args.bound_a_max), float(args.bound_c0_max), float(args.bound_c_max)],
    )

    fits: List[Dict[str, Any]] = []
    for code in main_codes:
        indicator = (codes == int(code)).astype(float)
        n_pos = int(indicator.sum())
        n_neg = int(len(indicator) - n_pos)
        print(f"\n--- code={code} samples: pos={n_pos}, neg={n_neg}, total={len(indicator)} ---")

        result = experimental_variogram_indicator(
            xyz=xyz,
            indicator=indicator,
            max_lag=float(args.max_lag),
            n_bins=int(args.n_bins),
        )
        if result is None:
            print(f"skip code={code}: too few points")
            continue

        centers, gamma_exp, cnt = result
        fit_mask = (cnt >= int(args.min_pairs_per_bin)) & np.isfinite(gamma_exp)
        if int(fit_mask.sum()) < int(args.min_bins_with_pairs):
            print(f"skip code={code}: insufficient valid bins ({int(fit_mask.sum())})")
            continue

        xdata = centers[fit_mask]
        ydata = gamma_exp[fit_mask]
        sigma = 1.0 / np.sqrt(np.maximum(cnt[fit_mask], 1))

        try:
            popt, _ = curve_fit(
                gamma_spherical,
                xdata,
                ydata,
                p0=p0,
                bounds=bounds,
                sigma=sigma,
                absolute_sigma=False,
                maxfev=20000,
            )
        except Exception as exc:
            print(f"fit failed for code={code}: {exc}")
            continue

        a, c0, c = [float(v) for v in popt]
        print(f"fit => a={a:.2f}, C0={c0:.4f}, C={c:.4f}")

        out_png = out_dir / f"variogram_code_{int(code)}.png"
        plot_variogram(centers, gamma_exp, cnt, int(code), (a, c0, c), out_png)

        fits.append(
            {
                "code": int(code),
                "n_pos": n_pos,
                "a": a,
                "C0": c0,
                "C": c,
            }
        )

    shared = choose_shared_params(fits, args.shared_mode)
    shared["input_csv"] = str(source_csv)
    shared["real_only"] = int(args.real_only)
    shared["shared_mode"] = args.shared_mode

    fits_df = pd.DataFrame(fits).sort_values(["n_pos"], ascending=False) if fits else pd.DataFrame(columns=["code", "n_pos", "a", "C0", "C"])
    fits_csv = out_dir / "variogram_fits.csv"
    fits_df.to_csv(fits_csv, index=False, encoding="utf-8-sig")

    shared_json = out_dir / "variogram_shared_params.json"
    shared_json.write_text(json.dumps(shared, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_json = out_dir / "variogram_summary.json"
    summary_json.write_text(
        json.dumps(
            {
                "input_csv": str(source_csv),
                "n_rows": int(len(df)),
                "n_codes": int(df[args.code_col].nunique()),
                "main_codes": [int(x) for x in main_codes],
                "shared": shared,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\nsaved: {fits_csv}")
    print(f"saved: {shared_json}")
    print(f"saved: {summary_json}")
    print("shared params => " + json.dumps(shared, ensure_ascii=False))


if __name__ == "__main__":
    main()
