# 生产级 Multi-Agent 研报系统开发规范

1. **核心技术栈**：
   - 使用 Python 3.11+、FastAPI（提供异步 Web 服务）、LangGraph（StateGraph 多智能体编排）。
   - 大模型默认使用 `langchain_openai.ChatOpenAI` 对接 DeepSeek API（从 `.env` 读取 `OPENAI_API_KEY` 和 `OPENAI_API_BASE_URL`）。
2. **强制兼容性铁律**：
   - 凡是调用 `llm.with_structured_output()`，**必须显式传入 `method="function_calling"`**，防止 DeepSeek 接口报 400 错误。
   - 上下游 Agent 之间必须通过 Pydantic `BaseModel` 强类型对象交接核心分析指标，禁止全量传递杂乱网页原文。
3. **高可用与防死循环规范**：
   - 所有 LangGraph 节点函数必须定义为 `async def`，使用 `await llm.ainvoke()` 实现全链路异步非阻塞。
   - 质检打回重写（Reflection）循环边必须配置硬性计数器熔断（`revision_count >= 2` 时强制放行结束），杜绝死循环刷爆 Token。
   - 联网搜索工具必须内置 `try...except` 异常处理与离线降级兜底数据，确保在任何网络环境下均能 100% 跑通全流程。
4. **工程目录与可观测性**：
   - 按 `app/config.py`、`app/schemas.py`、`app/tools.py`、`app/nodes.py`、`app/graph.py`、`app/main.py` 严格分层，提示词外置到 `prompts.yaml`。
   - 集成 FastAPI `Trace ID` 请求追踪中间件与标准 `logging` 日志，支持可选挂载 Langfuse 回调追踪。
