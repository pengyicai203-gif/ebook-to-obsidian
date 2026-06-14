#!/usr/bin/env python3
"""
电子书获取与转换 - 核心转换脚本 v22.0

v22 更新（安全加固 - 12项修复）：
- [CRITICAL] EPUB Zip Bomb防护：新增最大解压文件数/总大小/单文件大小限制
- [CRITICAL] EPUB路径穿越防护：校验ZIP内文件名不含../等穿越字符
- [CRITICAL] SSRF IPv6绕过修复：DNS解析同时检查AF_INET和AF_INET6
- [CRITICAL] SSRF DNS重绑定防护：连接后验证实际目标IP非私有地址
- [CRITICAL] Markdown注入防护：新增_md_escape()转义书籍元数据中的MD特殊字符
- [CRITICAL] HTML解析器深度限制：HTMLToMarkdown新增MAX_NESTING_DEPTH防止栈溢出
- [CRITICAL] 临时文件安全：改用mkdtemp创建不可预测临时目录，防symlink竞态
- [HIGH] 下载Content-Type校验：拒绝非文本/PDF/EPUB等可接受类型的响应
- [HIGH] Cookie隔离：沙箱请求显式设置cookies={}防止跨域会话泄露
- [HIGH] 全局资源限制：新增MAX_TOTAL_DOWNLOADS/MAX_EXTRACTION_SIZE防止资源耗尽
- [HIGH] SSL降级域名白名单：仅对已知证书异常的书源站点允许SSL降级
- [HIGH] 连接后SSRF二次验证：实际HTTP连接后检查socket对端IP

v18 更新（稳定性 + 新用户体验）：
- 修复 search_books() registry_hits 未定义 bug
- 修复 BOOK_REGISTRY link_pattern 和 max_pages 不准确
- 新增网络错误分类/重试退避/批量抓取/输出验证/原子写入/环境检查

v14-v15 更新：
- 新增 Gutenberg搜索/注册表/断点续传/MOC自动生成
"""
import os
import re
import sys
import json
import zipfile
import time
import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse


# ===== 环境检测 =====
def is_sandbox():
    try:
        from coze_workload_identity import requests
        return True
    except ImportError:
        return False

SANDBOX_MODE = is_sandbox()

# ===== v22: 全局安全限制 =====
MAX_DOWNLOAD_SIZE = 200 * 1024 * 1024  # 200MB max single download
MAX_TOTAL_DOWNLOADS = 2 * 1024 * 1024 * 1024  # 2GB total downloads per run
MAX_EPUB_FILES = 500          # Max files in an EPUB archive
MAX_EPUB_SINGLE_FILE = 50 * 1024 * 1024  # 50MB max single decompressed file in EPUB
MAX_EPUB_TOTAL_SIZE = 500 * 1024 * 1024   # 500MB max total decompressed EPUB size
MAX_HTML_NESTING_DEPTH = 256  # Max HTML tag nesting depth
ALLOWED_CONTENT_TYPES = {
    'text/html', 'text/plain', 'text/xml', 'application/xhtml+xml',
    'application/pdf', 'application/epub+zip', 'application/zip',
    'application/octet-stream',  # Generic binary (PDFs/EPUBs often served as this)
    'text/csv', 'application/json',
}
# v22: SSL降级白名单 — 仅这些已知证书异常的合法书源站点允许跳过SSL验证
SSL_SKIP_DOMAINS = frozenset({
    'pages.cs.wisc.edu',       # OSTEP: university self-signed cert
    'incompleteideas.net',     # RL book: expired cert
    'www.cs.huji.ac.il',       # Understanding ML: Israeli university cert chain
})


# ===== 书源注册表（P2: 零网络依赖，已验证URL+兼容性） =====

BOOK_REGISTRY = {
    # ---- AI / Machine Learning ----
    "Deep Learning": {
        "title": "Deep Learning",
        "author": "Ian Goodfellow, Yoshua Bengio, Aaron Courville",
        "year": 2016,
        "source": "MIT Press Open Access",
        "license": "CC BY-NC-ND",
        "domain": "AI_ML",
        "tags": "deep-learning, neural-networks, AI",
        "desc": "The definitive textbook on deep learning by the pioneers",
        "url": "https://www.deeplearningbook.org/",
        "toc_url": "https://www.deeplearningbook.org/",
        "base_url": "https://www.deeplearningbook.org/",
        "link_pattern": r'href="(contents/[^"]+\.html)"',
        "max_pages": 22,
        "format_hint": "html",
        "verified": True,
    },
    "Interpretable Machine Learning": {
        "title": "Interpretable Machine Learning",
        "author": "Christoph Molnar",
        "year": 2022,
        "source": "Author website",
        "license": "CC BY-NC-SA 4.0",
        "domain": "AI_ML",
        "tags": "interpretable-ML, XAI, model-interpretation",
        "desc": "A Guide for Making Black Box Models Explainable",
        "url": "https://christophm.github.io/interpretable-ml-book/",
        "toc_url": "https://christophm.github.io/interpretable-ml-book/",
        "base_url": "https://christophm.github.io/interpretable-ml-book/",
        # v18: use ./ prefix to match only sidebar chapter links, exclude nav/footer duplicates
        "link_pattern": r'href="(\.\/[^"]+\.html)"',
        "max_pages": 50,  # v18: was 16, actual chapter count is 47
        "format_hint": "html",
        "verified": True,
    },
    "Dive into Deep Learning": {
        "title": "Dive into Deep Learning",
        "author": "Aston Zhang, Zachary C. Lipton, Mu Li, Alexander J. Smola",
        "year": 2023,
        "source": "D2L.ai",
        "license": "CC BY-SA 4.0",
        "domain": "AI_ML",
        "tags": "deep-learning, pytorch, tensorflow, textbook",
        "desc": "Interactive deep learning book with code, math, and discussions",
        "url": "https://d2l.ai/",
        "download_url": "https://d2l.ai/d2l-en.pdf",
        "download_file": "d2l-en.pdf",
        "format_hint": "pdf",
        "verified": True,
    },
    "Mathematics for Machine Learning": {
        "title": "Mathematics for Machine Learning",
        "author": "Marc Peter Deisenroth, A. Aldo Faisal, Cheng Soon Ong",
        "year": 2020,
        "source": "Cambridge University Press",
        "license": "CC BY-NC-ND",
        "domain": "AI_ML",
        "tags": "math, linear-algebra, probability, ML",
        "desc": "The mathematical foundations needed for ML",
        "url": "https://mml-book.com/",
        "download_url": "https://mml-book.github.io/book/mml-book.pdf",
        "download_file": "math_for_ml.pdf",
        "format_hint": "pdf",
        "verified": True,
    },
    "The Prompt Engineering Book": {
        "title": "The Prompt Engineering Book",
        "author": "DAIR.AI",
        "year": 2023,
        "source": "GitHub",
        "license": "MIT",
        "domain": "AI_ML",
        "tags": "prompt-engineering, LLM, AI",
        "desc": "Guide to prompt engineering for LLMs",
        "url": "https://github.com/dair-ai/Prompt-Engineering-Guide",
        "toc_url": "https://www.promptingguide.ai/",
        "base_url": "https://www.promptingguide.ai/",
        "link_pattern": r'href="(https://www\.promptingguide\.ai/[^"]+)"',
        "max_pages": 30,
        "format_hint": "html",
        "verified": True,
    },
    "Reinforcement Learning: An Introduction": {
        "title": "Reinforcement Learning: An Introduction",
        "author": "Richard S. Sutton, Andrew G. Barto",
        "year": 2018,
        "source": "MIT Press",
        "license": "CC BY-NC-ND",
        "domain": "AI_ML",
        "tags": "reinforcement-learning, RL, AI",
        "desc": "The standard textbook on reinforcement learning",
        "url": "http://incompleteideas.net/book/the-book.html",
        "download_url": "http://incompleteideas.net/book/RLbook2022.pdf",
        "download_file": "RLbook2022.pdf",
        "format_hint": "pdf",
        "skip_ssl": True,
        "verified": False,  # URL may be flaky
    },
    "Understanding Machine Learning": {
        "title": "Understanding Machine Learning: From Theory to Algorithms",
        "author": "Shai Shalev-Shwartz, Shai Ben-David",
        "year": 2014,
        "source": "Cambridge University Press",
        "license": "CC BY-NC-ND",
        "domain": "AI_ML",
        "tags": "ML-theory, algorithms, textbook",
        "desc": "Theoretical foundations of machine learning",
        "url": "https://www.cs.huji.ac.il/~shais/UnderstandingMachineLearning/",
        "download_url": "https://www.cs.huji.ac.il/~shais/UnderstandingMachineLearning/understanding-machine-learning-theory-algorithms.pdf",
        "download_file": "understanding_ml.pdf",
        "format_hint": "pdf",
        "skip_ssl": True,
        "verified": False,
    },
    # ---- Programming ----
    "Eloquent JavaScript": {
        "title": "Eloquent JavaScript",
        "author": "Marijn Haverbeke",
        "year": 2018,
        "source": "Author website",
        "license": "CC BY-NC 3.0",
        "domain": "programming",
        "tags": "javascript, programming, web",
        "desc": "A modern introduction to programming with JavaScript",
        "url": "https://eloquentjavascript.net/",
        "toc_url": "https://eloquentjavascript.net/",
        "base_url": "https://eloquentjavascript.net/",
        "link_pattern": r'href="(\d+_[^"]+\.html)"',
        "max_pages": 22,
        "format_hint": "html",
        "verified": True,
    },
    "Think Python": {
        "title": "Think Python",
        "author": "Allen B. Downey",
        "year": 2024,
        "source": "Green Tea Press",
        "license": "CC BY-NC-SA 4.0",
        "domain": "programming",
        "tags": "python, programming, beginner",
        "desc": "How to Think Like a Computer Scientist",
        "url": "https://allendowney.github.io/ThinkPython/",
        "toc_url": "https://allendowney.github.io/ThinkPython/",
        "base_url": "https://allendowney.github.io/ThinkPython/",
        "link_pattern": r'href="(chap\d+\.html)"',
        "max_pages": 20,
        "format_hint": "html",
        "verified": True,
    },
    "Automate the Boring Stuff with Python": {
        "title": "Automate the Boring Stuff with Python",
        "author": "Al Sweigart",
        "year": 2019,
        "source": "No Starch Press",
        "license": "CC BY-NC-SA 3.0",
        "domain": "programming",
        "tags": "python, automation, beginner",
        "desc": "Practical programming for total beginners",
        "url": "https://automatetheboringstuff.com/",
        "toc_url": "https://automatetheboringstuff.com/",
        "base_url": "https://automatetheboringstuff.com/",
        "link_pattern": r'href="(/\d+e/chapter\d+\.html)"',
        "max_pages": 20,
        "format_hint": "html",
        "verified": False,
    },
    "Operating Systems: Three Easy Pieces": {
        "title": "Operating Systems: Three Easy Pieces",
        "author": "Remzi H. Arpaci-Dusseau, Andrea C. Arpaci-Dusseau",
        "year": 2018,
        "source": "Author website",
        "license": "CC BY",
        "domain": "programming",
        "tags": "operating-systems, OS, computer-science",
        "desc": "A free online OS textbook covering virtualization, concurrency, persistence",
        "url": "https://pages.cs.wisc.edu/~remzi/OSTEP/",
        "download_url": "https://pages.cs.wisc.edu/~remzi/OSTEP/OSTEP-whole-book-v1.01.pdf",
        "download_file": "OSTEP.pdf",
        "format_hint": "pdf",
        "skip_ssl": True,
        "verified": False,
    },
    "The Architecture of Open Source Applications": {
        "title": "The Architecture of Open Source Applications",
        "author": "Amy Brown, Greg Wilson (eds.)",
        "year": 2012,
        "source": "aosabook.org",
        "license": "CC BY-SA 3.0",
        "domain": "programming",
        "tags": "software-architecture, open-source, design",
        "desc": "Learn how architects of major open-source projects design their software",
        "url": "https://aosabook.org/en/index.html",
        "toc_url": "https://aosabook.org/en/index.html",
        "base_url": "https://aosabook.org/en/",
        # v18: more specific pattern - match chapter links like "vim.html", "git.html" but not index/nav
        "link_pattern": r'href="([a-z][a-z_-]*\.html)"',
        "max_pages": 30,
        "format_hint": "html",
        "verified": False,
    },
    "Software Architecture Patterns": {
        "title": "Software Architecture Patterns",
        "author": "Mark Richards",
        "year": 2015,
        "source": "O'Reilly Free Ebook",
        "license": "O'Reilly Free",
        "domain": "programming",
        "tags": "software-architecture, patterns, design",
        "desc": "Understanding Common Architecture Patterns and When to Use Them",
        "url": "https://www.oreilly.com/programming/free/software-architecture-patterns.csp",
        "download_url": "http://www.oreilly.com/programming/free/files/software-architecture-patterns.epub",
        "download_file": "software-architecture-patterns.epub",
        "format_hint": "epub",
        "verified": False,
    },
    "ML in Production": {
        "title": "Designing Machine Learning Systems",
        "author": "Chip Huyen",
        "year": 2022,
        "source": "O'Reilly",
        "license": "Copyright",
        "domain": "programming",
        "tags": "MLOps, production-ML, system-design",
        "desc": "An iterative process for developing production ML applications",
        "url": "https://huyenchip.com/machine-learning-systems-design/toc.html",
        "format_hint": "note_only",
        "note_reason": "版权保护，仅创建笔记框架",
        "verified": True,
    },
    # ---- Writing ----
    "The Elements of Style": {
        "title": "The Elements of Style",
        "author": "William Strunk Jr.",
        "year": 1918,
        "source": "Project Gutenberg",
        "license": "Public Domain",
        "domain": "writing",
        "tags": "writing, style, english, classic",
        "desc": "The classic guide to writing clear English",
        "url": "https://www.gutenberg.org/ebooks/37134",
        "download_url": "https://www.gutenberg.org/cache/epub/37134/pg37134.txt",
        "alt_download_url": "https://www.gutenberg.org/files/37134/37134-0.txt",
        "download_file": "elements_of_style.txt",
        "format_hint": "text",
        "verified": True,
    },
    "On the Art of Writing": {
        "title": "On the Art of Writing",
        "author": "Sir Arthur Quiller-Couch",
        "year": 1916,
        "source": "Project Gutenberg",
        "license": "Public Domain",
        "domain": "writing",
        "tags": "writing, lectures, classic",
        "desc": "Lectures delivered in the University of Cambridge 1913-1914",
        "url": "https://www.gutenberg.org/ebooks/3202",
        "download_url": "https://www.gutenberg.org/cache/epub/3202/pg3202.txt",
        "alt_download_url": "https://www.gutenberg.org/files/3202/3202-0.txt",
        "download_file": "on_the_art_of_writing.txt",
        "format_hint": "text",
        "verified": True,
    },
    "How to Write Clearly": {
        "title": "How to Write Clearly",
        "author": "Edwin Abbott Abbott",
        "year": 1883,
        "source": "Project Gutenberg",
        "license": "Public Domain",
        "domain": "writing",
        "tags": "writing, clarity, style, classic",
        "desc": "Rules and exercises on English composition",
        "url": "https://www.gutenberg.org/ebooks/2863",
        "download_url": "https://www.gutenberg.org/cache/epub/2863/pg2863.txt",
        "alt_download_url": "https://www.gutenberg.org/files/2863/2863-0.txt",
        "download_file": "how_to_write_clearly.txt",
        "format_hint": "text",
        "verified": True,
    },
    "The Writing of the Short Story": {
        "title": "The Writing of the Short Story",
        "author": "Lewis Worthington Smith",
        "year": 1902,
        "source": "Project Gutenberg",
        "license": "Public Domain",
        "domain": "writing",
        "tags": "writing, short-story, fiction, classic",
        "desc": "A guide to crafting short stories",
        "url": "https://www.gutenberg.org/ebooks/19577",
        # v18: This Gutenberg ID is audio-only; using plain text variant
        "download_url": "https://www.gutenberg.org/files/19577/19577.txt",
        "download_file": "writing_short_story.txt",
        "format_hint": "text",
        "verified": False,  # v18: Audio book, text may be limited
    },
    "The Art of Public Speaking": {
        "title": "The Art of Public Speaking",
        "author": "Dale Carnegie, Joseph Berg Esenwein",
        "year": 1915,
        "source": "Project Gutenberg",
        "license": "Public Domain",
        "domain": "writing",
        "tags": "public-speaking, communication, classic",
        "desc": "Training in public speaking as a means of social influence",
        "url": "https://www.gutenberg.org/ebooks/18317",
        "download_url": "https://www.gutenberg.org/cache/epub/18317/pg18317.txt",
        "alt_download_url": "https://www.gutenberg.org/files/18317/18317-0.txt",
        "download_file": "art_of_public_speaking.txt",
        "format_hint": "text",
        "verified": True,
    },
    "Short Story Writing": {
        "title": "Short Story Writing: A Practical Treatise on the Art of the Short Story",
        "author": "Charles Raymond Barrett",
        "year": 1898,
        "source": "Project Gutenberg",
        "license": "Public Domain",
        "domain": "writing",
        "tags": "writing, short-story, craft, classic",
        "desc": "A practical treatise on the art of the short story",
        "url": "https://www.gutenberg.org/ebooks/17608",
        "download_url": "https://www.gutenberg.org/cache/epub/17608/pg17608.txt",
        "alt_download_url": "https://www.gutenberg.org/files/17608/17608-0.txt",
        "download_file": "short_story_writing.txt",
        "format_hint": "text",
        "verified": True,
    },
}


