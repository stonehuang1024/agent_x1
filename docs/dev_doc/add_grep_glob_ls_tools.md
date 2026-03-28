# 全工具超时与输出保护 + 新增 Grep/Glob/LS 工具

为 agent_x1 所有工具（现有 50+ 及新增 grep/glob/ls）统一加入超时和输出限制保护机制，在 Tool 基类、Engine 层、每个工具三层实现防崩溃安全网。

---

## 现状审计

### 全部工具清单与当前安全状态

| 模块 | 工具数 | 当前有超时? | 当前有输出限制? | 风险等级 |
|------|--------|-------------|----------------|----------|
| `file_tools` | 10 | ❌ 无 | 部分(max_chars) | **高** — search_in_files 大项目可能卡死 |
| `bash_tools` | 5 | ✅ subprocess层 | 硬编码截断 | **高** — 已有保护但不统一 |
| `web_tools` | 6 | ✅ requests层 | 部分(max_chars) | 中 — 网络超时已有 |
| `search_tools` | 2 | ❌ 无 | ❌ 无 | 中 — 依赖外部API |
| `stock_tools` | 4 | ❌ 无 | ❌ 无 | **高** — yfinance 大量数据 |
| `stock_analysis` | 1 | ❌ 无 | ❌ 无 | **高** — 计算密集+绘图 |
| `economics_tools` | 5 | ❌ 无 | ❌ 无 | 中 — 外部API |
| `pdf_tools` | 7 | ❌ 无 | 部分(max_chars) | 中 — 大PDF |
| `ppt_tools` | 4 | ❌ 无 | ❌ 无 | 低 |
| `data_tools` | 7 | ❌ 无 | 部分(max_rows) | **高** — pandas大文件 |
| `reader_tools` | 4 | ❌ 无 | ❌ 无 | **高** — 网络+文件转换 |
| `arxiv_tools` | 4 | ✅ urllib层 | ❌ 无 | 中 — 已有部分 |
| `example_tools` | 4 | ❌ 无 | ❌ 无 | 低 — mock数据 |
| **新增 codebase** | **3** | 待实现 | 待实现 | 高 — 文件遍历 |

**结论**: 50+ 工具中仅 ~10 个有部分超时保护，几乎没有统一的输出限制。任何工具都可能因大数据/网络/计算导致程序挂死。

---

## 实施计划

### Phase 1: Tool 基类增强 — 通用超时 + 输出保护

**文件**: `src/core/tool.py`

#### 1.1 Tool 类新增属性

```python
class Tool:
    def __init__(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        func: Callable,
        timeout_seconds: Optional[int] = None,     # None = 使用全局默认
        max_output_chars: Optional[int] = None,     # None = 使用全局默认
        is_readonly: bool = False,                  # 幂等标记
    ):
```

#### 1.2 全局默认常量

```python
# 全局安全默认值 — 所有工具的兜底保护
GLOBAL_DEFAULT_TIMEOUT = 120       # 2分钟
GLOBAL_DEFAULT_MAX_OUTPUT = 50000  # 50K字符 (~12K tokens)
```

#### 1.3 execute() 方法重构

```python
def execute(self, arguments: str) -> str:
    """带超时和输出限制的统一执行入口"""
    args = json.loads(arguments)
    timeout = self.timeout_seconds or GLOBAL_DEFAULT_TIMEOUT

    # 1. 超时保护: 用 concurrent.futures.ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(self.func, **args)
        try:
            result = future.result(timeout=timeout)
        except TimeoutError:
            return json.dumps({
                "error": f"Tool '{self.name}' timed out after {timeout}s",
                "timeout_seconds": timeout
            })

    # 2. 序列化
    output = json.dumps(result, ensure_ascii=False)

    # 3. 输出截断
    max_chars = self.max_output_chars or GLOBAL_DEFAULT_MAX_OUTPUT
    if len(output) > max_chars:
        output = output[:max_chars]
        # 追加截断标记（保持 JSON 可读性）
        output += '\n... [OUTPUT TRUNCATED at {max_chars} chars]'

    return output
```

#### 1.4 schema 增强

在 `get_schema()` 返回中附加 `_metadata`，让 LLM 感知工具的超时和限制：

```python
def get_schema(self) -> Dict[str, Any]:
    schema = self.schema.copy()
    schema["_metadata"] = {
        "timeout_seconds": self.timeout_seconds or GLOBAL_DEFAULT_TIMEOUT,
        "max_output_chars": self.max_output_chars or GLOBAL_DEFAULT_MAX_OUTPUT,
        "is_readonly": self.is_readonly,
    }
    return schema
```

