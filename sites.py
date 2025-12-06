from typing import Dict, List

from crawl4ai.extraction_strategy import JsonCssExtractionStrategy

# 站点配置：可按需新增站点，只需提供 start_url/domains/css_schema/llm_instruction
SITE_CONFIG: Dict[str, Dict] = {
    "acura": {
        "start_url": "https://www.acurapartswarehouse.com",
        "domains": ["acurapartswarehouse.com", "www.acurapartswarehouse.com"],
        "css_schema": {
            "name": "Parts",
            "baseSelector": (
                "li.part-desc-layout, div.part-desc-layout, .pn-detail"
            ),
            "fields": [
                {
                    "name": "partnumber",
                    "selector": (
                        ".pd-ll-sub-n a, "
                        ".pn-spec-list tr:nth-child(2) td:last-child, "
                        ".pn-spec-list tr:nth-child(7) td:last-child, "
                        "[data-part-number]"
                    ),
                    "type": "text",
                    "default": "",
                },
                {
                    "name": "price",
                    "selector": ".price-section-price",
                    "type": "text",
                    "default": "",
                },
                {
                    "name": "image",
                    "selector": ".fpc-image img, .pn-img-img img",
                    "type": "attribute",
                    "attribute": "src",
                    "default": "",
                },
                {
                    "name": "name",
                    "selector": ".pd-ll-desc-url-n, h1.pn-detail-h1",
                    "type": "text",
                    "default": "",
                },
                {
                    "name": "description",
                    "selector": ".pd-ll-desc-url-n, h1.pn-detail-h1",
                    "type": "text",
                    "default": "",
                },
            ],
        },
        "llm_instruction": (
            "从内容中提取所有汽车零件，返回 items 数组，每项包含 partNumber, price, image, title, description。"
        ),
        "tracking_params": {"utm_source", "utm_medium", "utm_campaign", "gclid", "fbclid"},
        "pagination": {
            "pattern": r"Page\s+(\d+)\s+of\s+(\d+)",
            "param": "page",
            "max_jump": 20,  # 最大追加页数保护
        },
    },
}


def build_css_strategy(site_name: str):
    return JsonCssExtractionStrategy(SITE_CONFIG[site_name]["css_schema"])