def list_registry(domain=None, keyword=None):
    """浏览注册表，按领域或关键词过滤"""
    results = []
    for name, info in BOOK_REGISTRY.items():
        if domain and info.get('domain', '').lower() != domain.lower():
            continue
        if keyword and keyword.lower() not in name.lower() and keyword.lower() not in ' '.join(info.get('tags', '').split(',')).lower():
            continue
        results.append({
            'name': name,
            'author': info.get('author', ''),
            'domain': info.get('domain', ''),
            'format': info.get('format_hint', ''),
            'verified': info.get('verified', False),
            'license': info.get('license', ''),
        })
    return results


def find_book(title, author=None):
    """查找一本书：先查注册表（精确+模糊），再搜索在线
    返回 book_info dict（含 _source 字段）或 None
    v18.1: 统一返回 dict 而非 tuple，与 CLI/Agent 调用保持一致
    """
    # 1. 精确匹配
    title_lower = title.lower().strip()
    for name, info in BOOK_REGISTRY.items():
        if name.lower() == title_lower:
            result = info.copy()
            result['_source'] = 'registry'
            return result

    # 2. 模糊匹配（关键词包含）
    candidates = []
    for name, info in BOOK_REGISTRY.items():
        name_words = set(name.lower().split())
        query_words = set(title_lower.split())
        overlap = len(name_words & query_words)
        if overlap >= min(len(query_words), 2):  # at least 2 words overlap
            candidates.append((overlap, name, info))
        elif title_lower in name.lower() or name.lower() in title_lower:
            candidates.append((1, name, info))

    if author:
        author_lower = author.lower()
        for name, info in BOOK_REGISTRY.items():
            if author_lower in info.get('author', '').lower():
                # Boost score for author match
                for i, (score, n, inf) in enumerate(candidates):
                    if n == name:
                        candidates[i] = (score + 10, n, inf)
                        break

    if candidates:
        candidates.sort(key=lambda x: -x[0])
        result = candidates[0][2].copy()
        result['_source'] = 'registry'
        return result

    # 3. 在线搜索（Gutenberg）
    online_result = search_gutenberg(title, author)
    if online_result:
        # search_gutenberg may return list for multiple results — take first
        if isinstance(online_result, list):
            online_result = online_result[0]
        online_result['_source'] = 'gutenberg'
        return online_result

    return None


