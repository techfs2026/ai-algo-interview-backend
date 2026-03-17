"""
用户画像相关的 Pydantic Schema
请求/响应模型，与数据库模型分离
"""
from datetime import datetime
from pydantic import BaseModel, Field


# ─── 知识点技能 ───────────────────────────────────────────────────────────────

class SkillState(BaseModel):
    level:          float = Field(0.5,  ge=0.0, le=1.0, description="掌握水平")
    confidence:     float = Field(0.2,  ge=0.0, le=1.0, description="置信度")
    question_count: int   = Field(0,    ge=0,            description="累计答题数")


# ─── 冷启动问卷 ───────────────────────────────────────────────────────────────

# 5档自评分值映射
SELF_RATING_MAP = {
    1: 0.0,   # 一点不会
    2: 0.3,   # 会做简单题
    3: 0.6,   # 会做中等题
    4: 0.85,  # 会做难题
    5: 1.0,   # 难不倒我
}

# 15个核心知识点
KNOWLEDGE_TAGS = [
    "数组", "字符串", "哈希表", "链表", "栈",
    "队列", "二叉树", "图", "动态规划", "回溯",
    "贪心", "二分查找", "双指针", "滑动窗口", "排序",
]


class QuestionnaireItem(BaseModel):
    tag:    str = Field(..., description="知识点标签")
    rating: int = Field(..., ge=1, le=5, description="自评分 1-5")


class QuestionnaireRequest(BaseModel):
    items: list[QuestionnaireItem] = Field(
        ...,
        min_length=len(KNOWLEDGE_TAGS),
        max_length=len(KNOWLEDGE_TAGS),
        description="15个知识点的自评"
    )


# ─── 用户画像响应 ─────────────────────────────────────────────────────────────

class UserProfileResponse(BaseModel):
    user_id:           str
    username:          str
    skills:            dict[str, SkillState]
    calibration_done:  bool
    total_questions:   int
    swap_remaining:    int   = Field(description="今日剩余换题次数")
    created_at:        datetime
    last_active:       datetime

    model_config = {"from_attributes": True}


class CreateUserResponse(BaseModel):
    user_id:  str
    username: str
    message:  str = "用户创建成功，请完成初始问卷"


# ─── 画像更新（答题后回写）───────────────────────────────────────────────────

class SkillUpdatePayload(BaseModel):
    tag:           str
    question_id:   int
    difficulty:    str
    passed:        bool
    time_used:     int   = Field(..., description="答题用时（秒）")
    expected_time: int   = Field(..., description="参考用时（秒）")
    submit_count:  int   = Field(1, ge=1)
    tags:          list[str] = Field(default_factory=list, description="题目标签")
    ac_rate:       float = Field(0.5, ge=0.0, le=1.0, description="题目通过率")