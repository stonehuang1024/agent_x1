# edit_file Tool — Phase 1 完整实现计划

实现基于 SEARCH/REPLACE 格式的 `edit_file` tool，包含 DiffParser、精确匹配引擎、EditApplier、read-before-edit Guard，EditManager 放在 `src/core/edit_manager.py`。

---

## 一、架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│  LLM Tool Call: edit_file(file_path, diff, ...)                  │
└──────────────┬───────────────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────────────┐
│  src/tools/file_tools.py :: edit_file()                          │
│  ┌───────────────────────────────────────────────────────────┐   │
│  │ 1. _resolve_safe_path(path)          — 路径安全            │   │
│  │ 2. edit_manager.validate_edit(path)  — read-before-edit   │   │
│  │ 3. edit_manager.apply_edit(path, diff, opts)              │   │
│  └───────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────────────┐
│  src/core/edit_manager.py  (新建)                                 │
│                                                                   │
│  ┌─────────────────┐  ┌──────────────┐  ┌──────────────────┐    │
│  │  DiffParser      │  │ SearchEngine │  │   EditApplier    │    │
│  │  解析 SEARCH/    │  │ 精确字符串   │  │  顺序无关应用    │    │
│  │  REPLACE 块      │  │ 匹配 + 建议  │  │  倒序替换防漂移  │    │
│  └─────────────────┘  └──────────────┘  └──────────────────┘    │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  FileEditingGuard (单例)                                    │  │
│  │  - session_read_files: Set[str]    记录已读取文件            │  │
│  │  - file_content_cache: Dict        缓存读取内容             │  │
│  │  - last_read_time: Dict            读取时间戳               │  │
│  │  - CACHE_TTL = 120s                缓存过期时间             │  │
│  │  + record_read(path, content)      read_file 调用时注册     │  │
│  │  + validate_edit(path) → bool      edit_file 调用时校验     │  │
│  │  + get_cached_content(path)        获取缓存内容             │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
               ▲
┌──────────────────────────────────────────────────────────────────┐
│  src/tools/file_tools.py :: read_file()  (小改)                   │
│  → 在成功读取后调用 edit_manager.record_read(path, content)      │
└──────────────────────────────────────────────────────────────────┘
```

## 二、新建文件：`src/core/edit_manager.py`

### 2.1 DiffParser 类

```python
class DiffParser:
    """解析 SEARCH/REPLACE 格式的 diff 文本为 ReplaceBlock 列表。"""

    @staticmethod
    def parse(diff_content: str) -> List[ReplaceBlock]:
        """
        解析格式：
        ------- SEARCH
        [原始代码]
        =======
        [替换代码]
        +++++++ REPLACE

        支持多个连续块。容忍 Markdown 代码围栏包裹。
        """
```

**关键实现点：**
- 状态机 (idle → search → replace → idle)
- 支持标记变体：`------- SEARCH`, `<<<<<<< SEARCH`
- 自动 strip markdown ` ```diff ` 围栏
- 空 SEARCH 块 → 创建新文件语义（仅当 diff 中只有一个块时）
- 格式错误时抛出 `DiffParseError(message, line_number)`

### 2.2 SearchEngine 类

```python
class SearchEngine:
    """在文件内容中查找 SEARCH 块的位置。Phase 1 仅实现精确匹配。"""

    @staticmethod
    def find_exact(content: str, search: str, start_from: int = 0) -> MatchResult

    @staticmethod
    def count_matches(content: str, search: str) -> int

    @staticmethod
    def find_near_matches(content: str, search: str, top_n: int = 3) -> List[str]
        """未匹配时，基于首行相似度找到 top-N 近似位置作为建议。"""
```

**Phase 1 范围**：仅精确匹配 (`str.find`)。未匹配时返回近似建议（基于首行逐行对比）。

### 2.3 EditApplier 类

```python
class EditApplier:
    """将多个 ReplaceBlock 应用到文件内容，支持顺序无关应用。"""

    @staticmethod
    def apply(
        original_content: str,
        blocks: List[ReplaceBlock],
        replace_all: bool = False
    ) -> EditResult
```

