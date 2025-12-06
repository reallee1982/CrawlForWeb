# Crawl4AI（基于 v0.7.4 官方文档）
https://docs.crawl4ai.com/
## 核心理念
- 开源、LLM 友好：输出干净 Markdown / 结构化数据，直接喂给 RAG、Agent 或数据管线。
- 高性能异步抓取：`AsyncWebCrawler` 驱动，支持并发、批量、可适配调度器。
- 数据民主化：无强制 API Key，提供丰富配置（缓存/会话/代理/指纹/多引擎）。
- 关注动态站点与防爬：可执行 JS、虚拟滚动、等待条件、模拟用户、stealth。
- 自适应爬取：可按查询需求智能跟踪链接并在“信息够用”时停止。

## 核心 SDK 组件
- `AsyncWebCrawler`：核心入口，推荐 `async with` 自动 start/close，也可手动 `start()/close()`.
- `BrowserConfig`（全局浏览器设置）：`browser_type`(chromium/firefox/webkit)、`headless`、`proxy_config`、`viewport`、`cookies/headers/user_agent/user_agent_mode`、`use_persistent_context`、`enable_stealth`、`text_mode/light_mode` 等。
- `CrawlerRunConfig`（单次运行设置，核心参数集中地）：
  - 缓存：`cache_mode`=`ENABLED|DISABLED|READ_ONLY|WRITE_ONLY|BYPASS`（默认 ENABLED）。
  - 内容过滤：`word_count_threshold`、`css_selector`/`target_elements`、`excluded_tags`、`remove_forms/overlay`、`exclude_external_links/images/social` 等。
  - 导航/时机：`wait_for="css:..."|"js:()"`、`delay_before_return_html`、`page_timeout`、`mean_delay/max_range`（批量节流）。
  - JS 交互：`js_code`（列表或单串），`js_only` 续会话执行。
  - 媒体：`screenshot`/`pdf`/`capture_mhtml`、`image_score_threshold` 等。
  - 会话：`session_id` 复用上下文；支持 `kill_session` 清理。
  - 抗检测：`magic`、`simulate_user`、`override_navigator`。
  - 抓取记录：`capture_network_requests`、`capture_console_messages`。
  - 提取：`extraction_strategy`（CSS/XPath/Regex/LLM）。
- `LLMConfig`：`provider`（如 `openai/gpt-4o`、`ollama/llama3.3` 等）、`api_token`（可 `env:` 取环境变量）、`base_url`、`extra_args`。
- `CrawlResult`：`html/cleaned_html/markdown`（含 `fit_markdown`）、`extracted_content`、`media`、`links`、`screenshot/pdf/mhtml`、`metadata`、`network_requests`、`console_messages`、`dispatch_result`、`success/status_code/error_message/session_id`。

## 关键用法与模式
### 单页基础流程
```python
import asyncio
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

async def main():
    browser = BrowserConfig(headless=True)
    run = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, css_selector="main")
    async with AsyncWebCrawler(config=browser) as crawler:
        result = await crawler.arun("https://example.com", config=run)
        print(result.markdown.raw_markdown[:200])

asyncio.run(main())
```
要点：用 `BrowserConfig` 放浏览器级设置，用 `CrawlerRunConfig` 放本次抓取逻辑；避免旧版直接给 `arun()` 传零散参数。

### 多 URL 并发 `arun_many`
- 默认 `MemoryAdaptiveDispatcher` 按内存自适应并发；可换 `SemaphoreDispatcher`。
- `stream=True` 用 `async for` 流式消费；`stream=False` 返回列表。
- 支持为不同 URL 配置 `url_matcher` 的 `CrawlerRunConfig` 列表（glob/函数匹配，按顺序第一命中生效）。

### 自适应抓取
- `AdaptiveCrawler(crawler).digest(start_url, query=...)`：自动挑选链接、计算置信度、信息够用就停，适合问答式抓取。

### 会话与步骤化交互
- `session_id` 复用页面（登录后续页、分页滚动等），配合 `js_only=True` 连续操作；结束后调用 `kill_session` 释放。
- JS 交互：`js_code` 列表 + `wait_for`（css/js 条件）应对动态加载、点击“更多”等。

### 缓存策略
- 新鲜数据：`CacheMode.BYPASS`；重复调试或离线回放：`ENABLED/READ_ONLY`；避免写入：`READ_ONLY`；仅写不读：`WRITE_ONLY`。

