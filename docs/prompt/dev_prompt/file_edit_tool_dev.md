1. Claude Code：搜索替换（Search/Replace）机制
Claude Code 不使用传统 diff 格式，而是采用基于精确字符串匹配的编辑工具 。
核心工具设计
Claude Code 提供两个主要文件操作工具：
表格
工具	用途	参数	工作机制
Write	创建新文件或完全覆盖	file_path, content	原子写入，要求必须先读取文件（read-before-write 安全机制）
Edit	精确修改现有内容	file_path, old_string, new_string, replace_all	基于精确字符串匹配的搜索替换
Edit 工具的详细工作原理 
JSON
复制
{
  "file_path": "/absolute/path/to/file.py",
  "old_string": "    for num in range(2, limit + 1)",  // 必须完全匹配原文
  "new_string": "    for num in range(2, limit + 1):", // 替换后的内容
  "replace_all": false  // 默认只替换第一个匹配项，true 则替换所有
}
关键特性：
非行号依赖：不使用行号定位，而是搜索实际代码内容
唯一性验证：old_string 必须在文件中有且只有一个匹配（除非 replace_all=true）
空白敏感：完全保留原始缩进（空格/制表符）
会话状态追踪：系统强制要求必须先使用 Read 工具读取文件，才能执行 Edit
LLM 返回的内容格式
当 Claude Code 修改代码时，LLM 返回的是：
修改片段：仅包含被替换的 old_string 和 new_string 对
非完整文件：不需要重写整个文件，只发送变更的部分
工作流程示例：
LLM 先调用 Read 查看文件内容
分析需要修改的代码段
构造包含精确代码片段的 Edit 请求
系统验证 old_string 存在且唯一，执行替
换


基于 Cline  和 Claude Code  的最佳实践，我为您设计一套完整的类 Claude Code 文件编辑机制。此设计兼顾精确性、安全性和模型友好性，特别适合 Kimi 的长上下文能力。
一、系统架构概览
graph TD
    A[用户请求修改] --> B{文件大小检查}
    B -->|< 100行| C[使用 Write 工具<br/>完整重写]
    B -->|>= 100行| D[使用 Edit 工具<br/>Search/Replace]
    D --> E[强制 Read 检查]
    E -->|未读取| F[报错: 必须先读取]
    E -->|已读取| G[解析 Diff 块]
    G --> H{匹配验证}
    H -->|精确匹配| I[执行替换]
    H -->|模糊匹配| J[警告并确认]
    H -->|无匹配| K[返回错误&原始上下文]
    I --> L[语法/格式检查]
    L -->|通过| M[写入文件]
    L -->|失败| N[回滚&报错]

二、工具 Schema 定义
1. 文件操作工具集
TypeScript
复制
// 工具定义（Function Calling Schema）
const fileTools = [
  {
    name: "read_file",
    description: "读取文件内容。编辑文件前必须先调用此工具，这是强制安全要求。",
    input_schema: {
      type: "object",
      properties: {
        file_path: { 
          type: "string", 
          description: "文件的绝对路径或相对工作区根目录的路径" 
        },
        offset: { 
          type: "integer", 
          description: "起始行号（可选，用于大文件分块读取）", 
          default: 0 
        },
        limit: { 
          type: "integer", 
          description: "读取的最大行数（可选，默认读取整个文件）", 
          default: 0 
        }
      },
      required: ["file_path"]
    }
  },
  {
    name: "edit_file",
    description: `精确修改文件中的特定代码片段。使用 SEARCH/REPLACE 格式。
规则：
1. 必须先从文件中精确复制 SEARCH 块（含缩进和换行）
2. 每个 SEARCH 必须在文件中唯一存在（除非使用 replace_all）
3. 支持多个连续编辑块，按文件中出现顺序排列
4. 如果修改超过50%内容，请改用 write_file 重写整个文件`,
    input_schema: {
      type: "object",
      properties: {
        file_path: { 
          type: "string", 
          description: "要编辑的文件路径（必须先通过 read_file 读取）" 
        },
        diff: { 
          type: "string", 
          description: `SEARCH/REPLACE 格式的差异文本。格式如下：
------- SEARCH
[精确匹配的原始代码]
=======
[替换后的代码]
+++++++ REPLACE
可包含多个连续的 SEARCH/REPLACE 块` 
        },
        replace_all: { 
          type: "boolean", 
          description: "如果为true，替换所有匹配项（默认false，只替换第一个）", 
          default: false 
        },
        relaxed_mode: {
          type: "boolean", 
          description: "启用宽松匹配：忽略行尾空格和换行符差异（危险，慎用）", 
          default: false 
        }
      },
      required: ["file_path", "diff"]
    }
  },
  {
    name: "write_file",
    description: "创建新文件或完全覆盖现有文件。用于小文件(<100行)或大比例重构(>30%内容变更)。",
    input_schema: {
      type: "object",
      properties: {
        file_path: { type: "string" },
        content: { type: "string", description: "完整的文件内容" }
      },
      required: ["file_path", "content"]
    }
  }
];
三、核心算法实现
1. Diff 解析器（Multi-Block 支持）
TypeScript
复制
interface ReplaceBlock {
  search: string;
  replace: string;
  lineRange?: { start: number; end: number }; // 可选：用于调试和验证
}