**核心算法**：
1. 遍历 blocks，用 SearchEngine 定位每个 block 在 `original_content` 中的位置
2. 按位置**倒序排序** (position desc)，从后往前替换，避免位置漂移
3. 检测重叠：如果两个块影响同一区域，拒绝并报错
4. 返回 `EditResult(success, new_content, applied_edits, failed_edits)`

### 2.4 FileEditingGuard 类（单例）

```python
class FileEditingGuard:
    """会话级文件编辑守卫，强制 read-before-edit。"""

    CACHE_TTL = 120  # 秒

    def record_read(self, file_path: str, content: str) -> None
    def validate_edit(self, file_path: str) -> Tuple[bool, Optional[str]]
    def get_cached_content(self, file_path: str) -> Optional[str]
    def verify_freshness(self, file_path: str, current_content: str) -> bool
    def invalidate(self, file_path: str) -> None
    def reset(self) -> None

# 模块级单例
_guard_instance: Optional[FileEditingGuard] = None

def get_edit_guard() -> FileEditingGuard:
    ...
```

**单例模式**：与 `session_manager.py` 中的 `get_session_manager()` 模式一致。

### 2.5 数据类

```python
@dataclass
class ReplaceBlock:
    search: str
    replace: str

@dataclass
class MatchResult:
    found: bool
    position: int = -1
    end_position: int = -1
    match_count: int = 0
    suggestions: Optional[List[str]] = None

@dataclass
class EditResult:
    success: bool
    new_content: str
    applied_count: int = 0
    failed_edits: Optional[List[Dict[str, Any]]] = None
    snippet_after: Optional[str] = None  # 替换区域上下文

class DiffParseError(ValueError):
    """Diff 格式解析错误。"""
    pass
```

## 三、修改文件：`src/tools/file_tools.py`

### 3.1 修改 `read_file()` — 注册读取记录

在 `read_file()` 成功读取后，增加一行：

```python
# 在 return 之前
from ..core.edit_manager import get_edit_guard
get_edit_guard().record_read(str(resolved), content)
```

**改动极小**：仅在成功读取路径、返回 dict 之前增加 2 行。

### 3.2 新增 `edit_file()` 函数

```python
def edit_file(
    file_path: str,
    diff: str,
    replace_all: bool = False,
    encoding: str = "utf-8"
) -> Dict[str, Any]:
```

**流程**：
1. `_resolve_safe_path(file_path)`
2. 调用 `get_edit_guard().validate_edit(path)` — 检查 read-before-edit
3. 调用 `DiffParser.parse(diff)` — 解析 SEARCH/REPLACE 块
4. 读取文件当前内容
5. 调用 `get_edit_guard().verify_freshness(path, current_content)` — 防并发
6. 调用 `EditApplier.apply(current_content, blocks, replace_all)` — 执行替换
7. 原子写入：写临时文件 → `os.replace()`
8. 更新 guard 缓存：`get_edit_guard().record_read(path, new_content)`
9. 返回结果

### 3.3 新增 `EDIT_FILE_TOOL` 定义

```python
EDIT_FILE_TOOL = Tool(
    name="edit_file",
    description="""Perform precise code edits using SEARCH/REPLACE blocks.
Rules:
1. You MUST call read_file first before editing any file.
2. SEARCH blocks must exactly match the file content (including indentation).
3. Each SEARCH must uniquely match one location (unless replace_all=true).
4. For small files (<100 lines) or large rewrites (>50%), prefer write_file.

Diff format:
------- SEARCH
[exact original code]
=======
[replacement code]
+++++++ REPLACE

Multiple blocks supported in a single call.""",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "File path to edit (must have been read via read_file first)"
            },
            "diff": {
                "type": "string",
                "description": "SEARCH/REPLACE formatted diff text"
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all matches (default: false, only first match)"
            },
            "encoding": {
                "type": "string",
                "description": "File encoding (default: utf-8)"
            }
        },
        "required": ["file_path", "diff"]
    },
    func=edit_file
)
```

加入 `FILE_TOOLS` 列表。

## 四、修改文件：`src/tools/__init__.py`

新增导入和导出：

```python
from .file_tools import (
    ...,
    EDIT_FILE_TOOL,      # 新增
    FILE_TOOLS,
)

__all__ = [
    ...,
    "EDIT_FILE_TOOL",    # 新增
]
```