## 内容处理与 Markdown
- 默认 `DefaultMarkdownGenerator` 将 HTML 转 Markdown，可选内容滤器。
- 过滤器：`PruningContentFilter(threshold=...)`、`BM25ContentFilter`、`LLMContentFilter`；使用过滤器时 `result.markdown.fit_markdown/fit_html` 代表“精简版”文本。
- 链接引用：可开启引用格式的 `markdown_with_citations` + `references_markdown`。

## 数据提取策略
- **Schema 直提（推荐优先）**：`JsonCssExtractionStrategy` / `JsonXPathExtractionStrategy`，使用 `baseSelector/fields/baseFields` 定义结构；一次性用 `generate_schema`（OpenAI 或 Ollama）生成 schema 后批量复用。
- **正则提取**：`RegexExtractionStrategy` 内置邮箱/电话/URL/日期等模式，快速低成本。
- **LLM 提取**：`LLMExtractionStrategy`（成本高但灵活）关键参数：
  - `extraction_type="schema"` 或 `"block"`；`schema` 建议用 Pydantic `model_json_schema()`.
  - `instruction` 明确输出 JSON 结构；`input_format="markdown"|"fit_markdown"|"html"`。
  - 大文本控制：`chunk_token_threshold`、`overlap_rate`、`apply_chunking`; 用 `show_usage()` 观察 token。
  - 若页面结构稳定，优先用 CSS/XPath/Regex，LLM 仅在需要理解/重组时使用。

## 页面交互与动态加载
- 等待：`wait_for="css:..."` 或 `wait_for="js:() => ..."`；必要时 `delay_before_return_html`。
- 滚动/点击：`js_code` 执行自定义脚本；虚拟滚动配置用于懒加载。
- 截图/PDF：`screenshot`、`pdf` 可用于调试或归档。

## 链接与媒体
- `links["internal"/"external"]`、`media["images"/"videos"/...]` 自动提取；可用 `exclude_external_links/images/social`、`exclude_domains` 做域名过滤。
- 链接打分（intrinsic/contextual）可用于推荐/导航优先级；`image_score_threshold` 过滤低价值图片。

## 最佳实践（踩坑清单）
- 生命周期：优先 `async with`，长驻服务再用手动 `start/close`。
- 配置分层：浏览器级放 `BrowserConfig`，单次爬取放 `CrawlerRunConfig`，避免遗留裸参数。
- 兼容 robots 与伦理：`check_robots_txt=True`，合理设置并发与节流（`semaphore_count`、`mean_delay`）。
- 动态页：总是配合 `wait_for`/`js_code`/滚动；必要时 `simulate_user`、`enable_stealth`、自定义 UA/代理。
- 会话：登录/多步流程务必用 `session_id`；认证逻辑可放 hook（`on_page_context_created` 等）避免重复登录。
- 提取策略：优先 CSS/XPath/Regex，其次 LLM；LLM 场景要控制 chunk，Pydantic 校验输出，关注 token 成本。
- Markdown 体积：设置 `word_count_threshold`，必要时加 `Pruning/BM25` 获取更短的 `fit_markdown`。
- 并发：批量爬取时监控资源，必要时改用 `MemoryAdaptiveDispatcher` 或降低 `semaphore_count`；错误处理前先检查 `result.success`/`status_code`。
- 性能：静态站点可开 `text_mode`、关闭图片；动态站点用精准 `wait_for` 而非固定延时，减少空耗；合理使用缓存避免重复抓取。
- 可观察性：调试期开启 `verbose`、`capture_network_requests`、`capture_console_messages`，结合 `screenshot/pdf` 快速定位空白页/403/前端报错。
- 稳定性：遇到 429/限流时降低并发并增加 `mean_delay`；必要时切换代理或 UA。

