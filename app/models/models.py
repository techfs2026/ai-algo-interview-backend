import uuid
from datetime import datetime
from sqlalchemy import String, Integer, Float, Boolean, JSON, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


class UserProfile(Base):
    """用户画像"""
    __tablename__  = "user_profiles"
    __table_args__ = {"extend_existing": True}

    user_id:    Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    username:   Mapped[str] = mapped_column(String(50), nullable=False)

    skills:            Mapped[dict] = mapped_column(JSON, default=dict)
    calibration_done:  Mapped[bool] = mapped_column(Boolean, default=False)
    total_questions:   Mapped[int]  = mapped_column(Integer, default=0)
    solved_ids:        Mapped[list] = mapped_column(JSON, default=list)
    failed_ids:        Mapped[list] = mapped_column(JSON, default=list)
    swap_date:  Mapped[str | None]  = mapped_column(String(10), nullable=True)
    swap_used:  Mapped[int]         = mapped_column(Integer, default=0)
    created_at:  Mapped[datetime]   = mapped_column(DateTime, default=datetime.utcnow)
    last_active: Mapped[datetime]   = mapped_column(DateTime, default=datetime.utcnow,
                                                    onupdate=datetime.utcnow)

    sessions: Mapped[list["InterviewSession"]] = relationship(back_populates="user")


class Question(Base):
    """题目元数据"""
    __tablename__  = "questions"
    __table_args__ = {"extend_existing": True}

    id:               Mapped[int]         = mapped_column(Integer, primary_key=True)
    title:            Mapped[str]         = mapped_column(String(200), nullable=False)
    title_slug:       Mapped[str]         = mapped_column(String(200), nullable=False, unique=True)
    difficulty:       Mapped[str]         = mapped_column(String(10), nullable=False)
    is_paid:          Mapped[bool]        = mapped_column(Boolean, default=False)
    tags:             Mapped[list]        = mapped_column(JSON, default=list)
    ac_rate:          Mapped[float]       = mapped_column(Float, default=0.5)
    core_skills:      Mapped[list | None] = mapped_column(JSON, nullable=True)
    suitable_level:   Mapped[str | None]  = mapped_column(String(20), nullable=True)
    thinking_pattern: Mapped[str | None]  = mapped_column(String(200), nullable=True)
    semantic_text:    Mapped[str | None]  = mapped_column(Text, nullable=True)
    is_indexed:       Mapped[bool]        = mapped_column(Boolean, default=False)
    created_at:       Mapped[datetime]    = mapped_column(DateTime, default=datetime.utcnow)


class InterviewSession(Base):
    """面试会话"""
    __tablename__  = "interview_sessions"
    __table_args__ = {"extend_existing": True}

    id:            Mapped[str]          = mapped_column(String(36), primary_key=True, default=gen_uuid)
    user_id:       Mapped[str]          = mapped_column(String(36), ForeignKey("user_profiles.user_id"))
    question_id:   Mapped[int]          = mapped_column(Integer, ForeignKey("questions.id"))
    language:      Mapped[str | None]   = mapped_column(String(20), nullable=True)
    code:          Mapped[str | None]   = mapped_column(Text, nullable=True)
    time_used:     Mapped[int | None]   = mapped_column(Integer, nullable=True)
    time_limit:    Mapped[int | None]   = mapped_column(Integer, nullable=True)
    passed:        Mapped[int | None]   = mapped_column(Integer, nullable=True)
    total:         Mapped[int | None]   = mapped_column(Integer, nullable=True)
    submit_count:  Mapped[int]          = mapped_column(Integer, default=0)
    status:        Mapped[str]          = mapped_column(String(20), default="active")
    select_reason: Mapped[str | None]   = mapped_column(Text, nullable=True)
    created_at:    Mapped[datetime]     = mapped_column(DateTime, default=datetime.utcnow)
    finished_at:   Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["UserProfile"] = relationship(back_populates="sessions")


class TestCase(Base):
    """题目测试用例"""
    __tablename__  = "test_cases"
    __table_args__ = {"extend_existing": True}

    id:          Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question_id: Mapped[int] = mapped_column(Integer, ForeignKey("questions.id"), nullable=False)
    input_data:  Mapped[str] = mapped_column(Text, nullable=False)
    expected:    Mapped[str] = mapped_column(Text, nullable=False)
    case_type:   Mapped[str] = mapped_column(String(20), default="sample")
    # sample=示例用例  edge=边界用例
    created_at:  Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RecommendationLog(Base):
    """推荐题单埋点"""
    __tablename__  = "recommendation_logs"
    __table_args__ = {"extend_existing": True}

    id:             Mapped[int]          = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id:        Mapped[str]          = mapped_column(String(36))
    session_id:     Mapped[str]          = mapped_column(String(36))
    question_id:    Mapped[int]          = mapped_column(Integer)
    recommend_type: Mapped[str]          = mapped_column(String(20))
    user_level:     Mapped[float | None] = mapped_column(Float, nullable=True)
    was_clicked:    Mapped[bool]         = mapped_column(Boolean, default=False)
    was_completed:  Mapped[bool]         = mapped_column(Boolean, default=False)
    created_at:     Mapped[datetime]     = mapped_column(DateTime, default=datetime.utcnow)

class LLMCallLog(Base):
    """LLM 调用日志 - 可观测性核心表"""
    __tablename__  = "llm_call_logs"
    __table_args__ = {"extend_existing": True}

    id:             Mapped[int]          = mapped_column(Integer, primary_key=True, autoincrement=True)
    scene:          Mapped[str]          = mapped_column(String(50), nullable=False)
    # select / analyze / feedback / questionnaire
    model:          Mapped[str]          = mapped_column(String(100), nullable=False)
    attempts:       Mapped[int]          = mapped_column(Integer, default=1)
    repair_success: Mapped[bool]         = mapped_column(Boolean, default=False)
    fallback_used:  Mapped[bool]         = mapped_column(Boolean, default=False)
    latency_ms:     Mapped[int]          = mapped_column(Integer, default=0)
    tokens_used:    Mapped[int]          = mapped_column(Integer, default=0)
    failure_reason: Mapped[str | None]   = mapped_column(String(200), nullable=True)
    # ok / timeout / json_parse_error / schema_error / exception
    created_at:     Mapped[datetime]     = mapped_column(DateTime, default=datetime.utcnow)