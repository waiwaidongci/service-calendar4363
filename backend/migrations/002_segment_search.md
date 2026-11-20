# 002 分段复盘

新增 `segment_searches`：

- `run_id` 唯一，一份报告一行；`version` 每次“重新搜索”自增。
- `status`：queued / running / completed / failed；`stage`：-1 未开始、0 已完成底稿聚合、1..3 已完成对应切点层。
- `data`：日级聚合矩阵、各层 dp 与最终 result；`error` 记录失败原因；`updated` 为时间戳。

服务启动时 `Base.metadata.create_all` 会为已有数据库补建该表，不影响既有表数据。worker 的每次写入都以 `(id, run_id, version)` 为条件，被重新搜索取代的旧任务无法覆盖新结果；`resume` 在 worker 启动时把未完成的搜索重新入队，从 `stage` 的下一层继续。
