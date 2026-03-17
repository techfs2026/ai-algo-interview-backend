import uuid
from datetime import datetime
from sqlalchemy import String, Integer, Float, Boolean, JSON, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


class UserProfile(Base):
    """用户画像"""
    __tablename__ = "user_profiles"

    user_id:    Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    username:   Mapped[str] = mapped_column(String(50), nullable=False)

    # 能力矩阵：{知识点: {level, confidence, question_count}}
    skills:     Mapped[dict] = mapped_column(JSON, default=dict)

    # 校准状态
    calibration_done:  Mapped[bool] = mapped_column(Boolean, default=False)
    total_questions:   Mapped[int]  = mapped_column(Integer, default=0)

    # 答题历史
    solved_ids: Mapped[list] = mapped_column(JSON, default=list)
    failed_ids: Mapped[list] = mapped_column(JSON, default=list)

    # 换题配额
    swap_date:  Mapped[str | None] = mapped_column(String(10), nullable=True)
    swap_used:  Mapped[int]        = mapped_column(Integer, default=0)

    created_at:  Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_active: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow,
                                                   onupdate=datetime.utcnow)

    # 关联
    sessions: Mapped[list["InterviewSession"]] = relationship(back_populates="user")


class Question(Base):
    """题目元数据（不存题目原文内容）"""
    __tablename__ = "questions"

    id:             Mapped[int]  = mapped_column(Integer, primary_key=True)  # LeetCode题号
    title:          Mapped[str]  = mapped_column(String(200), nullable=False)
    title_slug:     Mapped[str]  = mapped_column(String(200), nullable=False, unique=True)
    difficulty:     Mapped[str]  = mapped_column(String(10), nullable=False)  # easy/medium/hard
    is_paid:        Mapped[bool] = mapped_column(Boolean, default=False)
    tags:           Mapped[list] = mapped_column(JSON, default=list)
    ac_rate:        Mapped[float]= mapped_column(Float, default=0.5)

    # LLM扩展的语义信息
    core_skills:      Mapped[list | None] = mapped_column(JSON, nullable=True)
    suitable_level:   Mapped[str | None]  = mapped_column(String(20), nullable=True)
    thinking_pattern: Mapped[str | None]  = mapped_column(String(200), nullable=True)
    semantic_text:    Mapped[str | None]  = mapped_column(Text, nullable=True)

    # 向量是否已入库
    is_indexed: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class InterviewSession(Base):
    """面试会话"""
    __tablename__ = "interview_sessions"

    id:          Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    user_id:     Mapped[str] = mapped_column(String(36), ForeignKey("user_profiles.user_id"))
    question_id: Mapped[int] = mapped_column(Integer, ForeignKey("questions.id"))

    # 答题信息
    language:     Mapped[str | None]  = mapped_column(String(20), nullable=True)
    code:         Mapped[str | None]  = mapped_column(Text, nullable=True)
    time_used:    Mapped[int | None]  = mapped_column(Integer, nullable=True)   # 秒
    time_limit:   Mapped[int | None]  = mapped_column(Integer, nullable=True)   # 秒

    # 判题结果
    passed:       Mapped[int | None]  = mapped_column(Integer, nullable=True)
    total:        Mapped[int | None]  = mapped_column(Integer, nullable=True)
    submit_count: Mapped[int]         = mapped_column(Integer, default=0)

    # 状态
    status: Mapped[str] = mapped_column(String(20), default="active")
    # active / submitted / analyzed / completed

    # 选题信息
    select_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at:  Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["UserProfile"] = relationship(back_populates="sessions")


class RecommendationLog(Base):
    """推荐题单埋点"""
    __tablename__ = "recommendation_logs"

    id:             Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id:        Mapped[str] = mapped_column(String(36))
    session_id:     Mapped[str] = mapped_column(String(36))
    question_id:    Mapped[int] = mapped_column(Integer)
    recommend_type: Mapped[str] = mapped_column(String(20))  # related/weakness/new
    user_level:     Mapped[float | None] = mapped_column(Float, nullable=True)

    # 用户行为
    was_clicked:   Mapped[bool] = mapped_column(Boolean, default=False)
    was_completed: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)