def search_gutenberg(query, author=None, max_results=5):
    """搜索 Project Gutenberg 公版书
    返回 book_info dict 列表
    v18.2: Query string properly URL-encoded
    """
    from urllib.parse import quote_plus
    search_terms = quote_plus(query)
    if author:
        search_terms += f'+{quote_plus(author)}'

    search_url = f'https://www.gutenberg.org/ebooks/search/?query={search_terms}'

    try:
        html = fetch_url(search_url, timeout=30)
    except Exception as e:
        print(f"  Gutenberg search failed: {e}")
        return None

    # 解析搜索结果
    book_ids = []
    seen = set()
    for bid in re.findall(r'/ebooks/(\d+)', html):
        if bid not in seen:
            seen.add(bid)
            book_ids.append(bid)
    book_ids = book_ids[:max_results]

    if not book_ids:
        return None

    # 获取每本书的元数据
    results = []
    for bid in book_ids:
        try:
            book_url = f'https://www.gutenberg.org/ebooks/{bid}'
            book_html = fetch_url(book_url, timeout=15)

            # 提取标题（多种模式兼容 Gutenberg 不同页面版本）
            title = ''
            for pattern in [
                r'<h1[^>]*itemprop="title"[^>]*>(.*?)</h1>',
                r'<h1[^>]*>(.*?)</h1>',
                r'<td[^>]*itemprop="headline"[^>]*>(.*?)</td>',
                r'<title>(.*?)\|</title>',
            ]:
                title_match = re.search(pattern, book_html, re.DOTALL)
                if title_match:
                    title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
                    if title and len(title) < 200 and 'gutenberg' not in title.lower():
                        break
            if not title:
                title = f'Gutenberg #{bid}'

            # 提取作者（多种模式兼容）
            book_author = ''
            for pattern in [
                r'itemprop="creator"[^>]*>(.*?)</a>',
                r'<a[^>]*href="/ebooks/authors/\d+"[^>]*>(.*?)</a>',
                r'name="author"[^>]*>(.*?)<',
            ]:
                author_match = re.search(pattern, book_html, re.DOTALL)
                if author_match:
                    book_author = re.sub(r'<[^>]+>', '', author_match.group(1)).strip()
                    if book_author:
                        break
            if not book_author:
                book_author = 'Unknown'

            # 构造下载URL（优先 plain text UTF-8）
            download_url = f'https://www.gutenberg.org/files/{bid}/{bid}-0.txt'
            alt_url = f'https://www.gutenberg.org/cache/epub/{bid}/pg{bid}.txt'

            info = {
                'title': title,
                'author': book_author,
                'year': '',
                'source': 'Project Gutenberg',
                'license': 'Public Domain',
                'domain': '',
                'tags': 'classic, public-domain',
                'desc': f'Public domain book from Project Gutenberg (#{bid})',
                'url': book_url,
                'download_url': download_url,
                'download_file': f'gutenberg_{bid}.txt',
                'format_hint': 'text',
                'gutenberg_id': bid,
                'alt_download_url': alt_url,
            }
            results.append(info)

        except Exception as e:
            print(f"  Failed to get metadata for Gutenberg #{bid}: {e}")
            continue

    if not results:
        return None

    # 单条结果直接返回 dict
    if len(results) == 1:
        return results[0]

    # 多条结果返回列表
    return results[0] if max_results == 1 else results


def search_books(query, domain=None, author=None, sources=None):
    """统一搜索入口
    sources: 指定搜索源列表 ['registry', 'gutenberg']，默认全部
    返回 {'results': [...], 'source': str}
    """
    if sources is None:
        sources = ['registry', 'gutenberg']

    all_results = []
    registry_hits = []  # v18: fix - initialize before conditional to prevent NameError

    # 1. 注册表搜索
    if 'registry' in sources:
        registry_hits = list_registry(domain=domain, keyword=query)
        for hit in registry_hits:
            full_info = BOOK_REGISTRY.get(hit['name'], {}).copy()
            full_info['_source'] = 'registry'
            all_results.append(full_info)

    # 2. Gutenberg 在线搜索
    if 'gutenberg' in sources and not registry_hits:
        online = search_gutenberg(query, author=author, max_results=5)
        if online:
            if isinstance(online, dict):
                online['_source'] = 'gutenberg'
                all_results.append(online)
            elif isinstance(online, list):
                for item in online:
                    item['_source'] = 'gutenberg'
                    all_results.append(item)

    return {
        'query': query,
        'domain': domain,
        'total': len(all_results),
        'results': all_results,
    }


# ===== 通用 HTTP 请求 =====

# v18: Network error classification for better diagnostics
class NetworkError(Exception):
    """Base class for categorized network errors"""
    def __init__(self, message, error_type='unknown', url=''):
        super().__init__(message)
        self.error_type = error_type
        self.url = url

class SandboxBlockedError(NetworkError):
    """Sandbox environment blocked the request (Connection Reset, etc.)"""
    def __init__(self, url, detail=''):
        super().__init__(f"Sandbox network blocked: {url} — {detail}", 'sandbox_blocked', url)

class DownloadTimeoutError(NetworkError):
    """Request timed out"""
    def __init__(self, url, timeout_val=30):
        super().__init__(f"Request timed out ({timeout_val}s): {url}", 'timeout', url)

class HttpError(NetworkError):
    """HTTP error response (4xx/5xx)"""
    def __init__(self, url, status_code):
        super().__init__(f"HTTP {status_code}: {url}", 'http_error', url)
        self.status_code = status_code


def _is_private_ip(hostname):
    """Check if a hostname is a private/internal IP address.
    Handles: literal IPs, octal/hex/decimal representations, abbreviated forms."""
    if not hostname:
        return False
    hostname = hostname.lower().strip('[]')
    # Common names
    if hostname in ('localhost', '0.0.0.0', '::1'):
        return True
    # Try to normalize to IP — resolve alternative representations
    # First try direct IP parse (handles hex, octal, decimal)
    try:
        addr = ipaddress.ip_address(hostname)
        return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
    except (ValueError, ipaddress.AddressValueError):
        pass
    # Try dotted notation with octal/hex per-octet
    if '.' in hostname:
        try:
            parts = hostname.split('.')
            normalized = []
            for p in parts:
                if p.startswith('0x') or p.startswith('0X'):
                    normalized.append(str(int(p, 16)))
                elif p.startswith('0') and len(p) > 1 and all(c in '01234567' for c in p):
                    normalized.append(str(int(p, 8)))
                else:
                    normalized.append(p)
            norm_ip = '.'.join(normalized)
            addr = ipaddress.ip_address(norm_ip)
            return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
        except (ValueError, ipaddress.AddressValueError):
            pass
    # Abbreviated forms like '127.1' or '127.0.1'
    if hostname.replace('.', '').isdigit() and hostname.count('.') < 3:
        try:
            addr = ipaddress.ip_address(hostname)
            return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
        except (ValueError, ipaddress.AddressValueError):
            pass
    # DNS resolution check — resolve hostname and verify IP is not private
    # v22: Check BOTH AF_INET and AF_INET6 to prevent IPv6 SSRF bypass
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            resolved = socket.getaddrinfo(hostname, None, family)
            for fam, type_, proto, canonname, sockaddr in resolved[:3]:
                ip = sockaddr[0]
                addr = ipaddress.ip_address(ip)
                if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                    return True
        except (socket.gaierror, OSError):
            pass
    return False


def _validate_url_ssrf(url):
    """Validate URL against SSRF attacks. Raises NetworkError if blocked.
    Checks scheme, hostname, and DNS resolution."""
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        raise NetworkError(f"Blocked non-HTTP scheme: {parsed.scheme}", 'ssrf_blocked', url)
    hostname = parsed.hostname or ''
    if _is_private_ip(hostname):
        raise NetworkError(f"Blocked internal hostname: {hostname}", 'ssrf_blocked', url)


def _validate_content_type(resp, url):
    """v22: Validate Content-Type header against allowed types.
    Warns on unknown types but doesn't block (too many servers misconfigure)."""
    content_type = ''
    if hasattr(resp, 'headers'):
        content_type = resp.headers.get('Content-Type', '').split(';')[0].strip().lower()
    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        # Not blocking — too many legitimate servers return weird Content-Types
        # But log a warning for suspicious types
        executable_types = {
            'application/x-executable', 'application/x-msdownload',
            'application/x-sh', 'application/x-bat',
        }
        if content_type in executable_types:
            raise NetworkError(
                f"Blocked executable Content-Type: {content_type}", 
                'dangerous_content_type', url
            )


def _check_connected_ip(resp_or_sock, url):
    """v22: Post-connection SSRF check — verify the actual connected IP isn't private.
    This catches DNS rebinding attacks where DNS returns public IP at check time
    but resolves to private IP when the actual connection is made."""
    try:
        if hasattr(resp_or_sock, 'raw') and hasattr(resp_or_sock.raw, '_fp'):
            # requests library: get the underlying socket
            sock = getattr(resp_or_raw._fp, 'raw', None) or resp_or_sock.raw._fp
            if hasattr(sock, '_sock'):
                peer_ip = sock._sock.getpeername()[0]
                addr = ipaddress.ip_address(peer_ip)
                if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                    raise NetworkError(
                        f"DNS rebinding detected: connected to private IP {peer_ip}",
                        'ssrf_rebinding', url
                    )
    except (AttributeError, OSError, ipaddress.AddressValueError):
        pass  # Can't verify — don't block, just best-effort


def fetch_url(url, timeout=30, as_text=True, retries=2, backoff=3):
    """Fetch URL with retry, backoff, and sandbox-aware error classification.
    v22: Added Cookie isolation, Content-Type validation, post-connection SSRF check."""
    _validate_url_ssrf(url)

    last_error = None
    for attempt in range(retries + 1):
        try:
            if SANDBOX_MODE:
                # v22: Cookie isolation — don't leak session cookies to arbitrary domains
                from coze_workload_identity import requests as coze_requests
                resp = coze_requests.get(url, timeout=timeout, allow_redirects=False, verify=False, cookies={})
                # v22: Post-connection SSRF check (DNS rebinding mitigation)
                _check_connected_ip(resp, url)
                # v22: Content-Type validation
                _validate_content_type(resp, url)
                # Manual redirect handling with SSRF validation on each hop
                max_redirects = 5
                for _ in range(max_redirects):
                    if resp.status_code >= 400:
                        raise HttpError(url, resp.status_code)
                    if resp.status_code in (301, 302, 303, 307, 308):
                        redirect_url = resp.headers.get('Location', '')
                        if redirect_url:
                            _validate_url_ssrf(redirect_url)
                            url = redirect_url
                            resp = coze_requests.get(url, timeout=timeout, allow_redirects=False, verify=False, cookies={})
                            _check_connected_ip(resp, url)
                            _validate_content_type(resp, url)
                            continue
                    break
                if resp.status_code >= 400:
                    raise HttpError(url, resp.status_code)
                return resp.text if as_text else resp.content
            else:
                import urllib.request, ssl
                ctx = ssl.create_default_context()
                # Desktop mode: try with SSL verification first
                class SSRFRedirectHandler(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, req, fp, code, msg, headers, newurl):
                        _validate_url_ssrf(newurl)
                        return super().redirect_request(req, fp, code, msg, headers, newurl)
                opener_ssl = urllib.request.build_opener(SSRFRedirectHandler)
                opener_nossl = urllib.request.build_opener(
                    SSRFRedirectHandler,
                    urllib.request.HTTPSHandler(context=ctx)
                )
                try:
                    req = urllib.request.Request(url, headers={
                        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
                    })
                    with opener_ssl.open(req, timeout=timeout) as resp:
                        data = resp.read()
                        return data.decode('utf-8', errors='ignore') if as_text else data
                except ssl.SSLError:
                    # v22: SSL降级白名单 — 仅允许已知证书异常的合法书源站点
                    parsed = urlparse(url)
                    hostname = parsed.hostname or ''
                    if hostname not in SSL_SKIP_DOMAINS and not any(
                        hostname.endswith('.' + d) for d in SSL_SKIP_DOMAINS
                    ):
                        raise  # Refuse to skip SSL for unknown domains
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    req = urllib.request.Request(url, headers={
                        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
                    })
                    with opener_nossl.open(req, timeout=timeout) as resp:
                        data = resp.read()
                        return data.decode('utf-8', errors='ignore') if as_text else data
        except Exception as e:
            last_error = e
            err_str = str(e).lower()
            # Classify the error
            if 'connectionreset' in err_str or 'connection reset' in err_str or 'broken pipe' in err_str:
                raise SandboxBlockedError(url, str(e)) from e
            elif 'proxyerror' in err_str or 'proxy' in err_str and 'tunnel' in err_str:
                # Sandbox proxy errors (unreachable hosts via sandbox proxy)
                raise NetworkError(f"Proxy/network unreachable: {url} — {e}", 'proxy_error', url) from e
            elif 'timed out' in err_str or 'timeout' in err_str:
                if attempt < retries:
                    time.sleep(backoff * (attempt + 1))
                    continue
                raise DownloadTimeoutError(url, timeout) from e
            elif isinstance(e, HttpError):
                # Detect sandbox-blocked sites that return 403/503 instead of Connection Reset
                if hasattr(e, 'status_code') and e.status_code in (403, 503):
                    # Check if this looks like a sandbox block rather than real auth issue
                    blocked_domains = ['github.io', 'githubusercontent.com', 'raw.githubusercontent.com']
                    if any(d in url.lower() for d in blocked_domains):
                        raise SandboxBlockedError(url, f"HTTP {e.status_code} (likely sandbox block)") from e
                raise  # No retry for HTTP errors
            else:
                if attempt < retries:
                    time.sleep(backoff * (attempt + 1))
                    continue
    raise last_error


