# 数据库规范

- 表名使用小写 snake_case
- 所有表必须有 id, created_at, updated_at 三列
- 查询必须带索引，禁止全表扫描
- JOIN 查询不超过 3 张表
