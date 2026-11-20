# 初始结构

SQLAlchemy启动创建以下业务表：service_periods, service_cases, business_calendars, sla_runs, delivery_schedules, deliveries, segment_searches、users、audits。Source专属JSON字段由对应validation严格校验；Policy为不可覆盖的版本记录；Run保存source/policy冻结快照；Period仅用于已有期间选择器，不计入核心复杂度。segment_searches 每份报告一行（run_id 唯一），version 在每次“重新搜索”时自增，worker 的每次写库都以 (id, run_id, version) 为条件，因此被取代的旧任务无法覆盖新结果；stage 记录已完成到哪个搜索层，data 保存日级聚合与各层 dp，中断后从下一层继续。已有数据库升级需先备份，后续字段演进应以独立迁移执行。
