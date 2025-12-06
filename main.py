import argparse
import asyncio
import json
import logging
import pathlib
from datetime import datetime
from collections import deque
from typing import Dict, List, Tuple
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    LLMConfig,
)
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
from crawl4ai.extraction_strategy import LLMExtractionStrategy

from sites import SITE_CONFIG, build_css_strategy
import re

# --------------- 默认参数 ---------------
DEFAULT_SITE = "acura"
DEFAULT_MAX_DEPTH = 8
DEFAULT_MAX_PAGES = 100
DEFAULT_BATCH_SIZE = 8
DEFAULT_OUTPUT_DIR = pathlib.Path("output")

# 抗爬 & 浏览器配置
browser_cfg = BrowserConfig(
    headless=False,
    user_agent_mode="random",
    enable_stealth=True,
    viewport_width=1280,
    viewport_height=720,
    text_mode=False,  # 如需极致性能可改 True（禁图）
)

# LLM schema（通用）
llm_schema = {
    "title": "PartsList",
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "partNumber": {"type": "string"},
                    "price": {"type": "string"},
                    "image": {"type": "array", "items": {"type": "string"}},
                    "description": {"type": "string"},
                    "title": {"type": "string"}
                },
                "required": ["partNumber"],
            },
        }
    },
    "required": ["items"],
}

# 日志
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("acura_crawler")


# --------------- 工具函数 ---------------
def same_domain(url: str, domains: set) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    netloc = parsed.netloc.lower()
    if netloc in domains:
        return True
    return any(netloc.endswith(f".{host}") for host in domains)


def normalize_url(url: str, tracking_params: set) -> str:
    """标准化 URL 以便去重：去掉 fragment，统一域名大小写，保留业务 query，过滤追踪参数。"""
    parsed = urlparse(url)
    filtered_query = []
    for k, v in parse_qsl(parsed.query, keep_blank_values=True):
        if k.lower() in tracking_params:
            continue
        filtered_query.append((k, v))
    new_query = urlencode(filtered_query)
    return (
        parsed._replace(
            fragment="",
            query=new_query,
            netloc=parsed.netloc.lower(),
        ).geturl()
    )


def normalize_items(items: List[Dict], base_url: str = "") -> List[Dict]:
    out = []
    for item in items or []:
        part = item.get("partnumber", "").strip()
        price = item.get("price", "").strip()
        image = item.get("image", "").strip()
        if base_url and image:
            image = urljoin(base_url, image)
        title = item.get("title", "").strip()
        if not part:
            continue
        out.append(
            {
                "partnumber": part,
                "price": price,
                "image": image,
                "title": title,
            }
        )
    return out


def enqueue_pagination(
    res,
    depth: int,
    max_depth: int,
    queue: deque,
    queued: set,
    visited: set,
    tracking_params: set,
    pagination_cfg: Dict,
):
    logger.info(
        "enqueue_pagination depth=%s tracking_params=%s visited=%s",
        depth,
        tracking_params,
        visited
    )
    """根据站点分页配置，按 ?param= 构造后续页 URL 入队。"""
    if not pagination_cfg:
        return
    if depth >= max_depth:
        return
    pattern = pagination_cfg.get("pattern")
    param = pagination_cfg.get("param", "page")
    max_jump = pagination_cfg.get("max_jump", 50)
    if not pattern:
        return
    html_text = getattr(res, "html", None) or getattr(res, "cleaned_html", None) or ""
    m = re.search(pattern, html_text, re.I)
    if not m:
        return
    current, total = int(m.group(1)), int(m.group(2))
    if current >= total:
        return
    base = urlparse(res.url)
    added = 0
    for p in range(current + 1, total + 1):
        if added >= max_jump:
            break
        qs = dict(parse_qsl(base.query, keep_blank_values=True))
        qs[param] = str(p)
        new_query = urlencode(qs)
        next_url = base._replace(query=new_query).geturl()
        next_url = normalize_url(next_url, tracking_params)
        if next_url in visited or next_url in queued:
            continue
        queued.add(next_url)
        queue.append((next_url, depth + 1))
        added += 1


