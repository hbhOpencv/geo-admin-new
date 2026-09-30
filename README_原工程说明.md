# 地质模型管理系统（Python + Vue + SQLite）

这是你原来 Streamlit 管理端的“工程化版本”：
- 后端：FastAPI + SQLAlchemy + SQLite（管理员端 API）
- 前端：Vue 3 + Vite + Element Plus（管理员端界面）
- 权限：管理员登录（JWT）

## 目录
- backend：后端 API
- frontend：前端管理后台

---

## 1) 后端启动

```bash
cd backend
python -m venv .venv
# Windows:
# .venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt

# 配置环境变量（建议复制）
copy .env.example .env   # Windows PowerShell: cp .env.example .env
# 或手动设置 ADMIN_USERNAME / ADMIN_PASSWORD / JWT_SECRET

# 把你的 SQLite 放到 backend/ 下：
#   geology_norm.db
# 如果你已经有 geology_norm.db（之前系统生成的），直接复制过来即可。

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

打开 API 文档：
- http://localhost:8000/docs

---

## 2) 前端启动

```bash
cd frontend
npm install
npm run dev

打开：
- http://localhost:5173

---

## 3) 登录账号

默认（可在 backend/.env 修改）：
- username: admin
- password: admin123

---

## 4) 化学元素挖掘分析

系统已集成两个彼此独立的算法：

- **化学元素变化规律挖掘算法**：计算背景值、自动异常下限、CV、项目内部富集指数，识别并合并钻孔异常深度段。
- **化学元素相关性挖掘算法**：计算 Pearson/Spearman 相关矩阵，执行 R 型层次聚类，提取候选元素组合及组合异常共现区间。

使用步骤：

1. 登录后在模型树中选择 **化学元素挖掘分析**；
2. 选择运行算法、元素、钻孔、深度范围或地质分段；
3. 点击 **开始分析**，等待后台任务完成；
4. 在变化规律页查看自动结论、异常统计、钻孔纵向变化及三维异常图层；
5. 在相关性页查看自动结论、R型聚类、组合稳定性、地质位置摘要及与变化规律任务的异常共现；
6. 结果可查看中文报告，并导出 CSV、Markdown、PNG、SVG、PDF 和完整 ZIP 成果包。

系统会在 `backend/storage/geochem_mining/{job_id}/outputs` 保存每次任务的可追溯结果。三维异常段按垂直钻孔假定近似定位，相关关系不能直接解释为因果关系。

后端算法回归测试：

```bash
cd backend
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

前端生产构建检查：

```bash
cd frontend
npm run build
```

---

## 5) 地球化学找矿证据工作流（2026-07 优化版）

系统按以下固定依赖顺序运行，不能跳过上游证据：

1. **数据与规则**：创建规则草稿，维护统一背景方法、相对背景倍数色阶、元素角色、检出限状态、最低边界品位和工业品位。
2. **正异常与元素优先级**：运行变化规律任务，冻结模型级背景，分别输出统计正异常、专业品位和综合关注记录。
3. **元素组合**：相关性任务必须显式绑定一个数据哈希、筛选条件和规则版本完全一致的变化规律任务。
4. **三维核查**：三维任务必须绑定变化规律任务；原始样点、浓度估计、阈值支持度、可信度和空白原因分别表达。
5. **报告与追溯**：证据总览页汇总规则版本、任务链、留一钻孔验证、候选组合和候选异常区。

工业品位和检出限尚未由地质专家提供时必须保持 `pending`，系统不会自行填值，也不会把相应区域描述为工业品位。规则一经确认即不可直接修改，只能复制为新版本。

数据库修复脚本默认只检查、不修改：

```bash
cd backend
.venv\Scripts\python.exe scripts/repair_database.py
```

确认检查结果后才使用 `--apply`。执行时会先备份数据库、重建索引，并把孤儿钻孔完整保存到 `borehole_quarantine` 后再从活动数据中移除。
