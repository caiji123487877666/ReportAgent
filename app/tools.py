"""联网检索工具及离线备用竞品情报。"""

import asyncio
from datetime import datetime, timezone
import time
from typing import Any

from ddgs import DDGS

SEARCH_TIMEOUT_SECONDS = 12
# ddgs 9.x 已禁用 google 引擎，grokipedia 会拖慢请求，这里只保留可用的文本后端。
SEARCH_BACKENDS = ("duckduckgo,yahoo,startpage,mojeek,brave",)

OFFLINE_COMPETITOR_INTEL: list[dict[str, str]] = [
    {
        "title": "产品与定位（离线备用）",
        "url": "",
        "snippet": (
            "通用竞品研究线索：优先核验目标公司的核心产品、目标客户、"
            "收费模式与差异化定位。本条目不是目标公司的已验证事实。"
        ),
        "source": "offline_fallback",
    },
    {
        "title": "竞争优势与痛点（离线备用）",
        "url": "",
        "snippet": (
            "通用竞品研究线索：可从功能完整度、价格、渠道、品牌、服务与"
            "合规能力分析优势，从成本、迁移门槛、稳定性和用户反馈识别痛点。"
            "本条目不是目标公司的已验证事实。"
        ),
        "source": "offline_fallback",
    },
    {
        "title": "近期动态（离线备用）",
        "url": "",
        "snippet": (
            "当前无法核验近期产品发布、融资、财报、合作或组织变化；"
            "恢复联网后应重新检索并补充可追溯来源。"
        ),
        "source": "offline_fallback",
    },
]


def _run_search(query: str, max_results: int) -> list[dict[str, Any]]:
    """依次尝试各搜索后端，单个后端失败后短暂重试一次。"""

    last_error: Exception | None = None
    for backend in SEARCH_BACKENDS:
        for attempt in range(2):
            try:
                with DDGS(timeout=SEARCH_TIMEOUT_SECONDS) as ddgs:
                    results = list(
                        ddgs.text(
                            query,
                            region="us-en",
                            safesearch="moderate",
                            timelimit="m",
                            max_results=max_results,
                            backend=backend,
                        )
                    )
                if results:
                    return results
                last_error = LookupError(f"{backend} 后端未返回搜索结果")
            except Exception as exc:
                last_error = exc
            if attempt == 0:
                time.sleep(1)

    raise last_error or LookupError("所有搜索后端均未返回结果")


def _offline_payload(company: str, query: str, reason: str) -> dict[str, Any]:
    """构造明确标记为未联网核验的备用情报。"""

    return {
        "company": company,
        "query": query,
        "source": "offline_fallback",
        "is_offline": True,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "warning": f"联网搜索不可用，已使用离线备用竞品情报：{reason}",
        "results": [
            {**item, "company": company, "verified": False}
            for item in OFFLINE_COMPETITOR_INTEL
        ],
    }


async def search_company_news(
    company: str,
    max_results: int = 5,
    timeout: float = 45,
) -> dict[str, Any]:
    """异步检索近期动态，失败、超时或无结果时返回离线备用情报。"""

    query = f"{company} 最新动态 产品 融资 财报 竞争"
    try:
        raw_results = await asyncio.wait_for(
            asyncio.to_thread(_run_search, query, max_results),
            timeout=timeout,
        )
        if not raw_results:
            raise LookupError("ddgs 未返回搜索结果")
    except Exception as exc:
        return _offline_payload(company, query, f"{type(exc).__name__}: {exc}")

    return {
        "company": company,
        "query": query,
        "source": "ddgs",
        "is_offline": False,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "warning": None,
        "results": [
            {
                "title": item.get("title", ""),
                "url": item.get("href", ""),
                "snippet": item.get("body", ""),
                "source": "ddgs",
                "company": company,
                "verified": False,
            }
            for item in raw_results
        ],
    }
