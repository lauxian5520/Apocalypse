from pydantic import BaseModel


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
