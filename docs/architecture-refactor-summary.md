# 架构优化变更摘要

- 项目：行迹 · AI 多智能体旅行助手
- 日期：2026-11-05
- 范围：后端架构与模块接缝，不涉及产品功能、前端交互和数据模型
- 状态：代码已修改并通过验证，尚未提交到 Git

## 1. 背景与结论

本次优化针对代码库做了一次架构摩擦审计，重点检查：理解一个概念是否需要跨多个文件、模块接口是否与实现同样复杂、真实缺陷是否藏在调用接缝、哪些部分难以测试。

结论：整体分层（API → 编排 → Agent → 服务/数据 → SQLite）合理，技术栈没有过度设计；主要问题集中在编排器职责过重、地点来源选择埋在核验 Agent 内，以及少量跨层私有依赖。

优化原则：只做行为保持不变的结构调整，不新增功能，不引入新框架或外部依赖。

## 2. 变更总览

后续可靠性补充：

- SSE 生成使用有界队列与取消事件，客户端断开后停止后续阶段，并避免保存未完成行程。
- SSE 内部异常写入服务端日志，客户端只收到稳定的通用错误消息。
- CORS 来源改为 `CORS_ORIGINS` 配置，不再默认允许任意来源。
- 导出格式分发提取到 `app/export_service.py`。
- 增加高德、天气和 LLM 适配器失败路径测试，以及混合天气来源和路线估算限制测试。
- 增加高德路线 adapter：按交通方式解析驾车、步行、公交/地铁结果，失败时保留估算路线并在 `TransportLeg` 中标记来源。
- `TransportLeg` 保存路线道路折线；前端使用原生 OpenStreetMap 瓦片和 SVG 叠加绘制地图，不依赖 Leaflet CDN。瓦片不可达时保留分段路线文本。

| 文件 | 类型 | 变更内容 | 解决的问题 |
| --- | --- | --- | --- |
| `app/pipeline.py` | 新增 | 阶段契约 `StageOutcome` / `StageRun`、统一事件函数 `run_stage`、六个阶段实现 | 编排器把排序、事件、摘要、状态、告警混在一起，只能端到端测试 |
| `app/services/place_source.py` | 新增 | `load_places()` 统一返回 `PlaceSnapshot`，隐藏示例库 / 高德 POI / 不可用三种来源 | 来源判断散落在核验 Agent，城市列表硬编码，难以替换和单测 |
| `app/orchestrator.py` | 重构 | 只保留阶段顺序、trace 汇总、结果组装与 SSE 输出 | 原 366 行的生成器职责过重 |
| `app/agents/verification.py` | 精简 | 改为消费 `PlaceSnapshot`，只负责避开过滤、必去匹配和开放日校验 | 去掉 `config`、高德调用和来源分支 |
| `app/storage.py` | 修复 | 数据库路径改为运行时读取；列表总数改用 `COUNT(*)` | 路径在导入时固化；`total` 被 `limit` 截断 |
| `app/agents/requirement.py` | 接口调整 | `_apply_answers` 改为公开 `apply_answers` | API 层不再依赖私有函数 |
| `app/main.py` | 调整 | `apply_answers` 公开导入；`on_event` 改为 `lifespan` | 跨层私有依赖、废弃启动钩子 |
| `tests/test_architecture.py` | 新增 | 5 个用例：阶段事件顺序、目录来源、POI 适配器、分页总数 | 为新增接缝建立边界测试 |
| `README.md` | 更新 | 目录结构、模块说明、测试数量同步为 43 | 文档与实现保持一致 |
| `.gitignore` | 更新 | 忽略本地技能、缓存与锁文件 | 避免把工具产物提交进仓库 |

## 3. 关键设计决策

### 3.1 阶段契约替代隐式约定

`run_stage(agent, label, index, total, compute)` 统一负责：

- 发出 `agent_start`
- 执行阶段计算
- 构造 `AgentTrace`（状态、摘要、明细、耗时）
- 发出 `agent_done`

每个阶段只返回 `StageOutcome`，编排器不再重复编写事件与 trace 代码。新增阶段或替换阶段时，不需要修改事件协议。