class DiffParser {
  // 支持的标记变体（提高模型兼容性）
  private static readonly SEARCH_MARKERS = [
    /^------- SEARCH$/,
    /^<<<<<<< SEARCH$/,
    /^------- BEGIN SEARCH-------$/
  ];
  
  private static readonly SEPARATOR_MARKERS = [
    /^=======$/,
    /^======= -------$/
  ];
  
  private static readonly REPLACE_MARKERS = [
    /^\+{3,} REPLACE$/,
    /^>>>>>>> REPLACE$/,
    /^------- END REPLACE-------$/
  ];

  static parse(diffContent: string): ReplaceBlock[] {
    // 1. 去除 Markdown 代码块包裹（模型常自动加 ```）
    const cleaned = this.stripCodeFences(diffContent);
    const lines = cleaned.split('\n');
    const blocks: ReplaceBlock[] = [];
    
    let state: 'idle' | 'search' | 'replace' = 'idle';
    let searchBuffer: string[] = [];
    let replaceBuffer: string[] = [];
    
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      
      if (this.isMarker(line, this.SEARCH_MARKERS)) {
        if (state !== 'idle') {
          throw new Error(`格式错误：第${i+1}行出现意外的 SEARCH 标记（状态: ${state}）`);
        }
        state = 'search';
        searchBuffer = [];
      } else if (this.isMarker(line, this.SEPARATOR_MARKERS)) {
        if (state !== 'search') {
          throw new Error(`格式错误：第${i+1}行出现意外的分隔符（缺少 SEARCH 块）`);
        }
        state = 'replace';
        replaceBuffer = [];
      } else if (this.isMarker(line, this.REPLACE_MARKERS)) {
        if (state !== 'replace') {
          throw new Error(`格式错误：第${i+1}行出现意外的 REPLACE 标记（缺少 REPLACE 块）`);
        }
        // 保存块
        blocks.push({
          search: searchBuffer.join('\n'),
          replace: replaceBuffer.join('\n')
        });
        state = 'idle';
      } else {
        // 累积内容
        if (state === 'search') searchBuffer.push(line);
        else if (state === 'replace') replaceBuffer.push(line);
        // idle 状态忽略多余空行
      }
    }
    
    if (state !== 'idle') {
      throw new Error('格式错误：diff 块未闭合（缺少最后的 REPLACE 标记）');
    }
    
    if (blocks.length === 0) {
      throw new Error('格式错误：未找到有效的 SEARCH/REPLACE 块');
    }
    
    return blocks;
  }
  
  private static stripCodeFences(content: string): string {
    // 去除 ```diff 或 ``` 包裹
    return content
      .replace(/^```[\w]*\n/, '')
      .replace(/```$/, '')
      .trim();
  }
  
  private static isMarker(line: string, patterns: RegExp[]): boolean {
    return patterns.some(p => p.test(line.trim()));
  }
}
2. 匹配引擎（精确 vs 宽松）
TypeScript
复制
interface MatchResult {
  found: boolean;
  position: number;      // 在原始内容中的字符位置
  endPosition: number;   // 匹配结束位置
  isFuzzy?: boolean;     // 是否通过宽松匹配找到
  confidence?: number;   // 匹配置信度（模糊匹配时）
  suggestions?: string[]; // 未找到时的建议（接近匹配）
}