#### 1.5 向后兼容

- 新增参数全部 Optional，默认 None
- 现有 `Tool(name=..., description=..., parameters=..., func=...)` 调用不变
- 现有工具自动获得 GLOBAL_DEFAULT 保护

---

### Phase 2: 为所有现有工具配置合理的超时和输出限制

修改每个工具模块的 Tool 实例化，添加 `timeout_seconds` 和 `max_output_chars`。

#### 2.1 per-tool 默认配置表

| 工具 | timeout_s | max_output | is_readonly | 理由 |
|------|-----------|------------|-------------|------|
| **file_tools** | | | | |
| `read_file` | 30 | 60000 | ✓ | 大文件读取 |
| `write_file` | 30 | 5000 | ✗ | 写操作，输出简短 |
| `append_file` | 30 | 5000 | ✗ | |
| `list_directory` | 30 | 30000 | ✓ | 大目录 |
| `search_in_files` | 60 | 30000 | ✓ | 文件搜索可能慢 |
| `move_file` | 30 | 5000 | ✗ | |
| `copy_file` | 60 | 5000 | ✗ | 大文件拷贝 |
| `delete_file` | 30 | 5000 | ✗ | |
| `get_file_info` | 15 | 5000 | ✓ | |
| `create_directory` | 15 | 5000 | ✗ | |
| **bash_tools** | | | | |
| `run_command` | 120 | 30000 | ✗ | 已有subprocess超时，加一层 |
| `run_python_script` | 120 | 30000 | ✗ | |
| `run_bash_script` | 120 | 30000 | ✗ | |
| `get_system_info` | 15 | 10000 | ✓ | |
| `get_environment_variable` | 10 | 5000 | ✓ | |
| **web_tools** | | | | |
| `fetch_url` | 30 | 60000 | ✓ | 已有requests超时 |
| `extract_webpage_text` | 30 | 60000 | ✓ | |
| `extract_links` | 30 | 30000 | ✓ | |
| `download_file` | 120 | 5000 | ✗ | 大文件下载 |
| `check_url` | 15 | 5000 | ✓ | |
| `fetch_rss_feed` | 30 | 30000 | ✓ | |
| **search_tools** | | | | |
| `search_google` | 30 | 30000 | ✓ | 外部API |
| `web_search_exa` | 30 | 30000 | ✓ | |
| **stock_tools** | | | | |
| `get_stock_kline` | 60 | 50000 | ✓ | yfinance 可能慢 |
| `get_stock_snapshot` | 30 | 10000 | ✓ | |
| `get_stock_financials` | 60 | 50000 | ✓ | 大量财务数据 |
| `get_stock_info` | 30 | 10000 | ✓ | |
| **stock_analysis** | | | | |
| `analyze_stock` | 180 | 30000 | ✓ | 计算密集+绘图 |
| **economics_tools** | | | | |
| `get_fred_series` | 60 | 30000 | ✓ | |
| `get_world_bank_indicator` | 60 | 30000 | ✓ | |
| `get_exchange_rates` | 30 | 10000 | ✓ | |
| `get_economic_calendar` | 30 | 30000 | ✓ | |
| `generate_economic_report` | 120 | 50000 | ✓ | |
| **pdf_tools** | | | | |
| `read_pdf` | 60 | 60000 | ✓ | 大PDF |
| `get_pdf_metadata` | 15 | 5000 | ✓ | |
| `merge_pdfs` | 120 | 5000 | ✗ | |
| `split_pdf` | 120 | 5000 | ✗ | |
| `create_pdf_from_text` | 60 | 5000 | ✗ | |
| `extract_pdf_images` | 120 | 10000 | ✗ | |
| `markdown_to_pdf` | 60 | 5000 | ✗ | |
| **ppt_tools** | | | | |
| `create_presentation` | 60 | 5000 | ✗ | |
| `read_presentation` | 30 | 30000 | ✓ | |
| `add_slide` | 30 | 5000 | ✗ | |
| `export_ppt_to_pdf` | 120 | 5000 | ✗ | LibreOffice 转换慢 |
| **data_tools** | | | | |
| `read_csv` | 60 | 50000 | ✓ | pandas 大CSV |
| `read_json_file` | 30 | 50000 | ✓ | |
| `read_excel` | 60 | 50000 | ✓ | |
| `analyze_dataframe` | 120 | 30000 | ✓ | 统计计算 |
| `filter_csv` | 60 | 50000 | ✓ | |
| `save_as_csv` | 30 | 5000 | ✗ | |
| `convert_data_format` | 60 | 5000 | ✗ | |
| **reader_tools** | | | | |
| `convert_url_to_markdown` | 60 | 60000 | ✓ | 网络 + 转换 |
| `convert_pdf_to_markdown` | 120 | 60000 | ✓ | |
| `convert_html_to_markdown` | 30 | 60000 | ✓ | |
| `convert_file_to_markdown` | 60 | 60000 | ✓ | |
| **arxiv_tools** | | | | |
| `search_arxiv_papers` | 60 | 30000 | ✓ | 已有urllib超时 |
| `get_arxiv_paper_details` | 30 | 10000 | ✓ | |
| `download_arxiv_pdf` | 180 | 5000 | ✗ | 大PDF下载 |
| `batch_download_arxiv_pdfs` | 600 | 10000 | ✗ | 批量下载 |
| **example_tools** | | | | |
| `get_weather` | 10 | 5000 | ✓ | mock |
| `calculate` | 10 | 5000 | ✓ | |
| `get_current_time` | 10 | 5000 | ✓ | |
| `search_knowledge` | 10 | 10000 | ✓ | |

