from pydantic import BaseModel, Field, field_validator
from typing import Optional

class ProjectCreate(BaseModel):
    prompt: str = Field(min_length=1)

class AgentRequest(BaseModel):
    instruction: Optional[str] = None

class RenderRequest(BaseModel):
    width: int = Field(1920, ge=64, le=7680)
    height: int = Field(1080, ge=64, le=4320)
    fps: int = Field(30, ge=1, le=120)

    @field_validator("width", "height")
    @classmethod
    def _even(cls, v: int) -> int:
        # H.264 with yuv420p (needed for broad player support) requires even dimensions.
        if v % 2:
            raise ValueError("must be an even number")
        return v

class JobRequest(BaseModel):
    kind: str = Field(pattern="^(agent|render)$")
    instruction: Optional[str] = None
    width: int = Field(1920, ge=64, le=7680)
    height: int = Field(1080, ge=64, le=4320)
    fps: int = Field(30, ge=1, le=120)

    @field_validator("width", "height")
    @classmethod
    def _even(cls, v: int) -> int:
        if v % 2:
            raise ValueError("must be an even number")
        return v

class ToolResult(BaseModel):
    ok: bool
    message: str
    data: dict = {}