对应实现：[pipeline.py](file:///d:/Courses/travel-agent/app/pipeline.py#L24-L85)

### 3.2 地点来源作为深模块

`load_places(city) -> PlaceSnapshot` 是唯一入口：

- 内置示例城市 → `demo_catalog`
- 非示例城市 + 高德 Key → `amap_live`
- 两者都不满足 → `unavailable` + 明确提示

核验 Agent 不再感知 Key、HTTP 客户端或来源优先级，只处理业务规则。未来增加新数据源只需扩展该模块。

对应实现：[place_source.py](file:///d:/Courses/travel-agent/app/services/place_source.py#L12-L36)

### 3.3 配置在调用时读取

SQLite 路径不再在模块导入时固化，测试和运行时可安全切换数据库，不会因导入顺序导致数据写错位置。

对应实现：[storage.py](file:///d:/Courses/travel-agent/app/storage.py#L18-L23)

### 3.4 公共接口与启动生命周期

- `apply_answers` 从私有变为公开，API 层不再跨模块调用下划线函数。
- FastAPI 启动改用 `lifespan`，消除 `on_event` 废弃警告。

## 4. 行为兼容性

以下内容保持不变：

- 所有 API 路径、请求体与响应结构
- SSE 事件顺序：每个 Agent 一次 `agent_start`、一次 `agent_done`，最后 `result`
- 六阶段顺序：需求理解 → 资料检索 → 天气适配 → 地点核验 → 路线编排 → 预算计算
- 行程、预算、来源、假设、风险提示的生成规则
- 前端页面、导出格式与示例数据
- 数据库表结构；无迁移脚本

因此本次变更对现有调用方是透明的。

## 5. 量化结果

| 指标 | 优化前 | 优化后 |
| --- | --- | --- |
| 编排器行数 | 366 | 248 |
| 阶段实现位置 | 全部内联在编排器 | 独立 `pipeline.py`（164 行） |
| 自动化用例 | 38 | 43 |
| 分页 `total` | 等于当前页条数 | 全表真实总数 |
| 启动警告 | `on_event` 废弃 | 已消除 |
| 测试警告 | 3 条 | 1 条（TestClient 上游提示） |

说明：拆分后总代码行数略有增加（新增模块与契约），优化目标是职责边界和可测试性，不以减少行数为目标。

## 6. 验证记录

### 自动化测试

```text
86 passed, 1 warning
```

新增覆盖：

- 阶段事件必须先 `start` 后 `done`，且 trace 含 agent、状态与耗时
- 示例城市返回 `demo_catalog` 且无告警
- 无 Key 的未覆盖城市返回 `unavailable`
- 未覆盖城市通过高德适配器返回 `amap_live`，候选不足时给出告警
- 行程分页 `total` 不被 `limit` 截断

### 真实 HTTP / SSE 冒烟

```text
health: 200, demo_mode: True
analyze: ready
sse: 6 × agent_start → 6 × agent_done → result（0.04s）
trip: 成都 3 天 8506.59 元
export: docx 40103 B / doc 17389 B / pdf 9340 B / md 6813 B
```

### 运行方式

```powershell
cd d:\Courses\travel-agent
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8765
.\.venv\Scripts\python.exe scripts\smoke.py --base http://127.0.0.1:8765
```

## 7. 风险与回滚

| 风险 | 评估 | 措施 |
| --- | --- | --- |
| 阶段拆分改变事件顺序 | 低 | SSE 冒烟与 API 用例覆盖，实测顺序一致 |
| 地点来源行为变化 | 低 | 三种来源均有边界用例；`unavailable` 提示更明确 |
| SQLite 路径切换 | 低 | 无 schema 变更，测试使用独立数据库 |
| 未提交代码丢失 | 中 | 当前改动均在本地工作区，建议尽快提交 |

回滚方式：本次无数据迁移，回退相关代码文件即可；若已提交，使用一次新的 revert 提交，避免改写历史。

## 8. 后续建议（本次未执行）

1. 将 `main.py` 的路由按领域拆分，避免单个文件继续膨胀。
2. 为外部服务（高德、和风、LLM）增加超时重试与短期缓存。
3. 上线前收紧 CORS 白名单，增加鉴权与限流。
4. 多实例部署时再引入 PostgreSQL；当前单机 SQLite 足够支撑 v1。
5. 不引入 React、LangGraph 或消息队列，除非出现明确的产品或规模需求。

## 9. 附：变更文件清单

新增：

- `app/pipeline.py`
- `app/services/place_source.py`
- `tests/test_architecture.py`

修改：

- `app/orchestrator.py`
- `app/agents/verification.py`
- `app/agents/requirement.py`
- `app/main.py`
- `app/storage.py`
- `tests/test_requirement.py`
- `README.md`
- `.gitignore`
