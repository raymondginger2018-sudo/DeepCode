# API 错误处理规范

- 所有 API 返回统一格式: { code, message, data }
- 业务异常使用 BusinessException，HTTP 异常使用 HttpException
- 敏感信息（API Key、Token）绝对不能出现在返回体或日志中
