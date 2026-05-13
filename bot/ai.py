"""Phase 2: Analyze user requirements with DeepSeek.

Receives the structure fingerprint + user's natural language request,
returns a structured cleaning recipe.
"""

import json
import logging
import time

from openai import OpenAI, APIError, APITimeoutError, APIConnectionError

from .config import get_config

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是一个小说TXT文件处理专家。用户会提供：
1. 小说文件的结构指纹（章节模式、包裹字符、重复情况、广告检测等）
2. 用户的自然语言需求描述

请分析并输出一个严格的JSON，描述需要的处理操作：

{
  "strip_wrappers": true,          // 是否去除章节标题的包裹字符(===, #等)
  "strip_ad_suffixes": true,       // 是否去除章节标题中的广告后缀
  "deduplicate_chapters": true,    // 是否去除重复章节
  "ad_actions": {                  // 广告分级处理决策
    "http_links": "delete",        // delete|keep|standalone_only|strip_title
    "qq_groups": "delete",
    "email": "delete",
    "wechat": "standalone_only",
    "group_invite": "standalone_only",
    "promo": "standalone_only",
    "begging": "strip_title"
  },
  "chapter_pattern": "第\\\\d+章",  // 章节匹配正则(用于kaf-cli)
  "format": "epub",               // epub/mobi/azw3/all
  "bookname": null,               // 书名(null=自动从文件名取)
  "author": null,                 // 作者(null=默认)
  "lang": "zh",                   // 语言
  "extra_replacements": [],       // 额外正则替换
  "reasoning": "分析过程简述"       // 一句话说明你的判断依据
}

## 广告分级处理规则

指纹中"📢 广告内容检测"列出了采样行中的疑似广告，分为三个可信度等级：

| 等级 | 类型 | 判断依据 |
|------|------|----------|
| L1 | URL/QQ群号+数字/邮箱 | 正文几乎不可能出现 → 默认delete |
| L2 | 微信/公众号/群引导/推广用语 | 可能出现在角色对话中 → 默认standalone_only（仅独立成行时删除） |
| L3 | 求票/求订阅 | 作者章末留言常见 → 默认strip_title（仅标题去后缀） |

位置信息说明：
- standalone: 前后有空行，可能是独立广告
- near_chapter: 在章节标题±2行内，可能是作者留言
- embedded: 嵌入段落中，可能是角色对话

决策指南：
- L1 → 几乎肯定 delete
- L2 standalone多 → standalone_only (独立成行才删，对话中保留)
- L2 embedded多 → keep (可能是剧情，保留)
- L3 near_chapter多 → strip_title (只在标题去后缀)
- L3 standalone多 → 可设 delete (整行删)
- 用户明确说"保留广告"/"不删"/"别删" → 对应项设 keep

## 通用规则
- 用户只说"转epub"没提清理 → 根据指纹判断默认清理
- 明显的包裹字符(===, #, ---) → 默认strip_wrappers=true
- 重复章节 → 默认deduplicate_chapters=true
- chapter_pattern要对应用户小说的实际章节格式
- 只输出JSON，不要markdown代码块包裹"""


def _get_client() -> OpenAI:
    config = get_config()
    ds = config.get("deepseek", {})
    return OpenAI(
        api_key=ds.get("api_key", ""),
        base_url=ds.get("base_url", "https://api.deepseek.com"),
    )


def analyze(fingerprint_prompt: str, user_request: str) -> dict:
    """Send fingerprint + user request to DeepSeek, return cleaning recipe.
    
    Retries up to 2 times on network/transient errors with exponential backoff.
    """
    config = get_config()
    ds = config.get("deepseek", {})
    model = ds.get("model", "deepseek-chat")
    timeout = int(ds.get("timeout", 60))
    max_retries = int(ds.get("max_retries", 2))

    client = _get_client()

    user_message = f"""## 小说结构指纹
{fingerprint_prompt}

## 用户需求
{user_request}"""

    retryable = (APITimeoutError, APIConnectionError)

    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.1,
                max_tokens=1000,
                timeout=timeout,
            )
            break
        except retryable as e:
            if attempt == max_retries:
                raise APIError(f"DeepSeek API 不可达，已重试{max_retries}次: {e}") from e
            delay = 2 ** attempt
            logger.warning("DeepSeek API retry %d/%d in %ds: %s", attempt + 1, max_retries, delay, e)
            time.sleep(delay)

    content = response.choices[0].message.content.strip()

    # Strip markdown code fences if present
    if content.startswith("```"):
        content = content.split("\n", 1)[1]
        if content.endswith("```"):
            content = content.rsplit("\n", 1)[0]

    try:
        recipe = json.loads(content)
    except json.JSONDecodeError:
        raise ValueError(f"DeepSeek returned invalid JSON:\n{content}")

    return recipe
