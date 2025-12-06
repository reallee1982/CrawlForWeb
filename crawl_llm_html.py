"""
使用 crawl4ai 的 LLMExtractionStrategy 直接解析本地 HTML，输出 JSON 结果。

用法示例：
  ~/.venv/bin/python crawl_llm_html.py --site acura --html file.html
  cat file.html | ~/.venv/bin/python crawl_llm_html.py --site acura --html -
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from crawl4ai.extraction_strategy import LLMExtractionStrategy
from crawl4ai import LLMConfig

from main import llm_schema  # 复用与主爬虫一致的 schema
from sites import SITE_CONFIG


def load_html(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


async def run(
    html: str,
    site: str,
    input_format: str = "html",
    chunk_token_threshold: int = 8000,
    overlap_rate: float = 0.1,
):
    if site not in SITE_CONFIG:
        raise ValueError(f"Unknown site: {site}")
    site_cfg = SITE_CONFIG[site]
    instruction = site_cfg.get(
        "llm_instruction",
        "从内容中提取所有汽车零件，返回 items 数组，每项包含 partNumber, price, image, title, description。",
    )

    llm_strategy = LLMExtractionStrategy(
        llm_config=LLMConfig(
            # provider="openai/gpt-4o-mini",
            provider="openai/gpt-4.1",
            api_token="env:OPENAI_API_KEY",
        ),
        schema=llm_schema,
        extraction_type="schema",
        instruction=instruction,
        input_format=input_format,
        apply_chunking=True,
        chunk_token_threshold=chunk_token_threshold,
        overlap_rate=overlap_rate,
        verbose=False,
    )

    # 使用 arun 以触发 chunk 拆分（sections 列表可包含一段长 HTML，将按阈值拆分）
    result = await llm_strategy.arun("local://input", [html])
    usage = {
        "prompt_tokens": llm_strategy.total_usage.prompt_tokens,
        "completion_tokens": llm_strategy.total_usage.completion_tokens,
        "total_tokens": llm_strategy.total_usage.total_tokens,
    }
    return result, usage


def main():
    parser = argparse.ArgumentParser(description="LLM extract from HTML via crawl4ai LLMExtractionStrategy")
    parser.add_argument("--html", required=True, help="HTML 文件路径，或 - 表示从 stdin 读取")
    parser.add_argument("--site", default="acura", help="站点 key，定义于 sites.py")
    parser.add_argument("--input-format", default="html", choices=["html", "markdown", "fit_markdown", "raw_markdown"])
    parser.add_argument("--chunk-threshold", type=int, default=8000, help="chunk_token_threshold")
    parser.add_argument("--overlap-rate", type=float, default=0.1, help="chunk overlap rate")
    parser.add_argument("--output", type=Path, help="输出文件路径；未提供则按脚本名+站点+时间戳生成 JSON")
    args = parser.parse_args()

    html = load_html(args.html)
    data, usage = asyncio.run(
        run(
            html,
            args.site,
            args.input_format,
            chunk_token_threshold=args.chunk_threshold,
            overlap_rate=args.overlap_rate,
        )
    )

    if args.output:
        out_path = args.output
    else:
        ts = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = Path("output") / f"{Path(__file__).stem}-{args.site}-{ts}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"items": data, "usage": usage}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}, usage={usage}")


if __name__ == "__main__":
    main()
