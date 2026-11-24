"""多智能体研报流程的数据结构与共享图状态。"""

from pydantic import BaseModel, Field


class CompetitorAnalysis(BaseModel):
    """商业数据分析师输出的竞争分析结果。"""

    positioning: str = Field(description="目标公司的市场定位")
    strengths: list[str] = Field(description="有事实依据的核心优势列表")
    pain_points: list[str] = Field(description="用户或业务痛点列表")
    threat_index: int = Field(
        ge=1,
        le=10,
        description="竞争威胁指数，1 分最低，10 分最高",
    )


class ReviewFeedback(BaseModel):
    """严苛质检主编输出的审核结论。"""

    is_approved: bool = Field(description="研报是否通过审核")
    critique: str = Field(description="审核意见；未通过时应包含可执行的修改建议")


class ReportTeamState(BaseModel):
    """在研报团队各图节点之间传递的共享状态。"""

    target_company: str = Field(default="", description="目标公司名称")
    clean_facts: str = Field(default="", description="清洗后的结构化事实材料")
    analysis_data: CompetitorAnalysis | None = Field(
        default=None,
        description="目标公司的商业分析结果",
    )
    draft_report: str = Field(default="", description="当前研报草稿")
    review_critique: str = Field(default="", description="最新一轮质检意见")
    is_approved: bool = Field(default=False, description="当前草稿是否通过质检")
    revision_count: int = Field(default=0, ge=0, description="草稿修订次数")
    output_file_path: str = Field(default="", description="最终研报文件路径")
