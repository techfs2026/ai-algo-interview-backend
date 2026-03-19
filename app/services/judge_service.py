"""
判题服务 - 公开接口层

职责：
1. 工厂方法：根据配置返回对应的判题实现
2. 数据库操作：读取测试用例
3. 上层调用入口：judge()

上层代码（API 层）只 import 这个文件，
不直接接触任何具体实现（SubprocessJudge / Judge0Judge）。
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.models import TestCase
from app.schemas.analysis import JudgeResult
from app.services.judge.base import BaseJudge

logger   = logging.getLogger(__name__)
settings = get_settings()


# ─── 工厂方法 ─────────────────────────────────────────────────────────────────

def get_judge() -> BaseJudge:
    """
    根据 JUDGE_PROVIDER 配置返回对应的判题实现。

    .env 配置：
        JUDGE_PROVIDER=subprocess   本地开发（默认）
        JUDGE_PROVIDER=judge0       生产环境

    切换判题方式只需改 .env，代码零改动。
    """
    provider = getattr(settings, "judge_provider", "subprocess").lower()

    if provider == "judge0":
        from app.services.judge.judge0_judge import Judge0Judge
        impl = Judge0Judge()
    else:
        from app.services.judge.subprocess_judge import SubprocessJudge
        impl = SubprocessJudge()

    logger.debug(f"判题实现: {impl.name}")
    return impl


# ─── 公开接口 ─────────────────────────────────────────────────────────────────

class JudgeService:
    """
    判题服务公开接口。
    上层只需调用 judge() 和 get_test_cases()，不关心底层实现。
    """

    def __init__(self):
        self._impl: BaseJudge = get_judge()

    async def judge(
        self,
        code:       str,
        language:   str,
        test_cases: list[dict],
    ) -> JudgeResult:
        """执行判题，委托给具体实现"""
        logger.info(
            f"判题开始 [impl={self._impl.name}] "
            f"lang={language} cases={len(test_cases)}"
        )
        return await self._impl.execute(code, language, test_cases)

    async def get_test_cases(
        self,
        question_id: int,
        db:          AsyncSession,
    ) -> list[dict]:
        """从数据库读取测试用例，数据来源单一"""
        result = await db.execute(
            select(TestCase).where(TestCase.question_id == question_id)
        )
        cases = result.scalars().all()
        return [{"input": t.input_data, "expected": t.expected} for t in cases]

    async def health_check(self) -> bool:
        return await self._impl.health_check()

    @property
    def provider(self) -> str:
        return self._impl.name


judge_service = JudgeService()