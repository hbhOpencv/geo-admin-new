export type Page<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
};

export type GeologicalModel = {
  id: number;
  name: string;
  description?: string | null;
  created_at: string;
};

export type Borehole = {
  id: number;
  model_id: number;
  Borehole: string;
  // 坐标与深度（主要用于列表展示）
  Easting?: number | null;   // X
  Northing?: number | null;  // Y
  Elevation?: number | null; // Z
  Holedepth?: number | null;

  // 便于显示的派生字段（后端会尽量填）
  x?: number | null;
  y?: number | null;
  z?: number | null;

  原点?: string | null; // 导入保留
  范围下限?: string | null;
  范围上限?: string | null;
  created_at: string;
};

export type ChemicalBorehole = {
  id: number;
  hole_id: string;
  collar_x?: number | null;
  collar_y?: number | null;
  collar_z?: number | null;
  depth_min?: number | null;
  depth_max?: number | null;
  sample_count: number;
  element_count: number;
  problem_count: number;
  unit?: string | null;
};

export type BoreholeSection = {
  id: number;
  borehole_id: number;
  项?: string | null;
  高度?: number | null;
  底坐标?: string | null;
  底半径?: number | null;
  顶坐标?: string | null;
  Borehole?: string | null;
  Depth?: number | null;
  Bottom?: number | null;
  Description?: string | null;
  元素ID?: number | null;
  范围下限?: string | null;
  范围上限?: string | null;
  geobody_key?: string | null;
  created_at: string;
};

export type Geobody = {
  id: number;
  model_id: number;
  key?: string | null;
  层?: string | null;
  体积?: string | null;
  表面积?: string | null;
  元素ID?: string | null;
  范围下限?: string | null;
  范围上限?: string | null;
  潮湿程度?: string | null;
  单轴饱和抗压强度?: string | null;
  地层代号?: string | null;
  风化程度?: string | null;
  工程等级?: string | null;
  基本承载力?: string | null;
  基地摩擦系数?: string | null;
  临时挖方边坡率?: string | null;
  密实状态?: string | null;
  内摩擦角?: string | null;
  凝聚力?: string | null;
  时代成因?: string | null;
  塑性状态?: string | null;
  天然密度?: string | null;
  填料类别?: string | null;
  岩土名称?: string | null;
  永久挖方边坡率?: string | null;
  钻孔灌注桩桩周极限摩阻力?: string | null;
  created_at: string;
};

export type ReconstructJob = {
  id: string;
  model_id: number;
  status: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  outputs: string[];
  log_tail: string;
  params?: Record<string, any>;
};

export type User = {
  id: number;
  username: string;
  is_active: boolean;
  is_admin: boolean;
  created_at: string;
};
