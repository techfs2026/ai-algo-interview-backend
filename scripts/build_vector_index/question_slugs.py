"""
LeetCode 高频题 slug 列表
来源：LeetCode 精选题单 + 面试高频题

好处：
1. 不依赖不稳定的列表查询接口
2. 质量有保证，都是面试高频题
3. 按难度分类，方便按需建库
"""

EASY_SLUGS = [
    "two-sum",
    "valid-parentheses",
    "merge-two-sorted-lists",
    "best-time-to-buy-and-sell-stock",
    "valid-palindrome",
    "invert-binary-tree",
    "valid-anagram",
    "binary-search",
    "flood-fill",
    "lowest-common-ancestor-of-a-binary-search-tree",
    "balanced-binary-tree",
    "linked-list-cycle",
    "implement-queue-using-stacks",
    "first-bad-version",
    "ransom-note",
    "climbing-stairs",
    "longest-common-prefix",
    "contains-duplicate",
    "maximum-depth-of-binary-tree",
    "same-tree",
    "symmetric-tree",
    "path-sum",
    "pascal-triangle",
    "intersection-of-two-linked-lists",
    "reverse-linked-list",
    "excel-sheet-column-number",
    "majority-element",
    "reverse-bits",
    "number-of-1-bits",
    "house-robber",
    "counting-bits",
    "missing-number",
    "find-the-duplicate-number",
    "move-zeroes",
    "find-all-anagrams-in-a-string",
    "diameter-of-binary-tree",
    "island-perimeter",
    "max-consecutive-ones",
    "number-of-segments-in-a-string",
    "average-of-levels-in-binary-tree",
]

MEDIUM_SLUGS = [
    "longest-substring-without-repeating-characters",
    "two-sum-ii-input-array-is-sorted",
    "3sum",
    "container-with-most-water",
    "group-anagrams",
    "maximum-subarray",
    "spiral-matrix",
    "jump-game",
    "merge-intervals",
    "unique-paths",
    "word-search",
    "decode-string",
    "number-of-islands",
    "rotting-oranges",
    "search-in-rotated-sorted-array",
    "find-minimum-in-rotated-sorted-array",
    "combination-sum",
    "permutations",
    "subsets",
    "letter-combinations-of-a-phone-number",
    "course-schedule",
    "implement-trie-prefix-tree",
    "coin-change",
    "product-of-array-except-self",
    "validate-binary-search-tree",
    "kth-smallest-element-in-a-bst",
    "construct-binary-tree-from-preorder-and-inorder-traversal",
    "binary-tree-level-order-traversal",
    "binary-tree-right-side-view",
    "number-of-connected-components-in-an-undirected-graph",
    "pacific-atlantic-water-flow",
    "longest-consecutive-sequence",
    "top-k-frequent-elements",
    "encode-and-decode-strings",
    "sort-colors",
    "find-the-celebrity",
    "task-scheduler",
    "lru-cache",
    "minimum-window-substring",
    "sliding-window-maximum",
    "longest-increasing-subsequence",
    "clone-graph",
    "evaluate-division",
    "keys-and-rooms",
    "max-area-of-island",
    "odd-even-linked-list",
    "linked-list-cycle-ii",
    "remove-nth-node-from-end-of-list",
    "swap-nodes-in-pairs",
    "rotate-list",
    "partition-list",
    "reverse-linked-list-ii",
]

HARD_SLUGS = [
    "median-of-two-sorted-arrays",
    "trapping-rain-water",
    "n-queens",
    "word-ladder",
    "word-ladder-ii",
    "longest-valid-parentheses",
    "wildcard-matching",
    "jump-game-ii",
    "maximum-rectangle",
    "binary-tree-maximum-path-sum",
    "word-break-ii",
    "merge-k-sorted-lists",
    "reverse-nodes-in-k-group",
    "find-median-from-data-stream",
    "sliding-window-maximum",
    "minimum-window-substring",
    "serialize-and-deserialize-binary-tree",
    "alien-dictionary",
    "edit-distance",
    "best-time-to-buy-and-sell-stock-iii",
]

# 全部题目（按需使用）
ALL_SLUGS = EASY_SLUGS + MEDIUM_SLUGS + HARD_SLUGS


def get_slugs_by_difficulty(difficulty: str) -> list[str]:
    """
    按难度获取 slug 列表。
    difficulty: "easy" | "medium" | "hard" | "all"
    """
    mapping = {
        "easy":   EASY_SLUGS,
        "medium": MEDIUM_SLUGS,
        "hard":   HARD_SLUGS,
        "all":    ALL_SLUGS,
    }
    return mapping.get(difficulty.lower(), ALL_SLUGS)