class SearchEngine {
  // 精确匹配（默认）
  static findExact(
    content: string, 
    search: string, 
    startFrom: number = 0
  ): MatchResult {
    // 严格匹配，包括换行符和缩进
    const pos = content.indexOf(search, startFrom);
    if (pos >= 0) {
      // 检查是否还有后续匹配（用于 replace_all 或警告）
      const nextPos = content.indexOf(search, pos + 1);
      return {
        found: true,
        position: pos,
        endPosition: pos + search.length,
        isFuzzy: false,
        hasMultipleMatches: nextPos >= 0
      };
    }
    return {
      found: false,
      position: -1,
      endPosition: -1,
      suggestions: this.findNearMatches(content, search)
    };
  }
  
  // 宽松匹配（容错模式）
  static findRelaxed(
    content: string, 
    search: string, 
    threshold: number = 0.85
  ): MatchResult {
    // 归一化：忽略行尾空格、统一换行符
    const normalize = (s: string) => 
      s.replace(/[ \t]+\n/g, '\n').replace(/\r\n/g, '\n').trim();
    
    const normContent = normalize(content);
    const normSearch = normalize(search);
    
    // 使用滑动窗口 + 相似度计算（Levenshtein 或 Jaccard）
    const windowSize = normSearch.length;
    let bestMatch = { pos: -1, similarity: 0 };
    
    for (let i = 0; i <= normContent.length - windowSize; i++) {
      const window = normContent.substring(i, i + windowSize);
      const sim = this.calculateSimilarity(window, normSearch);
      if (sim > bestMatch.similarity) {
        bestMatch = { pos: i, similarity: sim };
      }
    }
    
    if (bestMatch.similarity >= threshold) {
      // 映射回原始内容位置（需要字符级对齐）
      const originalPos = this.mapNormalizedToOriginal(
        content, normContent, bestMatch.pos
      );
      return {
        found: true,
        position: originalPos,
        endPosition: originalPos + search.length,
        isFuzzy: true,
        confidence: bestMatch.similarity
      };
    }
    
    return { found: false, position: -1, endPosition: -1 };
  }
  
  // 未找到时提供建议（Levenshtein 距离 top-3）
  private static findNearMatches(content: string, search: string): string[] {
    const lines = content.split('\n');
    const searchLines = search.split('\n');
    const firstLine = searchLines[0];
    
    // 找到包含相似首行的位置
    const candidates: Array<{lines: string[], score: number, startLine: number}> = [];
    
    for (let i = 0; i < lines.length; i++) {
      const similarity = this.lineSimilarity(lines[i], firstLine);
      if (similarity > 0.6) {
        // 提取候选上下文
        const contextLines = lines.slice(i, i + searchLines.length);
        const fullSimilarity = this.calculateSimilarity(
          contextLines.join('\n'), 
          search
        );
        candidates.push({
          lines: contextLines,
          score: fullSimilarity,
          startLine: i + 1
        });
      }
    }
    
    // 按相似度排序，返回前3个建议
    return candidates
      .sort((a, b) => b.score - a.score)
      .slice(0, 3)
      .map(c => `第${c.startLine}行起 (${Math.round(c.score*100)}%匹配):\n${c.lines.join('\n')}`);
  }
}
3. 编辑应用引擎（顺序无关应用）
TypeScript
复制
interface EditOperation {
  search: string;
  replace: string;
  originalIndex: number; // 原始在 diff 中的顺序
}

interface EditResult {
  success: boolean;
  newContent: string;
  appliedEdits: Array<{
    originalIndex: number;
    position: number;
    length: number;
    isFuzzy: boolean;
  }>;
  failedEdits: Array<{
    originalIndex: number;
    reason: string;
    suggestions?: string[];
  }>;
}