def save_jsonl(path: pathlib.Path, records: List[Dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def summarize_stats(page_stats: List[Dict], field_missing: Dict[str, int]) -> Dict[str, object]:
    total = len(page_stats)
    success = sum(1 for p in page_stats if p["success"])
    failed = total - success
    css_hit = sum(1 for p in page_stats if p["css_hit"])
    llm_hit = sum(1 for p in page_stats if p["llm_hit"])
    empty = sum(1 for p in page_stats if p["empty"])
    console_err_pages = sum(1 for p in page_stats if p["console_errors"] > 0)
    network_err_pages = sum(1 for p in page_stats if p["network_errors"] > 0)

    status_dist: Dict[str, int] = {}
    for p in page_stats:
        status = str(p.get("status_code"))
        status_dist[status] = status_dist.get(status, 0) + 1

    summary = {
        "pages_total": total,
        "pages_success": success,
        "pages_failed": failed,
        "css_hit_pages": css_hit,
        "llm_hit_pages": llm_hit,
        "empty_pages": empty,
        "collected_items_total": sum(p["items_count"] for p in page_stats),
        "failed_pages_ratio": failed / total if total else 0,
        "css_hit_ratio": css_hit / total if total else 0,
        "llm_ratio": llm_hit / total if total else 0,
        "status_distribution": status_dist,
        "console_error_pages": console_err_pages,
        "network_error_pages": network_err_pages,
        "field_missing": field_missing,
    }
    return summary


# --------------- 核心流程 ---------------
async def crawl(
    site: str = DEFAULT_SITE,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_pages: int = DEFAULT_MAX_PAGES,
    batch_size: int = DEFAULT_BATCH_SIZE,
    output_dir: pathlib.Path = DEFAULT_OUTPUT_DIR,
):
    if site not in SITE_CONFIG:
        raise ValueError(f"Unknown site: {site}")

    site_cfg = SITE_CONFIG[site]
    start_url = site_cfg["start_url"]
    domains = set(site_cfg["domains"])
    tracking_params = set(site_cfg.get("tracking_params", []))
    css_strategy = build_css_strategy(site)
    pagination_cfg = site_cfg.get("pagination", {})

    llm_instruction = site_cfg.get(
        "llm_instruction",
        "从内容中提取所有汽车零件，返回 items 数组，每项包含 partnumber, price, image, title。",
    )

    llm_strategy = LLMExtractionStrategy(
        llm_config=LLMConfig(
            provider="openai/gpt-4o-mini",
            api_token="env:OPENAI_API_KEY",
        ),
        schema=llm_schema,
        extraction_type="schema",
        instruction=llm_instruction,
        chunk_token_threshold=8000, # 控制多长的文本要被“切块（chunk）”
        overlap_rate=0.1,
        apply_chunking=True, # 自动切块机制
        input_format="markdown",
        verbose=True,
    )

    start = normalize_url(start_url, tracking_params)
    queue: deque[Tuple[str, int]] = deque([(start, 0)])
    visited = set()
    queued = {start}
    processed = 0
    collected: List[Dict] = []
    seen_parts = set()
    page_stats: List[Dict] = []
    field_missing = {"price": 0, "image": 0, "title": 0}
    failed_pages_details: List[Dict] = []
    css_fail_details: List[Dict] = []
    llm_fail_details: List[Dict] = []
    console_error_details: List[Dict] = []
    network_error_details: List[Dict] = []

    run_cfg_css = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        wait_for="css:body",
        extraction_strategy=css_strategy,
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter()
        ),
        simulate_user=True,
        override_navigator=True,
        check_robots_txt=True,
        capture_network_requests=True,
        capture_console_messages=True,
        screenshot=False,  # 调试可改 True
        pdf=False,  # 调试可改 True
        stream=False,
        semaphore_count=1,
        mean_delay=1.0,
        max_range=1.5,
    )

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        while queue and processed < max_pages:
            batch = []
            while queue and len(batch) < batch_size:
                url, depth = queue.popleft()
                if depth > max_depth or url in visited:
                    continue
                batch.append((url, depth))

            if not batch:
                break

            batch_map = dict(batch)
            urls = list(batch_map.keys())
            results = await crawler.arun_many(urls, config=run_cfg_css)

            async def process_one(res):
                nonlocal processed
                req_url = getattr(res, "request_url", res.url)
                req_url_norm = normalize_url(req_url, tracking_params)
                depth = batch_map.get(req_url_norm, 0)
                processed += 1
                visited.add(req_url_norm)
                page_stat = {
                    "url": res.url,
                    "request_url": req_url,
                    "depth": depth,
                    "success": res.success,
                    "status_code": res.status_code,
                    "css_hit": False,
                    "llm_hit": False,
                    "empty": False,
                    "items_count": 0,
                    "console_errors": 0,
                    "network_errors": 0,
                }
                logger.info(
                    "Fetched depth=%s url=%s success=%s status=%s",
                    depth,
                    res.url,
                    res.success,
                    res.status_code,
                )
                if not res.success:
                    logger.warning(
                        "Crawl failed depth=%s url=%s status=%s error=%s",
                        depth,
                        res.url,
                        res.status_code,
                        getattr(res, "error_message", None),
                    )
                    failed_pages_details.append(
                        {
                            "url": res.url,
                            "depth": depth,
                            "status_code": res.status_code,
                            "error": getattr(res, "error_message", None),
                        }
                    )

                items = []
                if res.success and res.extracted_content:
                    try:
                        items = normalize_items(json.loads(res.extracted_content), res.url)
                        page_stat["css_hit"] = len(items) > 0
                    except Exception as e:
                        items = []
                        css_fail_details.append(
                            {"url": res.url, "depth": depth, "error": str(e)}
                        )

                if res.success and not items:
                    fallback_cfg = run_cfg_css.clone(
                        extraction_strategy=llm_strategy,
                        cache_mode=CacheMode.BYPASS,
                    )
                    fallback_cfg.stream = False
                    try:
                        llm_res = await crawler.arun(url=res.url, config=fallback_cfg)
                        if llm_res.success and llm_res.extracted_content:
                            data = json.loads(llm_res.extracted_content)
                            items = normalize_items(data.get("items", []), res.url)
                            logger.info("LLM fallback extracted %s items from %s", len(items), res.url)
                            page_stat["llm_hit"] = len(items) > 0
                    except Exception as e:
                        logger.warning("LLM fallback failed on %s: %s", res.url, e)
                        llm_fail_details.append(
                            {"url": res.url, "depth": depth, "error": str(e)}
                        )

                for item in items:
                    part_key = item.get("partnumber")
                    if part_key in seen_parts:
                        continue
                    seen_parts.add(part_key)
                    collected.append({"source": res.url, **item})
                    if not item.get("price"):
                        field_missing["price"] += 1
                    if not item.get("image"):
                        field_missing["image"] += 1
                    if not item.get("title"):
                        field_missing["title"] += 1

                page_stat["items_count"] = len(items)
                page_stat["empty"] = res.success and len(items) == 0

                if res.console_messages:
                    err_msgs = [
                        {"type": getattr(msg, "type", ""), "text": getattr(msg, "text", "")}
                        for msg in res.console_messages
                        if getattr(msg, "type", "").lower() == "error"
                    ]
                    page_stat["console_errors"] = len(err_msgs)
                    if err_msgs:
                        console_error_details.append({"url": res.url, "errors": err_msgs})
                if res.network_requests:
                    err_reqs = [
                        {
                            "url": getattr(req, "url", ""),
                            "status": getattr(req, "status", None),
                            "method": getattr(req, "method", ""),
                        }
                        for req in res.network_requests
                        if getattr(req, "status", 200) >= 400
                    ]
                    page_stat["network_errors"] = len(err_reqs)
                    if err_reqs:
                        network_error_details.append({"url": res.url, "requests": err_reqs})

                page_stats.append(page_stat)

                if res.success and res.links:
                    for link in res.links.get("internal", []):
                        href = link.get("href")
                        if not href:
                            continue
                        abs_url = urljoin(res.url, href)
                        abs_url = normalize_url(abs_url, tracking_params)
                        if same_domain(abs_url, domains) and abs_url not in visited and abs_url not in queued:
                            queued.add(abs_url)
                            queue.append((abs_url, depth + 1))
                if res.success:
                    enqueue_pagination(
                        res,
                        depth,
                        max_depth,
                        queue,
                        queued,
                        visited,
                        tracking_params,
                        pagination_cfg,
                    )

            if hasattr(results, "__aiter__"):
                async for res in results:
                    try:
                        await process_one(res)
                    except Exception as e:
                        logger.warning("Process page failed url=%s error=%s", getattr(res, "url", ""), e)
            else:
                for res in results:
                    try:
                        await process_one(res)
                    except Exception as e:
                        logger.warning("Process page failed url=%s error=%s", getattr(res, "url", ""), e)

        # 落盘
        domain = urlparse(start_url).netloc.lower().replace(":", "_")
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_path = output_dir / site / f"{domain}-{ts}.jsonl"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        save_jsonl(output_path, collected)
        logger.info("Done. visited=%s, collected=%s, output=%s", len(visited), len(collected), output_path)
        summary = summarize_stats(page_stats, field_missing)
        summary_path = output_dir / site / f"{domain}-summary-{ts}.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary["site"] = site
        summary["start_url"] = start_url
        summary["timestamp"] = ts
        summary["failed_pages_details"] = failed_pages_details
        summary["css_fail_details"] = css_fail_details
        summary["llm_fail_details"] = llm_fail_details
        summary["console_error_details"] = console_error_details
        summary["network_error_details"] = network_error_details

        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Summary: %s", summary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-site parts crawler")
    parser.add_argument("--site", default=DEFAULT_SITE, help="Site key defined in sites.py")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--output-dir", type=pathlib.Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    asyncio.run(
        crawl(
            site=args.site,
            max_depth=args.max_depth,
            max_pages=args.max_pages,
            batch_size=args.batch_size,
            output_dir=args.output_dir,
        )
    )