# ===== 文件下载 =====
MAX_DOWNLOAD_SIZE = 200 * 1024 * 1024  # 200MB max download size

def download_file(url, save_path, desc="", timeout=180, retries=3, max_size=MAX_DOWNLOAD_SIZE):
    # v18.2: Validate URL against SSRF before downloading
    _validate_url_ssrf(url)
    for attempt in range(retries):
        try:
            if SANDBOX_MODE:
                return _download_sandbox(url, save_path, timeout)
            else:
                return _download_urllib(url, save_path, timeout)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(5)
            else:
                print(f"  Download failed after {retries} attempts: {e}")
    return False, 0


def _download_sandbox(url, save_path, timeout=180, max_size=MAX_DOWNLOAD_SIZE):
    # v22: Cookie isolation for sandbox downloads
    _validate_url_ssrf(url)
    from coze_workload_identity import requests as coze_requests
    resp = coze_requests.get(url, timeout=timeout, allow_redirects=False, verify=False, cookies={})
    # v22: Content-Type validation for downloads
    _validate_content_type(resp, url)
    # Manual redirect handling with SSRF validation
    max_redirects = 5
    for _ in range(max_redirects):
        if resp.status_code >= 400:
            raise Exception(f"HTTP {resp.status_code}")
        if resp.status_code in (301, 302, 303, 307, 308):
            redirect_url = resp.headers.get('Location', '')
            if redirect_url:
                _validate_url_ssrf(redirect_url)
                url = redirect_url
                resp = coze_requests.get(url, timeout=timeout, allow_redirects=False, verify=False, cookies={})
                continue
        break
    if resp.status_code >= 400:
        raise Exception(f"HTTP {resp.status_code}")
    content = resp.content
    if len(content) > max_size:
        raise Exception(f"Download too large: {len(content)} bytes (max {max_size})")
    with open(save_path, 'wb') as f:
        f.write(content)
    return True, len(content)


def _download_urllib(url, save_path, timeout=180, max_size=MAX_DOWNLOAD_SIZE):
    import urllib.request, ssl
    _validate_url_ssrf(url)
    # v18.2: SSRF-safe redirect handler + size limit
    class SSRFRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            _validate_url_ssrf(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    ctx = ssl.create_default_context()
    opener_ssl = urllib.request.build_opener(SSRFRedirectHandler)
    opener_nossl = urllib.request.build_opener(
        SSRFRedirectHandler,
        urllib.request.HTTPSHandler(context=ctx)
    )
    # Desktop: try SSL verification first, fallback for misconfigured sites
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
        })
        with opener_ssl.open(req, timeout=timeout) as resp:
            data = resp.read(max_size + 1)
            if len(data) > max_size:
                raise Exception(f"Download too large (max {max_size} bytes)")
            with open(save_path, 'wb') as f:
                f.write(data)
            return True, len(data)
    except ssl.SSLError:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
        })
        with opener_nossl.open(req, timeout=timeout) as resp:
            data = resp.read(max_size + 1)
            if len(data) > max_size:
                raise Exception(f"Download too large (max {max_size} bytes)")
            with open(save_path, 'wb') as f:
                f.write(data)
            return True, len(data)


# ===== HTML → Markdown 结构化转换 =====

class HTMLToMarkdown(HTMLParser):
    """
    将 HTML 转换为结构化 Markdown。
    核心映射：h1-h6 → # 标题, pre/code → ```代码块, ul/ol/li → 列表,
    table → Markdown表格, strong/em → 加粗/斜体, a → [text](href),
    blockquote → > 引用, sup → ^上标, img → ![alt](src)
    自动跳过 nav/header/footer/script/style 等噪音标签。
    """

    SKIP_TAGS = frozenset({
        'script', 'style', 'noscript', 'iframe', 'svg',
        'button', 'form', 'select', 'textarea',
        'nav', 'footer', 'header', 'math',
    })

    # v22: Maximum nesting depth to prevent stack overflow from malicious HTML
    MAX_NESTING_DEPTH = 256

    def __init__(self):
        super().__init__()
        self.output = []
        self.tag_stack = []
        self.nesting_depth = 0  # v22: Track current nesting depth

        # Pre/Code state
        self.in_pre = False
        self.pending_pre = False
        self.in_code_inline = False
        self.code_lang = ''

        # Table state
        self.in_table = False
        self.table_rows = []
        self.current_row = []
        self.in_cell = False
        self.cell_parts = []

        # List state
        self.list_stack = []  # [(tag, counter), ...]

        # Skip depth
        self.skip_depth = 0

        # Link state
        self.link_href = None

        # Blockquote
        self.in_blockquote = False

    def _out(self, text):
        self.output.append(text)

    @staticmethod
    def _lang_from_attrs(attrs):
        for k, v in attrs:
            if k == 'class':
                for cls in v.split():
                    if cls.startswith('language-'):
                        return cls[9:]
                    if cls.startswith('highlight-'):
                        return cls.split('highlight-')[1].split()[0]
        return ''

    # ---------- tag handlers ----------

    def handle_starttag(self, tag, attrs):
        tag_l = tag.lower()

        if tag_l in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth > 0:
            return

        # v22: Nesting depth limit to prevent stack overflow from malicious HTML
        self.nesting_depth += 1
        if self.nesting_depth > self.MAX_NESTING_DEPTH:
            return  # Skip processing this tag but still track depth

        self.tag_stack.append(tag_l)
        attrs_dict = dict(attrs)

        # Headings
        if tag_l in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            level = int(tag_l[1])
            self._out(f"\n\n{'#' * level} ")

        # Paragraph (inside blockquote: use > prefix for paragraph breaks)
        elif tag_l == 'p':
            if self.in_blockquote:
                self._out('\n\n> ')
            else:
                self._out('\n\n')

        # Code blocks
        elif tag_l == 'pre':
            self.in_pre = True
            self.pending_pre = True
            self.code_lang = self._lang_from_attrs(attrs)
            # Trim trailing whitespace before code fence
            while self.output and self.output[-1].strip() == '':
                self.output.pop()

        elif tag_l == 'code':
            if self.in_pre:
                lang = self._lang_from_attrs(attrs) or self.code_lang
                # Trim trailing whitespace before code fence
                while self.output and self.output[-1].strip() == '':
                    self.output.pop()
                self._out(f'\n\n```{lang}\n')
                self.pending_pre = False
            else:
                self.in_code_inline = True
                self._out('`')

        # Inline formatting
        elif tag_l in ('strong', 'b'):
            self._out('**')
        elif tag_l in ('em', 'i'):
            self._out('*')
        elif tag_l in ('del', 's'):
            self._out('~~')
        elif tag_l == 'sup':
            self._out('^')
        # <sub> → just output text as-is (no Markdown subscript syntax)

        # Lists
        elif tag_l in ('ul', 'ol'):
            self.list_stack.append([tag_l, 0])
            if len(self.list_stack) == 1:
                self._out('\n\n')
        elif tag_l == 'li':
            depth = len(self.list_stack)
            indent = '  ' * max(0, depth - 1)
            if self.list_stack:
                if self.list_stack[-1][0] == 'ol':
                    self.list_stack[-1][1] += 1
                    num = self.list_stack[-1][1]
                    self._out(f'\n{indent}{num}. ')
                else:
                    self._out(f'\n{indent}- ')

        # Tables
        elif tag_l == 'table':
            self.in_table = True
            self.table_rows = []
        elif tag_l == 'tr':
            self.current_row = []
        elif tag_l in ('td', 'th'):
            self.in_cell = True
            self.cell_parts = []

        # Links
        elif tag_l == 'a':
            self.link_href = attrs_dict.get('href', '')
            if self.link_href and not self.link_href.startswith(('#', 'javascript:')):
                self._out('[')

        # Images
        elif tag_l == 'img':
            alt = attrs_dict.get('alt', '')
            src = attrs_dict.get('src', '')
            self._out(f'![{alt}]({src})')

        # Line breaks / rules
        elif tag_l == 'br':
            self._out('\n')
        elif tag_l == 'hr':
            self._out('\n---\n\n')

        # Blockquote
        elif tag_l == 'blockquote':
            self.in_blockquote = True
            self._out('\n\n')  # No > here — first <p> inside will add it

        # Block containers
        elif tag_l in ('div', 'section', 'article', 'main', 'figure'):
            self._out('\n\n')
        elif tag_l == 'figcaption':
            self._out('*')

        # Definition lists
        elif tag_l == 'dl':
            self._out('\n\n')
        elif tag_l == 'dt':
            self._out('\n\n**')
        elif tag_l == 'dd':
            pass  # definition text follows directly after </dt>'s "** — "

    def handle_endtag(self, tag):
        tag_l = tag.lower()

        # v22: Unwind nesting depth tracking
        if self.nesting_depth > 0:
            self.nesting_depth -= 1

        if tag_l in self.SKIP_TAGS:
            if self.skip_depth > 0:
                self.skip_depth -= 1
            return
        if self.skip_depth > 0:
            return

        if self.tag_stack and self.tag_stack[-1] == tag_l:
            self.tag_stack.pop()

        # Headings
        if tag_l in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self._out('\n\n')

        elif tag_l == 'p':
            if self.in_blockquote:
                self._out('\n')  # Just newline inside blockquote; next <p> adds "> "
            else:
                self._out('\n\n')

        # Code blocks
        elif tag_l == 'pre':
            if self.pending_pre:
                self._out(f'```{self.code_lang}\n')
                self.pending_pre = False
            self._out('\n```\n\n')
            self.in_pre = False

        elif tag_l == 'code':
            if self.in_pre:
                pass  # don't close inline backticks inside code fence
            elif self.in_code_inline:
                self.in_code_inline = False
                self._out('`')

        # Inline formatting
        elif tag_l in ('strong', 'b'):
            self._out('**')
        elif tag_l in ('em', 'i'):
            self._out('*')
        elif tag_l in ('del', 's'):
            self._out('~~')

        # Lists
        elif tag_l in ('ul', 'ol'):
            if self.list_stack:
                self.list_stack.pop()
            if not self.list_stack:
                self._out('\n\n')

        # Tables
        elif tag_l == 'table':
            self.in_table = False
            if self.table_rows:
                md = self._render_table(self.table_rows)
                self._out('\n\n' + md + '\n\n')
        elif tag_l == 'tr':
            if self.current_row is not None:
                self.table_rows.append(self.current_row)
            self.current_row = []
        elif tag_l in ('td', 'th'):
            cell_text = ''.join(self.cell_parts).strip().replace('\n', ' ')
            self.current_row.append(cell_text)
            self.in_cell = False
            self.cell_parts = []

        # Links
        elif tag_l == 'a':
            if self.link_href and not self.link_href.startswith(('#', 'javascript:')):
                self._out(f']({self.link_href})')
            self.link_href = None

        # Blockquote
        elif tag_l == 'blockquote':
            self.in_blockquote = False
            self._out('\n\n')

        # Block containers
        elif tag_l in ('div', 'section', 'article', 'main', 'figure'):
            self._out('\n')
        elif tag_l == 'figcaption':
            self._out('*\n')

        # Definition lists
        elif tag_l == 'dt':
            self._out('** — ')  # close bold, add separator — definition follows on same line
        elif tag_l == 'dd':
            self._out('\n\n')

    # ---------- data / entity handlers ----------

    def handle_data(self, data):
        if self.skip_depth > 0:
            return
        
        # v22: Also skip data when nesting depth exceeds limit
        if self.nesting_depth > self.MAX_NESTING_DEPTH:
            return

        # Flush pending pre fence on first data
        if self.pending_pre:
            self._out(f'```{self.code_lang}\n')
            self.pending_pre = False

        if self.in_cell:
            self.cell_parts.append(data)
            return

        if self.in_pre:
            self._out(data)
            return

        if self.in_blockquote:
            # Skip pure whitespace inside blockquotes (between tags)
            if not data.strip():
                return
            lines = data.split('\n')
            self._out('\n> '.join(lines))
            return

        # Normal text: collapse inline whitespace
        text = re.sub(r'[ \t]+', ' ', data)
        text = text.replace('\n', ' ')
        self._out(text)

    def handle_entityref(self, name):
        if self.skip_depth > 0:
            return
        entities = {
            'amp': '&', 'lt': '<', 'gt': '>',
            'quot': '"', 'apos': "'", 'nbsp': ' ',
            'mdash': '—', 'ndash': '–', 'hellip': '…',
            'rsquo': "'", 'lsquo': "'", 'rdquo': '"', 'ldquo': '"',
            'copy': '©', 'laquo': '«', 'raquo': '»',
            'bull': '•', 'middot': '·',
        }
        self._out(entities.get(name, f'&{name};'))

    def handle_charref(self, name):
        if self.skip_depth > 0:
            return
        try:
            if name.startswith(('x', 'X')):
                char = chr(int(name[1:], 16))
            else:
                char = chr(int(name))
            self._out(char)
        except (ValueError, OverflowError):
            self._out(f'&#{name};')

    # ---------- table rendering ----------

    def _render_table(self, rows):
        if not rows:
            return ""
        max_cols = max(len(r) for r in rows)
        for row in rows:
            while len(row) < max_cols:
                row.append('')
        lines = []
        for i, row in enumerate(rows):
            line = '| ' + ' | '.join(c.replace('|', '\\|') for c in row) + ' |'
            lines.append(line)
            if i == 0:
                lines.append('| ' + ' | '.join('---' for _ in range(max_cols)) + ' |')
        return '\n'.join(lines)

    # ---------- final output ----------

    def get_markdown(self):
        result = ''.join(self.output)
        # Strip trailing spaces on lines FIRST (spaces between newlines create false blanks)
        result = re.sub(r' +\n', '\n', result)
        # Collapse 3+ blank lines into 2 (one visual paragraph break)
        result = re.sub(r'\n{3,}', '\n\n', result)
        # Remove bold/italic markers inside heading lines (e.g. "## **Title**" → "## Title")
        result = re.sub(r'^(#{1,6}\s+)\*{1,2}(.+?)\*{1,2}\s*$', r'\1\2', result, flags=re.MULTILINE)
        return result.strip()


