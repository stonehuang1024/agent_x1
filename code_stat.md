# Agent X1 Code Statistics

> Generated: 2026-03-25 14:41

---

## 📊 Overall Statistics

| Metric | Count | Percentage |
|--------|-------|------------|
| **Total Lines** | 15,552 | 100% |
| **Code Lines** | 9,792 | 63.0% |
| **Comment Lines** | 3,693 | 23.7% |
| **Blank Lines** | 2,067 | 13.3% |
| **Python Files** | 50 | - |

---

## 🗂️ Directory Structure & Code Distribution

```
agent_x1/
├── main.py .......................................... 451 lines (2.9%)
├── src/ .......................................... 10,077 lines (64.8%)
│   ├── core/ ...................................... 1,281 lines (8.2%)
│   │   ├── __init__.py (70)
│   │   ├── config.py (511)
│   │   ├── models.py (105)
│   │   ├── session_manager.py (385)
│   │   └── tool.py (210)
│   ├── engine/ .................................... 1,201 lines (7.7%)
│   │   ├── __init__.py (268)
│   │   ├── anthropic_engine.py (426)
│   │   ├── base.py (241)
│   │   └── kimi_engine.py (266)
│   ├── skills/ .................................... 1,070 lines (6.9%)
│   │   ├── __init__.py (59)
│   │   ├── context_manager.py (321)
│   │   ├── loader.py (259)
│   │   ├── models.py (156)
│   │   ├── registry.py (145)
│   │   └── workspace.py (130)
│   ├── tools/ ..................................... 6,907 lines (44.4%)
│   │   ├── __init__.py (276)
│   │   ├── arxiv_tools.py (877)
│   │   ├── bash_tools.py (376)
│   │   ├── data_tools.py (565)
│   │   ├── economics_tools.py (483)
│   │   ├── example_tools.py (238)
│   │   ├── file_tools.py (598)
│   │   ├── pdf_tools.py (805)
│   │   ├── ppt_tools.py (436)
│   │   ├── reader_tools.py (531)
│   │   ├── search_tools.py (196)
│   │   ├── stock_analysis.py (576)
│   │   ├── stock_tools.py (316)
│   │   ├── tool_registry.py (180)
│   │   └── web_tools.py (454)
│   └── util/ ........................................ 618 lines (4.0%)
│       ├── logger.py (376)
│       ├── mail_utils.py (146)
│       └── parallel.py (96)
├── tests/ ........................................... 3,947 lines (25.4%)
│   ├── integration/ ................................. 1,742 lines (11.2%)
│   │   ├── test_anthropic_kimi.py (88)
│   │   ├── test_arxiv_integration.py (388)
│   │   ├── test_integration_tools.py (490)
│   │   ├── test_kimi_api.py (188)
│   │   ├── test_reader_integration.py (348)
│   │   └── _*.py (240)
│   └── unit/ ........................................ 2,205 lines (14.2%)
│       ├── test_arxiv_tools.py (492)
│       ├── test_imports.py (131)
│       ├── test_reader_tools.py (309)
│       ├── test_skills.py (591)
│       └── test_tools_unit.py (682)
└── config/, docs/, skills/, memory_data/ .............. 1,077 lines (6.9%)
```

---

## 📈 Module Breakdown

### Core Module (`src/core/`)
| File | Lines | Description |
|------|-------|-------------|
| config.py | 511 | Configuration management (LLM, Paths, Provider) |
| session_manager.py | 385 | Session lifecycle & LLM interaction logging |
| tool.py | 210 | Tool wrapper & ToolRegistry |
| models.py | 105 | Message dataclass & Role enum |
| __init__.py | 70 | Core exports |
| **Total** | **1,281** | **Foundation layer** |

### Engine Module (`src/engine/`)
| File | Lines | Description |
|------|-------|-------------|
| anthropic_engine.py | 426 | Anthropic-style API implementation |
| __init__.py | 268 | Engine factory & registry |
| kimi_engine.py | 266 | Kimi OpenAI-compatible API |
| base.py | 241 | BaseEngine abstract class |
| **Total** | **1,201** | **LLM engine layer** |

### Tools Module (`src/tools/`)
| File | Lines | Category |
|------|-------|----------|
| arxiv_tools.py | 877 | Academic Research |
| pdf_tools.py | 805 | PDF Processing |
| tool_registry.py | 180 | Tool categorization |
| stock_analysis.py | 576 | Financial Analysis |
| data_tools.py | 565 | Data Processing |
| file_tools.py | 598 | File Operations |
| reader_tools.py | 531 | Document Reading |
| web_tools.py | 454 | Web Scraping |
| ppt_tools.py | 436 | PowerPoint |
| economics_tools.py | 483 | Economic Data |
| bash_tools.py | 376 | System/Bash |
| stock_tools.py | 316 | Stock Data |
| search_tools.py | 196 | Search |
| example_tools.py | 238 | Utilities |
| __init__.py | 276 | Tool exports |
| **Total** | **6,907** | **54+ tools across 10 categories** |

### Skills Module (`src/skills/`)
| File | Lines | Description |
|------|-------|-------------|
| context_manager.py | 321 | Skill lifecycle & prompt assembly |
| loader.py | 259 | SKILL.md parser |
| models.py | 156 | Skill data models |
| registry.py | 145 | Skill discovery & indexing |
| workspace.py | 130 | Session-scoped workspaces |
| __init__.py | 59 | Skill exports |
| **Total** | **1,070** | **Skill framework** |

### Test Suite (`tests/`)
| Category | Files | Lines | Coverage |
|----------|-------|-------|----------|
| Unit Tests | 5 | 2,205 | Import, tools, skills |
| Integration Tests | 6 | 1,742 | API, tools, arxiv, reader |
| **Total** | **11** | **3,947** | **96+ tests** |

---

## 💡 Code Quality Insights

- **Code-to-Comment Ratio**: 2.65:1 (Healthy - well-documented)
- **Tests to Code Ratio**: 0.39:1 (Good coverage)
- **Largest Module**: `src/tools/` (44.4% of codebase)
- **Core Foundation**: `src/core/` + `src/engine/` (16% of codebase)
- **Entry Point**: `main.py` (451 lines, CLI interface)

---

## 🔢 Summary by Category

| Category |Lines | Percentage |
|----------|------|------------|
| **Tools** | 6,907 | 44.4% |
| **Tests** | 3,947 | 25.4% |
| **Core** | 1,281 | 8.2% |
| **Engine** | 1,201 | 7.7% |
| **Skills** | 1,070 | 6.9% |
| **Util** | 618 | 4.0% |
| **Main** | 451 | 2.9% |
| **Others** | 1,077 | 6.9% |

