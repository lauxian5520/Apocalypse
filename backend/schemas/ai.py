from typing import Literal

from pydantic import BaseModel, Field, field_validator


TranslationLanguage = Literal['auto', 'zh', 'en', 'ja', 'ko', 'fr', 'de', 'es', 'ru']


class TranslateIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    source_language: TranslationLanguage = 'auto'
    target_language: TranslationLanguage = 'zh'

    @field_validator('text')
    @classmethod
    def nonempty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('翻译内容不能为空')
        return value

    @field_validator('target_language')
    @classmethod
    def concrete_target(cls, value: str) -> str:
        if value == 'auto':
            raise ValueError('请选择目标语言')
        return value


class ChatMsg(BaseModel):
    role: str
    content: str


class ChatIn(BaseModel):
    messages: list[ChatMsg]


class SummarizeIn(BaseModel):
    text: str
    context: str = "内容"


class ExplainIn(BaseModel):
    content: str
    image_urls: list[str] = []


class ProviderOut(BaseModel):
    name: str
    model: str
    active: bool
