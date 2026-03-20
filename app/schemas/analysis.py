"""
代码分析相关 Schema
"""
from pydantic import BaseModel, Field


class AnalysisRequest(BaseModel):
    session_id:   str
    code:         str
    language:     str  = Field(description="python/javascript/java/cpp")
    time_used:    int  = Field(description="答题用时（秒）")
    run_only:     bool = Field(False, description="仅运行模式：只跑用例，不写库不触发AI分析")


class JudgeResult(BaseModel):
    passed:        int   = Field(description="通过的测试用例数")
    total:         int   = Field(description="总测试用例数")
    status:        str   = Field(description="Accepted/Wrong Answer/Time Limit Exceeded/...")
    runtime_ms:    int   = Field(0, description="运行时间（毫秒）")
    memory_kb:     int   = Field(0, description="内存使用（KB）")
    error_message: str   = Field("", description="错误信息")
    submit_count:  int   = Field(1)
    # 第一条失败的测试用例详情（仅 Wrong Answer 时有值）
    failed_input:    str | None = Field(None, description="失败用例的输入")
    failed_expected: str | None = Field(None, description="失败用例的期望输出")
    failed_actual:   str | None = Field(None, description="失败用例的实际输出")


class RecommendQuestion(BaseModel):
    id:             int
    title:          str
    title_slug:     str
    difficulty:     str
    tags:           list[str]
    recommend_type: str   = Field(description="related/weakness/new")
    reason:         str   = Field(description="推荐理由")