class EditApplier {
  // 关键：支持顺序无关应用（Order-invariant Apply）
  // 防止 LLM 返回的块顺序与实际文件顺序不一致
  static apply(
    originalContent: string,
    blocks: ReplaceBlock[],
    options: {
      relaxedMode?: boolean;
      replaceAll?: boolean;
      allowOverlapping?: boolean; // 默认 false
    } = {}
  ): EditResult {
    // 策略：按在文件中实际出现位置排序应用，而非按 blocks 数组顺序
    const operations = this.planExecutionOrder(originalContent, blocks, options);
    
    let currentContent = originalContent;
    const applied: EditResult['appliedEdits'] = [];
    const failed: EditResult['failedEdits'] = [];
    
    // 从后往前应用（避免位置漂移）或基于索引映射
    // 这里采用基于原始位置的映射策略
    const sortedOps = operations.sort((a, b) => b.position - a.position); // 倒序
    
    for (const op of sortedOps) {
      if (op.found) {
        // 检查重叠（如果启用严格模式）
        if (!options.allowOverlapping && this.hasOverlap(applied, op)) {
          failed.push({
            originalIndex: op.originalIndex,
            reason: '与已应用的编辑块重叠'
          });
          continue;
        }
        
        currentContent = 
          currentContent.slice(0, op.position) +
          op.replace +
          currentContent.slice(op.endPosition);
          
        applied.push({
          originalIndex: op.originalIndex,
          position: op.position,
          length: op.replace.length,
          isFuzzy: op.isFuzzy || false
        });
      } else {
        failed.push({
          originalIndex: op.originalIndex,
          reason: op.error || '未找到匹配内容',
          suggestions: op.suggestions
        });
      }
    }
    
    return {
      success: failed.length === 0,
      newContent: currentContent,
      appliedEdits: applied.reverse(), // 恢复原始顺序展示
      failedEdits: failed
    };
  }
  
  private static planExecutionOrder(
    content: string,
    blocks: ReplaceBlock[],
    options: any
  ): Array<EditOperation & MatchResult & { error?: string }> {
    const operations: Array<EditOperation & MatchResult & { error?: string }> = [];
    
    for (let i = 0; i < blocks.length; i++) {
      const block = blocks[i];
      let result = SearchEngine.findExact(content, block.search);
      
      if (!result.found && options.relaxedMode) {
        result = SearchEngine.findRelaxed(content, block.search);
      }
      
      operations.push({
        ...result,
        search: block.search,
        replace: block.replace,
        originalIndex: i,
        error: result.found ? undefined : 'SEARCH_NOT_FOUND'
      });
    }
    
    return operations;
  }
  
  private static hasOverlap(
    applied: Array<{position: number; length: number}>,
    candidate: {position: number; endPosition: number}
  ): boolean {
    return applied.some(a => 
      (candidate.position >= a.position && candidate.position < a.position + a.length) ||
      (candidate.endPosition > a.position && candidate.endPosition <= a.position + a.length)
    );
  }
}
四、会话状态管理与安全机制
1. 强制 Read-before-Edit 验证
TypeScript
复制
class FileEditingGuard {
  private sessionReadFiles: Set<string> = new Set();
  private fileContentCache: Map<string, string> = new Map();
  private lastReadTime: Map<string, number> = new Map();
  private readonly CACHE_TTL = 60000; // 60秒缓存过期（防止基于过时内容编辑）
  
  // 记录读取操作
  recordRead(filePath: string, content: string) {
    this.sessionReadFiles.add(filePath);
    this.fileContentCache.set(filePath, content);
    this.lastReadTime.set(filePath, Date.now());
  }
  
  // 验证编辑权限
  validateEdit(filePath: string): { allowed: boolean; reason?: string } {
    // 1. 检查是否已读取
    if (!this.sessionReadFiles.has(filePath)) {
      return { 
        allowed: false, 
        reason: `安全策略禁止：必须先调用 read_file 读取 "${filePath}" 才能编辑。这是为了防止基于过时或假设的内容进行编辑。` 
      };
    }
    
    // 2. 检查缓存是否过期（文件可能被外部修改）
    const lastRead = this.lastReadTime.get(filePath) || 0;
    if (Date.now() - lastRead > this.CACHE_TTL) {
      return {
        allowed: false,
        reason: `缓存过期：文件 "${filePath}" 上次读取超过 ${this.CACHE_TTL/1000}秒前，请重新读取以确保内容最新。`
      };
    }
    
    return { allowed: true };
  }
  
  // 获取缓存内容用于对比
  getCachedContent(filePath: string): string | undefined {
    return this.fileContentCache.get(filePath);
  }
  
