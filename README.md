# 文档解析与知识抽取平台

面向多格式文档批量接入、版式解析、分块切分、实体关系抽取、向量化嵌入与抽取质量校验入库的非结构化知识抽取平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/routers/batch_ops.py 批次归属/质检证据集/复核派单专用接口
│   ├── app/services/batch_attribution.py 批次归属校验与证据集（归属唯一权威入口）
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 文件接入 | `file_intake` | 接入批次 | 批次编号、来源系统、文件清单 |
| 格式解析 | `format_parse` | 解析任务 | 任务编号、文档编号、解析器类型 |
| 版式还原 | `layout_restore` | 版式结构 | 文档编号、段落结构、标题层级 |
| 表格抽取 | `table_extract` | 表格区域 | 表格编号、文档编号、页码位置 |
| OCR识别 | `ocr_engine` | 识别任务 | 任务编号、图片区域、识别语言 |
| 分块切分 | `chunk_split` | 文本分块 | 分块编号、文档编号、分块长度 |
| 实体识别 | `entity_recognize` | 命名实体 | 实体编号、分块编号、实体类型 |
| 关系抽取 | `relation_extract` | 实体关系 | 关系编号、头实体、尾实体 |
| 术语归一 | `term_normalize` | 术语映射 | 映射编号、源术语、标准术语 |
| 敏感词过滤 | `sensitive_filter` | 过滤规则 | 规则编号、匹配模式、命中词条 |
| 向量嵌入 | `vector_embed` | 向量化任务 | 任务编号、分块编号、嵌入模型 |
| 知识图谱 | `kg_build` | 图谱三元组 | 三元组编号、头实体、谓词关系 |
| 抽取质量 | `extract_quality` | 质检报告 | 报告编号、批次编号、抽样比例 |
| 人工复核 | `annot_review` | 复核任务 | 复核编号、分块编号、复核对象 |
| 文档版本 | `doc_version` | 版本快照 | 快照编号、文档编号、版本号 |
| 抽取血缘 | `extract_lineage` | 血缘链路 | 链路编号、源文件、解析节点 |
| 结果交付 | `result_deliver` | 交付任务 | 交付编号、快照编号、交付格式 |
| 失败重采 | `parse_retry` | 重采任务 | 重采编号、失败环节、原始批次 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
- 批次归属（实体/关系源记录属于哪个接入批次）只能用证据确认，证据优先级：
  记录内 `batch_key` → `data/provenance/artifact_sources.jsonl` 处理来源登记 →
  接入清单文档编号匹配；同名文件跨批且无更强证据时不猜测，登记为待核
  （`data/provenance/unresolved.jsonl`）。质检报告与人工复核只消费
  `data/quality_evidence/{批次}_evidence.jsonl` 批次证据集，不扫描全局候选，
  重算报告时待核历史数据不计入当前批次。批次专用接口前缀为 `/api/batches`。

领域：数据工程
子方向：数据采集、爬虫与RPA