无需修改 `TOOL_REGISTRY` 注册代码，因为 `EDIT_FILE_TOOL` 已在 `FILE_TOOLS` 列表中，会被 `register_many(FILE_TOOLS, "file")` 自动注册。

## 五、修改文件：`src/core/__init__.py`

新增导出：

```python
from .edit_manager import (
    EditManager,
    DiffParser,
    FileEditingGuard,
    get_edit_guard,
)
```

## 六、测试计划

### 6.1 新建 `tests/unit/test_edit_manager.py`

EditManager 核心组件独立测试（不依赖 file tools）：

| 测试 | 描述 |
|------|------|
| `test_diff_parser_single_block` | 解析单个 SEARCH/REPLACE 块 |
| `test_diff_parser_multi_block` | 解析多个连续块 |
| `test_diff_parser_strip_code_fence` | 自动去除 markdown 围栏 |
| `test_diff_parser_unclosed_block_error` | 未闭合块 → DiffParseError |
| `test_diff_parser_empty_search` | 空 SEARCH 块（创建语义） |
| `test_diff_parser_variant_markers` | 兼容 `<<<<<<< SEARCH` 等变体 |
| `test_search_engine_exact_match` | 精确匹配成功 |
| `test_search_engine_no_match` | 未匹配 + 返回建议 |
| `test_search_engine_multiple_matches` | 多次匹配计数 |
| `test_edit_applier_single_block` | 单块替换 |
| `test_edit_applier_multi_block_order_invariant` | 多块顺序无关应用 |
| `test_edit_applier_overlapping_error` | 重叠块检测 |
| `test_edit_applier_replace_all` | replace_all 全部替换 |
| `test_edit_applier_delete_text` | new_string 为空 → 删除 |
| `test_guard_record_and_validate` | 记录读取 → 验证通过 |
| `test_guard_reject_unread` | 未读取 → 验证拒绝 |
| `test_guard_cache_expiry` | 超过 TTL → 验证拒绝 |
| `test_guard_freshness_check` | 文件被外部修改 → 不新鲜 |

### 6.2 在 `tests/unit/test_tools_unit.py` 的 `TestFileTools` 中新增

| 测试 | 描述 |
|------|------|
| `test_edit_file_basic` | 完整 read → edit 流程 |
| `test_edit_file_without_read_error` | 未 read 直接 edit → 报错 |
| `test_edit_file_multi_block` | 多块编辑 |
| `test_edit_file_no_match_error` | SEARCH 不匹配 → 报错 + 建议 |
| `test_edit_file_atomic_write` | 原子写入不损坏原文件 |
| `test_edit_file_tool_execute_interface` | 通过 `EDIT_FILE_TOOL.execute(json)` 调用 |

### 6.3 运行命令

```bash
# 单独运行 EditManager 测试
python -m pytest tests/unit/test_edit_manager.py -v

# 运行 file tools 测试（含 edit_file）
python -m pytest tests/unit/test_tools_unit.py::TestFileTools -v

# 全量回归
python -m pytest tests/unit/ -v
```

## 七、文件变更清单

| 文件 | 操作 | 改动规模 |
|------|------|----------|
| `src/core/edit_manager.py` | **新建** | ~350 行：DiffParser, SearchEngine, EditApplier, FileEditingGuard, 数据类 |
| `src/core/__init__.py` | 修改 | +5 行：新增导出 |
| `src/tools/file_tools.py` | 修改 | +80 行：`edit_file()` 函数 + `EDIT_FILE_TOOL` 定义 + `FILE_TOOLS` 更新；`read_file()` +2 行 |
| `src/tools/__init__.py` | 修改 | +2 行：导入 + `__all__` |
| `tests/unit/test_edit_manager.py` | **新建** | ~250 行：18 个测试用例 |
| `tests/unit/test_tools_unit.py` | 修改 | +80 行：6 个测试用例 |

**总计**：新建 2 文件，修改 4 文件，约 770 行新增代码。

## 八、Phase 2 预留（不在本次实现）

- 模糊匹配 (`SearchEngine.find_relaxed`)
- 语法校验 (post-edit syntax validation via AST parse)
- Engine 层拦截 (`_execute_tools` 中的 hook)
- System Prompt 自动注入编辑规则
- Fallback 机制（edit_file 连续失败 2 次 → 建议 write_file）

