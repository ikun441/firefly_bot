"""流萤 Firefly · LLM 客户端封装

统一使用 OpenAI 兼容协议，因此可无缝切换：
    * DeepSeek      https://api.deepseek.com/v1
    * 通义千问       https://dashscope.aliyuncs.com/compatible-mode/v1
    * 智谱 GLM      https://open.bigmodel.cn/api/paas/v4
    * 本地 Ollama   http://localhost:11434/v1

设计要点：
    1. 未配置 API Key 时自动降级为规则模式，服务仍可启动（答辩演示的保险）
    2. 所有调用带缓存，避免重复消耗 token
    3. 结构化抽取强制 JSON 输出，并做容错解析
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from ..config import get_config

# 轻量内存缓存：同一次演示会话中重复内容不必重复请求
_CACHE: Dict[str, Any] = {}
_CACHE_MAX = 256


class LLMNotReadyError(RuntimeError):
    """LLM 未就绪（未启用或缺少 API Key）"""


def _cache_key(model: str, messages: List[Dict[str, str]], temperature: float) -> str:
    raw = json.dumps(
        {"m": model, "msg": messages, "t": temperature}, ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_ready() -> bool:
    """LLM 是否可用于实际调用"""
    return get_config().llm.is_ready


def get_client():
    """构造 OpenAI 兼容客户端

    延迟导入 openai，使未安装依赖时模块本身仍可导入。
    """
    config = get_config()
    if not config.llm.enabled:
        raise LLMNotReadyError("LLM 未启用（config.yaml: llm.enabled = false）")
    if not config.llm.api_key:
        raise LLMNotReadyError(
            "未配置 LLM API Key，请在 .env 中设置 LLM_API_KEY"
        )

    try:
        from openai import AsyncOpenAI
    except ImportError as exc:  # pragma: no cover
        raise LLMNotReadyError("未安装 openai 依赖，请执行 pip install openai") from exc

    return AsyncOpenAI(
        api_key=config.llm.api_key,
        base_url=config.llm.base_url,
        timeout=config.llm.timeout,
        max_retries=config.llm.max_retries,
    )


async def chat(
    messages: List[Dict[str, str]],
    *,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    use_cache: bool = True,
) -> str:
    """通用对话补全，返回文本内容"""
    config = get_config()
    temp = config.llm.temperature if temperature is None else temperature
    tokens = config.llm.max_tokens if max_tokens is None else max_tokens

    key = _cache_key(config.llm.model, messages, temp)
    if use_cache and key in _CACHE:
        return _CACHE[key]

    client = get_client()
    response = await client.chat.completions.create(
        model=config.llm.model,
        messages=messages,  # type: ignore[arg-type]
        temperature=temp,
        max_tokens=tokens,
    )
    content = (response.choices[0].message.content or "").strip()

    if use_cache:
        if len(_CACHE) >= _CACHE_MAX:
            # 简单淘汰：清掉最早插入的一批
            for k in list(_CACHE.keys())[: _CACHE_MAX // 4]:
                _CACHE.pop(k, None)
        _CACHE[key] = content

    return content


def _strip_code_fence(text: str) -> str:
    """去掉模型可能包裹的 ```json ``` 代码块"""
    text = text.strip()
    fence = re.match(r"^```(?:json|JSON)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return text


def parse_json_response(text: str) -> Any:
    """从模型输出中稳健地提取 JSON

    模型常见问题：包裹代码块、前后加解释文字、使用中文标点。
    逐级降级尝试，避免整条链路因格式问题失败。
    """
    cleaned = _strip_code_fence(text)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 尝试截取第一个 { 或 [ 到最后一个 } 或 ]
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = cleaned.find(open_ch)
        end = cleaned.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            candidate = cleaned[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                # 常见于尾随逗号
                repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
                try:
                    return json.loads(repaired)
                except json.JSONDecodeError:
                    continue

    raise ValueError(f"无法从模型输出中解析 JSON: {text[:200]}")


async def chat_json(
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    use_cache: bool = True,
) -> Any:
    """要求模型返回 JSON 并解析

    抽取/分类类任务统一用 temperature=0，保证结果可复现（论文实验要求）。
    """
    text = await chat(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        use_cache=use_cache,
    )
    return parse_json_response(text)


def clear_cache() -> None:
    """清空 LLM 响应缓存"""
    _CACHE.clear()


__all__ = [
    "LLMNotReadyError",
    "is_ready",
    "get_client",
    "chat",
    "chat_json",
    "parse_json_response",
    "clear_cache",
]
