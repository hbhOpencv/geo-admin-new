# -*- coding: utf-8 -*-
"""Import two CSVs into SQLite.

Usage:
  cd backend
  python scripts/import_two_csv.py --model_name "xxx" --borehole_csv path --geobody_csv path

This script is based on your previous import_two_csv.py.
"""

# import_two_csv_norm.py
# -*- coding: utf-8 -*-
import argparse
import csv
import re
from pathlib import Path
from typing import List, Dict, Optional

from models import Base, get_engine, get_session, GeologicalModel, Borehole, BoreholeSection, Geobody


def read_csv_rows(path: Path) -> List[List[str]]:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            with path.open("r", encoding=enc, newline="") as f:
                reader = csv.reader(f, delimiter=",", quotechar='"')
                return [row for row in reader if row and any(str(x).strip() for x in row)]
        except Exception:
            continue
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f, delimiter=",", quotechar='"')
        return [row for row in reader if row and any(str(x).strip() for x in row)]


def normalize_borehole_header(header: List[str]) -> List[str]:
    # 钻孔CSV里两个空列 -> 临时命名（只为读取，分段表不会用到）
    out = []
    empty_cnt = 0
    for h in header:
        h = (h or "").strip()
        if h == "":
            empty_cnt += 1
            out.append("体积" if empty_cnt == 1 else "表面积")
        else:
            out.append(h)
    return out


def row_to_dict(header: List[str], row: List[str]) -> Dict[str, str]:
    if len(row) < len(header):
        row = row + [""] * (len(header) - len(row))
    if len(row) > len(header):
        row = row[:len(header)]
    return {header[i]: row[i] for i in range(len(header))}


_num_keep = re.compile(r"[^0-9eE\+\-\.]")


def parse_float_any(v: Optional[str]) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s == "/":
        return None
    s = s.replace("立方m", "").replace("平方m", "")
    s = s.replace("立方 m", "").replace("平方 m", "")
    s = s.replace("m", "").replace("米", "")
    s = s.replace(" ", "")
    s2 = _num_keep.sub("", s)
    if not s2:
        return None
    try:
        return float(s2)
    except Exception:
        return None


