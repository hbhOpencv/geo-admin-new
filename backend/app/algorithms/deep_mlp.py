# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
import numpy as np
import pandas as pd


def _setup_style() -> None:
    font_candidates = [
        r"C:\Windows\Fonts\NotoSansSC-VF.ttf",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
    ]
    font_name = "DejaVu Sans"
    for font_file in font_candidates:
        path = Path(font_file)
        if not path.exists():
            continue
        try:
            fm.fontManager.addfont(str(path))
            font_name = fm.FontProperties(fname=str(path)).get_name()
            break
        except Exception:
            pass

    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [font_name, "Arial", "DejaVu Sans"],
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "axes.spines.right": False,
            "axes.spines.top": False,
            "axes.unicode_minus": False,
            "font.size": 8,
        }
    )


_setup_style()


def log(path: Path, msg: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(str(msg) + "\n")


def _find_col(columns: List[str], candidates: List[str], required: bool = True) -> str:
    normalized = {str(c).strip().lower(): c for c in columns}
    for cand in candidates:
        key = cand.strip().lower()
        if key in normalized:
            return str(normalized[key])
    for col in columns:
        low = str(col).strip().lower()
        if any(cand.strip().lower() in low for cand in candidates):
            return str(col)
    if required:
        raise ValueError(f"找不到所需列，候选={candidates}，实际列={columns}")
    return ""


def _borehole_columns(df: pd.DataFrame) -> Dict[str, str]:
    columns = [str(c) for c in df.columns]
    return {
        "hole": _find_col(columns, ["钻孔ID", "borehole", "hole"]),
        "x": _find_col(columns, ["X坐标(m)", "X坐标", "x"]),
        "y": _find_col(columns, ["Y坐标(m)", "Y坐标", "y"]),
        "z": _find_col(columns, ["岩性段中点Z(m)", "中点Z", "Z坐标", "z"]),
        "code": _find_col(columns, ["岩性编码", "code", "lithology"]),
    }


def softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / np.maximum(e.sum(axis=1, keepdims=True), 1e-12)


def relu(x: np.ndarray) -> np.ndarray:
    return np.maximum(x, 0.0)


def relu_grad(x: np.ndarray) -> np.ndarray:
    return (x > 0.0).astype(np.float32)


class NumpyMLP:
    def __init__(self, input_dim: int, hidden1: int, hidden2: int, n_classes: int, seed: int = 202501):
        rng = np.random.default_rng(seed)
        self.params = {
            "W1": rng.normal(0, math.sqrt(2 / input_dim), size=(input_dim, hidden1)).astype(np.float32),
            "b1": np.zeros(hidden1, dtype=np.float32),
            "W2": rng.normal(0, math.sqrt(2 / hidden1), size=(hidden1, hidden2)).astype(np.float32),
            "b2": np.zeros(hidden2, dtype=np.float32),
            "W3": rng.normal(0, math.sqrt(2 / hidden2), size=(hidden2, n_classes)).astype(np.float32),
            "b3": np.zeros(n_classes, dtype=np.float32),
        }

    def forward(self, x: np.ndarray):
        p = self.params
        z1 = x @ p["W1"] + p["b1"]
        a1 = relu(z1)
        z2 = a1 @ p["W2"] + p["b2"]
        a2 = relu(z2)
        logits = a2 @ p["W3"] + p["b3"]
        probs = softmax(logits)
        return z1, a1, z2, a2, logits, probs

    def predict_proba(self, x: np.ndarray, batch_size: int = 100000) -> np.ndarray:
        outs = []
        for i in range(0, len(x), batch_size):
            outs.append(self.forward(x[i : i + batch_size])[-1])
        return np.vstack(outs)


class Adam:
    def __init__(self, params: Dict[str, np.ndarray], lr: float = 1e-3):
        self.params = params
        self.lr = float(lr)
        self.t = 0
        self.m = {k: np.zeros_like(v) for k, v in params.items()}
        self.v = {k: np.zeros_like(v) for k, v in params.items()}

    def step(self, grads: Dict[str, np.ndarray]) -> None:
        self.t += 1
        beta1, beta2, eps = 0.9, 0.999, 1e-8
        for key, grad in grads.items():
            self.m[key] = beta1 * self.m[key] + (1 - beta1) * grad
            self.v[key] = beta2 * self.v[key] + (1 - beta2) * (grad * grad)
            m_hat = self.m[key] / (1 - beta1**self.t)
            v_hat = self.v[key] / (1 - beta2**self.t)
            self.params[key] -= self.lr * m_hat / (np.sqrt(v_hat) + eps)


def _clean_borehole_samples(borehole_csv: Path) -> Tuple[pd.DataFrame, Dict[str, str]]:
    df = pd.read_csv(borehole_csv, encoding="utf-8-sig")
    cols = _borehole_columns(df)
    out = pd.DataFrame(
        {
            "hole": df[cols["hole"]],
            "X": df[cols["x"]],
            "Y": df[cols["y"]],
            "Z": df[cols["z"]],
            "code": df[cols["code"]],
        }
    )
    for col in ["X", "Y", "Z", "code"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["X", "Y", "Z", "code"])
    out["code"] = out["code"].astype(int)
    return out.reset_index(drop=True), cols


def _read_p_cols(path: Path) -> List[str]:
    cols = pd.read_csv(path, nrows=1, encoding="utf-8-sig").columns.tolist()
    return [str(c) for c in cols if str(c).startswith("p_")]


def _normalizer_from_voxels(voxels_csv: Path) -> Dict[str, List[float]]:
    mins = np.array([np.inf, np.inf, np.inf], dtype=float)
    maxs = np.array([-np.inf, -np.inf, -np.inf], dtype=float)
    for chunk in pd.read_csv(voxels_csv, usecols=["X", "Y", "Z"], chunksize=400000, encoding="utf-8-sig"):
        xyz = chunk[["X", "Y", "Z"]].apply(pd.to_numeric, errors="coerce").dropna().to_numpy(float)
        if len(xyz) == 0:
            continue
        mins = np.minimum(mins, xyz.min(axis=0))
        maxs = np.maximum(maxs, xyz.max(axis=0))
    if not np.isfinite(mins).all() or not np.isfinite(maxs).all():
        raise ValueError("算法 B 体素坐标为空，无法训练深度模型")
    return {"min": mins.tolist(), "max": maxs.tolist()}


def _norm_xyz(xyz: np.ndarray, norm: Dict[str, List[float]]) -> np.ndarray:
    mn = np.array(norm["min"], dtype=float)
    mx = np.array(norm["max"], dtype=float)
    return ((xyz - mn) / np.maximum(mx - mn, 1e-9) * 2.0 - 1.0).astype(np.float32)


def _features_from_df(df: pd.DataFrame, p_cols: List[str], norm: Dict[str, List[float]]) -> np.ndarray:
    xyz = df[["X", "Y", "Z"]].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy(np.float32)
    parts = [_norm_xyz(xyz, norm)]
    probs = df[p_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy(np.float32)
    row_sum = probs.sum(axis=1, keepdims=True)
    probs = probs / np.maximum(row_sum, 1e-9)
    parts.append(probs)
    parts.append(probs.max(axis=1, keepdims=True).astype(np.float32))
    return np.hstack(parts).astype(np.float32)


def _nearest_probs_for_boreholes(boreholes: pd.DataFrame, ikrig_csv: Path, p_cols: List[str]) -> pd.DataFrame:
    xyz_parts = []
    p_parts = []
    pred_parts = []
    usecols = ["X", "Y", "Z", "pred_code"] + p_cols
    for chunk in pd.read_csv(ikrig_csv, usecols=usecols, chunksize=500000, encoding="utf-8-sig"):
        xyz_parts.append(chunk[["X", "Y", "Z"]].apply(pd.to_numeric, errors="coerce").to_numpy(np.float32))
        p_parts.append(chunk[p_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy(np.float32))
        pred_parts.append(pd.to_numeric(chunk["pred_code"], errors="coerce").fillna(-1).to_numpy(int))

    if not xyz_parts:
        raise ValueError("voxels_ikrig.csv 为空，无法提取 Kriging 概率特征")

    from scipy.spatial import cKDTree

    xyz = np.vstack(xyz_parts)
    probs = np.vstack(p_parts)
    preds = np.concatenate(pred_parts)
    tree = cKDTree(xyz)
    q = boreholes[["X", "Y", "Z"]].to_numpy(np.float32)
    dist, idx = tree.query(q, k=1)
    out = pd.DataFrame({"X": q[:, 0], "Y": q[:, 1], "Z": q[:, 2], "pred_code": preds[idx]})
    for j, col in enumerate(p_cols):
        out[col] = probs[idx, j]
    out["nearest_voxel_distance"] = dist
    return out


def _collect_pseudo_samples(
    ikrig_csv: Path,
    final_csv: Path,
    p_cols: List[str],
    norm: Dict[str, List[float]],
    threshold: float,
    max_samples: int,
    seed: int,
) -> Tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    selected_x: List[np.ndarray] = []
    selected_y: List[np.ndarray] = []
    seen = 0
    final_cols = pd.read_csv(final_csv, nrows=1, encoding="utf-8-sig").columns.tolist()
    label_col = "pred_code_final" if "pred_code_final" in final_cols else "pred_code"
    final_iter = pd.read_csv(final_csv, usecols=["X", "Y", "Z", label_col], chunksize=200000, encoding="utf-8-sig")
    ik_iter = pd.read_csv(ikrig_csv, usecols=["X", "Y", "Z", "pred_code"] + p_cols, chunksize=200000, encoding="utf-8-sig")

    for ik, fin in zip(ik_iter, final_iter):
        probs = ik[p_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy(np.float32)
        conf = probs.max(axis=1)
        labels = pd.to_numeric(fin[label_col], errors="coerce").fillna(-1).to_numpy(int)
        mask = (conf >= float(threshold)) & (labels >= 0)
        idx = np.where(mask)[0]
        if len(idx) == 0:
            continue
        seen += len(idx)
        keep_prob = min(1.0, max_samples / max(seen, 1) * 0.8)
        idx = idx[rng.random(len(idx)) < keep_prob]
        if len(idx) == 0:
            continue
        selected_x.append(_features_from_df(ik.iloc[idx], p_cols, norm))
        selected_y.append(labels[idx])

    if not selected_x:
        return np.empty((0, 0), dtype=np.float32), np.empty(0, dtype=int)
    x = np.vstack(selected_x)
    y = np.concatenate(selected_y)
    if len(x) > max_samples:
        idx = rng.choice(len(x), size=max_samples, replace=False)
        x = x[idx]
        y = y[idx]
    return x.astype(np.float32), y.astype(int)


def _one_hot(y_idx: np.ndarray, n_classes: int) -> np.ndarray:
    out = np.zeros((len(y_idx), n_classes), dtype=np.float32)
    out[np.arange(len(y_idx)), y_idx] = 1.0
    return out


def train_mlp(
    x: np.ndarray,
    y_idx: np.ndarray,
    weights: np.ndarray,
    n_classes: int,
    hidden1: int,
    hidden2: int,
    epochs: int,
    lr: float,
    batch_size: int,
    seed: int,
):
    rng = np.random.default_rng(seed)
    n = len(x)
    order = rng.permutation(n)
    val_n = max(1, int(n * 0.15))
    val_idx = order[:val_n]
    tr_idx = order[val_n:]
    if len(tr_idx) == 0:
        tr_idx = val_idx

    model = NumpyMLP(x.shape[1], hidden1, hidden2, n_classes, seed=seed)
    opt = Adam(model.params, lr=lr)
    hist = []
    yoh_all = _one_hot(y_idx, n_classes)

    for epoch in range(1, epochs + 1):
        tr_perm = rng.permutation(tr_idx)
        for start in range(0, len(tr_perm), batch_size):
            ids = tr_perm[start : start + batch_size]
            xb = x[ids]
            yb = yoh_all[ids]
            wb = weights[ids].reshape(-1, 1).astype(np.float32)
            z1, a1, z2, a2, _logits, probs = model.forward(xb)
            scale = max(float(wb.sum()), 1e-9)
            dlogits = (probs - yb) * wb / scale
            grads = {"W3": a2.T @ dlogits, "b3": dlogits.sum(axis=0)}
            da2 = dlogits @ model.params["W3"].T
            dz2 = da2 * relu_grad(z2)
            grads["W2"] = a1.T @ dz2
            grads["b2"] = dz2.sum(axis=0)
            da1 = dz2 @ model.params["W2"].T
            dz1 = da1 * relu_grad(z1)
            grads["W1"] = xb.T @ dz1
            grads["b1"] = dz1.sum(axis=0)
            opt.step(grads)

        tr_prob = model.predict_proba(x[tr_idx])
        val_prob = model.predict_proba(x[val_idx])
        tr_pred = tr_prob.argmax(axis=1)
        val_pred = val_prob.argmax(axis=1)
        tr_loss = -np.mean(np.log(np.maximum(tr_prob[np.arange(len(tr_idx)), y_idx[tr_idx]], 1e-9)))
        val_loss = -np.mean(np.log(np.maximum(val_prob[np.arange(len(val_idx)), y_idx[val_idx]], 1e-9)))
        hist.append(
            {
                "epoch": epoch,
                "train_loss": float(tr_loss),
                "val_loss": float(val_loss),
                "train_acc": float((tr_pred == y_idx[tr_idx]).mean()),
                "val_acc": float((val_pred == y_idx[val_idx]).mean()),
            }
        )
    return model, hist


def _plot_history(hist: List[Dict[str, Any]], out: Path) -> None:
    df = pd.DataFrame(hist)
    fig, ax1 = plt.subplots(figsize=(7.2, 4.0))
    ax1.plot(df["epoch"], df["train_loss"], color="#1F4E79", label="训练 loss", linewidth=1.8)
    ax1.plot(df["epoch"], df["val_loss"], color="#9E2F2F", label="验证 loss", linewidth=1.8)
    ax1.set_xlabel("Epoch")
    ax1.set_ylabel("Cross entropy")
    ax1.grid(True, axis="y", color="#D8DEE9", alpha=0.8, linewidth=0.6)

    ax2 = ax1.twinx()
    ax2.plot(df["epoch"], df["train_acc"], color="#4E79A7", linestyle="--", label="训练准确率", linewidth=1.5)
    ax2.plot(df["epoch"], df["val_acc"], color="#E15759", linestyle="--", label="验证准确率", linewidth=1.5)
    ax2.set_ylabel("Accuracy")

    lines, labels = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines + lines2, labels + labels2, frameon=False, loc="center right")
    ax1.set_title("Kriging 引导 MLP 训练曲线", loc="left", fontweight="bold")
    fig.tight_layout()
    fig.savefig(out, dpi=450, bbox_inches="tight")
    fig.savefig(out.with_suffix(".svg"), bbox_inches="tight")
    fig.savefig(out.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(out.with_suffix(".tif"), dpi=450, bbox_inches="tight")
    plt.close(fig)


def _color_table(codes: List[int], fault_codes: List[int] | None = None) -> Dict[int, Tuple[int, int, int]]:
    import colorsys

    fault_set = set(int(c) for c in (fault_codes or []))
    table = {}
    for i, code in enumerate(sorted(set(int(c) for c in codes))):
        if int(code) in fault_set:
            table[int(code)] = (0, 0, 0)
            continue
        hue = (i * 0.61803398875) % 1.0
        r, g, b = colorsys.hsv_to_rgb(hue, 0.55, 0.88)
        table[code] = (int(r * 255), int(g * 255), int(b * 255))
    return table


def _load_source_color_table(path: Path) -> tuple[Dict[int, Tuple[int, int, int]], List[Dict[str, Any]]]:
    rows = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(rows, list):
        raise ValueError("算法 B 岩性颜色表格式无效。")
    colors: Dict[int, Tuple[int, int, int]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            code = int(row["\u5ca9\u6027\u7f16\u7801"])
            colors[code] = (int(row["R"]), int(row["G"]), int(row["B"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not colors:
        raise ValueError("算法 B 岩性颜色表中没有可用的岩性编码和 RGB。")
    return colors, rows


def _infer_fault_codes(final_csv: Path, p_cols: List[str]) -> List[int]:
    """Fault labels are hard geological constraints from Algorithm B.

    Algorithm B stores normal lithology probabilities as p_<code>. The final
    result can additionally contain a fault code after condition simulation.
    Codes that appear in final labels but do not have p_<code> probability
    columns are treated as hard-constraint fault classes.
    """
    prob_codes = set()
    for col in p_cols:
        try:
            prob_codes.add(int(str(col).split("_", 1)[1]))
        except Exception:
            pass

    cols = pd.read_csv(final_csv, nrows=1, encoding="utf-8-sig").columns.tolist()
    label_col = "pred_code_final" if "pred_code_final" in cols else "pred_code"
    seen = set()
    for chunk in pd.read_csv(final_csv, usecols=[label_col], chunksize=500000, encoding="utf-8-sig"):
        vals = pd.to_numeric(chunk[label_col], errors="coerce").dropna().astype(int).unique().tolist()
        seen.update(int(v) for v in vals)
    return sorted(seen - prob_codes)


def _write_ply(path: Path, xyz: np.ndarray, codes: np.ndarray, colors: Dict[int, Tuple[int, int, int]]) -> None:
    with path.open("w", encoding="ascii") as f:
        f.write("ply\nformat ascii 1.0\n")
        f.write(f"element vertex {len(xyz)}\n")
        f.write("property float x\nproperty float y\nproperty float z\n")
        f.write("property uchar red\nproperty uchar green\nproperty uchar blue\n")
        f.write("end_header\n")
        for point, code in zip(xyz, codes):
            rgb = colors.get(int(code), (180, 180, 180))
            f.write(f"{point[0]:.3f} {point[1]:.3f} {point[2]:.3f} {rgb[0]} {rgb[1]} {rgb[2]}\n")


def run_deep_mlp(
    borehole_csv: Path,
    ikrig_csv: Path,
    final_csv: Path,
    source_color_map: Path,
    outdir: Path,
    log_path: Path,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    outdir.mkdir(parents=True, exist_ok=True)
    seed = int(params.get("seed", 202501))
    rng = np.random.default_rng(seed)
    log(log_path, "=== Algorithm C: Kriging-guided MLP start ===")

    p_cols = _read_p_cols(ikrig_csv)
    if not p_cols:
        raise ValueError("voxels_ikrig.csv 中没有 p_<岩性编码> 概率列，无法构建深度学习特征")

    fault_codes = _infer_fault_codes(final_csv, p_cols)
    norm = _normalizer_from_voxels(ikrig_csv)
    boreholes, source_cols = _clean_borehole_samples(borehole_csv)
    bore_probs = _nearest_probs_for_boreholes(boreholes, ikrig_csv, p_cols)

    x_real = _features_from_df(bore_probs, p_cols, norm)
    y_real = boreholes["code"].to_numpy(int)
    x_pseudo, y_pseudo = _collect_pseudo_samples(
        ikrig_csv=ikrig_csv,
        final_csv=final_csv,
        p_cols=p_cols,
        norm=norm,
        threshold=float(params.get("pseudo_threshold", 0.8)),
        max_samples=int(params.get("max_pseudo_samples", 50000)),
        seed=seed,
    )

    if len(x_pseudo) == 0:
        x = x_real
        y = y_real
        weights = np.full(len(y), float(params.get("real_weight", 5.0)), dtype=np.float32)
    else:
        x = np.vstack([x_real, x_pseudo])
        y = np.concatenate([y_real, y_pseudo])
        weights = np.concatenate(
            [
                np.full(len(y_real), float(params.get("real_weight", 5.0)), dtype=np.float32),
                np.full(len(y_pseudo), float(params.get("pseudo_weight", 1.0)), dtype=np.float32),
            ]
        )

    classes = np.array(sorted(set(int(c) for c in y.tolist())), dtype=int)
    class_to_idx = {int(code): i for i, code in enumerate(classes)}
    y_idx = np.array([class_to_idx[int(code)] for code in y], dtype=int)
    log(
        log_path,
        f"training samples: real={len(x_real)}, pseudo={len(x_pseudo)}, classes={len(classes)}, feature_dim={x.shape[1]}",
    )

    model, hist = train_mlp(
        x=x,
        y_idx=y_idx,
        weights=weights,
        n_classes=len(classes),
        hidden1=int(params.get("hidden1", 96)),
        hidden2=int(params.get("hidden2", 64)),
        epochs=int(params.get("epochs", 80)),
        lr=float(params.get("lr", 0.001)),
        batch_size=int(params.get("batch_size", 512)),
        seed=seed,
    )
    pd.DataFrame(hist).to_csv(outdir / "deep_train_log.csv", index=False, encoding="utf-8-sig")
    _plot_history(hist, outdir / "deep_loss_curve.png")

    probs_real = model.predict_proba(x_real)
    pred_real = classes[probs_real.argmax(axis=1)]
    real_acc = float((pred_real == y_real).mean())
    log(log_path, f"real borehole apparent accuracy={real_acc:.4f}")

    max_points = int(params.get("max_points_ply", 600000))
    sample_xyz: List[np.ndarray] = []
    sample_code: List[np.ndarray] = []
    sample_conf: List[np.ndarray] = []
    processed = 0
    agreement_count = 0
    agreement_total = 0

    final_cols = pd.read_csv(final_csv, nrows=1, encoding="utf-8-sig").columns.tolist()
    final_label_col = "pred_code_final" if "pred_code_final" in final_cols else "pred_code"
    final_iter = pd.read_csv(final_csv, usecols=["X", "Y", "Z", final_label_col], chunksize=200000, encoding="utf-8-sig")

    for chunk, final_chunk in zip(pd.read_csv(ikrig_csv, chunksize=200000, encoding="utf-8-sig"), final_iter):
        feat = _features_from_df(chunk, p_cols, norm)
        probs = model.predict_proba(feat, batch_size=100000)
        pred = classes[probs.argmax(axis=1)]
        conf = probs.max(axis=1)
        final_label = pd.to_numeric(final_chunk[final_label_col], errors="coerce").fillna(-1).to_numpy(int)
        if fault_codes:
            fault_mask = np.isin(final_label, np.array(fault_codes, dtype=int))
            pred[fault_mask] = final_label[fault_mask]
            conf[fault_mask] = 1.0
        comparable = final_label >= 0
        agreement_count += int((pred[comparable] == final_label[comparable]).sum())
        agreement_total += int(comparable.sum())
        xyz = chunk[["X", "Y", "Z"]].apply(pd.to_numeric, errors="coerce").to_numpy(np.float32)
        finite = np.isfinite(xyz).all(axis=1)
        processed += len(chunk)
        keep_prob = min(1.0, max_points / max(processed, 1) * 0.7)
        keep = finite & (rng.random(len(chunk)) < keep_prob)
        if keep.any():
            sample_xyz.append(xyz[keep])
            sample_code.append(pred[keep])
            sample_conf.append(conf[keep])
        if processed % 1000000 < len(chunk):
            log(log_path, f"predicted voxels: {processed:,}")

    if not sample_xyz:
        raise ValueError("深度模型预测后没有可导出的体素点")

    xyz_all = np.vstack(sample_xyz)
    code_all = np.concatenate(sample_code)
    conf_all = np.concatenate(sample_conf)
    if len(xyz_all) > max_points:
        ids = rng.choice(len(xyz_all), size=max_points, replace=False)
        xyz_all = xyz_all[ids]
        code_all = code_all[ids]
        conf_all = conf_all[ids]

    source_colors, source_color_rows = _load_source_color_table(source_color_map)
    missing_color_codes = sorted(set(int(code) for code in code_all.tolist()) - set(source_colors))
    if missing_color_codes:
        raise ValueError(f"算法 B 颜色表缺少算法 C 预测编码: {missing_color_codes}")
    colors = {code: source_colors[code] for code in sorted(set(int(c) for c in code_all.tolist()))}
    _write_ply(outdir / "deep_result.ply", xyz_all, code_all, colors)
    pd.DataFrame({"X": xyz_all[:, 0], "Y": xyz_all[:, 1], "Z": xyz_all[:, 2], "pred_code": code_all, "confidence": conf_all}).to_csv(
        outdir / "deep_voxels_sample.csv", index=False, encoding="utf-8-sig"
    )

    pd.DataFrame(source_color_rows).to_csv(outdir / "lithology_color_map.csv", index=False, encoding="utf-8-sig")
    (outdir / "lithology_color_map.json").write_text(
        json.dumps(source_color_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = {
        "method": "Kriging-guided MLP",
        "real_samples": int(len(x_real)),
        "pseudo_samples": int(len(x_pseudo)),
        "n_classes": int(len(classes)),
        "feature_dim": int(x.shape[1]),
        "real_apparent_accuracy": real_acc,
        "final_train_loss": hist[-1]["train_loss"],
        "final_val_loss": hist[-1]["val_loss"],
        "final_train_acc": hist[-1]["train_acc"],
        "final_val_acc": hist[-1]["val_acc"],
        "mean_prediction_confidence": float(conf_all.mean()),
        "source_b_compared_voxels": int(agreement_total),
        "source_b_agreement_voxels": int(agreement_count),
        "source_b_changed_voxels": int(agreement_total - agreement_count),
        "source_b_agreement_rate": float(agreement_count / agreement_total) if agreement_total else None,
        "fault_codes_preserved_from_algorithm_b": [int(c) for c in fault_codes],
        "source_columns": source_cols,
        "source_files": {
            "voxels_ikrig": str(ikrig_csv),
            "voxels_final": str(final_csv),
            "lithology_color_map": str(source_color_map),
        },
        "color_mapping": "inherited_from_source_algorithm_b",
        "outputs": ["deep_result.ply", "deep_voxels_sample.csv", "deep_loss_curve.png", "lithology_color_map.json"],
    }
    (outdir / "deep_metrics.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log(log_path, "=== Algorithm C done ===")
    return summary
