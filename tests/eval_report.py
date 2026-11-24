"""研报系统批量准确率评测脚本。

直接驱动 app.graph 工作流，对 10 个热门竞品做并发评测，统计三项硬指标：
1. 结构化契约解析率：analysis_data 是否为合法 CompetitorAnalysis 且威胁指数为 1-10 整数。
2. 事实忠实度：DeepSeek LLM-as-a-Judge 逐条核对研报与 clean_facts，发现编造直接 1 分。
3. 流程健康度：一次通过率（revision_count == 0）与平均修订轮次。

用法：
    .\\.venv\\Scripts\\python.exe tests\\eval_report.py
    .\\.venv\\Scripts\\python.exe tests\\eval_report.py --limit 2

可选环境变量：
    EVAL_CONCURRENCY    最大并发数，默认 3
    EVAL_TASK_TIMEOUT   单样本工作流超时秒数，默认 600
    EVAL_JUDGE_TIMEOUT  单样本裁判超时秒数，默认 180
"""

import argparse
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
import sys
import time
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from langchain_openai import ChatOpenAI  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app.graph import graph  # noqa: E402
from app.schemas import CompetitorAnalysis, ReportTeamState  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

COMPETITORS = [
    "Dify",
    "Coze",
    "LangGraph",
    "Notion AI",
    "Cursor",
    "RAGFlow",
    "Milvus",
    "Supabase",
    "Vercel",
    "Stripe",
]
CONCURRENCY = int(os.getenv("EVAL_CONCURRENCY", "3"))
TASK_TIMEOUT = float(os.getenv("EVAL_TASK_TIMEOUT", "600"))
JUDGE_TIMEOUT = float(os.getenv("EVAL_JUDGE_TIMEOUT", "180"))
SUMMARY_PATH = Path(__file__).resolve().parent / "report_eval_summary.json"


class FaithfulnessJudgeResult(BaseModel):
    """裁判结构化评分卡；核查结论位于评分之前以触发先核查后打分。"""

    hallucination_check: str = Field(
        description="逐条列岀研报中的具体数字、价格、客户数量和产品特性，并说明是否能在原始情报中找到依据"
    )
    is_fabricated: bool = Field(
        description="只要存在任何原始情报未提及的具体数据或功能描述，即为 True"
    )
    faithfulness_score: int = Field(
        ge=1,
        le=5,
        description="事实忠实度 1-5 分；is_fabricated 为 True 时必须为 1 分",
    )


