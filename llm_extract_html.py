"""
单页 LLM 解析脚本：读取本地 HTML，用与主爬虫一致的 LLM 配置进行零件抽取并输出 JSON。

用法示例：
  ~/.venv/bin/python llm_extract_html.py --html file.html --site acura
  cat file.html | ~/.venv/bin/python llm_extract_html.py --html -

依赖：
  - 环境变量 OPENAI_API_KEY 已配置
  - sites.py 中的站点配置（提示词/域名/Schema）
"""

import argparse
import json
import sys
from pathlib import Path

from openai import OpenAI

from sites import SITE_CONFIG


def load_html(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def build_prompt(instruction: str, html: str) -> str:
    schema_hint = (
        '请仅输出 JSON，结构为 {"items": [{"partnumber": "...", "price": "...", "image": "...", "title": "..."}]}，'
        "items 可以为一个空数组；如果某字段缺失，请用空字符串 \"\" 代替，不要省略 key，不要输出多余文字。"
    )

    return f"{instruction}\n{schema_hint}\nHTML 内容如下：\n```\n{html}\n```"


def main():
    parser = argparse.ArgumentParser(description="LLM extract from local HTML")
    parser.add_argument("--html", required=True, help="HTML 文件路径，或 - 表示从 stdin 读取")
    parser.add_argument("--site", default="acura", help="站点 key，定义于 sites.py")
    parser.add_argument("--api-key", help="OpenAI API Key，未提供时读取环境变量 OPENAI_API_KEY")
    args = parser.parse_args()

    if args.site not in SITE_CONFIG:
        raise ValueError(f"Unknown site: {args.site}")
    site_cfg = SITE_CONFIG[args.site]
    instruction = site_cfg.get(
        "llm_instruction",
        "从内容中提取所有汽车零件，返回 items 数组，每项包含 partnumber, price, image, title。",
    )

    html = load_html(args.html)
    prompt = build_prompt(instruction, html)

    if args.api_key:
        client = OpenAI(api_key=args.api_key)
    else:
        client = OpenAI()
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a precise information extraction engine."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        sys.stderr.write("LLM 返回非 JSON，原文输出:\n")
        print(content)
        sys.exit(1)

    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