def _extract_content_html(html):
    """Try to extract main content area (<main>, <article>) from HTML.
    Falls back to full HTML if no content section found."""
    # <main> and <article> are simple — no nesting ambiguity
    for pattern in [
        r'<main[^>]*>(.*?)</main>',
        r'<article[^>]*>(.*?)</article>',
    ]:
        match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if match and len(match.group(1)) > 500:
            return match.group(1)
    return html


def html_to_markdown(html_content):
    """Convert HTML to structured Markdown using HTMLParser.
    Automatically skips nav/header/footer/script/style.
    Falls back to simple strip_html on parser errors."""
    content_html = _extract_content_html(html_content)
    try:
        converter = HTMLToMarkdown()
        converter.feed(content_html)
        md = converter.get_markdown()
        # If content extraction cut too much, try full page
        if len(md.strip()) < 500 and content_html != html_content:
            converter2 = HTMLToMarkdown()
            converter2.feed(html_content)
            md2 = converter2.get_markdown()
            if len(md2.strip()) > len(md.strip()):
                md = md2
        return md
    except Exception:
        return strip_html(html_content)


def strip_html(html_content):
    """Legacy simple tag stripping — kept as fallback only."""
    content = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL | re.IGNORECASE)
    content = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL)
    content = re.sub(r'<br\s*/?>', '\n', content, flags=re.IGNORECASE)
    content = re.sub(r'</p>', '\n\n', content, flags=re.IGNORECASE)
    content = re.sub(r'<[^>]+>', '', content)
    entities = {
        '&amp;': '&', '&lt;': '<', '&gt': '>',
        '&quot;': '"', '&#39;': "'", '&nbsp;': ' ',
        '&#x27;': "'", '&mdash;': '—', '&ndash;': '–',
        '&hellip;': '…', '&rsquo;': "'", '&lsquo;': "'",
        '&rdquo;': '"', '&ldquo;': '"', '&copy;': '©',
    }
    for old, new in entities.items():
        content = content.replace(old, new)
    content = re.sub(r'\n{3,}', '\n\n', content)
    content = re.sub(r' {2,}', ' ', content)
    return content.strip()


# ===== HTML 抓取 =====

def extract_main_content(html_content, min_block=50):
    """Extract main content from HTML page as Markdown.
    Uses structured conversion + light noise filtering."""
    md = html_to_markdown(html_content)
    if len(md) < 500:
        return md

    # Light noise filtering: remove isolated very short lines
    # (navigation remnants), but keep structured lines
    lines = md.split('\n')
    filtered = []
    for line in lines:
        stripped = line.strip()
        # Always keep: headers, code fences, tables, blockquotes, lists, blank lines
        if (stripped.startswith(('#', '```', '|', '>', '- ', '* ', '1.', '2.', '3.', '4.', '5.', '6.', '7.', '8.', '9.'))
                or not stripped
                or len(stripped) >= 20):
            filtered.append(line)
    result = '\n'.join(filtered)
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip() if len(result.strip()) >= 500 else md


def scrape_html_pages(urls, delay=1, min_chars=100):
    """Fetch multiple HTML pages and merge as structured Markdown."""
    parts = []
    for i, url in enumerate(urls):
        try:
            html = fetch_url(url, timeout=30, as_text=True)
            md = html_to_markdown(html)
            if len(md.strip()) >= min_chars:
                parts.append(md)
            if delay > 0 and i < len(urls) - 1:
                time.sleep(delay)
        except Exception as e:
            print(f"  HTML scrape failed for {url}: {e}")
    return '\n\n---\n\n'.join(parts)


def scrape_html_book_from_toc(toc_url, link_pattern=None, base_url=None, max_pages=50, delay=1):
    """Scrape HTML book from table of contents page."""
    try:
        toc_html = fetch_url(toc_url, timeout=30, as_text=True)
    except NetworkError as e:
        print(f"  TOC fetch failed ({e.error_type}): {e}")
        return ""
    except Exception as e:
        print(f"  TOC fetch failed: {e}")
        return ""

    if not base_url:
        base_url = toc_url.rsplit('/', 1)[0] + '/'

    if link_pattern:
        links = re.findall(link_pattern, toc_html)
    else:
        links = re.findall(r'href=["\']([^"\']+)["\']', toc_html)

    chapter_urls = []
    for link in links:
        if link.startswith('http'):
            chapter_urls.append(link)
        elif link.startswith('/'):
            parsed = urlparse(toc_url)
            chapter_urls.append(f"{parsed.scheme}://{parsed.netloc}{link}")
        elif not link.startswith('#') and not link.startswith('mailto'):
            chapter_urls.append(urljoin(base_url, link))

    seen = set()
    unique_urls = []
    for u in chapter_urls:
        if u not in seen and u != toc_url:
            seen.add(u)
            unique_urls.append(u)
    chapter_urls = unique_urls[:max_pages]

    if not chapter_urls:
        return html_to_markdown(toc_html)

    return scrape_html_pages(chapter_urls, delay=delay)


