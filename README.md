# 📚 ebook-to-obsidian

从合法免费来源搜索、下载电子书并转换为 Markdown 存入 [Obsidian](https://obsidian.md) 知识库。

[![Skill Version](https://img.shields.io/badge/version-v18.2-blue)](https://github.com/pengyicai203-gif/ebook-to-obsidian)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Security](https://img.shields.io/badge/security-audited-brightgreen)](#security)

## ✨ 核心特性

- **多格式支持**：PDF / EPUB / HTML / TXT 自动识别并转换为结构化 Markdown
- **HTML 结构化转换**：保留标题层级、代码块（含语言标识）、表格、列表、引用等格式
- **合法来源优先**：内置 20+ 本已验证的合法免费书源注册表，支持 Project Gutenberg 在线搜索
- **Obsidian 集成**：自动生成 YAML frontmatter（title/author/year/source/license/tags 等）
- **笔记框架**：版权保护书籍自动创建笔记框架 + 在线阅读链接
- **分批抓取**：大 HTML 书籍（>10 章）自动分批处理，避免超时
- **断点续传**：批量处理自动保存进度，中断后可恢复
- **MOC 生成**：自动生成 Obsidian MOC 索引文件

## 🚀 快速开始

### 环境要求

- Python 3.9+
- 桌面端推荐（沙箱部分网站受限）

### 安装依赖

```bash
pip install -r requirements.txt
```

### 环境检查

```bash
python main.py setup
```

### 使用示例

```bash
# 浏览内置书源
python main.py registry
python main.py registry AI_ML          # 按领域筛选

# 查找书籍
python main.py find "Think Python"
python main.py find "Deep Learning" "Goodfellow"

# 在线搜索
python main.py search "python" programming

# 批量处理
python main.py process /path/to/output books.json --moc
```

### 作为 Coze 技能使用

在 [Coze](https://www.coze.cn) 中搜索并安装「电子书获取与转换」技能，即可在对话中直接使用：

> "帮我找一本 Python 入门的书，下载后存到我的 Obsidian 知识库"

## 📖 支持的书源

### 内置注册表（零网络，秒级响应）

| 领域 | 书籍 |
|------|------|
| AI/ML | Deep Learning, Interpretable ML, D2L, Math for ML, RL: An Introduction |
| 编程 | Eloquent JavaScript, Think Python, Automate Boring Stuff, OSTEP |
| 写作 | Elements of Style, On the Art of Writing, How to Write Clearly |

### 在线搜索

- **Project Gutenberg** — 公共领域经典著作
- **Agent search_web** — 注册表和 Gutenberg 均未覆盖的书籍

## 🔄 转换质量

| 源格式 | 方法 | Markdown 质量 |
|--------|------|:---:|
| HTML | 结构化转换（保留代码块/表格/列表） | ⭐⭐⭐ |
| EPUB | zipfile 解压 → HTML 结构化转换 | ⭐⭐⭐ |
| TXT | 直接读取 | ⭐⭐ |
| PDF | pdfplumber → PyPDF2 → pdfminer | ⭐ |

## 🔒 Security

v18.2 通过了最严格的安全审计，修复了 11 个安全漏洞：

| 级别 | 漏洞 | 修复方案 |
|------|------|---------|
| 🔴 CRITICAL | SSRF bypass via redirect | 手动重定向跟踪 + 每跳 SSRF 验证 |
| 🔴 CRITICAL | download_file 无 SSRF 防护 | `_validate_url_ssrf` + 重定向验证 |
| 🔴 CRITICAL | YAML frontmatter 注入 | `_yaml_escape` 三重防护 |
| 🟠 HIGH | 下载无大小限制 | `MAX_DOWNLOAD_SIZE=200MB` |
| 🟠 HIGH | DNS rebinding SSRF | `socket.getaddrinfo` + `ipaddress` 验证 |
| 🟡 MEDIUM | 替代 IP 格式绕过 | `_is_private_ip` 支持 hex/octal/decimal |
| 🟡 MEDIUM | FD 泄漏 | `fd_closed` 布尔标志 |
| 🟡 MEDIUM | tmp_dir 权限过宽 | `chmod 0o700` |
| 🟡 MEDIUM | URL 参数注入 | `quote_plus` 编码 |
| 🟡 MEDIUM | 进度文件非原子写入 | 改用 `_atomic_write` |

### 安全架构

- **SSRF 三层防护**：URL 入口验证 → 重定向目标验证 → DNS 解析后 IP 验证
- **YAML 注入防护**：换行→空格 / `---`→`—` / 单引号包裹 + 引号双写转义
- **原子写入**：所有文件通过 `_atomic_write()` 先写临时文件再重命名
- **下载大小限制**：200MB 上限防止 DoS

## 📁 文件结构

```
ebook-to-obsidian/
├── SKILL.md           # 技能指令文件（Coze 技能元数据 + 使用文档）
├── main.py            # 核心代码（约 2260 行）
├── requirements.txt   # Python 依赖
├── README.md          # 本文件
└── LICENSE            # MIT 许可证
```

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 License

MIT License — 详见 [LICENSE](LICENSE) 文件

## ⚠️ 免责声明

- 本工具仅从**合法免费来源**下载电子书
- 版权保护书籍只创建笔记框架 + 在线阅读链接，不提供盗版下载
- 用户需自行确保下载使用行为符合当地版权法规
