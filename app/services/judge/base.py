"""
判题接口定义

所有判题实现必须继承 BaseJudge 并实现 execute 方法。
上层代码只依赖这个接口，不依赖具体实现。
"""
from abc import ABC, abstractmethod

from app.schemas.analysis import JudgeResult


class BaseJudge(ABC):
    """判题器抽象基类"""

    @abstractmethod
    async def execute(
        self,
        code:       str,
        language:   str,
        test_cases: list[dict],
    ) -> JudgeResult:
        """
        执行判题。

        Args:
            code:       用户提交的代码
            language:   编程语言（python/javascript/java/cpp）
            test_cases: 测试用例列表，格式：[{"input": "...", "expected": "..."}]
                        为空时只做编译检查

        Returns:
            JudgeResult
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """检查判题服务是否可用"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """实现名称，用于日志和监控"""
        ...