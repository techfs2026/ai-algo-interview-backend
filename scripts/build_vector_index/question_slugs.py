"""
NeetCode 150 精选题单（共 127 道）

剔除了 18 道测试用例难以可靠提取的题目，详见文件末尾说明。
"""

EASY_SLUGS = [
    # Arrays & Hashing
    "contains-duplicate", "valid-anagram", "two-sum",
    # Two Pointers
    "valid-palindrome",
    # Sliding Window
    "best-time-to-buy-and-sell-stock",
    # Stack
    "valid-parentheses",
    # Binary Search
    "binary-search",
    # Linked List
    "reverse-linked-list", "merge-two-sorted-lists", "linked-list-cycle",
    # Trees
    "invert-binary-tree", "maximum-depth-of-binary-tree", "diameter-of-binary-tree",
    "balanced-binary-tree", "same-tree", "subtree-of-another-tree",
    # Heap
    "kth-largest-element-in-a-stream", "last-stone-weight",
    # DP 1D
    "climbing-stairs", "min-cost-climbing-stairs",
    # Bit Manipulation
    "single-number", "number-of-1-bits", "counting-bits",
    "reverse-bits", "missing-number",
    # Math
    "happy-number", "plus-one",
    # Greedy
    "maximum-subarray",
]

MEDIUM_SLUGS = [
    # Arrays & Hashing
    "group-anagrams", "top-k-frequent-elements", "product-of-array-except-self",
    "valid-sudoku", "longest-consecutive-sequence",
    # Two Pointers
    "two-sum-ii-input-array-is-sorted", "3sum", "container-with-most-water",
    # Sliding Window
    "longest-substring-without-repeating-characters",
    "longest-repeating-character-replacement", "permutation-in-string",
    "minimum-window-substring", "sliding-window-maximum",
    # Stack
    "min-stack", "evaluate-reverse-polish-notation", "generate-parentheses",
    "daily-temperatures", "car-fleet",
    # Binary Search
    "search-a-2d-matrix", "koko-eating-bananas",
    "find-minimum-in-rotated-sorted-array", "search-in-rotated-sorted-array",
    "time-based-key-value-store",
    # Linked List
    "reorder-list", "remove-nth-node-from-end-of-list",
    "copy-list-with-random-pointer", "add-two-numbers", "find-the-duplicate-number",
    # Trees
    "binary-tree-level-order-traversal", "binary-tree-right-side-view",
    "count-good-nodes-in-binary-tree", "validate-binary-search-tree",
    "kth-smallest-element-in-a-bst",
    "construct-binary-tree-from-preorder-and-inorder-traversal",
    "lowest-common-ancestor-of-a-binary-search-tree",
    # Heap
    "k-closest-points-to-origin", "kth-largest-element-in-an-array",
    "task-scheduler", "design-twitter",
    # Backtracking
    "subsets", "combination-sum", "permutations", "subsets-ii",
    "combination-sum-ii", "word-search", "palindrome-partitioning",
    "letter-combinations-of-a-phone-number",
    # Tries
    "implement-trie-prefix-tree",
    # Graphs
    "number-of-islands", "max-area-of-island", "rotting-oranges",
    "course-schedule", "course-schedule-ii", "redundant-connection",
    "number-of-connected-components-in-an-undirected-graph", "graph-valid-tree",
    # DP 1D
    "house-robber", "house-robber-ii", "longest-palindromic-substring",
    "palindromic-substrings", "decode-ways", "coin-change",
    "maximum-product-subarray", "word-break", "longest-increasing-subsequence",
    "partition-equal-subset-sum",
    # DP 2D
    "unique-paths", "longest-common-subsequence",
    "best-time-to-buy-and-sell-stock-with-cooldown", "coin-change-ii",
    "target-sum", "interleaving-string", "longest-increasing-path-in-a-matrix",
    # Greedy
    "jump-game", "jump-game-ii", "gas-station", "hand-of-straights",
    "merge-intervals", "non-overlapping-intervals", "partition-labels",
    # Intervals
    "insert-interval", "minimum-number-of-arrows-to-burst-balloons",
    # Math
    "rotate-image", "spiral-matrix", "set-matrix-zeroes",
    "pow-x-n", "multiply-strings", "detect-squares",
    # Bit Manipulation
    "sum-of-two-integers", "reverse-integer",
]

HARD_SLUGS = [
    # Two Pointers
    "trapping-rain-water",
    # Stack
    "largest-rectangle-in-histogram",
    # Binary Search
    "median-of-two-sorted-arrays",
    # Linked List
    "merge-k-sorted-lists",
    # Graphs
    "walls-and-gates",
    # DP 2D
    "distinct-subsequences", "edit-distance", "regular-expression-matching",
    # Greedy
    "meeting-rooms",
    # Math
    "detect-squares",
]

ALL_SLUGS = list(dict.fromkeys(EASY_SLUGS + MEDIUM_SLUGS + HARD_SLUGS))


def get_slugs_by_difficulty(difficulty: str) -> list[str]:
    return {
        "easy":   EASY_SLUGS,
        "medium": MEDIUM_SLUGS,
        "hard":   HARD_SLUGS,
        "all":    ALL_SLUGS,
    }.get(difficulty.lower(), ALL_SLUGS)


"""
已剔除的 18 道题（测试用例难以可靠提取）：

lru-cache                              设计题，需要模拟操作序列
insert-delete-getrandom-o1             随机化，输出不确定
find-median-from-data-stream           流式输入设计题
serialize-and-deserialize-binary-tree  输入输出格式特殊
design-add-and-search-words-data-structure  设计题
word-search-ii                         输出列表顺序不确定
alien-dictionary                       多个合法答案
word-ladder                            测试用例复杂
clone-graph                            图输入格式复杂
pacific-atlantic-water-flow            输出坐标列表顺序不确定
encode-and-decode-strings              需要配对调用两个函数
meeting-rooms-ii                       需要 premium
minimum-interval-to-include-each-query 顺序相关
swim-in-rising-water                   复杂图题
reconstruct-itinerary                  输出顺序敏感
n-queens                               输出格式复杂
burst-balloons                         区间 DP 测试用例复杂
reverse-nodes-in-k-group               链表输出验证复杂
"""