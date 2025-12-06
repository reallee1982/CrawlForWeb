import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from sites import SITE_CONFIG


class MockCrawler:
    def __init__(self, responses, llm_response):
        self.responses = responses
        self.llm_response = llm_response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def arun_many(self, urls, config=None):
        async def gen():
            for url in urls:
                yield self.responses[url]

        return gen()

    async def arun(self, url, config=None):
        return self.llm_response.get(url, SimpleNamespace(success=False, extracted_content=None))


def build_mock(site: str):
    start = SITE_CONFIG[site]["start_url"]
    second = start + "?page=2"
    third = start + "?page=3"
    css_first = SimpleNamespace(
        url=start,
        success=True,
        status_code=200,
        extracted_content=json.dumps(
            [
                {
                    "partnumber": "PN-001",
                    "price": "$1.00",
                    "image": "/img1.png",
                    "title": "Part One",
                }
            ]
        ),
        links={"internal": []},
        console_messages=[],
        network_requests=[],
        html="<div class='pagination-main'>Page 1 of 3</div>",
    )
    css_second = SimpleNamespace(
        url=second,
        success=True,
        status_code=200,
        extracted_content="",  # trigger LLM fallback
        links={"internal": []},
        console_messages=[],
        network_requests=[],
        html="<div class='pagination-main'>Page 2 of 3</div>",
    )
    llm_second = SimpleNamespace(
        url=second,
        success=True,
        status_code=200,
        extracted_content=json.dumps(
            {"items": [{"partnumber": "PN-002", "price": "$2.00", "image": "/img2.png", "title": "Part Two"}]}
        ),
        links={},
        console_messages=[],
        network_requests=[],
    )
    css_third = SimpleNamespace(
        url=third,
        success=True,
        status_code=200,
        extracted_content=json.dumps(
            [
                {
                    "partnumber": "PN-003",
                    "price": "$3.00",
                    "image": "/img3.png",
                    "title": "Part Three",
                }
            ]
        ),
        links={"internal": []},
        console_messages=[],
        network_requests=[],
        html="<div class='pagination-main'>Page 3 of 3</div>",
    )
    crawler = MockCrawler(
        responses={start: css_first, second: css_second, third: css_third},
        llm_response={second: llm_second},
    )
    return crawler


async def run_flow(tmpdir: Path, site: str):
    origin_output_dir = main.DEFAULT_OUTPUT_DIR
    origin_browser_cfg = main.browser_cfg
    origin_async_crawler = main.AsyncWebCrawler

    main.DEFAULT_OUTPUT_DIR = tmpdir
    mock_crawler = build_mock(site)
    main.AsyncWebCrawler = lambda config=None: mock_crawler  # type: ignore

    try:
        await main.crawl(site=site, max_depth=2, max_pages=10, batch_size=4, output_dir=tmpdir)
    finally:
        main.DEFAULT_OUTPUT_DIR = origin_output_dir
        main.browser_cfg = origin_browser_cfg
        main.AsyncWebCrawler = origin_async_crawler

    files = sorted((tmpdir / site).glob("*.jsonl"))
    assert files, "No output file generated"
    content = files[-1].read_text(encoding="utf-8").strip().splitlines()
    assert len(content) == 3, "Should collect three parts across pagination"
    rows = [json.loads(line) for line in content]
    assert rows[0]["partnumber"] == "PN-001"
    assert rows[1]["partnumber"] == "PN-002"
    assert rows[2]["partnumber"] == "PN-003"


def main_test():
    site = "acura"
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(run_flow(Path(tmp), site))
    print("mock crawl test passed for site:", site)


if __name__ == "__main__":
    main_test()
