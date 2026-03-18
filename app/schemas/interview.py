"""
面试和选题相关的 Pydantic Schema
"""
from datetime import datetime
from pydantic import BaseModel, Field


# ─── 选题请求/响应 ────────────────────────────────────────────────────────────

class StartInterviewRequest(BaseModel):
    difficulty: str | None = Field(
        None,
        description="指定难度 easy/medium/hard，None 则由系统根据画像决定"
    )


class QuestionBrief(BaseModel):
    """题目简要信息（选题结果）"""
    id:            int
    title:         str
    title_slug:    str
    difficulty:    str
    tags:          list[str]
    ac_rate:       float
    time_limit:    int    = Field(description="建议用时（秒）")


class SelectionResult(BaseModel):
    """选题结果"""
    session_id:    str
    question:      QuestionBrief
    select_reason: str    = Field(description="AI选题理由，展示给用户")
    focus_point:   str    = Field(description="本次面试重点考察方向")


# ─── 换题 ─────────────────────────────────────────────────────────────────────

class SwapQuestionRequest(BaseModel):
    session_id: str
    reason:     str = Field(
        description="换题原因",
        examples=["太难了", "太简单了", "做太多了", "就是想换"]
    )


class SwapQuestionResponse(BaseModel):
    question:       QuestionBrief
    select_reason:  str
    swap_remaining: int   = Field(description="今日剩余换题次数")


# ─── 答题提交 ─────────────────────────────────────────────────────────────────

class SubmitCodeRequest(BaseModel):
    session_id:   str
    code:         str
    language:     str = Field(description="python/javascript/java/cpp")
    time_used:    int = Field(description="答题用时（秒）")


# ─── 面试会话状态 ─────────────────────────────────────────────────────────────

class SessionStatus(BaseModel):
    session_id:   str
    status:       str
    question_id:  int
    created_at:   datetime