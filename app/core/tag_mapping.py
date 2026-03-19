"""
LeetCode 标签映射表

LeetCode API 返回英文标签，系统内部使用中文知识点。
此文件是唯一的映射来源，所有转换都通过这里。
"""

# 英文 → 中文映射
# 覆盖 15 个核心知识点 + 常见 LeetCode 标签变体
EN_TO_ZH: dict[str, str] = {
    # 核心15个知识点
    "Array":              "数组",
    "String":             "字符串",
    "Hash Table":         "哈希表",
    "Linked List":        "链表",
    "Stack":              "栈",
    "Queue":              "队列",
    "Binary Tree":        "二叉树",
    "Tree":               "二叉树",    # LeetCode 有时用 Tree
    "Graph":              "图",
    "Dynamic Programming":"动态规划",
    "Backtracking":       "回溯",
    "Greedy":             "贪心",
    "Binary Search":      "二分查找",
    "Two Pointers":       "双指针",
    "Sliding Window":     "滑动窗口",
    "Sorting":            "排序",

    # 其他常见标签（保留英文，不映射到核心知识点）
    "Math":               "数学",
    "Bit Manipulation":   "位运算",
    "Recursion":          "递归",
    "Depth-First Search": "深度优先搜索",
    "Breadth-First Search":"广度优先搜索",
    "Heap (Priority Queue)":"堆",
    "Priority Queue":     "堆",
    "Trie":               "前缀树",
    "Union Find":         "并查集",
    "Monotonic Stack":    "单调栈",
    "Divide and Conquer": "分治",
    "Simulation":         "模拟",
    "Design":             "设计",
    "Matrix":             "矩阵",
    "Number Theory":      "数论",
    "Memoization":        "记忆化搜索",
    "Counting":           "计数",
    "Prefix Sum":         "前缀和",
    "Binary Search Tree": "二叉搜索树",
    "Segment Tree":       "线段树",
    "Binary Indexed Tree":"树状数组",
    "Interactive":        "交互",
    "Randomized":         "随机化",
    "Game Theory":        "博弈论",
    "Geometry":           "几何",
    "Topological Sort":   "拓扑排序",
    "Shortest Path":      "最短路径",
    "Minimum Spanning Tree":"最小生成树",
}

# 中文 → 英文（反查，用于调试）
ZH_TO_EN: dict[str, str] = {v: k for k, v in EN_TO_ZH.items()}

# 15个核心知识点（中文），用于用户画像
CORE_TAGS_ZH = [
    "数组", "字符串", "哈希表", "链表", "栈",
    "队列", "二叉树", "图", "动态规划", "回溯",
    "贪心", "二分查找", "双指针", "滑动窗口", "排序",
]


def to_zh(en_tag: str) -> str:
    """英文标签转中文，未知标签保留原文"""
    return EN_TO_ZH.get(en_tag, en_tag)


def tags_to_zh(en_tags: list[str]) -> list[str]:
    """批量转换标签列表"""
    return [to_zh(t) for t in en_tags]


def is_core_tag(zh_tag: str) -> bool:
    """判断是否是核心知识点"""
    return zh_tag in CORE_TAGS_ZH