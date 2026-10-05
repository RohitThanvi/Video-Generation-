from pydantic import BaseModel, Field
from typing import Literal, Optional

class ProjectCreate(BaseModel):
    prompt: str = Field(min_length=1)

class AgentRequest(BaseModel):
    instruction: Optional[str] = None

class RenderRequest(BaseModel):
    width: int = 1920
    height: int = 1080
    fps: int = 30

class ToolResult(BaseModel):
    ok: bool
    message: str
    data: dict = {}
