# 阶段接口

`POST /api/stages/<stage>` 接收 `multipart/form-data`。字段 `sample=dirty`、`sample=clean` 或 `file=<CSV>` 三选一。阶段为 `intake`、`prepare`、`calculate`、`policy`、`balance`。预览不入库。

`POST /api/runs` 接收同样的输入，执行全批并返回运行报告。`GET /api/runs` 返回最近 50 批。`GET /api/runs/<id>` 查询一批和当前复核状态。

`PATCH /api/runs/<id>` 接收 `revision`、`status` 和 `note`。状态使用 `APPROVED` 或 `HELD`。冲突返回 409，客户端读取新版本后重试。

`GET /api/runs/<id>/evidence` 下载内含运行清单、行号隔离、业务证据和复核信息的 JSON。

## 关键分区、配额与预留版本

- CSV 中可用 `kind=critical`（或 `key`）行：编号已存在时作为该任务的关键标记，否则本身是一个关键任务。也可在任务 `note` 中写“关键”。阶段预览/批次接口支持表单或 JSON 字段 `critical=1,2`、`quota=2`。
- 排程按字典序最大化 `(已排关键分区数, 已排普通分区数)`，仍满足依赖、释放点、截止时间与预留占用。`measure` 额外给出 `quota`、`quota_met`、`critical_scheduled`、`normal_scheduled`，未排原因仍在 `measure.evidence.rejected`。
- `plan.slots` 即签发配额：`已排数 >= quota` 该组才算满足；`balance.quota_met`/`releasable` 据此判定。未达配额的签发返回 `409`（`code=UNRESOLVED_ISSUES`，并带 `reason=QUOTA_NOT_MET`），批次保持 `PENDING`、`revision` 不变。
- `GET /api/runs/<id>/schedule` 按组返回排程、未排原因与配额；`GET /api/schedule` 返回最近一批的同一视图。
- `GET/PUT/POST/PATCH /api/reservations`（别名 `/api/reservation`、`/api/upstream/reservations`、`/api/board/reservations`）读取或更新全局上游预留 `{version,digest,reservations}`；相同内容幂等，可带 `version` 做乐观校验。
- `PUT/POST/PATCH /api/runs/<id>/reservations`（或 `/reservation`、`/reschedule`）提交 `{revision,reservations:[{release,deadline}],critical?:[...]}`，按原始来源与新预留重算并令 `revision+1`。
- 预留更新后，绑定旧 `reservation_digest` 的排程不能签发：签发在与预留更新互斥的立即写事务内校验版本，冲突返回 `409` 与 `STALE_REVIEW`（`codes` 中含 `STALE_SCHEDULE`/`RESERVATION_CHANGED`）。预留变更与签发并发时由 SQLite 写事务串行化，预留先提交即围栏旧排程，客户端刷新并重算后再签发。