#### 2.2 修改方式

每个 `*_tools.py` 中的 Tool 实例化加上参数，例如：

```python
# 修改前
READ_FILE_TOOL = Tool(
    name="read_file",
    description="...",
    parameters={...},
    func=read_file
)

# 修改后
READ_FILE_TOOL = Tool(
    name="read_file",
    description="...",
    parameters={...},
    func=read_file,
    timeout_seconds=30,
    max_output_chars=60000,
    is_readonly=True,
)
```

---

### Phase 3: 新建 `src/tools/codebase_search_tools.py`

（与原计划一致，此处配置表如下）

| 工具 | timeout_s | max_output | is_readonly | 实现策略 |
|------|-----------|------------|-------------|----------|
| `grep_search` | 60 | 30000 | ✓ | rg → Python re fallback |
| `glob_search` | 30 | 20000 | ✓ | fd → pathlib fallback |
| `ls_directory` | 10 | 10000 | ✓ | 纯 Python os.scandir |

工具函数内部也有自己的超时控制（subprocess timeout），与基类超时形成双重保护。

---

### Phase 4: Engine 层兜底保护

**文件**: `src/engine/anthropic_engine.py`, `src/engine/kimi_engine.py`

在 `_execute_tools()` 中增加最后一道防线：

```python
def _execute_tools(self, tool_calls):
    for call in tool_calls:
        start_time = time.time()
        tool = self.tools[tool_name]
        try:
            output = tool.execute(arguments)     # Tool层已有超时
        except Exception as e:
            output = json.dumps({"error": str(e)})

        elapsed = time.time() - start_time
        logger.info(f"Tool {tool_name} completed in {elapsed:.1f}s")

        # Engine层输出截断兜底 (30K chars)
        ENGINE_MAX_OUTPUT = 30000
        if len(output) > ENGINE_MAX_OUTPUT:
            output = output[:ENGINE_MAX_OUTPUT] + "\n...[ENGINE TRUNCATED]"

        results.append(tool_result_message)
```

---

### Phase 5: 工具注册与分类

**文件**: `src/tools/tool_registry.py`, `src/tools/__init__.py`

1. `TOOL_CATEGORIES` 新增 `"codebase"` 分类
2. `__init__.py` 导入 + 注册 `CODEBASE_TOOLS`
3. 更新 `__all__` 列表

---

### Phase 6: System Prompt 增强

**文件**: `config/config.yaml`

```yaml
system_prompt: |
  ...existing...

  ## Tool Safety
  All tools have timeout protection and output size limits.
  If a tool returns a truncated result, you can:
  - Narrow your query (e.g. add file pattern filters for grep)
  - Use pagination parameters (e.g. offset/limit for read_file)
  - Break the task into smaller steps

  ## Code Exploration Tools
  - `grep_search`: regex search in files (prefer over bash grep)
  - `glob_search`: find files by name pattern
  - `ls_directory`: quick directory listing
  - Start broad (files_with_matches), then narrow with context
```

---

### Phase 7: 测试验证

**新建**: `tests/unit/test_tool_safety.py` — 基类超时/输出限制测试
**新建**: `tests/unit/test_codebase_search_tools.py` — 新工具测试