  // 验证编辑基于最新内容（防止并发修改）
  async verifyContentFreshness(
    filePath: string, 
    currentContent: string
  ): Promise<boolean> {
    const cached = this.fileContentCache.get(filePath);
    if (!cached) return false;
    
    // 如果缓存与当前磁盘内容不一致，说明文件已被外部修改
    if (cached !== currentContent) {
      // 检查是否只是换行符差异
      const normalizedCache = cached.replace(/\r\n/g, '\n');
      const normalizedCurrent = currentContent.replace(/\r\n/g, '\n');
      return normalizedCache === normalizedCurrent;
    }
    return true;
  }
}
2. 文件系统安全边界
TypeScript
复制
class FileSystemGuard {
  private allowedBasePaths: string[];
  private deniedPatterns: RegExp[] = [
    /\.git\//,
    /node_modules\//,
    /\.env$/i,
    /.*\.pem$/i,
    /.*\.key$/i
  ];
  
  constructor(basePaths: string[]) {
    this.allowedBasePaths = basePaths.map(p => path.resolve(p));
  }
  
  validatePath(filePath: string): { safe: boolean; reason?: string } {
    const absolute = path.resolve(filePath);
    
    // 1. 路径遍历检查
    const isWithinAllowed = this.allowedBasePaths.some(base => 
      absolute.startsWith(base + path.sep) || absolute === base
    );
    if (!isWithinAllowed) {
      return { safe: false, reason: '路径超出允许的工作区范围' };
    }
    
    // 2. 敏感文件检查
    for (const pattern of this.deniedPatterns) {
      if (pattern.test(absolute)) {
        return { safe: false, reason: `禁止访问敏感文件模式: ${pattern}` };
      }
    }
    
    return { safe: true };
  }
}

五、系统提示词（System Prompt）设计
Markdown
复制
代码
预览
你是 Kimi Code，一个具有文件编辑能力的 AI 编程助手。你拥有以下文件操作工具：

## 文件编辑规则（必须遵守）

### 1. 读取优先原则（强制）
**在任何修改文件的操作之前，你必须先使用 `read_file` 读取该文件。**
- 这是安全机制，防止基于过时假设编辑文件
- 如果文件较大（>200行），你可以只读取需要修改的部分（使用 offset/limit 参数）

### 2. 编辑格式规范
使用 `edit_file` 时，`diff` 参数必须遵循以下精确格式：

------- SEARCH
[从文件中完整复制的原始代码，包括：
 - 所有缩进空格
 - 行尾换行符
 - 完整函数或逻辑块，不要截断]
=======
[替换后的新代码]
+++++++ REPLACE

### 3. 选择编辑策略
- **小文件（<100行）或大重构（>30%变更）**：使用 `write_file` 完全重写
- **大文件局部修改**：使用 `edit_file` 精确替换
- **多处修改**：可以在一个 `edit_file` 调用中包含多个 SEARCH/REPLACE 块，按文件中出现顺序排列

### 4. 唯一性保证
确保每个 SEARCH 块在目标文件中**只匹配一个位置**。如果存在重复代码：
- 扩大 SEARCH 上下文（多包含几行周围代码）使其唯一
- 或使用 `replace_all: true` 替换所有匹配项（谨慎使用）

### 5. 错误处理
如果收到 `search_not_found` 错误：
1. 重新 `read_file` 确认文件当前内容
2. 检查 SEARCH 块是否包含隐形字符（如特殊空格）
3. 调整 SEARCH 范围，确保完全匹配（包括换行）