def _build_judge() -> ChatOpenAI:
    """构造 temperature=0 的裁判模型。"""

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise SystemExit("FAIL: 未配置 OPENAI_API_KEY，无法运行 LLM-as-a-Judge")
    return ChatOpenAI(
        model=os.getenv("EVAL_JUDGE_MODEL", "deepseek-chat"),
        temperature=0,
        api_key=api_key,
        base_url=(
            os.getenv("OPENAI_API_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.deepseek.com/v1"
        ),
    )


async def _judge_faithfulness(
    judge: ChatOpenAI,
    company: str,
    clean_facts: str,
    report: str,
) -> tuple[FaithfulnessJudgeResult, int]:
    """对比原始情报与研报，返回裁判结论与最终得分。"""

    structured = judge.with_structured_output(
        FaithfulnessJudgeResult,
        method="function_calling",
    )
    prompt = f"""
    你是一名极其严谨的商业研报质量审计裁判。请严格对比【原始搜集情报】与【Agent 生成的最终研报】，审计事实忠实度。

    审计规则：
    1. 研报中出现的每一个价格、客户数量、融资金额、功能特性、合作方或时间点，必须全部来源于【原始搜集情报】。
    2. 只要发现一处原始情报未提及的具体数据或功能描述，即判定 is_fabricated = True，且 faithfulness_score 必须为 1。
    3. 若全部事实均有据可查，按 2-5 分评估；信息完整且无任何夸大取 5 分。
    4. 不要因为文笔流畅或结构完整而放宽事实标准。

    【目标公司】：{company}

    【原始搜集情报】：
    {clean_facts}

    【Agent 生成的最终研报】：
    {report}
    """
    result = await structured.ainvoke(prompt)
    score = 1 if result.is_fabricated else result.faithfulness_score
    return result, score


async def _evaluate_one(
    company: str,
    judge: ChatOpenAI,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """评测单个竞品，所有异常均转为结构化记录。"""

    async with semaphore:
        started = time.perf_counter()
        record: dict[str, Any] = {
            "company": company,
            "status": "failed",
            "contract_ok": False,
            "revision_count": None,
            "threat_index": None,
            "faithfulness_score": None,
            "is_fabricated": None,
            "hallucination_check": "",
            "judge_error": None,
            "output_file_ok": False,
            "error": None,
            "elapsed_seconds": 0.0,
        }
        try:
            state = await asyncio.wait_for(
                graph.ainvoke({"target_company": company}),
                timeout=TASK_TIMEOUT,
            )
            validated = ReportTeamState.model_validate(state)
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["elapsed_seconds"] = round(time.perf_counter() - started, 2)
            return record

        record["status"] = "completed"
        record["revision_count"] = validated.revision_count
        record["output_file_ok"] = bool(
            validated.output_file_path
            and Path(validated.output_file_path).is_file()
        )

        try:
            analysis = CompetitorAnalysis.model_validate(validated.analysis_data)
            threat_index = analysis.threat_index
            record["threat_index"] = threat_index
            record["contract_ok"] = (
                type(threat_index) is int and 1 <= threat_index <= 10
            )
        except Exception as exc:
            record["error"] = f"contract: {type(exc).__name__}: {exc}"
            record["elapsed_seconds"] = round(time.perf_counter() - started, 2)
            return record

        if validated.draft_report.strip():
            try:
                verdict, score = await asyncio.wait_for(
                    _judge_faithfulness(
                        judge,
                        company,
                        validated.clean_facts,
                        validated.draft_report,
                    ),
                    timeout=JUDGE_TIMEOUT,
                )
                record["faithfulness_score"] = score
                record["is_fabricated"] = verdict.is_fabricated
                record["hallucination_check"] = verdict.hallucination_check
            except Exception as exc:
                record["judge_error"] = f"{type(exc).__name__}: {exc}"

        record["elapsed_seconds"] = round(time.perf_counter() - started, 2)
        return record


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """按三项硬指标汇总评测结果。"""

    total = len(records)
    completed = [item for item in records if item["status"] == "completed"]
    contract_passed = [item for item in completed if item["contract_ok"]]
    scored = [
        item for item in completed if item["faithfulness_score"] is not None
    ]
    first_pass = [item for item in scored if item["revision_count"] == 0]
    fabricated = [item for item in scored if item["is_fabricated"]]

    def _rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator * 100, 2) if denominator else 0.0

    return {
        "total_samples": total,
        "completed_samples": len(completed),
        "failed_samples": total - len(completed),
        "contract_pass_count": len(contract_passed),
        "contract_parse_rate": _rate(len(contract_passed), total),
        "faithfulness_scored_count": len(scored),
        "judge_failed_count": len(completed) - len(scored),
        "avg_faithfulness_score": (
            round(sum(item["faithfulness_score"] for item in scored) / len(scored), 2)
            if scored
            else None
        ),
        "fabricated_count": len(fabricated),
        "first_pass_count": len(first_pass),
        "first_pass_rate": _rate(len(first_pass), len(scored)),
        "avg_revision_count": (
            round(
                sum(item["revision_count"] for item in scored) / len(scored),
                2,
            )
            if scored
            else None
        ),
    }


def _print_dashboard(metrics: dict[str, Any], records: list[dict[str, Any]]) -> None:
    """在终端打印汇总指标看板。"""

    def _value(value: Any, suffix: str = "") -> str:
        return "N/A" if value is None else f"{value}{suffix}"

    print("\n" + "=" * 62)
    print("  研报系统批量评测成绩单")
    print("=" * 62)
    print(
        f"  样本总数            {metrics['total_samples']}"
        f"（完成 {metrics['completed_samples']} / 失败 {metrics['failed_samples']}）"
    )
    print("-" * 62)
    print("  [指标一] 结构化契约解析率")
    print(
        f"      通过 {metrics['contract_pass_count']} / "
        f"{metrics['total_samples']}"
        f"  =>  {_value(metrics['contract_parse_rate'], '%')}"
    )
    print("  [指标二] 事实忠实度（LLM-as-a-Judge）")
    print(f"      平均得分  {_value(metrics['avg_faithfulness_score'], ' / 5')}")
    print(
        f"      已评分 {metrics['faithfulness_scored_count']} 条"
        f" / 裁判失败 {metrics['judge_failed_count']} 条"
        f" / 判定编造 {metrics['fabricated_count']} 条"
    )
    print("  [指标三] 流程健康度")
    print(
        f"      一次通过率  {_value(metrics['first_pass_rate'], '%')}"
        f"（{metrics['first_pass_count']} / {metrics['faithfulness_scored_count']}）"
    )
    print(f"      平均修订轮次  {_value(metrics['avg_revision_count'])}")
    print("-" * 62)
    print("  明细")
    for item in records:
        status = {
            "completed": "完成",
            "failed": "失败",
        }.get(item["status"], item["status"])
        score = (
            "-" if item["faithfulness_score"] is None
            else f"{item['faithfulness_score']}/5"
        )
        print(
            f"      {item['company']:<12} {status:<4} "
            f"契约={'是' if item['contract_ok'] else '否':<2} "
            f"威胁={str(item['threat_index']):<4} "
            f"修订={str(item['revision_count']):<2} "
            f"忠实度={score:<5} "
            f"耗时={item['elapsed_seconds']}s"
        )
        if item["error"]:
            print(f"          错误: {item['error']}")
        if item["judge_error"]:
            print(f"          裁判错误: {item['judge_error']}")
    print("=" * 62)


async def run_evaluation(companies: list[str], concurrency: int) -> dict[str, Any]:
    """并发运行全部样本并返回汇总结果。"""

    judge = _build_judge()
    semaphore = asyncio.Semaphore(concurrency)
    tasks = [
        asyncio.create_task(_evaluate_one(company, judge, semaphore))
        for company in companies
    ]

    records: list[dict[str, Any]] = []
    for completed_task in asyncio.as_completed(tasks):
        record = await completed_task
        records.append(record)
        score = record["faithfulness_score"]
        print(
            f"[{len(records)}/{len(companies)}] {record['company']}: "
            f"{record['status']}"
            + (f" | 忠实度={score}/5" if score is not None else "")
            + (f" | {record['error']}" if record["error"] else "")
        )

    order = {company: index for index, company in enumerate(companies)}
    records.sort(key=lambda item: order[item["company"]])
    metrics = _summarize(records)
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "concurrency": concurrency,
            "task_timeout_seconds": TASK_TIMEOUT,
            "judge_timeout_seconds": JUDGE_TIMEOUT,
            "judge_model": os.getenv("EVAL_JUDGE_MODEL", "deepseek-chat"),
            "samples": companies,
        },
        "metrics": metrics,
        "details": records,
    }
    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _print_dashboard(metrics, records)
    print(f"  详细日志已保存: {SUMMARY_PATH}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="研报系统批量准确率评测")
    parser.add_argument(
        "--limit",
        type=int,
        default=len(COMPETITORS),
        help="只评测前 N 个样本，用于快速冒烟",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=CONCURRENCY,
        help="最大并发数，默认 3",
    )
    args = parser.parse_args()

    companies = COMPETITORS[: max(1, args.limit)]
    concurrency = max(1, args.concurrency)
    print(
        f"开始评测 {len(companies)} 个竞品，最大并发 {concurrency}，"
        f"预计耗时较长，请耐心等待..."
    )
    summary = asyncio.run(run_evaluation(companies, concurrency))

    metrics = summary["metrics"]
    if metrics["failed_samples"] or metrics["contract_pass_count"] < len(companies):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
