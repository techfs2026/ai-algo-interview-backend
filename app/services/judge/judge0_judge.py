"""
Judge0 判题实现（占位）

接入方式：
1. 注册 RapidAPI 获取 Judge0 API Key
   https://rapidapi.com/judge0-official/api/judge0-ce
2. 在 .env 配置：
   JUDGE0_URL=https://judge0-ce.p.rapidapi.com
   JUDGE0_API_KEY=your-key
3. 将 JUDGE_PROVIDER=judge0 写入 .env

当前为占位实现，实际调用时会抛出 NotImplementedError。
"""
from app.schemas.analysis import JudgeResult
from app.services.judge.base import BaseJudge


class Judge0Judge(BaseJudge):
    """Judge0 云端判题实现（待接入）"""

    @property
    def name(self) -> str:
        return "judge0"

    async def execute(
        self,
        code:       str,
        language:   str,
        test_cases: list[dict],
    ) -> JudgeResult:
        # TODO: 接入 Judge0 API
        # 参考文档：https://docs.judge0.com
        # 语言 ID 映射：python=71, javascript=63, java=62, cpp=54
        raise NotImplementedError(
            "Judge0 尚未接入，请先配置 JUDGE0_URL 和 JUDGE0_API_KEY，"
            "然后实现此方法。接口已定义好，只需填入 HTTP 调用逻辑。"
        )

    async def health_check(self) -> bool:
        return False