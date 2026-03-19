"""
LeetCode 标签映射表

LeetCode API 返回英文标签，系统内部使用中文知识点。
此文件是唯一的映射来源，所有转换都通过这里。
"""

# 英文 → 中文映射（只包含 15 个核心知识点 + LeetCode 常见变体写法）
# 不在此表里的 tag 一律丢弃，不存入数据库
EN_TO_ZH: dict[str, str] = {
    "Array":              "数组",
    "String":             "字符串",
    "Hash Table":         "哈希表",
    "Linked List":        "链表",
    "Stack":              "栈",
    "Queue":              "队列",
    "Binary Tree":        "二叉树",
    "Tree":               "二叉树",   # LeetCode 有时写 Tree
    "Graph":              "图",
    "Dynamic Programming":"动态规划",
    "Backtracking":       "回溯",
    "Greedy":             "贪心",
    "Binary Search":      "二分查找",
    "Two Pointers":       "双指针",
    "Sliding Window":     "滑动窗口",
    "Sorting":            "排序",
}

# 15个核心知识点（中文），用于用户画像
CORE_TAGS_ZH = [
    "数组", "字符串", "哈希表", "链表", "栈",
    "队列", "二叉树", "图", "动态规划", "回溯",
    "贪心", "二分查找", "双指针", "滑动窗口", "排序",
]


def to_zh(en_tag: str) -> str | None:
    """英文标签转中文，不在核心列表里的返回 None"""
    return EN_TO_ZH.get(en_tag)


def tags_to_zh(en_tags: list[str]) -> list[str]:
    """
    批量转换标签：英文 → 中文，同时过滤掉非核心 tag。
    不在 15 个核心知识点里的 tag 直接丢弃，保证数据干净。
    """
    result = []
    seen   = set()   # 去重（Tree 和 Binary Tree 都映射到"二叉树"）
    for en in en_tags:
        zh = EN_TO_ZH.get(en)   # 不在映射表里的返回 None
        if zh and zh not in seen:
            result.append(zh)
            seen.add(zh)
    return result


def is_core_tag(zh_tag: str) -> bool:
    """判断是否是核心知识点"""
    return zh_tag in CORE_TAGS_ZH