#### 7.1 基类安全测试 (`test_tool_safety.py`)

| 测试 | 描述 |
|------|------|
| `test_timeout_triggers` | 慢函数触发超时，返回 error JSON |
| `test_timeout_default` | 无显式设置时使用 GLOBAL_DEFAULT_TIMEOUT |
| `test_output_truncation` | 大输出被截断并标记 |
| `test_output_default` | 无显式设置时使用 GLOBAL_DEFAULT_MAX_OUTPUT |
| `test_normal_execution_unaffected` | 正常执行不受影响（无性能回退） |
| `test_exception_handling` | 函数内部异常正确捕获 |
| `test_json_parse_error` | 非法参数 JSON 返回友好错误 |
| `test_schema_metadata` | schema 包含 timeout/output 元数据 |

#### 7.2 新工具测试 (`test_codebase_search_tools.py`)

| 测试 | 描述 |
|------|------|
| `test_grep_ripgrep_path` | rg 可用时走 subprocess |
| `test_grep_python_fallback` | rg 不可用时 fallback |
| `test_grep_timeout` | 超时返回错误 |
| `test_grep_max_results` | 结果截断 |
| `test_grep_case_sensitive` | 大小写控制 |
| `test_glob_fd_path` | fd 可用时走 subprocess |
| `test_glob_python_fallback` | fallback 到 pathlib |
| `test_glob_excludes` | 排除目录 |
| `test_ls_basic` | 基本列举 |
| `test_ls_max_entries` | 条目限制 |
| `test_registry_codebase` | 注册到 codebase 分类 |

#### 7.3 回归测试

```bash
python -m pytest tests/unit/test_tool_safety.py -v
python -m pytest tests/unit/test_codebase_search_tools.py -v
python -m pytest tests/unit/test_imports.py -v      # 确保不破坏现有导入
```

---

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/core/tool.py` | **修改** | Tool 类增加 timeout/output/readonly，execute() 重构 |
| `src/tools/file_tools.py` | **修改** | 10 个 Tool 加 timeout/output/readonly 参数 |
| `src/tools/bash_tools.py` | **修改** | 5 个 Tool 加参数 |
| `src/tools/web_tools.py` | **修改** | 6 个 Tool 加参数 |
| `src/tools/search_tools.py` | **修改** | 2 个 Tool 加参数 |
| `src/tools/stock_tools.py` | **修改** | 4 个 Tool 加参数 |
| `src/tools/stock_analysis.py` | **修改** | 1 个 Tool 加参数 |
| `src/tools/economics_tools.py` | **修改** | 5 个 Tool 加参数 |
| `src/tools/pdf_tools.py` | **修改** | 7 个 Tool 加参数 |
| `src/tools/ppt_tools.py` | **修改** | 4 个 Tool 加参数 |
| `src/tools/data_tools.py` | **修改** | 7 个 Tool 加参数 |
| `src/tools/reader_tools.py` | **修改** | 4 个 Tool 加参数 |
| `src/tools/arxiv_tools.py` | **修改** | 4 个 Tool 加参数 |
| `src/tools/example_tools.py` | **修改** | 4 个 Tool 加参数 |
| `src/tools/codebase_search_tools.py` | **新建** | grep/glob/ls 混合实现 |
| `src/tools/tool_registry.py` | **修改** | 新增 "codebase" 分类 |
| `src/tools/__init__.py` | **修改** | 导入注册新工具 |
| `src/engine/anthropic_engine.py` | **修改** | _execute_tools 兜底保护 |
| `src/engine/kimi_engine.py` | **修改** | 同上 |
| `config/config.yaml` | **修改** | system_prompt 增强 |
| `tests/unit/test_tool_safety.py` | **新建** | 基类安全机制测试 |
| `tests/unit/test_codebase_search_tools.py` | **新建** | 新工具测试 |

**总计**: 修改 17 个文件 + 新建 3 个文件

---

## 设计原则

- **三层防护**: Tool 基类超时 → 工具函数内部控制 → Engine 层兜底
- **全量覆盖**: 所有 50+ 现有工具 + 3 个新工具均有保护
- **向后兼容**: 新参数全部 Optional，现有调用方式不变
- **合理默认**: 按工具风险等级分配不同超时（10s~600s）
- **LLM 友好**: 截断输出带明确标记，system prompt 指导重试策略
- **模块化**: 新工具独立文件，现有工具仅增加构造参数，不改函数逻辑

