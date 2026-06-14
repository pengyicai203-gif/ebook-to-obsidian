---
name: ebook-to-obsidian
description: 从合法免费来源搜索、下载电子书并转换为Markdown存入Obsidian知识库；支持PDF/EPUB提取文本、HTML在线书籍抓取、版权判断、Obsidian frontmatter生成、笔记框架创建；内置书源注册表（20+已验证书籍）和Gutenberg在线搜索；当用户提到找书、下载电子书、存入知识库、找编程书/AI书/写作书时触发
---

# 电子书获取与转换

从合法免费来源搜索电子书，下载并转换为带 Obsidian frontmatter 的 Markdown 文件存入知识库。

---

## 🚀 快速开始（新用户必读）

### 第一步：确认 Obsidian 路径

首次使用前，必须知道用户的 Obsidian Vault 路径。询问用户：

> "你的 Obsidian 知识库在哪个路径？例如 `/Users/xxx/Documents/Obsidian Vault`"

确认后，将路径记录到 MEMORY.md 供后续使用。

### 第二步：选择运行模式

根据当前环境选择最优执行方式：

| 场景 | 推荐方式 | 原因 |
|------|---------|------|
| **有桌面端设备** | 在桌面端直接运行 Python 脚本 | 网络无限制，所有书源可达 |
| **仅沙箱** | 沙箱内运行脚本 | 部分网站被沙箱防火墙拦截 |
| **桌面端 + 沙箱** | 桌面端为主，沙箱辅助 | 桌面端抓取后直接写入 Obsidian |

**⚠️ 沙箱网络限制**（实测已确认）：
- `github.io` → Connection Reset（无法访问）
- `gutendex.com` → 超时
- `deeplearningbook.org` → 部分可访问
- `gutenberg.org` → 通常可访问
- 桌面端无上述限制

**推荐执行流程**：在桌面端通过 bash 工具直接运行 Python 脚本，文件直接写入 Obsidian 目录。这是最稳定的方式。

### 第三步：运行环境检查

```bash
python main.py setup
```

此命令会检查 Python 版本、依赖库安装状态、网络连通性，并给出修复建议。

### 第四步：开始使用

```bash
# 浏览内置书源（零网络，秒级响应）
python main.py registry
python main.py registry AI_ML          # 按领域
python main.py registry writing        # 写作类

# 查找特定书籍
python main.py find "Think Python"
python main.py find "Deep Learning" "Goodfellow"

# 按领域搜索
python main.py search "python" programming
```

---

## 核心原则

- **只从合法免费来源下载**：作者开放授权、MIT Press Open Access、Creative Commons许可、公共领域（Project Gutenberg）、O'Reilly Free Ebook 等
- **版权保护书不盗版**：仍在版权期内且无免费授权的书，只创建笔记框架+在线阅读链接
- **优先转Markdown**：PDF/EPUB提取文本转MD，HTML-only书抓取在线内容转**结构化Markdown**（保留代码块、列表、表格等格式），无法抓取时创建笔记框架
- **标准frontmatter**：每本书必须包含 title/author/year/source/license/domain/tags/status/type/url

## 知识库路径配置

存放子目录规则（用户可自定义）：

| 领域 | 默认子目录 |
|------|--------|
| 写作 | `写作书籍/` |
| 编程 | `编程书籍/` |
| AI/ML | `AI_ML书籍/` |
| 其他 | 按领域创建子目录 |

完整路径 = `{Obsidian Vault路径}/01_Sources/{子目录}/`

## 书源注册表（零网络依赖）

内置 `BOOK_REGISTRY` 字典，覆盖 20+ 本已验证书籍，**无需网络请求**即可查找。

### 按领域分类

| 领域 | 书籍 | 格式 |
|------|------|------|
| AI/ML | Deep Learning, Interpretable ML, D2L, Math for ML, Prompt Engineering Book, RL: An Introduction, Understanding ML | html / pdf |
| 编程 | Eloquent JavaScript, Think Python, Automate Boring Stuff, OSTEP, AOSA, Software Architecture Patterns | html / pdf / epub |
| 写作 | Elements of Style, On the Art of Writing, How to Write Clearly, Writing of the Short Story, Art of Public Speaking, Short Story Writing | text |

### CLI 命令

```bash
python main.py registry                    # 全部
python main.py registry AI_ML              # 按领域
python main.py registry programming python # 按领域+关键词
python main.py find "Deep Learning"        # 查找单本书
python main.py search "machine learning" AI_ML  # 统一搜索
```