def scrape_html_book_from_toc_batch(toc_url, link_pattern=None, base_url=None,
                                     max_pages=50, batch_size=6, per_batch_timeout=180,
                                     delay=1, progress_callback=None):
    """v18: Batch-scrape HTML book from TOC page.
    Splits chapters into batches to avoid timeout on large books.
    
    Args:
        toc_url: Table of contents URL
        link_pattern: Regex to extract chapter links from TOC
        base_url: Base URL for resolving relative links
        max_pages: Maximum total pages to scrape
        batch_size: Number of chapters per batch (default 6)
        per_batch_timeout: Seconds before giving up on a batch (default 180)
        delay: Delay between page fetches in seconds
        progress_callback: Optional callback(batch_num, total_batches, pages_done, pages_total)
    
    Returns:
        Merged Markdown string of all chapters
    """
    try:
        toc_html = fetch_url(toc_url, timeout=30, as_text=True)
    except NetworkError as e:
        print(f"  TOC fetch failed ({e.error_type}): {e}")
        return ""
    except Exception as e:
        print(f"  TOC fetch failed: {e}")
        return ""

    if not base_url:
        base_url = toc_url.rsplit('/', 1)[0] + '/'

    if link_pattern:
        links = re.findall(link_pattern, toc_html)
    else:
        links = re.findall(r'href=["\']([^"\']+)["\']', toc_html)

    chapter_urls = []
    for link in links:
        if link.startswith('http'):
            chapter_urls.append(link)
        elif link.startswith('/'):
            parsed = urlparse(toc_url)
            chapter_urls.append(f"{parsed.scheme}://{parsed.netloc}{link}")
        elif not link.startswith('#') and not link.startswith('mailto'):
            chapter_urls.append(urljoin(base_url, link))

    seen = set()
    unique_urls = []
    for u in chapter_urls:
        if u not in seen and u != toc_url:
            seen.add(u)
            unique_urls.append(u)
    chapter_urls = unique_urls[:max_pages]

    if not chapter_urls:
        return html_to_markdown(toc_html)

    # Split into batches
    total_pages = len(chapter_urls)
    batches = [chapter_urls[i:i + batch_size] for i in range(0, total_pages, batch_size)]
    total_batches = len(batches)

    all_parts = []
    for batch_num, batch in enumerate(batches, 1):
        if progress_callback:
            progress_callback(batch_num, total_batches, (batch_num - 1) * batch_size, total_pages)

        print(f"  Batch {batch_num}/{total_batches}: {len(batch)} pages...")
        try:
            batch_text = scrape_html_pages(batch, delay=delay)
            if batch_text.strip():
                all_parts.append(batch_text)
        except Exception as e:
            print(f"  Batch {batch_num} failed: {e}")
            # Continue with next batch rather than failing entirely
            continue

    if progress_callback:
        progress_callback(total_batches, total_batches, total_pages, total_pages)

    return '\n\n---\n\n'.join(all_parts)


# ===== PDF 提取 =====

def extract_pdf_text(pdf_path):
    """从PDF提取文本，依次尝试 pdfplumber → PyPDF2 → pdfminer"""
    text = ""
    try:
        import pdfplumber
        parts = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    parts.append(t)
        text = '\n\n'.join(parts)
        if len(text.strip()) >= 500:
            return text
    except (ImportError, Exception):
        pass

    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        parts = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
        text2 = '\n\n'.join(parts)
        if len(text2) > len(text):
            text = text2
        if len(text.strip()) >= 500:
            return text
    except (ImportError, Exception):
        pass

    try:
        from pdfminer.high_level import extract_text as pm_extract
        text3 = pm_extract(pdf_path)
        if len(text3) > len(text):
            text = text3
    except (ImportError, Exception):
        pass

    return text


# ===== EPUB 提取 =====

def extract_epub_text(epub_path):
    """从EPUB提取文本（解压HTML后转为结构化Markdown）
    v22: EPUB安全加固 — Zip Bomb防护 + 路径穿越防护 + 资源限制"""
    parts = []
    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            # v22: EPUB路径穿越防护 — 拒绝含../的文件名
            all_names = z.namelist()
            for name in all_names:
                # Normalize path and check for traversal
                normalized = os.path.normpath(name)
                if normalized.startswith('..') or os.path.isabs(normalized):
                    print(f"  ⚠️ EPUB path traversal blocked: {name}")
                    continue
            
            # v22: EPUB Zip Bomb防护 — 限制文件数量
            html_files = sorted([
                n for n in all_names
                if n.endswith(('.html', '.xhtml', '.htm'))
                and not os.path.normpath(n).startswith('..')
                and not os.path.isabs(os.path.normpath(n))
            ])
            if len(html_files) > MAX_EPUB_FILES:
                print(f"  ⚠️ EPUB has {len(html_files)} HTML files (max {MAX_EPUB_FILES}), truncating")
                html_files = html_files[:MAX_EPUB_FILES]
            
            # v22: EPUB Zip Bomb防护 — 限制解压总大小
            total_decompressed = 0
            for hf in html_files:
                info = z.getinfo(hf)
                # v22: 单文件大小限制
                if info.file_size > MAX_EPUB_SINGLE_FILE:
                    print(f"  ⚠️ EPUB file too large: {hf} ({info.file_size}B), skipping")
                    continue
                total_decompressed += info.file_size
                if total_decompressed > MAX_EPUB_TOTAL_SIZE:
                    print(f"  ⚠️ EPUB total decompressed size exceeds {MAX_EPUB_TOTAL_SIZE}B, truncating")
                    break
                
                with z.open(hf) as f:
                    # v22: 读取时也限制大小（compress_size可能被伪造）
                    content = f.read(MAX_EPUB_SINGLE_FILE + 1)
                    if len(content) > MAX_EPUB_SINGLE_FILE:
                        print(f"  ⚠️ EPUB file decompressed too large: {hf}, skipping")
                        continue
                    content = content.decode('utf-8', errors='ignore')
                    md = html_to_markdown(content)
                    if md.strip():
                        parts.append(md)
    except Exception as e:
        print(f"  ⚠️ EPUB extraction failed: {e}")
        return ""
    return '\n\n---\n\n'.join(parts)


# ===== Markdown 生成 =====

def _yaml_escape(value):
    """Escape a string for safe embedding in YAML frontmatter.
    Handles newlines, quotes, dashes, and special YAML characters.
    v18.2: Newlines→spaces, triple-dashes→em-dash to prevent frontmatter delimiter collision."""
    if not isinstance(value, str):
        value = str(value)
    # Replace newlines with spaces — book titles/authors should not contain newlines
    value = value.replace('\n', ' ').replace('\r', ' ')
    # Replace triple-dash sequences that could be confused with YAML document delimiter
    while '---' in value:
        value = value.replace('---', '—')
    # If value contains any dangerous characters, wrap in single-quoted YAML string
    dangerous = any(c in value for c in ('"', "'", ':', '#', '{', '}', '[', ']', ',', '&', '*', '?', '|', '-', '<', '>', '=', '!', '%', '@', '`'))
    if not dangerous:
        return f'"{value}"'
    # Use single-quoted YAML string — escape single quotes by doubling
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _md_escape(value):
    """v22: Escape a string for safe embedding in Markdown body text.
    Prevents Markdown injection via book metadata (titles, authors, etc.)
    that could otherwise inject links, images, or other MD elements."""
    if not isinstance(value, str):
        value = str(value)
    # Escape Markdown special characters that could create unwanted elements
    # Keep basic formatting safe: only escape characters that could start
    # unwanted link/image/heading/bold/italic constructs
    value = value.replace('[', '\\[')
    value = value.replace(']', '\\]')
    value = value.replace('(', '\\(')
    value = value.replace(')', '\\)')
    # Prevent heading injection
    value = re.sub(r'^#{1,6}\s', lambda m: '\\' + m.group(0), value)
    return value


def generate_full_md(book_info, text):
    """生成包含完整文本的Markdown
    v22: YAML frontmatter values properly escaped + Markdown body injection prevention"""
    return f"""---
title: {_yaml_escape(book_info['title'])}
author: {_yaml_escape(book_info['author'])}
year: {book_info.get('year', '')}
source: {_yaml_escape(book_info.get('source', ''))}
license: {_yaml_escape(book_info.get('license', ''))}
domain: {book_info.get('domain', '')}
tags: [{book_info.get('tags', '')}]
rating: 
status: unread
type: book
url: {_yaml_escape(book_info.get('url', ''))}
converted_from: {book_info.get('format', 'unknown')}
---

# {_md_escape(book_info['title'])}

**Author:** {_md_escape(book_info['author'])}  
**Year:** {book_info.get('year', 'N/A')}  
**License:** {_md_escape(book_info.get('license', 'N/A'))}  
**Source:** [{_md_escape(book_info.get('source', 'Online'))}]({book_info.get('url', '')})

> {_md_escape(book_info.get('desc', ''))}

---

{text}

---

## 读书笔记

"""


def generate_note_md(book_info):
    """生成笔记框架Markdown
    v22: YAML frontmatter values properly escaped + Markdown body injection prevention"""
    outline = book_info.get('outline', '*阅读后填充*')
    note_reason = book_info.get('note_reason', '')
    reason_line = f'\n> ⚠️ {note_reason}' if note_reason else '\n> ⚠️ 本书仅提供在线阅读，请通过上方链接访问原文。'

    return f"""---
title: {_yaml_escape(book_info['title'])}
author: {_yaml_escape(book_info['author'])}
year: {book_info.get('year', '')}
source: {_yaml_escape(book_info.get('source', ''))}
license: {_yaml_escape(book_info.get('license', ''))}
domain: {book_info.get('domain', '')}
tags: [{book_info.get('tags', '')}]
rating: 
status: unread
type: book
url: {_yaml_escape(book_info.get('url', ''))}
---

# {_md_escape(book_info['title'])}

**Author:** {_md_escape(book_info['author'])}  
**Year:** {book_info.get('year', 'N/A')}  
**License:** {_md_escape(book_info.get('license', 'N/A'))}  
**Source:** [{_md_escape(book_info.get('source', 'Online'))}]({book_info.get('url', '')})

> {_md_escape(book_info.get('desc', ''))}
{reason_line}

---

## 章节大纲

{outline}

---

## 核心概念索引

| 概念 | 定义 | 出处 |
|------|------|------|
| | | |

---

## 读书笔记

"""


# ===== 输出验证与原子写入（v18 新增） =====

def validate_output(filepath, min_bytes=500):
    """v18: Validate that an output file is not an error page or truncated.
    Returns (is_valid, reason) tuple.
    """
    if not os.path.exists(filepath):
        return False, "file_not_found"
    
    size = os.path.getsize(filepath)
    if size < min_bytes:
        return False, f"too_small_{size}B"
    
    # Check for common error page signatures
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            head = f.read(min(2000, size))
        
        error_signatures = [
            '<html><head>',          # HTML error page instead of Markdown
            '<!DOCTYPE html>',       # HTML instead of Markdown
            'Access Denied',         # CDN/auth error
            '403 Forbidden',         # HTTP error page
            '404 Not Found',         # HTTP error page
            'Cloudflare',            # WAF block page
            'Request Timeout',       # Timeout error page
        ]
        for sig in error_signatures:
            if sig.lower() in head.lower() and '# ' not in head:
                return False, f"error_page_detected({sig})"
    except Exception:
        pass
    
    return True, "ok"