def norm_text(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s == "/":
        return None
    return s


def extract_geobody_key(item_tag: Optional[str]) -> Optional[str]:
    if not item_tag:
        return None
    m = re.match(r"^\s*<([^>,]+)", str(item_tag).strip())
    return m.group(1).strip() if m else None


def is_collar_row(d: Dict[str, str]) -> bool:
    # 汇总行：Borehole 列空 + 项 以 DZ- 开头
    return (not norm_text(d.get("Borehole"))) and (norm_text(d.get("项")) or "").startswith("DZ-")


def is_section_row(d: Dict[str, str]) -> bool:
    # 分段行：Borehole 列有值
    return norm_text(d.get("Borehole")) is not None


def main(borehole_csv: str, geobody_csv: str, model_name: str, reset: bool):
    engine = get_engine()
    Base.metadata.create_all(engine)

    borehole_path = Path(borehole_csv)
    geobody_path = Path(geobody_csv)
    if not borehole_path.exists():
        raise FileNotFoundError(f"找不到钻孔CSV：{borehole_path.resolve()}")
    if not geobody_path.exists():
        raise FileNotFoundError(f"找不到地质体CSV：{geobody_path.resolve()}")

    # 模型
    with get_session() as s:
        model = s.query(GeologicalModel).filter(GeologicalModel.name == model_name).first()
        if model and reset:
            s.delete(model)
            s.commit()
            model = None
        if not model:
            model = GeologicalModel(name=model_name, description="两份CSV导入（已删除 boreholes 的 x/y/collar_z/total_depth）")
            s.add(model)
            s.commit()
        model_id = model.id

    # 读CSV
    bh_rows = read_csv_rows(borehole_path)
    gb_rows = read_csv_rows(geobody_path)

    bh_header = normalize_borehole_header([str(x or "").strip() for x in bh_rows[0]])
    gb_header = [str(x or "").strip() for x in gb_rows[0]]

    # 清空旧数据（该模型）
    with get_session() as s:
        s.query(Geobody).filter(Geobody.model_id == model_id).delete(synchronize_session=False)
        old_bhs = s.query(Borehole).filter(Borehole.model_id == model_id).all()
        for bh in old_bhs:
            s.delete(bh)  # 级联删 sections
        s.commit()

    # 导入地质体：按表头字段原样写入
    with get_session() as s:
        for row in gb_rows[1:]:
            d = row_to_dict(gb_header, row)
            layer = norm_text(d.get("层"))
            if not layer:
                continue

            obj = Geobody(model_id=model_id)
            obj.层 = d.get("层")
            obj.key = layer.replace("地质-", "", 1) if layer.startswith("地质-") else layer

            for col in [
                "体积","表面积","元素ID","范围下限","范围上限","潮湿程度","单轴饱和抗压强度","地层代号","风化程度",
                "工程等级","基本承载力","基地摩擦系数","临时挖方边坡率","密实状态","内摩擦角","凝聚力","时代成因",
                "塑性状态","天然密度","填料类别","岩土名称","永久挖方边坡率","钻孔灌注桩桩周极限摩阻力"
            ]:
                if hasattr(obj, col):
                    setattr(obj, col, d.get(col))

            s.add(obj)
        s.commit()

    # ========= 处理钻孔CSV：汇总行 -> boreholes，分段行 -> borehole_sections =========
    agg: Dict[str, Dict[str, object]] = {}
    sections = []

    def ensure(code: str):
        if code not in agg:
            agg[code] = {
                "原点": None,
                "Northing": None,
                "Easting": None,
                "Elevation": None,
                "Holedepth": None,
                "范围下限": None,
                "范围上限": None,
            }

    def put_first(code: str, k: str, v):
        ensure(code)
        if agg[code][k] is None and v is not None:
            agg[code][k] = v

    for row in bh_rows[1:]:
        d = row_to_dict(bh_header, row)

        if is_collar_row(d):
            code = norm_text(d.get("项"))  # 汇总行的钻孔编号在“项”
            if not code:
                continue
            ensure(code)

            put_first(code, "原点", norm_text(d.get("原点")))

            # 兼容 Northing / Northong
            n = parse_float_any(d.get("Northing")) if d.get("Northing") else parse_float_any(d.get("Northong"))
            put_first(code, "Northing", n)
            put_first(code, "Easting", parse_float_any(d.get("Easting")))
            put_first(code, "Elevation", parse_float_any(d.get("Elevation")))
            put_first(code, "Holedepth", parse_float_any(d.get("Holedepth")))

            put_first(code, "范围下限", norm_text(d.get("范围下限")))
            put_first(code, "范围上限", norm_text(d.get("范围上限")))

        elif is_section_row(d):
            code = norm_text(d.get("Borehole"))
            if not code:
                continue
            ensure(code)

            item_tag = norm_text(d.get("项"))
            sections.append({
                "code": code,
                "项": item_tag,
                "高度": parse_float_any(d.get("高度")),
                "底坐标": norm_text(d.get("底坐标")),
                "底半径": parse_float_any(d.get("底半径")),
                "顶坐标": norm_text(d.get("顶坐标")),
                "Borehole": code,
                "Depth": parse_float_any(d.get("Depth")),
                "Bottom": parse_float_any(d.get("Bottom")),
                "Description": norm_text(d.get("Description")),
                "元素ID": int(parse_float_any(d.get("元素ID"))) if parse_float_any(d.get("元素ID")) is not None else None,
                "范围下限": norm_text(d.get("范围下限")),
                "范围上限": norm_text(d.get("范围上限")),
                "geobody_key": extract_geobody_key(item_tag),
            })

            # 如果汇总行没给 bbox，这里也用分段的 bbox 补一下
            put_first(code, "范围下限", norm_text(d.get("范围下限")))
            put_first(code, "范围上限", norm_text(d.get("范围上限")))

        else:
            continue

    # 写入 boreholes + sections
    with get_session() as s:
        borehole_id = {}
        for code in sorted(agg.keys()):
            bh = Borehole(model_id=model_id, Borehole=code)
            bh.原点 = agg[code]["原点"]
            bh.Northing = agg[code]["Northing"]
            bh.Easting = agg[code]["Easting"]
            bh.Elevation = agg[code]["Elevation"]
            bh.Holedepth = agg[code]["Holedepth"]
            bh.范围下限 = agg[code]["范围下限"]
            bh.范围上限 = agg[code]["范围上限"]
            s.add(bh)
            s.flush()
            borehole_id[code] = bh.id
        s.commit()

        for sec in sections:
            bhid = borehole_id.get(sec["code"])
            if not bhid:
                continue
            obj = BoreholeSection(
                borehole_id=bhid,
                项=sec["项"],
                高度=sec["高度"],
                底坐标=sec["底坐标"],
                底半径=sec["底半径"],
                顶坐标=sec["顶坐标"],
                Borehole=sec["Borehole"],
                Depth=sec["Depth"],
                Bottom=sec["Bottom"],
                Description=sec["Description"],
                元素ID=sec["元素ID"],
                范围下限=sec["范围下限"],
                范围上限=sec["范围上限"],
                geobody_key=sec["geobody_key"],
            )
            s.add(obj)
        s.commit()

    print("✅ 导入完成（boreholes 已删除 x/y/collar_z/total_depth）")
    print(f"DB: {engine.url}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--borehole_csv", required=True, help="钻孔CSV（可同时含汇总行+分段行）")
    ap.add_argument("--geobody_csv", required=True, help="地质体CSV")
    ap.add_argument("--model_name", default="德达隧道地质模型", help="模型名称")
    ap.add_argument("--reset", action="store_true", help="重导：删除该模型及其数据再导入")
    args = ap.parse_args()

    main(args.borehole_csv, args.geobody_csv, args.model_name, args.reset)
