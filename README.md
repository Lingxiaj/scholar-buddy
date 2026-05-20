# Scholar Buddy — AI 文献综述与学术助手

一个基于 LLM 的学术文献助手，支持多维文献检索、相似文献推荐、深度分析，以及自动化文献综述报告生成。

## 功能

- **智能文献检索** — 自动规划检索维度，多数据库并发搜索（PubMed、arXiv、Semantic Scholar、OpenAlex、Crossref）
- **多维文献综述** — 三阶段流程：规划维度 → 多库搜索 → 生成 HTML/Markdown 综述报告
- **相似文献推荐** — 给定一篇论文，自动查找语义相似文献并比较差异
- **深度论文分析** — 对特定论文进行结构化深度解读（背景、方法、发现、局限）
- **PDF 上传** — 上传 PDF 全文，智能体自动读取并基于内容回答问题
- **文献卡片展示** — 搜索结果以可视化卡片呈现，含标题翻译、期刊、引用数、IF 值
- **实时对话** — WebSocket 流式输出，支持中断回复
- **对话记录管理** — 自动保存历史，侧边栏切换，LLM 自动生成会话标题

## 快速开始

### 环境要求

- Python 3.11+
- API Key（DeepSeek / OpenAI / Anthropic 任选其一）

### 配置 API Key（二选一，推荐方式一）

**方式一：在网页界面设置（推荐）** — 启动后直接在浏览器里填，无需接触命令行

```bash
# 直接启动服务（不设任何环境变量）
pip install -r requirements.txt
uvicorn server.main:app --host 127.0.0.1 --port 8000
```

打开 `http://localhost:8000`，点击左上角 **设置图标**，填写：
- **API Key** → 你的密钥
- **API Base URL** → 例如 `https://api.deepseek.com/v1`
- **Model** → 例如 `deepseek-chat`

填好后设置会自动保存，下次打开还在。

**方式二：通过环境变量设置** — 适合 Docker / 服务器部署

```bash
# 以 DeepSeek 为例
export OPENAI_API_KEY=sk-你的密钥
export OPENAI_BASE_URL=https://api.deepseek.com/v1
uvicorn server.main:app --host 127.0.0.1 --port 8000
```

> Windows: 用 `set` 代替 `export`。
>
> 如果同时用了两种方式，**网页设置会覆盖环境变量**。

### Docker 运行

```bash
docker build -t scholar-buddy .
docker run -p 8000:8000 \
  -e OPENAI_API_KEY=sk-xxx \
  -e OPENAI_BASE_URL=https://api.deepseek.com/v1 \
  scholar-buddy
```

> Docker 方式只能用环境变量，因为容器里没有界面给你填设置。

## 使用指南

### 第二步：开始对话

在输入框输入你的需求，例如：

| 需求 | 示例指令 |
|------|---------|
| **快速找文献** | "帮我找5篇关于CAR-T治疗实体瘤的最新论文" |
| **写文献综述** | "帮我写一篇关于联邦学习的文献综述" |
| **深度分析论文** | "分析这篇文章：Attention Is All You Need" |
| **找相似文献** | "找跟这篇类似的文章：标题是xxx" |
| **上传 PDF 分析** | 点击上传按钮上传 PDF，然后提问"这篇论文的主要方法是什么？" |

### 文献综述流程

当你说"帮我写一篇文献综述"时，系统会自动执行三阶段流程：

```
Phase 1: 规划 → LLM 自动设计 3-5 个检索维度，等待你确认
Phase 2: 搜索 → 多数据库并发搜索，每维度结果以卡片展示
Phase 3: 生成 → 生成带参考文献和影响因子(IF)的完整 HTML 综述报告
```

每个阶段结束后会请求你的确认，你可以：
- **确认** → 进入下一阶段
- **修改** → 提供反馈意见，LLM 会根据反馈调整

### PDF 上传

支持上传 PDF 文件（论文全文），上传后智能体会自动读取内容。你可以：
- 询问论文主要内容
- 要求解读论文中的图表
- 让智能体基于全文进行深度分析

> PDF 全文每次对话只注入一次，避免重复消耗上下文窗口。

## 支持的数据库

| 数据库 | 用途 |
|-------|------|
| **PubMed** | 生物医学文献 |
| **arXiv** | 计算机科学、AI/ML、物理预印本 |
| **Semantic Scholar** | 通用学术搜索，含引用关系 |
| **OpenAlex** | 多学科综合检索 |
| **Crossref** | 有 DOI 的期刊文章 |

## 项目结构

```
├── core/                    # 核心引擎
│   ├── agent.py             # LLM 智能体（对话、工具调用）
│   ├── prompt.py            # 系统提示词构造
│   ├── tools.py             # 工具发现与注册
│   ├── session.py           # 会话持久化
│   ├── memory.py            # Auto Memory 系统
│   ├── skills.py            # 技能系统
│   ├── subagent.py          # 子智能体管理
│   ├── mcp_client.py        # MCP 协议客户端
│   └── ui.py                # 终端 UI 输出
├── server/                   # Web 服务器
│   ├── main.py              # FastAPI 入口（REST + WebSocket）
│   ├── web_agent.py         # WebSocket 适配层
│   ├── session_store.py     # 会话状态管理
│   └── static/              # 前端资源
│       ├── index.html       # 主页面
│       ├── app.js           # 前端交互逻辑
│       ├── style.css        # 样式
│       └── literature-visualization.css  # 文献卡片样式
├── tools/                    # 工具定义
│   ├── literature_agent.py  # 文献检索工具集
│   ├── base.py              # 工具基类
│   └── journal-if/          # 期刊影响因子数据
├── deploy/                   # 部署配置
│   ├── nginx.conf           # Nginx 反代配置
│   ├── docker-compose.yml   # Docker Compose
│   ├── setup.sh             # VPS 一键部署脚本
│   └── .env.example         # 环境变量模板
├── Dockerfile               # Docker 构建
├── requirements.txt         # Python 依赖
└── launcher.py              # 本地快速启动
```

## 技术栈

- **后端**: Python 3.12, FastAPI, Uvicorn, WebSocket
- **前端**: 原生 HTML/CSS/JavaScript（无框架依赖）
- **LLM**: DeepSeek / OpenAI / Anthropic API
- **文献数据**: Semantic Scholar API, OpenAlex API, PubMed API, arXiv API
- **PDF 解析**: PyMuPDF

## License

MIT