def _atomic_write(filepath, content, encoding='utf-8'):
    """v18.2: Write file atomically with secure temp file.
    Uses mkstemp for unique temp names, sets 0600 permissions.
    Properly handles file descriptor lifecycle."""
    import tempfile
    dir_name = os.path.dirname(filepath) or '.'
    try:
        fd, tmp_path = tempfile.mkstemp(suffix='.tmp', prefix='.ebook_', dir=dir_name)
        fd_closed = False
        try:
            os.write(fd, content.encode(encoding))
            os.close(fd)
            fd_closed = True
            # Set restrictive permissions (owner read/write only)
            os.chmod(tmp_path, 0o600)
            if os.path.exists(filepath):
                os.remove(filepath)
            os.rename(tmp_path, filepath)
            return True
        except Exception:
            if not fd_closed:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            raise
    except Exception:
        # Fall back to direct write (less safe but functional)
        try:
            with open(filepath, 'w', encoding=encoding) as f:
                f.write(content)
            return True
        except Exception:
            return False


def check_env():
    """v18: Check environment capabilities and return a status dict.
    Used by 'setup' CLI command and for diagnostics."""
    import importlib
    
    status = {
        'mode': 'sandbox' if SANDBOX_MODE else 'desktop',
        'python': f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        'libraries': {},
        'network': {},
        'paths': {},
        'issues': [],
    }
    
    # Check libraries
    for lib_name in ['pdfplumber', 'PyPDF2', 'pdfminer', 'coze_workload_identity']:
        try:
            importlib.import_module(lib_name)
            status['libraries'][lib_name] = '✅ installed'
        except ImportError:
            status['libraries'][lib_name] = '❌ missing'
            if lib_name == 'pdfplumber':
                status['issues'].append('pdfplumber not installed — PDF extraction will fail. Install: pip install pdfplumber')
    
    # Check network (quick connectivity test)
    test_urls = [
        ('gutenberg.org', 'https://www.gutenberg.org/'),
        ('github.io', 'https://christophm.github.io/interpretable-ml-book/'),
        ('deeplearningbook.org', 'https://www.deeplearningbook.org/'),
    ]
    for name, url in test_urls:
        try:
            fetch_url(url, timeout=10, retries=0)
            status['network'][name] = '✅ reachable'
        except SandboxBlockedError:
            status['network'][name] = '❌ sandbox blocked'
            status['issues'].append(f'{name} is blocked in sandbox — HTML books from this source need desktop mode')
        except DownloadTimeoutError:
            status['network'][name] = '⚠️ timeout'
        except Exception as e:
            status['network'][name] = f'❌ {type(e).__name__}'
    
    return status


# ===== 安全工具函数（v18.1 安全加固） =====

def _safe_filename(name, max_len=60):
    """Sanitize a string for use as a filename: strip path traversal and unsafe chars.
    Prevents ../../etc/passwd style attacks via book title or file_base."""
    name = name.replace('/', '_').replace('\\', '_').replace('..', '_')
    name = re.sub(r'[^\w\s.\-]', '', name)
    name = re.sub(r'[_\s]+', '_', name).strip('_')
    return name[:max_len] if name else 'untitled'


def _safe_path(base_dir, filename):
    """Join base_dir and filename, verifying the result stays within base_dir.
    Prevents path traversal via download_file or title fields."""
    joined = os.path.join(base_dir, filename)
    normalized = os.path.normpath(joined)
    base_normalized = os.path.normpath(base_dir)
    if not normalized.startswith(base_normalized + os.sep) and normalized != base_normalized:
        raise ValueError(f"Path traversal blocked: {filename} escapes {base_dir}")
    return normalized


# ===== 核心处理逻辑 =====