## HTML → Markdown 结构化转换

HTML书籍和EPUB内HTML均通过 `HTMLToMarkdown` 类做结构化转换，不再是暴力去标签。映射规则：

| HTML | → Markdown | 说明 |
|------|-----------|------|
| `<h1>`~`<h6>` | `#`~`######` | 标题层级完整保留 |
| `<pre><code class="language-python">` | ` ```python ` | 自动识别代码语言 |
| `<code>` | `` `inline` `` | 行内代码 |
| `<strong>/<b>` | `**bold**` | 加粗 |
| `<em>/<i>` | `*italic*` | 斜体 |
| `<ul><li>` | `- item` | 无序列表，支持嵌套 |
| `<ol><li>` | `1. item` | 有序列表 |
| `<table>` | Markdown表格 | 含表头分隔线 |
| `<a href>` | `[text](url)` | 链接 |
| `<blockquote>` | `> quote` | 引用块 |

**自动跳过的噪音标签**：`<nav>` `<header>` `<footer>` `<script>` `<style>` `<noscript>` `<iframe>` `<svg>` `<math>` `<form>` `<button>`

**内容区域提取**：优先从 `<main>` 或 `<article>` 提取正文区域，避免抓取整页导航。

## 工作流程

### Step 1: 解析需求 + 确认路径

从用户输入中提取主题/领域/具体书名/数量预期。检查 MEMORY.md 中是否有已配置的 Obsidian Vault 路径。

### Step 2: 搜索合法来源

**优先级：注册表 > Gutenberg 在线搜索 > search_web 手动搜索**

1. **内置注册表**（零网络，秒级响应）
2. **Project Gutenberg 在线搜索**（公版经典）
3. **Agent search_web 手动搜索**（注册表和Gutenberg都未覆盖的书）

**版权判断规则**：
- 出版年份 > 95年 且 作者已去世 > 50年 → 公共领域
- 作者明确声明免费/CC许可/开放获取 → 可下载
- 商业出版社仍在售 + 无免费授权声明 → 版权保护，只创建笔记框架

### Step 3: 确定获取方式

| 优先级 | 方式 | 适用场景 | 沙箱可用 | Markdown质量 |
|--------|------|---------|---------|-------------|
| 0 | 注册表命中 | book_info 中 format_hint 存在 | ✅ 零网络 | 取决于格式 |
| 1 | HTML在线抓取 | 有完整在线HTML版 | ✅ | ⭐⭐⭐ 结构化 |
| 2 | PDF下载+提取 | 有免费PDF下载链接 | ⚠️ 需pdfplumber | ⭐ 纯文本 |
| 3 | EPUB下载+提取 | 有免费EPUB下载链接 | ⚠️ 需unzip | ⭐⭐⭐ 结构化 |
| 4 | 笔记框架 | 版权保护/无法下载 | ✅ | N/A |

### Step 4: 下载与抓取

**HTML书籍**：v18 新增分批抓取能力。`max_pages > 10` 的书籍自动使用 `scrape_html_book_from_toc_batch()`，每批6章，避免超时。

**桌面端执行**（推荐）：用 bash 在桌面端运行 Python 脚本，直接写入 Obsidian 目录。

**沙箱执行**：HTML书籍可用，但部分网站可能被拦截。遇到 `SandboxBlockedError` 时应建议用户切换到桌面端。

### Step 5: 转换为 Markdown

| 源格式 | 提取方法 | Markdown质量 | 降级方案 |
|--------|---------|-------------|---------|
| HTML | html_to_markdown（结构化转换） | ⭐⭐⭐ | 笔记框架 |
| EPUB | zipfile解压HTML → html_to_markdown | ⭐⭐⭐ | 笔记框架 |
| PDF | pdfplumber → PyPDF2 → pdfminer | ⭐ 纯文本 | 笔记框架 |
| TXT | 直接读取 | ⭐⭐ 原文 | 笔记框架 |

**文本质量检查**：提取文本 < 500字符 → 视为提取失败，降级为笔记框架

**v18 输出验证**：每次写入后自动检查文件大小和内容，检测错误页面/截断文件，无效输出自动降级为笔记框架。

### Step 6: 生成 Obsidian 文件

完整文本和笔记框架各有标准模板，含 YAML frontmatter（title/author/year/source/license/domain/tags/status/type/url）。

文件命名：`{英文书名}_{作者姓}.md`，如 `Deep_Learning_Goodfellow.md`

### Step 7: 存入知识库

**桌面端**：通过 bash 在桌面端执行脚本直接写入 Obsidian 目录。
**沙箱**：文件保存到沙箱工作目录，由 Agent 通过 write_file 写入。

**v18 原子写入**：所有文件通过 `_atomic_write()` 写入，先写临时文件再重命名，防止中断产生半截文件。

### Step 8: 汇报结果

向用户汇报：
- ✅ N本成功转换为Markdown（附文件大小、来源格式）
- 📝 N本创建笔记框架（版权保护/无法抓取）
- ❌ N本下载失败（附原因和建议）
- 📂 存放路径

## Agent 执行最佳实践

### 推荐执行方式

```
1. 在桌面端 bash 执行 `python main.py setup` → 确认环境
2. 在桌面端 bash 执行 `python main.py find "书名"` → 查找书籍
3. 获取 book_info 后，在桌面端 bash 执行脚本直接处理并写入 Obsidian
```

### 沙箱环境注意事项

- **不要在沙箱内抓取 github.io 网站**（会被 Connection Reset）
- **沙箱抓取的大文件传到桌面端不可靠**：`file_to_url` 链接几分钟内过期
- **最佳方案**：直接在桌面端运行 Python 脚本从源站抓取
- 如果只能在沙箱运行，HTML书籍（非 github.io）通常可以成功

### 批量处理建议

- 使用 `batch_process()` 支持断点续传
- 大 HTML 书籍（>10章）自动分批抓取，每批6章
- 不要让子 session 直接写 Obsidian 文件——可能覆盖完整版为摘要版

## 断点续传（P3）

`batch_process()` 自动保存进度到 `{output_dir}/.progress.json`，中断后再次调用自动跳过已完成的书籍。

## MOC 自动生成（P4）

`generate_moc(output_dir)` 扫描领域子目录，为每个有书籍的目录生成 Obsidian MOC 索引文件。

```bash
python main.py moc /path/to/Obsidian/Vault/01_Sources
python main.py process /path/to/books books.json --moc  # 批量处理+自动MOC
```

## 边界情况

- **沙箱网络被拦截**：抛出 `SandboxBlockedError`，Agent 应建议用户切换到桌面端执行
- **HTML含JS渲染内容**：纯HTTP抓取拿不到JS渲染的正文，降级为笔记框架
- **PDF提取为空**：扫描版/图片PDF无法提取文本，降级为笔记框架
- **下载超时**：v18 自动重试2次（3秒退避），仍失败则降级
- **输出文件异常小**：v18 自动检测并降级为笔记框架
- **目录已存在同名文件(>50KB)**：跳过不覆盖，但会验证文件有效性
- **不确定版权状态**：保守处理，创建笔记框架而非下载
- **数学公式(MathML)**：自动跳过，LaTeX行内公式($...$)作为纯文本保留

## CLI 命令总览

| 命令 | 用途 |
|------|------|
| `python main.py setup` | 🆕 环境检查（新用户首选） |
| `python main.py registry [domain] [keyword]` | 浏览内置书源注册表 |
| `python main.py find "书名" [作者]` | 查找书籍（注册表→Gutenberg） |
| `python main.py search "关键词" [领域]` | 统一搜索 |
| `python main.py process <输出目录> <books.json>` | 批量处理书籍（支持断点续传） |
| `python main.py process <输出目录> <books.json> --moc` | 批量处理+自动生成MOC |
| `python main.py moc <输出目录>` | 单独生成MOC索引 |

## book_info 数据结构

| 字段 | 必填 | 说明 |
|------|------|------|
| title | ✅ | 书名 |
| author | ✅ | 作者 |
| year | | 出版年 |
| source | | 来源名称 |
| license | | 许可证 |
| domain | | 领域标签 |
| tags | | Obsidian tags |
| url | | 在线阅读链接 |
| desc | | 一句话描述 |
| file_base | | 输出文件名（不含.md） |
| download_url | | PDF/EPUB下载链接 |
| download_file | | 下载保存的文件名 |
| html_urls | | HTML章节URL列表（优先级最高） |
| toc_url | | 目录页URL（自动发现章节） |
| link_pattern | | 从目录页提取章节链接的正则 |
| base_url | | 解析相对链接的基础URL |
| max_pages | | 最大抓取页数（默认50，>10时自动分批） |
| outline | | 笔记框架的章节大纲 |
| note_reason | | 创建笔记框架的原因 |