## 典型案例模板
- 静态文档 → RAG：`CacheMode.ENABLED` + `word_count_threshold` + `css_selector` 指向正文；`PruningContentFilter` 获取短文本，直接喂入向量库。
- 新闻/博客无限滚动：`js_code` 滚动 + `wait_for` 内容选择器 + 适度 `delay_before_return_html`；如需多页，用 `session_id` 连续执行 `js_only=True`。
- 登录后抓取：在 `on_page_context_created` hook 登录或设置 cookie；后续请求复用同一 `session_id`；结束调用 `kill_session`。
- 大批量商品页：`arun_many(stream=True)` + CSS schema 提取；`CacheMode.BYPASS` 获取最新价格，或 `ENABLED` 做增量；`semaphore_count` 控制并发。
- 复杂页面数据提取（半结构）：先试 `RegexExtractionStrategy` 抽邮箱/电话/URL；再用 `JsonCssExtractionStrategy` 提取列表；最后才用 `LLMExtractionStrategy` 做清洗/重组，设置 `input_format="fit_markdown"` 控制 token。
- API/JSON 端点：`scraping_strategy` 设为 JSON/PDF 处理策略（如官方示例），跳过 Markdown 转换；`cache_mode=READ_ONLY` 可离线复跑。

## 速查代码片段
### 多 URL 流式抓取
```python
urls = ["https://a.com", "https://b.com"]
cfg = CrawlerRunConfig(stream=True, cache_mode=CacheMode.BYPASS)
async with AsyncWebCrawler() as crawler:
    async for r in await crawler.arun_many(urls, config=cfg):
        print(r.url, r.success, len(r.markdown.raw_markdown) if r.success else r.error_message)
```
### CSS Schema 提取
```python
schema = {
    "name": "Articles",
    "baseSelector": "article.post",
    "fields": [
        {"name": "title", "selector": "h2", "type": "text"},
        {"name": "url", "selector": "a", "type": "attribute", "attribute": "href"},
    ],
}
run = CrawlerRunConfig(extraction_strategy=JsonCssExtractionStrategy(schema))
async with AsyncWebCrawler() as crawler:
    res = await crawler.arun("https://example.com/blog", config=run)
    print(res.extracted_content)
```

以上内容可作为后续爬虫项目的速览与实践准则。需要更细参数时，按模块再查官方参考页（Complete SDK Reference）。

## 配置参数速览
### BrowserConfig（浏览器级）
- 核心：`browser_type`(chromium/firefox/webkit)、`headless`、`viewport_width/height`、`user_agent`/`user_agent_mode`、`proxy_config`、`cookies/headers`。
- 性能/轻量：`text_mode=True`（禁图）、`light_mode=True`、`extra_args`。
- 防爬：`enable_stealth`；必要时配合 run config 的 `override_navigator/simulate_user`。
- 状态复用：`use_persistent_context` + `user_data_dir`；或结合 `session_id`。
- 下载：`accept_downloads`、`downloads_path`（需要落盘文件时开启）。

### CrawlerRunConfig（单次抓取级）
- 缓存：`cache_mode`=ENABLED|DISABLED|READ_ONLY|WRITE_ONLY|BYPASS；也可用别名 `bypass_cache/disable_cache/...`。
- 内容：`word_count_threshold`、`css_selector/target_elements`、`excluded_tags`、`remove_forms/overlay`、`exclude_external_links/images/social`、`image_score_threshold`。
- 导航/等待：`wait_for="css:..."|"js:() => ..."`、`delay_before_return_html`、`page_timeout`、`mean_delay/max_range`（批量节流）。
- JS/交互：`js_code`（列表或字符串）、`js_only`、虚拟滚动配置。
- 媒体输出：`screenshot`、`screenshot_wait_for`、`pdf`、`capture_mhtml`。
- 会话：`session_id` 复用 tab；结束后可 `kill_session`。
- 抗检测：`magic`、`simulate_user`、`override_navigator`、`user_agent_mode` 随机化。
- 观测：`verbose`、`capture_network_requests`、`capture_console_messages`、`display_mode`（批量进度）。
- 提取/生成：`extraction_strategy`（CSS/XPath/Regex/LLM）、`markdown_generator`（可带 Pruning/BM25/LLM 过滤器）、`scraping_strategy`（PDF/JSON 特殊处理）。
- 并发/调度：`stream`（流式）、`semaphore_count`、`url_matcher/match_mode`（用于 arun_many 配置路由），`dispatcher` 在 `arun_many` 里传入。

### LLMConfig（供 LLMExtractionStrategy / LLMContentFilter）
- `provider`（如 `openai/gpt-4o`、`ollama/llama3.3`、`gemini/...`）、`api_token`（可用 `env:OPENAI_API_KEY`）、`base_url`。
- `extra_args`（温度、max_tokens 等）；大文本分块在策略层用 `chunk_token_threshold/overlap_rate/apply_chunking`，输入格式用 `input_format` 控制（markdown/fit_markdown/html）。