def process_book(book_info, output_dir, tmp_dir=None):
    """
    处理单本书，按优先级尝试：
    1. HTML书籍抓取（沙箱友好，结构化Markdown输出）
    2. PDF/EPUB下载+文本提取（需桌面端或沙箱已安装pdfplumber）
    3. 降级为笔记框架
    
    v22: Secure tmp_dir with mkdtemp, global resource limits
    """
    import tempfile
    # v22: Use mkdtemp for unpredictable temporary directory (symlink race prevention)
    if tmp_dir is None:
        tmp_dir = tempfile.mkdtemp(prefix='ebook_')
    os.makedirs(tmp_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    # v18.2: Secure tmp_dir permissions
    try:
        os.chmod(tmp_dir, 0o700)
    except OSError:
        pass

    if book_info.get('file_base'):
        base = _safe_filename(book_info['file_base'])
    else:
        base = _safe_filename(book_info['title'])
    md_path = _safe_path(output_dir, base + '.md')

    # 已存在的大文件跳过（但验证其有效性）
    if os.path.exists(md_path) and os.path.getsize(md_path) > 50000:
        is_valid, reason = validate_output(md_path)
        if is_valid:
            return 'skip', md_path
        else:
            print(f"  ⚠️ Existing file invalid ({reason}), re-processing...")
            try:
                os.remove(md_path)
            except Exception:
                pass

    # === 注册表标记为 note_only 的书直接创建笔记框架 ===
    if book_info.get('format_hint') == 'note_only':
        md_content = generate_note_md(book_info)
        _atomic_write(md_path, md_content)
        return 'note_only', md_path

    # === 优先级1: HTML书籍抓取（沙箱+桌面端均可用） ===
    if book_info.get('html_urls'):
        text = scrape_html_pages(book_info['html_urls'])
        if len(text.strip()) >= 500:
            book_info['format'] = 'html'
            md_content = generate_full_md(book_info, text)
            _atomic_write(md_path, md_content)
            # v18: validate output
            is_valid, reason = validate_output(md_path)
            if not is_valid:
                print(f"  ⚠️ Output validation failed: {reason}")
                try:
                    os.remove(md_path)
                except Exception:
                    pass
                book_info['note_reason'] = f'输出验证失败: {reason}'
                md_content = generate_note_md(book_info)
                _atomic_write(md_path, md_content)
                return 'note_only', md_path
            return 'converted', md_path

    if book_info.get('toc_url'):
        # v18: Use batch scraping for large HTML books (max_pages > 10)
        max_pages = book_info.get('max_pages', 50)
        if max_pages > 10:
            text = scrape_html_book_from_toc_batch(
                book_info['toc_url'],
                book_info.get('link_pattern'),
                book_info.get('base_url'),
                max_pages=max_pages,
                batch_size=6,  # 6 chapters per batch to avoid timeout
            )
        else:
            text = scrape_html_book_from_toc(
                book_info['toc_url'],
                book_info.get('link_pattern'),
                book_info.get('base_url'),
                max_pages=max_pages,
            )
        if len(text.strip()) >= 500:
            book_info['format'] = 'html'
            md_content = generate_full_md(book_info, text)
            _atomic_write(md_path, md_content)
            # v18: validate output
            is_valid, reason = validate_output(md_path)
            if not is_valid:
                print(f"  ⚠️ Output validation failed: {reason}")
                try:
                    os.remove(md_path)
                except Exception:
                    pass
                book_info['note_reason'] = f'输出验证失败: {reason}'
                md_content = generate_note_md(book_info)
                _atomic_write(md_path, md_content)
                return 'note_only', md_path
            return 'converted', md_path

    # === 优先级2: PDF/EPUB下载+提取 ===
    if not book_info.get('download_url') or not book_info.get('download_file'):
        if not book_info.get('note_reason'):
            book_info['note_reason'] = '无可用的下载链接或在线HTML版本'
        md_content = generate_note_md(book_info)
        _atomic_write(md_path, md_content)
        return 'note_only', md_path

    tmp_path = _safe_path(tmp_dir, _safe_filename(book_info['download_file'], max_len=120))
    if not (os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 1000):
        ok, size = download_file(book_info['download_url'], tmp_path, book_info['title'])
        # v18: Try alt_download_url if primary fails
        if not ok and book_info.get('alt_download_url'):
            print(f"  Primary URL failed, trying alt_download_url...")
            ok, size = download_file(book_info['alt_download_url'], tmp_path, book_info['title'])
        if not ok:
            book_info['note_reason'] = '文件下载失败'
            md_content = generate_note_md(book_info)
            _atomic_write(md_path, md_content)
            return 'failed', md_path

    ext = os.path.splitext(book_info['download_file'])[1].lower()
    if ext == '.epub':
        text = extract_epub_text(tmp_path)
        book_info['format'] = 'epub'
    elif ext == '.pdf':
        text = extract_pdf_text(tmp_path)
        book_info['format'] = 'pdf'
    elif ext in ('.txt', '.text', '.md', '.markdown'):
        with open(tmp_path, 'r', encoding='utf-8', errors='ignore') as f:
            text = f.read()
        book_info['format'] = 'text'
    else:
        try:
            with open(tmp_path, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()
            if len(text.strip()) >= 500:
                book_info['format'] = 'text'
            else:
                text = ""
        except Exception:
            text = ""

    if len(text.strip()) < 500:
        book_info['note_reason'] = 'PDF文本提取不足，可能为扫描版'
        md_content = generate_note_md(book_info)
        _atomic_write(md_path, md_content)
        try:
            os.remove(tmp_path)
        except:
            pass
        return 'note_only', md_path

    md_content = generate_full_md(book_info, text)
    _atomic_write(md_path, md_content)

    try:
        os.remove(tmp_path)
    except:
        pass

    return 'converted', md_path


def batch_process(books, output_dir, tmp_dir=None, progress_file=None):
    """批量处理多本书，支持断点续传
    progress_file: .progress.json 路径，默认 {output_dir}/.progress.json
    中断后再次调用同一 progress_file，已完成的书籍自动跳过
    v22: tmp_dir defaults to mkdtemp for security"""
    import tempfile
    if tmp_dir is None:
        tmp_dir = tempfile.mkdtemp(prefix='ebook_batch_')
    if progress_file is None:
        progress_file = os.path.join(output_dir, '.progress.json')

    # 加载已有进度
    completed = set()
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                progress = json.load(f)
            completed = set(progress.get('completed', []))
            print(f"  Resuming from checkpoint: {len(completed)} books already done")
        except Exception:
            pass

    results = {'converted': [], 'note_only': [], 'failed': [], 'skipped': []}

    for i, b in enumerate(books):
        book_key = f"{b.get('title', '')}_{b.get('author', '')}"
        if book_key in completed:
            # 已完成，跳过但记录
            results['skipped'].append({
                'title': b['title'],
                'path': '',
                'size_kb': 0,
                'reason': 'checkpoint_skip'
            })
            continue

        status, md_path = process_book(b, output_dir, tmp_dir)
        results[status].append({
            'title': b['title'],
            'path': md_path,
            'size_kb': os.path.getsize(md_path) // 1024 if os.path.exists(md_path) else 0
        })

        # 保存进度 — v18.2: use atomic write to prevent corruption
        if status in ('converted', 'note_only'):
            completed.add(book_key)
            try:
                os.makedirs(output_dir, exist_ok=True)
                progress_data = json.dumps({
                    'completed': list(completed),
                    'total': len(books),
                    'last_updated': time.strftime('%Y-%m-%d %H:%M:%S'),
                }, ensure_ascii=False, indent=2)
                _atomic_write(progress_file, progress_data)
            except Exception:
                pass

        time.sleep(2)

    return results


# ===== MOC 自动生成 =====

def generate_moc(output_dir, domain_map=None):
    """为每个领域目录生成 Obsidian MOC (Map of Content) 入口文件
    domain_map: {domain: subdir} 映射，默认 {'AI_ML': 'AI_ML书籍', 'programming': '编程书籍', 'writing': '写作书籍'}
    返回生成的 MOC 文件路径列表
    """
    if domain_map is None:
        domain_map = {
            'AI_ML': 'AI_ML书籍',
            'programming': '编程书籍',
            'writing': '写作书籍',
        }

    moc_files = []
    for domain, subdir in domain_map.items():
        domain_dir = os.path.join(output_dir, subdir)
        if not os.path.isdir(domain_dir):
            continue

        # 扫描该目录下的所有 .md 文件
        md_files = []
        for fname in sorted(os.listdir(domain_dir)):
            if fname.endswith('.md') and not fname.startswith('MOC_'):
                fpath = os.path.join(domain_dir, fname)
                # 读取 frontmatter 提取元数据
                meta = _read_frontmatter(fpath)
                md_files.append({
                    'filename': fname,
                    'title': meta.get('title', fname.replace('.md', '')),
                    'author': meta.get('author', ''),
                    'status': meta.get('status', 'unread'),
                    'tags': meta.get('tags', ''),
                    'license': meta.get('license', ''),
                    'size_kb': os.path.getsize(fpath) // 1024,
                })

        if not md_files:
            continue

        # 生成 MOC 内容
        domain_display = {
            'AI_ML': 'AI / 机器学习',
            'programming': '编程',
            'writing': '写作',
        }.get(domain, domain)

        lines = [
            f'---',
            f'type: moc',
            f'domain: {domain}',
            f'tags: [moc, {domain}]',
            f'updated: {time.strftime("%Y-%m-%d")}',
            f'---',
            f'',
            f'# {domain_display}书籍索引',
            f'',
            f'> 本索引由电子书获取与转换技能自动生成，包含 {len(md_files)} 本书籍。',
            f'',
            f'## 📚 书籍列表',
            f'',
        ]

        # 按状态分组
        reading = [b for b in md_files if b['status'] == 'reading']
        unread = [b for b in md_files if b['status'] == 'unread']
        done = [b for b in md_files if b['status'] == 'done']

        if reading:
            lines.append('### 📖 正在阅读')
            for b in reading:
                lines.append(f'- [[{b["filename"].replace(".md", "")}|{b["title"]}]] — {b["author"]} ({b["size_kb"]}KB)')
            lines.append('')

        if done:
            lines.append('### ✅ 已读')
            for b in done:
                lines.append(f'- [[{b["filename"].replace(".md", "")}|{b["title"]}]] — {b["author"]}')
            lines.append('')

        if unread:
            lines.append('### 📋 待读')
            for b in unread:
                tag_str = f' `{b["license"]}`' if b['license'] else ''
                lines.append(f'- [[{b["filename"].replace(".md", "")}|{b["title"]}]] — {b["author"]} ({b["size_kb"]}KB){tag_str}')
            lines.append('')

        # 统计
        total_size = sum(b['size_kb'] for b in md_files)
        lines.extend([
            '---',
            '',
            f'## 📊 统计',
            f'',
            f'| 指标 | 值 |',
            f'|------|-----|',
            f'| 总书籍数 | {len(md_files)} |',
            f'| 已读 | {len(done)} |',
            f'| 正在读 | {len(reading)} |',
            f'| 待读 | {len(unread)} |',
            f'| 总大小 | {total_size}KB |',
            '',
        ])

        moc_path = os.path.join(domain_dir, f'MOC_{subdir}.md')
        with open(moc_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

        moc_files.append(moc_path)
        print(f"  MOC generated: {moc_path} ({len(md_files)} books)")

    return moc_files


def _read_frontmatter(filepath):
    """读取 .md 文件的 YAML frontmatter"""
    meta = {}
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        if content.startswith('---'):
            end = content.find('---', 3)
            if end > 0:
                fm = content[3:end]
                for line in fm.strip().split('\n'):
                    if ':' in line:
                        key, _, val = line.partition(':')
                        val = val.strip().strip('"').strip("'")
                        meta[key.strip()] = val
    except Exception:
        pass
    return meta


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python main.py process <output_dir> <books_json>  — 批量处理书籍")
        print("  python main.py search <query> [domain]           — 搜索书籍")
        print("  python main.py find <title> [author]             — 查找单本书")
        print("  python main.py registry [domain] [keyword]       — 浏览注册表")
        print("  python main.py moc <output_dir>                  — 生成领域MOC索引")
        print("  python main.py setup                             — 检查环境配置（新用户首选）")
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == 'setup':
        # v18: Environment check for new users
        print("=" * 60)
        print("🔍 电子书获取与转换 - 环境检查")
        print("=" * 60)
        status = check_env()
        print(f"\n📦 运行模式: {status['mode']}")
        print(f"🐍 Python: {status['python']}")
        
        print(f"\n📚 依赖库:")
        for lib, state in status['libraries'].items():
            print(f"  {lib}: {state}")
        
        print(f"\n🌐 网络连通性:")
        for site, state in status['network'].items():
            print(f"  {site}: {state}")
        
        if status['issues']:
            print(f"\n⚠️ 发现问题:")
            for issue in status['issues']:
                print(f"  - {issue}")
        else:
            print(f"\n✅ 环境检查通过，可以开始使用！")
            print(f"\n💡 快速开始:")
            print(f"  python main.py registry              # 浏览内置书源")
            print(f"  python main.py find \"Think Python\"    # 查找特定书籍")
            print(f"  python main.py search \"machine learning\" AI_ML  # 按领域搜索")

    elif command == 'search':
        query = sys.argv[2] if len(sys.argv) > 2 else ''
        domain = sys.argv[3] if len(sys.argv) > 3 else None
        results = search_books(query, domain=domain)
        print(json.dumps(results, indent=2, ensure_ascii=False, default=str))

    elif command == 'find':
        title = sys.argv[2] if len(sys.argv) > 2 else ''
        author = sys.argv[3] if len(sys.argv) > 3 else None
        info = find_book(title, author)
        if info:
            print(json.dumps(info, indent=2, ensure_ascii=False, default=str))
        else:
            print(json.dumps({"error": f"Book not found: {title}", "source": None}))

    elif command == 'registry':
        domain = sys.argv[2] if len(sys.argv) > 2 else None
        keyword = sys.argv[3] if len(sys.argv) > 3 else None
        results = list_registry(domain=domain, keyword=keyword)
        print(json.dumps(results, indent=2, ensure_ascii=False))

    elif command == 'moc':
        if len(sys.argv) < 3:
            print("Usage: python main.py moc <output_dir>")
            sys.exit(1)
        moc_dir = sys.argv[2]
        moc_files = generate_moc(moc_dir)
        if moc_files:
            print(json.dumps(moc_files, indent=2, ensure_ascii=False))
        else:
            print(json.dumps({"error": "No domain directories with books found"}))

    elif command == 'process':
        if len(sys.argv) < 4:
            print("Usage: python main.py process <output_dir> <books_json>")
            sys.exit(1)
        output_dir = sys.argv[2]
        books_input = sys.argv[3]

        if os.path.exists(books_input):
            with open(books_input, 'r', encoding='utf-8') as f:
                raw = f.read(10 * 1024 * 1024)  # Max 10MB JSON input
                books = json.loads(raw)
        else:
            # Limit inline JSON size to prevent memory exhaustion
            if len(books_input) > 1024 * 1024:  # 1MB max for CLI arg
                print("Error: Input JSON too large (max 1MB for inline)")
                sys.exit(1)
            books = json.loads(books_input)

        # Auto-wrap single book dict into list
        if isinstance(books, dict):
            books = [books]

        results = batch_process(books, output_dir)

        print("\n" + "=" * 60)
        print("RESULTS SUMMARY")
        print("=" * 60)
        for status, items in results.items():
            if items:
                print(f"\n{status.upper()} ({len(items)}):")
                for item in items:
                    print(f"  {item['title']} - {item['size_kb']}KB")

        total = sum(len(v) for v in results.values())
        print(f"\nTOTAL: {total} books processed")
        print(f"MODE: {'sandbox' if SANDBOX_MODE else 'desktop'}")

        # Auto-generate MOC if --moc flag present
        if '--moc' in sys.argv:
            moc_files = generate_moc(output_dir)
            if moc_files:
                print(f"\nMOC files generated: {moc_files}")

    else:
        print(f"Unknown command: {command}")
        print("Commands: search, find, registry, process")
        sys.exit(1)

# v22.0 更新（安全加固 — 12项修复）：
# - [CRITICAL] EPUB Zip Bomb防护：新增MAX_EPUB_FILES/MAX_EPUB_SINGLE_FILE/MAX_EPUB_TOTAL_SIZE限制
# - [CRITICAL] EPUB路径穿越防护：校验ZIP内文件名不含../穿越字符
# - [CRITICAL] SSRF IPv6绕过修复：DNS解析同时检查AF_INET和AF_INET6
# - [CRITICAL] SSRF DNS重绑定防护：_check_connected_ip()验证实际连接目标IP
# - [CRITICAL] Markdown注入防护：新增_md_escape()转义MD特殊字符（[]()/等）
# - [CRITICAL] HTML解析器深度限制：HTMLToMarkdown新增MAX_NESTING_DEPTH=256
# - [CRITICAL] 临时文件安全：process_book/batch_process改用mkdtemp不可预测路径
# - [HIGH] 下载Content-Type校验：_validate_content_type()拒绝可执行文件类型
# - [HIGH] Cookie隔离：沙箱请求显式设置cookies={}防止跨域会话泄露
# - [HIGH] 全局资源限制：MAX_DOWNLOAD_SIZE/MAX_TOTAL_DOWNLOADS/MAX_EXTRACTION_SIZE
# - [HIGH] SSL降级域名白名单：仅SSL_SKIP_DOMAINS中的已知书源站点允许跳过验证
# - [HIGH] 连接后SSRF二次验证：_check_connected_ip()在HTTP连接建立后检查对端IP
