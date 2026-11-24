"""FastAPI 应用入口与研报生成接口。"""

import logging
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from .config import BASE_DIR
from .graph import graph
from .schemas import ReportTeamState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("report_agent")
UI_FILE = BASE_DIR / "report_ui.html"

app = FastAPI(title="Report Agent", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-ID"],
)


class GenerateReportRequest(BaseModel):
    """研报生成请求。"""

    target_company: str = Field(min_length=1, max_length=100)

    @field_validator("target_company")
    @classmethod
    def validate_company_name(cls, value: str) -> str:
        company = value.strip()
        if not company:
            raise ValueError("target_company 不能为空白字符")
        return company


class GenerateReportResponse(BaseModel):
    """研报生成结果。"""

    report_markdown: str
    revision_count: int = Field(ge=0)
    threat_index: int = Field(ge=1, le=10)
    output_file_path: str


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    """为每次请求生成或透传 Trace ID，并记录请求日志。"""

    trace_id = request.headers.get("X-Trace-ID") or uuid4().hex
    request.state.trace_id = trace_id
    logger.info(
        "trace_id=%s request_started method=%s path=%s",
        trace_id,
        request.method,
        request.url.path,
    )
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("trace_id=%s request_failed", trace_id)
        raise

    response.headers["X-Trace-ID"] = trace_id
    logger.info(
        "trace_id=%s request_completed status_code=%s",
        trace_id,
        response.status_code,
    )
    return response


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """返回单页研报展示界面。"""

    return FileResponse(UI_FILE, media_type="text/html")


@app.post("/api/generate_report", response_model=GenerateReportResponse)
async def generate_report(
    payload: GenerateReportRequest,
) -> GenerateReportResponse:
    """异步执行研报工作流并返回报告及评估指标。"""

    final_state = await graph.ainvoke(
        {"target_company": payload.target_company}
    )
    result = ReportTeamState.model_validate(final_state)
    if result.analysis_data is None:
        raise HTTPException(status_code=500, detail="商业分析结果缺失")

    return GenerateReportResponse(
        report_markdown=result.draft_report,
        revision_count=result.revision_count,
        threat_index=result.analysis_data.threat_index,
        output_file_path=result.output_file_path,
    )