### 6. 最佳实践
- **不要**在 SEARCH 中包含行号
- **不要**尝试计算统一 diff 格式（unified diff）
- **始终**保留原始缩进（空格/Tab）
- **优先**匹配完整的函数、类或逻辑块，而非孤立的几行
- 编辑后，如果文件是代码文件，可以运行 linter/formatter 验证语法正确性
六、完整执行流程示例
TypeScript
复制
// 完整的服务端处理示例
async function handleEditFile(toolCall: ToolCall): Promise<ToolResult> {
  const { file_path, diff, replace_all = false, relaxed_mode = false } = toolCall.parameters;
  
  // 1. 安全检查
  const pathCheck = fileSystemGuard.validatePath(file_path);
  if (!pathCheck.safe) {
    return { error: `路径安全检查失败: ${pathCheck.reason}` };
  }
  
  const editCheck = editingGuard.validateEdit(file_path);
  if (!editCheck.allowed) {
    return { error: editCheck.reason };
  }
  
  try {
    // 2. 读取当前文件内容（与缓存对比确保新鲜度）
    const currentContent = await fs.readFile(file_path, 'utf-8');
    if (!editingGuard.verifyContentFreshness(file_path, currentContent)) {
      return { 
        error: `文件 "${file_path}" 自上次读取后已被修改（可能由外部编辑器或并发操作导致）。请重新读取文件。`,
        suggestion: "请调用 read_file 重新获取最新内容，然后基于最新内容构造 edit_file。"
      };
    }
    
    // 3. 解析 Diff
    const blocks = DiffParser.parse(diff);
    
    // 4. 应用编辑（顺序无关算法）
    const result = EditApplier.apply(currentContent, blocks, {
      relaxedMode: relaxed_mode,
      replaceAll: replace_all,
      allowOverlapping: false
    });
    
    // 5. 处理失败项
    if (!result.success) {
      const failedDetails = result.failedEdits.map(f => 
        `块 #${f.originalIndex + 1}: ${f.reason}\n` +
        (f.suggestions ? `相似建议:\n${f.suggestions.join('\n---\n')}` : '')
      ).join('\n\n');
      
      return {
        error: `部分编辑未能应用:\n${failedDetails}`,
        partialContent: result.newContent, // 返回已部分修改的内容供人工检查
        hint: "建议使用更长的 SEARCH 上下文（包含更多周围代码行）以确保唯一匹配。"
      };
    }
    
    // 6. 语法验证（如果是代码文件）
    const ext = path.extname(file_path);
    if (['.js', '.ts', '.py', '.json'].includes(ext)) {
      const syntaxError = validateSyntax(result.newContent, ext);
      if (syntaxError) {
        return {
          error: `编辑导致语法错误: ${syntaxError}`,
          suggestion: "请检查 SEARCH/REPLACE 块是否意外地截断了代码结构（如未闭合的括号）。"
        };
      }
    }
    
    // 7. 原子写入（先写临时文件，再重命名）
    const tempPath = `${file_path}.tmp.${Date.now()}`;
    await fs.writeFile(tempPath, result.newContent, 'utf-8');
    await fs.rename(tempPath, file_path);
    
    // 8. 更新缓存
    editingGuard.recordRead(file_path, result.newContent);
    
    return {
      success: true,
      message: `成功应用 ${result.appliedEdits.length} 个编辑块`,
      appliedPositions: result.appliedEdits,
      fileSize: result.newContent.length,
      lineCount: result.newContent.split('\n').length
    };
    
  } catch (error) {
    return {
      error: `编辑执行失败: ${error.message}`,
      stack: process.env.NODE_ENV === 'development' ? error.stack : undefined
    };
  }
}
七、错误处理与重试策略
表格
错误类型	检测方式	自动重试策略	返回给 LLM 的信息
SEARCH_NOT_FOUND	字符串匹配失败	尝试宽松匹配（若启用 relaxed_mode）	原始 SEARCH 块 + 建议的相似匹配项
MULTIPLE_MATCHES	发现多个相同 SEARCH 块	拒绝执行，提示扩大上下文	所有匹配位置的行号和上下文
CONTENT_STALE	缓存与磁盘不一致	自动重新读取并提示	说明文件已被外部修改，需重新构造编辑
SYNTAX_ERROR	编辑后解析失败	拒绝写入，返回错误	语法错误详情和出错的代码行
OVERLAPPING_EDITS	多个块影响同一区域	按最小区块拆分或拒绝	指出冲突的块编号
八、针对 Kimi 的特别优化建议
利用长上下文：对于中等文件（100-500行），可以直接让 Kimi 使用 write_file 重写，减少出错概率，因为 Kimi 的上下文窗口通常充足（200K+ tokens）。
中文注释处理：如果代码中包含中文，确保在 Prompt 中强调保留原始编码和换行符，防止 Windows(CRLF) 和 Unix(LF) 换行符混用导致匹配失败。
批量编辑优化：Kimi 可以一次性处理多个 edit_file 调用，利用这一点将不相关的文件修改并行发送，提高响应效率。
Fallback 机制：当 edit_file 连续失败 2 次后，自动降级建议 Kimi 使用 write_file 完全重写该文件，避免陷入循环纠错。