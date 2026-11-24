"""研报团队的五个异步 LangGraph 节点。"""

import asyncio
import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .config import BASE_DIR, PROMPTS, settings
from .schemas import CompetitorAnalysis, ReportTeamState, ReviewFeedback
from .tools import search_company_news

OUTPUT_DIR = BASE_DIR / "outputs"


@lru_cache(maxsize=1)
def _get_llm() -> ChatOpenAI:
    """延迟创建模型客户端，避免导入图时检查 API 密钥。"""

    return ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key or None,
        base_url=settings.openai_base_url,
        temperature=0,
    )


def _message_text(content: Any) -> str:
    """将模型消息内容统一转换为文本。"""

    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


def _analysis_json(state: ReportTeamState) -> str:
    """序列化上游分析结果。"""

    if state.analysis_data is None:
        return "null"
    return state.analysis_data.model_dump_json(indent=2)


async def searcher_node(state: ReportTeamState) -> dict[str, Any]:
    """搜索近期动态，并提炼为纯净事实材料。"""

    search_results = await search_company_news(state.target_company)
    messages = [
        SystemMessage(content=PROMPTS["intelligence_cleaner"]["system_prompt"]),
        HumanMessage(
            content=(
                f"目标公司：{state.target_company}\n"
                "请清洗并提炼以下检索结果，保留来源链接，删除重复和无关信息。\n"
                "如果 is_offline 为 true，必须明确说明数据未联网核验，"
                "不得把备用研究线索写成目标公司的事实。\n\n"
                f"{json.dumps(search_results, ensure_ascii=False, indent=2)}"
            )
        ),
    ]
    response = await _get_llm().ainvoke(messages)
    return {"clean_facts": _message_text(response.content)}


async def analyst_node(state: ReportTeamState) -> dict[str, Any]:
    """使用结构化输出强制生成 CompetitorAnalysis 对象。"""

    structured_llm = _get_llm().with_structured_output(
        CompetitorAnalysis,
        method="function_calling",
    )
    messages = [
        SystemMessage(content=PROMPTS["business_analyst"]["system_prompt"]),
        HumanMessage(
            content=(
                f"目标公司：{state.target_company}\n\n"
                f"清洗后的事实材料：\n{state.clean_facts}\n\n"
                "请输出竞争分析对象，威胁指数必须为 1-10 的整数。"
            )
        ),
    ]
    analysis = await structured_llm.ainvoke(messages)
    return {"analysis_data": analysis}


async def writer_node(state: ReportTeamState) -> dict[str, Any]:
    """撰写初稿，或根据质检意见重写并累计修订次数。"""

    is_revision = bool(
        state.draft_report and state.review_critique and not state.is_approved
    )
    if is_revision:
        task = (
            "这是被打回后的重写任务。请逐条落实质检意见，结合事实材料和"
            "分析结果重写完整研报，不要只输出修改片段。\n\n"
            f"当前草稿：\n{state.draft_report}\n\n"
            f"质检意见：\n{state.review_critique}"
        )
    else:
        task = "这是首轮写作任务，请撰写完整的中文 Markdown 研报初稿。"

    messages = [
        SystemMessage(content=PROMPTS["chief_writer"]["system_prompt"]),
        HumanMessage(
            content=(
                f"目标公司：{state.target_company}\n\n"
                f"事实材料：\n{state.clean_facts}\n\n"
                f"商业分析：\n{_analysis_json(state)}\n\n"
                f"{task}"
            )
        ),
    ]
    response = await _get_llm().ainvoke(messages)
    return {
        "draft_report": _message_text(response.content),
        "is_approved": False,
        "review_critique": "",
        "revision_count": state.revision_count + (1 if is_revision else 0),
    }


async def reviewer_node(state: ReportTeamState) -> dict[str, Any]:
    """严格审核数据完整性与事实一致性，并写入结构化审核结论。"""

    structured_llm = _get_llm().with_structured_output(
        ReviewFeedback,
        method="function_calling",
    )
    messages = [
        SystemMessage(content=PROMPTS["quality_editor"]["system_prompt"]),
        HumanMessage(
            content=(
                f"目标公司：{state.target_company}\n"
                f"当前修订次数：{state.revision_count}\n\n"
                f"事实材料：\n{state.clean_facts}\n\n"
                f"商业分析：\n{_analysis_json(state)}\n\n"
                f"待审核研报：\n{state.draft_report}\n\n"
                "重点检查关键数据是否完整、是否与来源一致、缺失信息是否明确标注，"
                "以及优势和痛点是否均有事实依据。请返回 ReviewFeedback。"
            )
        ),
    ]
    feedback = await structured_llm.ainvoke(messages)
    return {
        "is_approved": feedback.is_approved,
        "review_critique": feedback.critique,
    }


async def export_node(state: ReportTeamState) -> dict[str, Any]:
    """将当前定稿保存为 outputs 目录下的 Markdown 文件。"""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_company = re.sub(
        r'[<>:"/\\|?*]', "_", state.target_company
    ).strip(" .")[:80] or "company"
    filename = f"{safe_company}_{datetime.now():%Y%m%d_%H%M%S_%f}.md"
    output_path = Path(OUTPUT_DIR / filename)
    await asyncio.to_thread(
        output_path.write_text,
        state.draft_report,
        encoding="utf-8",
    )
    return {"output_file_path": str(output_path)}
