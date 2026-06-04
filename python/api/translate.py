import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal

from utils.llm_client import LLMClient

router = APIRouter()

TRANSLATE_SYSTEM = """
你是一位具有深厚双语功底的专业翻译，同时精通学术、法律、医学、科技等专业领域。
翻译原则：
1. 忠实原文，不增删、不意译失真
2. 目标语言自然流畅，符合母语者表达习惯
3. 专业术语优先使用该领域标准译法
4. 遇到无法直译的文化特有词汇，在括号内附原文
5. 只输出译文，不要解释、不要注释
""".strip()


class TranslateRequest(BaseModel):
    text: str
    src: str = "auto"
    tgt: str = "zh"
    engine: Literal["google", "qwen", "kimi", "openai"] = "google"
    api_key: str = ""
    model: str = ""


@router.post("/")
async def translate(req: TranslateRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text is empty")

    if req.engine == "google":
        return await _google_translate(req)
    else:
        return await _llm_translate(req)


async def _google_translate(req: TranslateRequest):
    url = "https://translation.googleapis.com/language/translate/v2"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json={
            "q": req.text,
            "source": req.src if req.src != "auto" else None,
            "target": req.tgt,
            "format": "text",
            "key": req.api_key,
        })
        resp.raise_for_status()
    data = resp.json()
    return {"result": data["data"]["translations"][0]["translatedText"]}


async def _llm_translate(req: TranslateRequest):
    client = LLMClient(engine=req.engine, api_key=req.api_key, model=req.model)
    lang_map = {"zh": "中文", "en": "English", "ja": "日本語", "ko": "한국어", "fr": "Français", "de": "Deutsch"}
    tgt_lang = lang_map.get(req.tgt, req.tgt)
    prompt = f"请将以下文本翻译为{tgt_lang}：\n\n{req.text}"
    result = await client.chat(system=TRANSLATE_SYSTEM, user=prompt)
    return {"result": result}
