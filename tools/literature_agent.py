"""
文献检索智能体 v2.0 - 契约式设计
=================================

核心理念：
- 只定义 Plan/Search/Generate 三个阶段的输出格式（契约）
- 具体怎么检索（用什么数据库、什么查询语法）完全由 Agent 自主决定
- 前端可视化基于契约数据，与实现解耦

作者：Your Name
日期：2025-05-18
"""

import html as _html
import json
import os
from typing import List, Dict, Any, Optional, Callable
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum

# ============================================================================
# 期刊影响因子数据
# ============================================================================

_JOURNAL_IF_PATH = os.path.join(os.path.dirname(__file__), "journal-if", "journal-if.json")
_JOURNAL_IF_MAP: Dict[str, float] = {}
_JOURNAL_IF_MAP_NORM: Dict[str, float] = {}

def _load_journal_if() -> None:
    """加载期刊影响因子数据"""
    global _JOURNAL_IF_MAP, _JOURNAL_IF_MAP_NORM
    try:
        with open(_JOURNAL_IF_PATH, "r", encoding="utf-8") as f:
            _JOURNAL_IF_MAP = json.load(f)
        # 构建归一化键值查询表
        for k, v in _JOURNAL_IF_MAP.items():
            norm = k.lower().strip().replace("-", " ").replace(":", " ").replace(".", " ").replace("&", "and")
            while "  " in norm:
                norm = norm.replace("  ", " ")
            _JOURNAL_IF_MAP_NORM[norm] = v
    except Exception as e:
        print(f"[Journal IF] Failed to load: {e}")

def lookup_impact_factor(journal_name: str) -> Optional[float]:
    """通过期刊名称查找影响因子（大小写/缩写/标点容错）"""
    if not _JOURNAL_IF_MAP_NORM:
        _load_journal_if()
    if not journal_name:
        return None
    norm = journal_name.lower().strip().replace("-", " ").replace(":", " ").replace(".", " ").replace("&", "and")
    while "  " in norm:
        norm = norm.replace("  ", " ")
    # 精确匹配
    if norm in _JOURNAL_IF_MAP_NORM:
        return _JOURNAL_IF_MAP_NORM[norm]
    # 包含匹配 - 取最长匹配
    best = None
    for key, val in _JOURNAL_IF_MAP_NORM.items():
        if norm in key or key in norm:
            if best is None or len(key) > len(best[0]):
                best = (key, val)
    if best:
        return best[1]
    return None

_load_journal_if()

# ============================================================================
# 数据契约定义（Core Contracts）
# ============================================================================

class ResearchPhase(str, Enum):
    """检索阶段枚举"""
    PLANNING = "planning"
    SEARCHING = "searching"
    GENERATING = "generating"
    COMPLETED = "completed"


class SearchDimension(BaseModel):
    """检索维度（Plan 阶段输出契约）"""
    dimension_id: str = Field(..., description="维度唯一标识符，如 dim_1")
    dimension_name: str = Field(..., description="维度名称，如'基础理论研究'")
    search_keywords: str = Field(..., description="检索关键词，Agent 自行决定格式")
    target_count: int = Field(default=10, ge=1, le=50, description="目标文献数量")
    preferred_sources: List[str] = Field(
        default=["auto"],
        description="优先检索的数据库列表，可选: pubmed, arxiv, semantic_scholar, clinicaltrials, openalex, crossref。'auto' 表示自动回退",
    )
    quality_requirement: Optional[str] = Field(None, description="质量要求，如'IF>5'")
    year_range: Optional[str] = Field(None, description="年份范围，如'2020-2025'")

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "dimension_id": "dim_1",
            "dimension_name": "深度学习在医学影像中的应用",
            "search_keywords": "deep learning medical imaging diagnosis",
            "target_count": 15,
            "preferred_sources": ["pubmed", "arxiv"],
            "quality_requirement": "IF>3 OR 顶会论文",
            "year_range": "2020-2025"
        }
    })


class LiteratureItem(BaseModel):
    """单篇文献（Search 阶段输出契约）"""
    title: str = Field(..., description="论文标题（英文）")
    title_cn: Optional[str] = Field(None, description="论文标题的中文翻译（方便用户快速理解，可选但推荐提供）")
    authors: Optional[str] = Field(None, description="作者列表")
    journal: Optional[str] = Field(None, description="期刊/会议名称")
    year: Optional[int] = Field(None, description="发表年份")
    abstract: str = Field(..., description="摘要（必需，用于生成报告）")
    url: str = Field(..., description="论文链接（必需，报告中需要超链接）")
    doi: Optional[str] = Field(None, description="DOI")
    impact_factor: Optional[float] = Field(None, description="影响因子（可选）")
    citation_count: Optional[int] = Field(None, description="引用数（可选）")

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "title": "Deep Learning for Medical Image Segmentation",
            "title_cn": "深度学习在医学图像分割中的应用",
            "authors": "Zhang A, Li B, Wang C",
            "journal": "Nature Medicine",
            "year": 2023,
            "abstract": "This study presents a novel deep learning approach...",
            "url": "https://doi.org/10.1038/s41591-023-xxxxx",
            "doi": "10.1038/s41591-023-xxxxx",
            "impact_factor": 87.241,
            "citation_count": 342
        }
    })


class DimensionSearchResult(BaseModel):
    """单个维度的检索结果"""
    dimension_id: str
    dimension_name: str
    papers: List[LiteratureItem]
    search_summary: str = Field(..., description="检索摘要，如'在 PubMed 和 arXiv 检索到 18 篇相关文献'")


def _markdown_to_html(md: str) -> str:
    """Simple markdown to HTML conversion for literature reports."""
    import re as _re
    lines = md.split("\n")
    html_parts = []
    in_list = False
    for line in lines:
        # Headings
        m = _re.match(r'^(#{1,3})\s+(.+)$', line)
        if m:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            level = len(m.group(1))
            html_parts.append(f"<h{level}>{m.group(2)}</h{level}>")
            continue
        # Unordered list
        m = _re.match(r'^[-*]\s+(.+)$', line)
        if m:
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{m.group(1)}</li>")
            continue
        # Empty line
        if not line.strip():
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            else:
                html_parts.append("")
            continue
        # Regular paragraph
        if in_list:
            html_parts.append("</ul>")
            in_list = False
        # Process inline: [text](url), **bold**, *italic*
        processed = line
        processed = _re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', processed)
        processed = _re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', processed)
        processed = _re.sub(r'\*([^*]+)\*', r'<em>\1</em>', processed)
        html_parts.append(f"<p>{processed}</p>")
    if in_list:
        html_parts.append("</ul>")
    body = "\n".join(html_parts)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>{_re.sub(r'<[^>]+>', '', md.split(chr(10))[0]).strip(" #")}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 900px; margin: 0 auto; padding: 2em; line-height: 1.8; color: #333; }}
h1 {{ font-size: 1.8em; border-bottom: 2px solid #1e88e5; padding-bottom: 0.3em; }}
h2 {{ font-size: 1.4em; margin-top: 1.5em; color: #1565c0; }}
h3 {{ font-size: 1.2em; margin-top: 1.2em; color: #333; }}
a {{ color: #1e88e5; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
p {{ margin: 0.6em 0; }}
ul {{ padding-left: 1.5em; }}
li {{ margin: 0.3em 0; }}
strong {{ color: #000; }}
</style></head><body>
{body}
</body></html>"""


class ResearchReport(BaseModel):
    """最终报告（Generate 阶段输出契约）"""
    title: str = Field(..., description="报告标题")
    html_content: str = Field(..., description="完整 HTML 内容，每个引用必须包含超链接 <a href='...'>")
    markdown_content: Optional[str] = Field(None, description="Markdown 版本（可选）")
    summary: str = Field(..., description="核心结论摘要（500 字以内）")
    total_papers_reviewed: int = Field(..., description="总共综述的文献数量")

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "title": "深度学习在医学影像诊断中的应用综述（2020-2025）",
            "html_content": "<html><head>...</head><body><h1>...</h1>...</body></html>",
            "summary": "本综述分析了 2020-2025 年间深度学习在医学影像领域的...",
            "total_papers_reviewed": 45
        }
    })


# ============================================================================
# 进度回调接口
# ============================================================================

class ProgressUpdate(BaseModel):
    """进度更新数据结构"""
    phase: ResearchPhase
    message: str
    data: Optional[Dict[str, Any]] = None


class LiteratureAgent:
    """
    文献检索智能体 - 契约式设计
    
    职责：
    1. 定义三个阶段的输出格式（契约）
    2. 验证 Agent 返回的数据是否符合契约
    3. 触发前端可视化回调
    
    不负责：
    1. 具体的数据库检索逻辑（由 Agent 通过 web_search/web_fetch 自行完成）
    2. 查询语法的构造（Agent 自己决定用 PubMed 语法还是自然语言）
    3. 数据源的选择（Agent 自己决定搜 arXiv/PubMed/Google Scholar）
    """
    
    def __init__(self, progress_callback: Optional[Callable[[ProgressUpdate], None]] = None):
        """
        初始化文献检索智能体
        
        Args:
            progress_callback: 进度回调函数，用于前端可视化
        """
        self.progress_callback = progress_callback
        self.current_phase = ResearchPhase.PLANNING
        self._search_results: Dict[str, Dict] = {}  # dimension_id -> {"name": str, "papers": [LiteratureItem]}
        
    def _emit_progress(self, phase: ResearchPhase, message: str, data: Optional[Dict] = None):
        """触发进度更新"""
        cb = self.progress_callback
        if cb is None:
            print(f"[LitDebug] _emit_progress called but progress_callback is None! phase={phase}")
            return
        try:
            cb(ProgressUpdate(phase=phase, message=message, data=data))
        except Exception as e:
            print(f"[LitDebug] _emit_progress callback error: {e}")

    @staticmethod
    def _wrap_html(html_content: str, title: str) -> str:
        """Wrap simple HTML content in a styled document template."""
        import re as _re
        safe_title = _re.sub(r'<[^>]+>', '', title)
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{safe_title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 900px; margin: 0 auto; padding: 2em; line-height: 1.8; color: #333; background: #fff; }}
h1 {{ font-size: 1.8em; border-bottom: 2px solid #1e88e5; padding-bottom: 0.3em; }}
h2 {{ font-size: 1.4em; margin-top: 1.5em; color: #1565c0; }}
h3 {{ font-size: 1.2em; margin-top: 1.2em; color: #333; }}
a {{ color: #1e88e5; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
p {{ margin: 0.6em 0; }}
ul {{ padding-left: 1.5em; }}
li {{ margin: 0.3em 0; }}
strong {{ color: #000; }}
table {{ border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 14px; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
th {{ background: #f5f7fa; color: #1565c0; font-weight: 600; }}
tr:nth-child(even) {{ background: #fafbfc; }}
</style></head><body>
{html_content}
</body></html>"""

    # ========================================================================
    # Phase 1: Planning - 制定检索维度
    # ========================================================================
    
    def validate_plan(self, dimensions: List[Dict], session_topic: str = "") -> List[SearchDimension]:
        """
        验证 Plan 阶段的输出是否符合契约

        Args:
            dimensions: Agent 返回的维度列表（原始 dict）
            session_topic: Agent 提炼的对话主题（用于会话列表名称）

        Returns:
            验证后的 SearchDimension 列表
            
        Raises:
            ValueError: 如果不符合契约格式
            
        使用示例（给 Agent 的指令）：
        ```
        请根据用户的研究课题，制定 3-5 个检索维度。每个维度需要包含：
        - dimension_id: 唯一标识符
        - dimension_name: 维度名称
        - search_keywords: 检索关键词（你自己决定格式）
        - target_count: 目标文献数量
        - quality_requirement: 质量要求（可选）
        - year_range: 年份范围（可选）
        
        返回格式示例：
        {
            "dimensions": [
                {
                    "dimension_id": "dim_1",
                    "dimension_name": "基础理论研究",
                    "search_keywords": "deep learning theory convergence",
                    "target_count": 15
                },
                ...
            ]
        }
        ```
        """
        validated_dimensions = []
        for dim_dict in dimensions:
            try:
                dim = SearchDimension(**dim_dict)
                validated_dimensions.append(dim)
            except Exception as e:
                raise ValueError(f"维度格式错误: {dim_dict}\n错误: {str(e)}")
        
        # 触发前端可视化
        data = {"dimensions": [d.model_dump() for d in validated_dimensions]}
        if session_topic:
            data["session_topic"] = session_topic
        self._emit_progress(
            phase=ResearchPhase.PLANNING,
            message=f"检索计划已制定，共 {len(validated_dimensions)} 个维度",
            data=data
        )
        
        return validated_dimensions
    
    # ========================================================================
    # Phase 2: Searching - 执行文献检索
    # ========================================================================
    
    def validate_search_results(
        self, 
        dimension_id: str,
        dimension_name: str,
        papers: List[Dict]
    ) -> DimensionSearchResult:
        """
        验证 Search 阶段的输出是否符合契约
        
        Args:
            dimension_id: 维度标识符
            dimension_name: 维度名称
            papers: Agent 检索到的文献列表（原始 dict）
            
        Returns:
            验证后的 DimensionSearchResult
            
        Raises:
            ValueError: 如果文献数据缺少必需字段（title/abstract/url）
            
        使用示例（给 Agent 的指令）：
        ```
        请根据维度 "{dimension_name}" 的检索关键词，搜索相关文献。
        你可以自由选择数据源（PubMed/arXiv/Google Scholar/Semantic Scholar）。
        
        每篇文献必须包含：
        - title: 标题（必需）
        - abstract: 摘要（必需，用于生成报告）
        - url: 链接（必需，报告中需要超链接）
        
        推荐字段：
        - title_cn: 标题的中文翻译（强烈推荐，方便用户快速理解）
        - authors, journal, year, doi, impact_factor, citation_count

        返回格式示例：
        {
            "papers": [
                {
                    "title": "Deep Learning for Medical Imaging",
                    "title_cn": "深度学习在医学影像中的应用",
                    "abstract": "This paper presents...",
                    "url": "https://doi.org/10.xxxx/xxxx",
                    "journal": "Nature Medicine",
                    "year": 2023
                },
                ...
            ]
        }
        ```
        """
        validated_papers = []
        for paper_dict in papers:
            try:
                paper = LiteratureItem(**paper_dict)
                # 自动补充影响因子（如果 LLM 未提供或为 0）
                if not paper.impact_factor and paper.journal:
                    if_val = lookup_impact_factor(paper.journal)
                    if if_val is not None:
                        paper.impact_factor = if_val
                validated_papers.append(paper)
            except Exception as e:
                # 缺少必需字段时，给出详细错误信息
                missing_fields = []
                if 'title' not in paper_dict:
                    missing_fields.append('title')
                if 'abstract' not in paper_dict:
                    missing_fields.append('abstract')
                if 'url' not in paper_dict:
                    missing_fields.append('url')
                
                if missing_fields:
                    raise ValueError(
                        f"文献数据缺少必需字段: {missing_fields}\n"
                        f"问题文献: {paper_dict.get('title', '未知标题')}\n"
                        f"详细错误: {str(e)}"
                    )
                else:
                    raise ValueError(f"文献格式错误: {str(e)}")
        
        result = DimensionSearchResult(
            dimension_id=dimension_id,
            dimension_name=dimension_name,
            papers=validated_papers,
            search_summary=f"检索到 {len(validated_papers)} 篇相关文献"
        )
        
        # 触发前端可视化（展示文献表格）
        self._emit_progress(
            phase=ResearchPhase.SEARCHING,
            message=f"维度 [{dimension_name}] 检索完成",
            data={
                "dimension_id": dimension_id,
                "dimension_name": dimension_name,
                "paper_count": len(validated_papers),
                "papers": [p.model_dump() for p in validated_papers]
            }
        )

        # 保存到内部缓存，供 validate_report 自动生成文献汇总表
        self._search_results[dimension_id] = {
            "name": dimension_name,
            "papers": validated_papers
        }

        return result
    
    # ========================================================================
    # Phase 3: Generating - 生成综述报告
    # ========================================================================
    
    def _build_reference_table(self) -> str:
        """从缓存的检索结果自动生成文献汇总 HTML 表格（含真实 IF）。"""
        if not self._search_results:
            return ""
        parts = ['<h2>参考文献汇总</h2>']
        global_idx = 0
        for dim_id, dim_data in self._search_results.items():
            papers = dim_data.get("papers", [])
            if not papers:
                continue
            parts.append(f'<h3>{_html.escape(dim_data.get("name", dim_id))}（{len(papers)}篇）</h3>')
            parts.append('<table><thead><tr><th>#</th><th>标题</th><th>年份</th><th>期刊/会议</th><th>IF / 引用</th></tr></thead><tbody>')
            for p in papers:
                global_idx += 1
                title_link = f'<a href="{_html.escape(p.url)}">{_html.escape(p.title)}</a>'
                title_cn = p.title_cn or ""
                title_display = title_link
                if title_cn:
                    title_display += f'<br><span style="color:#666;font-size:13px;">{_html.escape(title_cn)}</span>'
                year = str(p.year) if p.year else "-"
                journal = _html.escape(p.journal) if p.journal else "-"
                if_val = ""
                if p.impact_factor:
                    if_val = f"IF {p.impact_factor:.1f}"
                elif p.citation_count:
                    if_val = f"被引 {p.citation_count}"
                else:
                    if_val = "-"
                parts.append(f'<tr><td>{global_idx}</td><td>{title_display}</td><td>{year}</td><td>{journal}</td><td>{if_val}</td></tr>')
            parts.append('</tbody></table>')
        return "\n".join(parts)

    def cache_papers(self, papers: List[Dict], group_name: str = "分析文献") -> None:
        """将外部传入的论文列表存入缓存，供 validate_report 自动生成汇总表。

        如果传入的论文缺少 impact_factor 或 citation_count，
        会自动从已有的检索结果中查找匹配并补全真实数据。
        """
        # 构建已有检索结果的索引（优先按 URL，其次按标题）
        known_papers: dict[str, LiteratureItem] = {}
        for dim_data in self._search_results.values():
            for p in dim_data.get("papers", []):
                if p.url:
                    known_papers[p.url] = p
                if p.title:
                    known_papers[p.title.lower().strip()] = p

        # 补全缺失的 IF / 引用数据
        enriched: list[dict] = []
        for p in papers:
            url = (p.get("url") or "").strip().rstrip("/")
            title = (p.get("title") or "").strip().lower()
            # 检查是否需要补全
            needs_if = p.get("impact_factor") is None and p.get("citation_count") is None
            if needs_if:
                # 按 URL 匹配
                matched = known_papers.get(url)
                if matched is None:
                    # 按标题匹配
                    matched = known_papers.get(title)
                if matched is not None and matched.impact_factor is not None:
                    p["impact_factor"] = matched.impact_factor
                    p["citation_count"] = matched.citation_count
            enriched.append(p)

        validated = []
        for p in enriched:
            try:
                validated.append(LiteratureItem(**p))
            except Exception:
                continue
        if validated:
            self._search_results["_external"] = {
                "name": group_name,
                "papers": validated
            }

    def validate_report(self, report_dict: Dict) -> ResearchReport:
        """
        验证 Generate 阶段的输出是否符合契约

        Args:
            report_dict: Agent 生成的报告（原始 dict）

        Returns:
            验证后的 ResearchReport

        Raises:
            ValueError: 如果报告格式不符合要求
        """
        try:
            report = ResearchReport(**report_dict)
        except Exception as e:
            raise ValueError(f"报告格式错误: {str(e)}")

        # 验证 HTML 中是否包含超链接引用
        if '<a href=' not in report.html_content:
            # 尝试从缓存的论文数据自动补全引用链接
            auto_link = ""
            for dim_data in self._search_results.values():
                for p in dim_data.get("papers", []):
                    if p.url:
                        auto_link = f'<p><a href="{_html.escape(p.url)}">[原文链接: {_html.escape(p.title or "论文")}]</a></p>'
                        break
                if auto_link:
                    break
            if auto_link:
                report.html_content += "\n" + auto_link
            else:
                raise ValueError(
                    "报告中缺少超链接引用！\n"
                    "请确保每个引用都带有超链接，格式为 [作者, 年份](论文URL)。"
                )

        # 自动附加真实文献汇总表（基于检索阶段收集的实际数据，含真实 IF）
        ref_table = self._build_reference_table()
        if ref_table:
            report.html_content += "\n" + ref_table
            # 同时附加到 markdown 版本
            if report.markdown_content:
                md_ref = "\n\n## 参考文献汇总\n\n"
                md_ref += "| # | 标题 | 年份 | 期刊 | IF / 引用 |\n"
                md_ref += "|---|---|---|---|---|\n"
                global_idx = 0
                for dim_data in self._search_results.values():
                    for p in dim_data.get("papers", []):
                        global_idx += 1
                        title_text = p.title.replace("|", "\\|")
                        journal_text = (p.journal or "-").replace("|", "\\|")
                        if_val = f"IF {p.impact_factor:.1f}" if p.impact_factor else (f"被引 {p.citation_count}" if p.citation_count else "-")
                        md_ref += f"| {global_idx} | [{title_text}]({p.url}) | {p.year or '-'} | {journal_text} | {if_val} |\n"
                report.markdown_content += md_ref
            # 清空缓存，避免下次报告重复包含
            self._search_results.clear()

        # 保存 HTML 到 output 目录，并记录路径
        html_path = ""
        md_path = ""
        try:
            html = report.html_content
            if not html.strip().startswith("<!") and not html.strip().startswith("<html"):
                html = self._wrap_html(html, report.title)
            output_dir = _os.path.join(_project_root, "output", "reports")
            _os.makedirs(output_dir, exist_ok=True)
            ts = int(__import__('time').time())
            safe_title = _re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff]+', '_', report.title)[:40]
            html_path = _os.path.join(output_dir, f"report_{ts}_{safe_title}.html")
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(html)
            if report.markdown_content:
                md_path = html_path.replace('.html', '.md')
                with open(md_path, 'w', encoding='utf-8') as f:
                    f.write(report.markdown_content)
        except Exception as e:
            pass  # 保存失败不影响主流程

        # 触发前端可视化（展示报告和下载链接）
        self._emit_progress(
            phase=ResearchPhase.COMPLETED,
            message="综述报告生成完成",
            data={
                "title": report.title,
                "summary": report.summary,
                "total_papers": report.total_papers_reviewed,
                "has_html": bool(report.html_content),
                "has_markdown": bool(report.markdown_content),
                "html_path": html_path or None,
                "markdown_path": md_path or None
            }
        )

        return report
    
    # ========================================================================
    # 工具导出（用于 MCP Server）
    # ========================================================================
    
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """
        返回三个工具的定义（给 MCP Server 注册）
        
        这三个工具的 description 是给 Agent 看的"契约说明书"：
        - 告诉 Agent 需要返回什么格式
        - 不规定 Agent 怎么实现（用什么数据库、什么查询语法）
        """
        return [
            {
                "name": "literature_plan",
                "description": """
制定文献检索计划（Phase 1: Planning）

根据用户的研究课题，拆解为 3-5 个检索维度。你可以自由决定：
- 怎么拆解课题（按技术路线/时间线/应用领域等）
- 用什么检索关键词（PubMed 语法/自然语言/布尔表达式）
- 各维度的数量分配

必需返回字段：
- dimension_id: 唯一标识符
- dimension_name: 维度名称
- search_keywords: 检索关键词
- target_count: 目标文献数量（1-50）
- session_topic: 用简短的中文概括这次对话的核心主题（用于会话列表名称，10字以内）

可选返回字段：
- quality_requirement: 质量要求（如 'IF>5'）
- year_range: 年份范围（如 '2020-2025'）

示例输出：
{
    "session_topic": "深度学习研究综述",
    "dimensions": [
        {
            "dimension_id": "dim_1",
            "dimension_name": "深度学习基础理论",
            "search_keywords": "deep learning convergence optimization",
            "target_count": 15,
            "year_range": "2020-2025"
        }
    ]
}
                """,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "session_topic": {
                            "type": "string",
                            "description": "用简短的中文概括这次对话的核心主题，用于会话列表名称（10字以内）"
                        },
                        "dimensions": {
                            "type": "array",
                            "description": "检索维度列表（你已经根据研究课题拆解好的维度）",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "dimension_id": {"type": "string", "description": "维度唯一标识符"},
                                    "dimension_name": {"type": "string", "description": "维度名称"},
                                    "search_keywords": {"type": "string", "description": "检索关键词"},
                                    "target_count": {"type": "integer", "description": "目标文献数量"},
                                    "preferred_sources": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "优先检索的数据库列表，可选: pubmed, arxiv, semantic_scholar, clinicaltrials, openalex, crossref。'auto' 表示自动回退"
                                    },
                                    "quality_requirement": {"type": "string", "description": "质量要求（可选）"},
                                    "year_range": {"type": "string", "description": "年份范围（可选）"}
                                }
                            }
                        }
                    },
                    "required": ["dimensions"]
                }
            },
            {
                "name": "literature_search",
                "description": """
执行文献检索（Phase 2: Searching）

根据指定的维度和检索关键词，搜索相关文献。你可以自由决定：
- 使用哪些数据源（PubMed/arXiv/Google Scholar/Semantic Scholar）
- 使用什么查询语法（根据数据源调整）
- 怎么去重和质量过滤

必需返回字段（每篇文献）：
- title: 标题
- abstract: 摘要（用于后续生成报告）
- url: 链接（报告中需要超链接）

推荐返回字段：
- title_cn: 标题的中文翻译（强烈推荐，方便用户快速理解）
- authors, journal, year, doi, impact_factor, citation_count

示例输出：
{
    "papers": [
        {
            "title": "Attention Is All You Need",
            "abstract": "The dominant sequence transduction models...",
            "url": "https://arxiv.org/abs/1706.03762",
            "authors": "Vaswani A, Shazeer N, et al.",
            "year": 2017,
            "citation_count": 98234
        }
    ]
}

注意：如果某个数据源失败，可以尝试其他源，最终只要返回符合格式的文献列表即可。
                """,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "dimension_id": {
                            "type": "string",
                            "description": "维度标识符（来自 Plan 阶段的 dimension_id）"
                        },
                        "dimension_name": {
                            "type": "string",
                            "description": "维度名称"
                        },
                        "papers": {
                            "type": "array",
                            "description": "你检索到的文献列表（使用 web_search/web_fetch 收集）",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string", "description": "论文标题（英文，必需）"},
                                    "title_cn": {"type": "string", "description": "论文标题的中文翻译（方便用户快速理解，推荐提供）"},
                                    "abstract": {"type": "string", "description": "摘要（必需，用于生成报告）"},
                                    "url": {"type": "string", "description": "论文链接（必需，报告中需要超链接）"},
                                    "authors": {"type": "string", "description": "作者（可选）"},
                                    "journal": {"type": "string", "description": "期刊名称（可选）"},
                                    "year": {"type": "integer", "description": "发表年份（可选）"},
                                    "doi": {"type": "string", "description": "DOI（可选）"},
                                    "impact_factor": {"type": "number", "description": "影响因子（可选）"},
                                    "citation_count": {"type": "integer", "description": "引用数（可选）"}
                                }
                            }
                        },
                        "search_keywords": {
                            "type": "string",
                            "description": "实际使用的检索关键词（可选，仅用于记录）"
                        },
                        "target_count": {
                            "type": "integer",
                            "description": "目标文献数量（可选，来自 Plan）"
                        }
                    },
                    "required": ["dimension_id", "dimension_name", "papers"]
                }
            },
            {
                "name": "literature_generate_report",
                "description": """
生成报告（Phase 3: Generating）

基于所有检索到的文献，生成一份符合用户需求的 HTML 报告。类型不限于综述：
- 文献综述：按维度/技术路线系统梳理研究进展
- 问答式：针对用户具体问题，引用文献逐条回答
- 横向对比：比较不同方法/模型的优缺点、适用场景
- 趋势分析：总结研究热点、技术演进、未来方向
- 或其他适合用户需求的格式

必需要求：
1. 所有结论必须基于提供的文献摘要，严禁编造内容
2. 每个引用必须包含超链接，格式如：<a href="论文URL">[作者, 年份]</a>
3. **不要在报告中包含文献汇总表** — 系统会自动附上完整的汇总表（含真实 IF）
4. **使用简单的 HTML 标签**：`<h1>`-`<h3>`, `<p>`, `<ul>`/`<li>`, `<a>`, `<strong>`, `<em>`
5. **不要包含 `<style>` 标签或内联 CSS** — 系统会自动添加样式

必需返回字段：
- title: 报告标题
- html_content: 简单的 HTML 内容（仅结构标签，无样式）
- summary: 核心结论摘要（500字以内）
- total_papers_reviewed: 综述的文献总数

示例输出：
{
    "title": "深度学习在医学影像中的应用综述（2020-2025）",
    "html_content": "<h1>引言</h1><p>深度学习技术在...</p>",
    "summary": "本综述分析了 2020-2025 年间深度学习在医学影像领域的 45 篇代表性研究...",
    "total_papers_reviewed": 45
}
                """,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "报告标题"
                        },
                        "html_content": {
                            "type": "string",
                            "description": "HTML 内容（仅用 <h1>-<h3>, <p>, <ul>/<li>, <a>, <strong>, <em>，不要 style 标签）"
                        },
                        "summary": {
                            "type": "string",
                            "description": "核心结论摘要（500字以内）"
                        },
                        "total_papers_reviewed": {
                            "type": "integer",
                            "description": "总共综述的文献数量"
                        },
                        "markdown_content": {
                            "type": "string",
                            "description": "Markdown 版本（可选）"
                        }
                    },
                    "required": ["title", "html_content", "summary", "total_papers_reviewed"]
                }
            }
        ]


# ============================================================================
# 示例：如何在 MCP Server 中注册这些工具
# ============================================================================

def example_mcp_integration():
    """
    示例：在 MCP Server 中集成文献检索 Agent
    
    在你的 server.py 中：
    """
    from mcp.server import Server
    
    # 1. 创建 Agent 实例
    def progress_handler(update: ProgressUpdate):
        # 发送给前端（通过 WebSocket 或 SSE）
        print(f"[{update.phase}] {update.message}")
        if update.data:
            print(f"Data: {json.dumps(update.data, indent=2, ensure_ascii=False)}")
    
    agent = LiteratureAgent(progress_callback=progress_handler)
    
    # 2. 注册工具
    server = Server("literature-agent")
    
    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        if name == "literature_plan":
            # Agent 自己决定怎么拆解课题，最后返回维度列表
            # 这里只是模拟，实际上 Agent 会调用 LLM 来生成
            dimensions = arguments.get("dimensions", [])
            validated = agent.validate_plan(dimensions)
            return {"dimensions": [d.model_dump() for d in validated]}
        
        elif name == "literature_search":
            # Agent 自己决定搜哪些数据库，最后返回文献列表
            papers = arguments.get("papers", [])
            result = agent.validate_search_results(
                dimension_id=arguments["dimension_id"],
                dimension_name=arguments["dimension_name"],
                papers=papers
            )
            return result.model_dump()
        
        elif name == "literature_generate_report":
            # Agent 自己决定报告结构，最后返回 HTML
            report = agent.validate_report(arguments)
            return report.model_dump()


# ============================================================================
# 学术数据库搜索引擎（中间层：快速获取结构化文献）
# ============================================================================
# 依次尝试 Semantic Scholar → arXiv → OpenAlex → Crossref
# 每个接口独立 try/except，一个失败自动切下一个

import urllib.request as _urllib_request
import urllib.parse as _urllib_parse
import urllib.error as _urllib_error
import ssl as _ssl
import xml.etree.ElementTree as _ET
import time as _time

_SSL_CTX = _ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = _ssl.CERT_NONE
_SEARCH_TIMEOUT = 10  # 每个接口超时（秒）
_ARXIV_TIMEOUT = 20  # arXiv 响应较慢


def _fetch_json(url: str) -> dict | None:
    """带超时和 SSL 容错的 JSON GET 请求"""
    try:
        req = _urllib_request.Request(url, headers={"User-Agent": "LiteratureAgent/2.0"})
        with _urllib_request.urlopen(req, timeout=_SEARCH_TIMEOUT, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def _fetch_text(url: str) -> str | None:
    """带超时和 SSL 容错的文本 GET 请求"""
    try:
        req = _urllib_request.Request(url, headers={"User-Agent": "LiteratureAgent/2.0"})
        with _urllib_request.urlopen(req, timeout=_SEARCH_TIMEOUT, context=_SSL_CTX) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _fetch_text_arxiv(url: str) -> str | None:
    """arXiv 专用文本 GET 请求（较长超时）"""
    try:
        req = _urllib_request.Request(url, headers={"User-Agent": "LiteratureAgent/2.0"})
        with _urllib_request.urlopen(req, timeout=_ARXIV_TIMEOUT, context=_SSL_CTX) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _search_semantic_scholar(keywords: str, limit: int) -> list[dict] | None:
    """Semantic Scholar API（结构化好，覆盖全，无 key 也可用）"""
    _time.sleep(1.0)  # 限速：Semantic Scholar 对无 key 请求限制较严
    q = _urllib_parse.quote(keywords)
    url = (
        f"https://api.semanticscholar.org/graph/v1/paper/search"
        f"?query={q}&limit={min(limit, 20)}&fields=title,abstract,url,authors,year,journal,externalIds"
    )
    data = _fetch_json(url)
    if not data or "data" not in data:
        return None
    papers = []
    for item in data["data"]:
        authors = item.get("authors") or []
        papers.append({
            "title": item.get("title", ""),
            "abstract": item.get("abstract") or "",
            "url": item.get("url") or (f"https://doi.org/{item.get('externalIds', {}).get('DOI', '')}" if item.get("externalIds", {}).get("DOI") else ""),
            "authors": ", ".join(a.get("name", "") for a in authors),
            "journal": item.get("journal", {}).get("name", "") if isinstance(item.get("journal"), dict) else "",
            "year": item.get("year"),
            "doi": item.get("externalIds", {}).get("DOI", ""),
        })
    return papers if papers else None


def _search_arxiv(keywords: str, limit: int) -> list[dict] | None:
    """arXiv API（开源论文为主，响应较慢需较长超时）"""
    q = _urllib_parse.quote(keywords)
    url = f"http://export.arxiv.org/api/query?search_query=all:{q}&max_results={limit}&sortBy=relevance&sortOrder=descending"
    text = _fetch_text_arxiv(url)
    if not text:
        return None
    try:
        root = _ET.fromstring(text)
    except Exception:
        return None
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    papers = []
    for entry in root.findall("atom:entry", ns):
        title = (entry.findtext("atom:title", "", ns) or "").replace("\n", " ").strip()
        summary = (entry.findtext("atom:summary", "", ns) or "").replace("\n", " ").strip()
        url = entry.findtext("atom:id", "", ns) or ""
        authors = []
        for author in entry.findall("atom:author", ns):
            name = author.findtext("atom:name", "", ns)
            if name:
                authors.append(name)
        published = entry.findtext("atom:published", "", ns)
        year = int(published[:4]) if published and len(published) >= 4 else None
        papers.append({
            "title": title,
            "abstract": summary,
            "url": url,
            "authors": ", ".join(authors),
            "journal": "arXiv",
            "year": year,
        })
    return papers if papers else None


def _search_openalex(keywords: str, limit: int) -> list[dict] | None:
    """OpenAlex API（免费，覆盖全学科）"""
    q = _urllib_parse.quote(keywords)
    url = f"https://api.openalex.org/works?search={q}&per_page={limit}&sort=cited_by_count:desc"
    data = _fetch_json(url)
    if not data or "results" not in data:
        return None
    papers = []
    for item in data["results"]:
        authors = item.get("authorships") or []
        papers.append({
            "title": item.get("title", ""),
            "abstract": item.get("abstract_inverted_index") and " ".join(item["abstract_inverted_index"].keys()) or "",
            "url": item.get("doi", "") or item.get("id", ""),
            "authors": ", ".join(a.get("author", {}).get("display_name", "") for a in authors),
            "journal": item.get("primary_location", {}).get("source", {}).get("display_name", "") if item.get("primary_location") else "",
            "year": item.get("publication_year"),
            "doi": item.get("doi", "").replace("https://doi.org/", ""),
        })
    return papers if papers else None


def _search_crossref(keywords: str, limit: int) -> list[dict] | None:
    """Crossref API（最全的期刊论文索引）"""
    q = _urllib_parse.quote(keywords)
    url = f"https://api.crossref.org/works?query={q}&rows={limit}&sort=relevance&order=desc"
    data = _fetch_json(url)
    if not data or "message" not in data:
        return None
    items = data["message"].get("items", [])
    if not items:
        return None
    papers = []
    for item in items:
        authors = item.get("author") or []
        papers.append({
            "title": item.get("title", [""])[0] if item.get("title") else "",
            "abstract": item.get("abstract", "") or "",
            "url": item.get("url", "") or (f"https://doi.org/{item.get('DOI', '')}" if item.get("DOI") else ""),
            "authors": ", ".join(
                f"{a.get('given', '')} {a.get('family', '')}".strip() for a in authors if a.get("family")
            ),
            "journal": item.get("container-title", [""])[0] if item.get("container-title") else "",
            "year": (item.get("published-print", {}) or item.get("published-online", {}) or {}).get("date-parts", [[None]])[0][0],
            "doi": item.get("DOI", ""),
        })
    return papers if papers else None


def _search_pubmed(keywords: str, limit: int) -> list[dict] | None:
    """PubMed E-utilities API（生物医学文献黄金标准）"""
    try:
        # Step 1: esearch — 获取 PMID 列表
        q = _urllib_parse.quote(keywords)
        search_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={q}&retmax={limit}&retmode=json"
        data = _fetch_json(search_url)
        if not data:
            return None
        id_list = data.get("esearchresult", {}).get("idlist", [])
        if not id_list:
            return None

        # Step 2: efetch — 获取完整元数据（含摘要）
        id_str = ",".join(id_list)
        fetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={id_str}&retmode=xml&rettype=abstract"
        req = _urllib_request.Request(fetch_url, headers={"User-Agent": "LiteratureAgent/2.0"})
        with _urllib_request.urlopen(req, timeout=_SEARCH_TIMEOUT, context=_SSL_CTX) as resp:
            xml_text = resp.read().decode("utf-8", errors="replace")

        # 解析 XML
        papers = []
        root = _ET.fromstring(xml_text)
        ns = {"pubmed": "http://www.ncbi.nlm.nih.gov/entrez/query/static"}
        # PubMed XML 不使用默认 namespace，直接用 local-name 查找
        for article in root.findall(".//PubmedArticle"):
            try:
                title_el = article.find(".//ArticleTitle")
                title = "".join(title_el.itertext()) if title_el is not None else ""

                abstract_el = article.find(".//AbstractText")
                abstract = "".join(abstract_el.itertext()) if abstract_el is not None else ""

                # 多个 AbstractText 标签拼接
                if not abstract:
                    abstract_parts = article.findall(".//AbstractText")
                    abstract = " ".join("".join(a.itertext()) for a in abstract_parts) if abstract_parts else ""

                pmid_el = article.find(".//PMID")
                pmid = pmid_el.text if pmid_el is not None else ""

                # Journal
                journal_el = article.find(".//Journal/Title")
                journal = journal_el.text if journal_el is not None else ""

                # Year
                year_el = article.find(".//PubDate/Year")
                if year_el is None:
                    medline_date = article.find(".//PubDate/MedlineDate")
                    year = int(medline_date.text[:4]) if medline_date is not None and medline_date.text else None
                else:
                    year = int(year_el.text) if year_el.text else None

                # Authors
                authors = []
                for author in article.findall(".//Author"):
                    last = author.findtext("LastName", "")
                    fore = author.findtext("ForeName", "")
                    if last:
                        authors.append(f"{fore} {last}".strip())
                    elif fore:
                        authors.append(fore)

                # DOI
                doi = ""
                for aid in article.findall(".//ArticleId"):
                    if aid.get("IdType") == "doi":
                        doi = aid.text or ""

                papers.append({
                    "title": title,
                    "abstract": abstract,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
                    "authors": ", ".join(authors),
                    "journal": journal,
                    "year": year,
                    "doi": doi,
                })
            except Exception:
                continue

        return papers if papers else None
    except Exception:
        return None


def _search_clinicaltrials(keywords: str, limit: int) -> list[dict] | None:
    """ClinicalTrials.gov API v2（临床试验数据）"""
    try:
        q = _urllib_parse.quote(keywords)
        url = f"https://clinicaltrials.gov/api/v2/studies?query.term={q}&pageSize={limit}&format=json"
        data = _fetch_json(url)
        if not data or "studies" not in data:
            return None
        papers = []
        for study in data["studies"]:
            try:
                proto = study.get("protocolSection", {})
                ident = proto.get("identificationModule", {})
                desc = proto.get("descriptionModule", {})
                status_mod = proto.get("statusModule", {})
                cond_mod = proto.get("conditionsModule", {})
                design_mod = proto.get("designModule", {})

                title = ident.get("briefTitle", "") or ident.get("officialTitle", "")
                nct_id = ident.get("nctId", "")
                summary = desc.get("briefSummary", "") or ""
                conditions = ", ".join(cond_mod.get("conditions", []) or [])
                status = status_mod.get("overallStatus", "")
                phase = ", ".join(design_mod.get("phases", []) or []) if design_mod.get("phases") else ""

                papers.append({
                    "title": title,
                    "abstract": f"Condition: {conditions}. Phase: {phase}. Status: {status}. {summary}".strip(),
                    "url": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else "",
                    "authors": "",
                    "journal": "ClinicalTrials.gov",
                    "year": None,
                    "doi": nct_id or "",
                })
            except Exception:
                continue
        return papers if papers else None
    except Exception:
        return None


_SOURCE_MAP = {
    "semantic_scholar": _search_semantic_scholar,
    "pubmed": _search_pubmed,
    "arxiv": _search_arxiv,
    "openalex": _search_openalex,
    "crossref": _search_crossref,
    "clinicaltrials": _search_clinicaltrials,
}

_SOURCES = [
    ("Semantic Scholar", _search_semantic_scholar),
    ("PubMed", _search_pubmed),
    ("arXiv", _search_arxiv),
    ("OpenAlex", _search_openalex),
    ("Crossref", _search_crossref),
    ("ClinicalTrials.gov", _search_clinicaltrials),
]


# ─── Free translation helper ─────────────────────────────────


def _translate_title(title: str) -> str:
    """Translate English paper title to Chinese using Google Translate (free, no API key needed)."""
    try:
        q = _urllib_parse.quote(title)
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=zh-CN&dt=t&q={q}"
        req = _urllib_request.Request(url, headers={"User-Agent": "LiteratureAgent/2.0"})
        with _urllib_request.urlopen(req, timeout=5, context=_SSL_CTX) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data and data[0] and data[0][0]:
                return data[0][0][0]
    except Exception:
        pass
    return ""


def search_academic_databases(keywords: str, limit: int = 10,
                               year_from: int | None = None,
                               year_to: int | None = None,
                               source: str | list[str] | None = None) -> dict:
    """多源学术检索入口

    Args:
        keywords: 检索关键词
        limit: 目标数量（1-20）
        year_from: 起始年份（可选）
        year_to: 截止年份（可选）
        source: 指定数据库，可选:
               - "auto" 或 None: 自动回退（默认）
               - "pubmed" / "arxiv" / "semantic_scholar" / "clinicaltrials" / "openalex" / "crossref": 只查指定库
               - ["pubmed", "arxiv"]: 按顺序只查这几个库

    Returns:
        {"papers": [...], "source": "使用的数据源名称", "total_found": int}
    """
    # 根据 source 参数确定要遍历的 (name, fn) 列表
    if source is None or source == "auto" or source == ["auto"]:
        source_list = _SOURCES
    elif isinstance(source, str):
        fn = _SOURCE_MAP.get(source)
        if fn is None:
            return {"error": f"未知数据库: {source}", "papers": [], "source": "none"}
        # Try the specified source first, then fall back to auto chain if empty
        source_list = [(source, fn)] + _SOURCES
    elif isinstance(source, list):
        source_list = []
        for s in source:
            if s == "auto":
                source_list.extend(_SOURCES)
            elif s in _SOURCE_MAP:
                source_list.append((s, _SOURCE_MAP[s]))
        if not source_list:
            source_list = _SOURCES
        else:
            # Append auto chain as fallback
            source_list = source_list + _SOURCES

    for name, search_fn in source_list:
        try:
            _time.sleep(0.3)  # 接口礼貌间隔
            papers = search_fn(keywords, limit)
            if papers is None:
                continue

            # 年份过滤
            if year_from or year_to:
                filtered = []
                for p in papers:
                    y = p.get("year")
                    if y and isinstance(y, (int, float)):
                        if year_from and y < year_from:
                            continue
                        if year_to and y > year_to:
                            continue
                        filtered.append(p)
                    else:
                        filtered.append(p)
                papers = filtered

            if papers:
                # 截取目标数量
                papers = papers[:limit]
                return {"papers": papers, "source": name, "total_found": len(papers)}
        except Exception:
            continue

    return {"papers": [], "source": "none", "total_found": 0}


# ============================================================================
# BaseTool 包装类（用于核心 Agent 自动发现）
# ============================================================================
# 这三个包装类将 v2 的契约验证工具注册为核心 Agent 可自动发现的工具。
# 核心 Agent 会扫描 tools/*.py 中的 BaseTool 子类，自动加载。

import os as _os
import sys as _sys
import re as _re

# 确保项目根路径
_project_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

from tools.base import BaseTool as _BaseTool

# 全局 LiteratureAgent 实例，WebAgent 可通过 setter 配置 progress_callback
_literature_agent_instance: LiteratureAgent | None = None
_literature_progress_callback: Callable | None = None

# 全局 confirm 回调 — 由 WebAgent 注册，用于阻塞等待用户确认
_literature_confirm_callback: Callable | None = None


def set_global_confirm_callback(callback: Callable) -> None:
    """设置全局 confirm 回调（供 WebAgent 调用以在 literature 阶段阻塞等待用户确认）"""
    global _literature_confirm_callback
    _literature_confirm_callback = callback


def _get_global_literature_agent() -> LiteratureAgent:
    """获取全局 LiteratureAgent 单例"""
    global _literature_agent_instance
    if _literature_agent_instance is None:
        _literature_agent_instance = LiteratureAgent(
            progress_callback=_literature_progress_callback
        )
    return _literature_agent_instance


def set_global_progress_callback(callback: Callable[[ProgressUpdate], None]) -> None:
    """设置全局进度回调（供 WebAgent 调用）"""
    global _literature_progress_callback, _literature_agent_instance
    _literature_progress_callback = callback
    if _literature_agent_instance is not None:
        _literature_agent_instance.progress_callback = callback


class LiteraturePlanTool(_BaseTool):
    """Phase 1: Planning - 制定文献检索计划"""
    name = "literature_plan"
    description = _get_global_literature_agent().get_tool_definitions()[0]["description"]
    input_schema = _get_global_literature_agent().get_tool_definitions()[0]["input_schema"]
    read_only = True

    def run(self, inp: dict) -> dict:
        agent = _get_global_literature_agent()
        dimensions = inp.get("dimensions", [])
        session_topic = inp.get("session_topic", "")
        if not dimensions:
            return {"error": "缺少 dimensions 字段", "hint": "请提供检索维度列表"}
        try:
            validated = agent.validate_plan(dimensions, session_topic=session_topic)
            return {
                "success": True,
                "message": f"检索计划已制定，共 {len(validated)} 个维度",
                "session_topic": session_topic,
                "dimensions": [d.model_dump() for d in validated]
            }
        except ValueError as e:
            return {"error": str(e), "hint": "请按照契约格式重新生成维度列表"}


class LiteratureSearchTool(_BaseTool):
    """Phase 2: Searching - 执行文献检索"""
    name = "literature_search"
    description = _get_global_literature_agent().get_tool_definitions()[1]["description"]
    input_schema = _get_global_literature_agent().get_tool_definitions()[1]["input_schema"]
    read_only = True

    def run(self, inp: dict) -> dict:
        agent = _get_global_literature_agent()
        dimension_id = inp.get("dimension_id", "")
        dimension_name = inp.get("dimension_name", "")
        papers = inp.get("papers", [])

        if not dimension_id or not dimension_name:
            return {"error": "缺少必需字段", "hint": "请提供 dimension_id 和 dimension_name"}
        if not papers:
            return {"error": "缺少 papers 字段", "hint": "请提供检索到的文献列表"}

        try:
            result = agent.validate_search_results(
                dimension_id=dimension_id,
                dimension_name=dimension_name,
                papers=papers
            )
            return {
                "success": True,
                "message": f"维度 [{dimension_name}] 检索完成",
                **result.model_dump()
            }
        except ValueError as e:
            return {"error": str(e), "hint": "请检查文献数据格式是否符合契约要求"}


class LiteratureGenerateReportTool(_BaseTool):
    """Phase 3: Generating - 生成综述报告"""
    name = "literature_generate_report"
    description = _get_global_literature_agent().get_tool_definitions()[2]["description"]
    input_schema = _get_global_literature_agent().get_tool_definitions()[2]["input_schema"]

    def run(self, inp: dict) -> dict:
        agent = _get_global_literature_agent()
        try:
            report = agent.validate_report(inp)
            return {
                "success": True,
                "message": "综述报告生成完成",
                **report.model_dump()
            }
        except ValueError as e:
            return {"error": str(e), "hint": "请检查报告格式是否符合契约要求"}


class LiteratureDbSearchTool(_BaseTool):
    """学术数据库搜索引擎 — 快速获取结构化文献列表"""
    name = "literature_db_search"
    description = (
        "Search academic databases for papers by keywords. "
        "Automatically tries Semantic Scholar, PubMed, arXiv, OpenAlex, Crossref, and ClinicalTrials.gov. "
        "Returns structured paper data (title, abstract, url, authors, journal, year). "
        "Use this FIRST when you need to find papers — it is faster and more reliable than web_fetch."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "keywords": {
                "type": "string",
                "description": "Search keywords (e.g. 'deep learning medical imaging')"
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (1-20, default 10)"
            },
            "source": {
                "type": "string",
                "description": "Database to search: 'auto' (fallback chain), 'pubmed', 'arxiv', 'semantic_scholar', 'clinicaltrials', 'openalex', 'crossref'. Default 'auto'."
            },
            "year_from": {
                "type": "integer",
                "description": "Earliest publication year (optional, e.g. 2020)"
            },
            "year_to": {
                "type": "integer",
                "description": "Latest publication year (optional, e.g. 2025)"
            },
            "dimension_id": {
                "type": "string",
                "description": "If provided, auto-submit results to frontend (recommended)"
            },
            "dimension_name": {
                "type": "string",
                "description": "If provided, auto-submit results to frontend (recommended)"
            }
        },
        "required": ["keywords"]
    }
    read_only = True

    def run(self, inp: dict) -> dict:
        keywords = inp.get("keywords", "").strip()
        if not keywords:
            return {"error": "keywords is required", "papers": [], "source": "none"}
        limit = max(1, min(inp.get("limit", 10), 20))
        year_from = inp.get("year_from")
        year_to = inp.get("year_to")
        source = inp.get("source", "auto")
        result = search_academic_databases(keywords, limit, year_from, year_to, source)
        # Auto-submit with Chinese title translations
        dimension_id = inp.get("dimension_id", "")
        dimension_name = inp.get("dimension_name", "")
        if dimension_id and dimension_name and result.get("papers"):
            papers = result["papers"]
            # Translate titles to Chinese
            for p in papers:
                if p.get("title") and not p.get("title_cn"):
                    cn = _translate_title(p["title"])
                    if cn:
                        p["title_cn"] = cn
            try:
                agent = _get_global_literature_agent()
                validated = agent.validate_search_results(
                    dimension_id=dimension_id,
                    dimension_name=dimension_name,
                    papers=papers,
                )
                result["submitted"] = True
                result["dimension_id"] = dimension_id
                result["dimension_name"] = dimension_name
                result["paper_count"] = len(validated.papers)
            except Exception as e:
                result["submit_warning"] = str(e)
        return result


class LiteratureConfirmTool(_BaseTool):
    """User confirmation checkpoint — blocks until the user confirms or revises."""
    name = "literature_confirm"
    description = (
        "Ask the user to confirm before proceeding to the next phase. "
        "Call this AFTER literature_plan (phase='planning') to let the user review dimensions, "
        "and AFTER all literature_search calls are done (phase='searching') to let the user "
        "decide if they want more searches or to generate the report."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "phase": {
                "type": "string",
                "enum": ["planning", "searching"],
                "description": "Which phase needs confirmation: 'planning' (after plan) or 'searching' (after all searches)"
            },
            "message": {
                "type": "string",
                "description": "Message to show the user describing what they are confirming"
            }
        },
        "required": ["phase", "message"]
    }
    read_only = True

    def run(self, inp: dict) -> dict:
        phase = inp.get("phase", "")
        message = inp.get("message", "")
        if not phase or phase not in ("planning", "searching"):
            return {"error": "phase must be 'planning' or 'searching'"}
        cb = _literature_confirm_callback
        if cb is None:
            return {"confirmed": True, "feedback": "", "note": "No confirm callback registered — auto-confirmed"}
        try:
            result = cb(phase, message)
            return result
        except Exception as e:
            return {"confirmed": True, "feedback": "", "note": f"Confirm callback error — auto-confirmed: {e}"}


class LiteratureQuickSearchTool(_BaseTool):
    """轻量文献搜索 — 快速找几篇文章，无需完整流程"""
    name = "literature_quick_search"
    description = (
        "Quick paper search — use this when the user wants to find a few specific papers "
        "or supporting references WITHOUT going through the full multi-dimension review pipeline. "
        "Results are rendered as a visual card automatically. "
        "Focus your response on answering the user's question — do NOT repeat paper titles/details in text."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "keywords": {
                "type": "string",
                "description": "Search keywords (e.g. 'deep learning medical imaging segmentation')"
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (1-10, default 5)"
            },
            "source": {
                "type": "string",
                "description": "Database: 'auto' (fallback chain), 'pubmed', 'arxiv', 'semantic_scholar', 'openalex', 'crossref'. Default 'auto'."
            },
            "year_from": {
                "type": "integer",
                "description": "Earliest publication year (optional)"
            },
            "year_to": {
                "type": "integer",
                "description": "Latest publication year (optional)"
            }
        },
        "required": ["keywords"]
    }
    read_only = True

    def run(self, inp: dict) -> dict:
        keywords = inp.get("keywords", "").strip()
        if not keywords:
            return {"error": "keywords is required", "papers": []}
        limit = max(1, min(inp.get("limit", 5), 10))
        year_from = inp.get("year_from")
        year_to = inp.get("year_to")
        source = inp.get("source", "auto")
        result = search_academic_databases(keywords, limit, year_from, year_to, source)
        papers = result.get("papers", [])
        # Translate titles to Chinese
        for p in papers:
            if p.get("title") and not p.get("title_cn"):
                cn = _translate_title(p["title"])
                if cn:
                    p["title_cn"] = cn
        # 通过全局进度回调发送文献卡片（前端渲染搜索卡片）
        try:
            global _literature_progress_callback
            if _literature_progress_callback:
                _literature_progress_callback(ProgressUpdate(
                    phase=ResearchPhase.SEARCHING,
                    message=f"快速搜索: {keywords}",
                    data={
                        "dimension_id": "_quick_" + str(int(__import__('time').time())),
                        "dimension_name": keywords[:40],
                        "paper_count": len(papers),
                        "papers": papers
                    }
                ))
        except Exception:
            pass
        return {
            "paper_count": len(papers),
            "source": result.get("source", source),
            "note": f"Found {len(papers)} papers. Papers have been shown in the literature card above — focus your response on answering the user, do NOT repeat paper details."
        }


class LiteratureAnalyzePapersTool(_BaseTool):
    """从指定的文献列表生成深度分析报告"""
    name = "literature_analyze_papers"
    description = (
        "Generate an in-depth analysis report from specific papers. "
        "Use this when the user asks you to analyze or write a report about papers "
        "that were found in previous searches. "
        "You pass the paper data directly — no separate search phase needed. "
        "The system will auto-append a reference table with accurate impact factors. "
        "Focus the report content on answering the user's specific request."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Report title"},
            "user_request": {"type": "string", "description": "What the user asked for — the analysis angle"},
            "html_content": {"type": "string", "description": "HTML content (use <h1>-<h3>, <p>, <ul>/<li>, <a>, <strong>, <em> only; no style tags)"},
            "summary": {"type": "string", "description": "Key conclusions summary (500 chars max)"},
            "total_papers_reviewed": {"type": "integer", "description": "Number of papers analyzed"},
            "papers": {
                "type": "array",
                "description": "The papers to include in the reference table. Pass the actual paper data from the search results.",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "title_cn": {"type": "string"},
                        "authors": {"type": "string"},
                        "journal": {"type": "string"},
                        "year": {"type": "integer"},
                        "abstract": {"type": "string", "description": "Paper abstract (required for report generation)"},
                        "url": {"type": "string", "description": "Paper URL (required, for hyperlinks)"},
                        "impact_factor": {"type": "number"},
                        "citation_count": {"type": "integer"}
                    }
                }
            }
        },
        "required": ["title", "html_content", "summary", "total_papers_reviewed"]
    }
    read_only = True

    def run(self, inp: dict) -> dict:
        agent = _get_global_literature_agent()
        papers = inp.get("papers", [])
        if papers:
            agent.cache_papers(papers)
        try:
            report = agent.validate_report(inp)
            return {"success": True, "message": "分析报告生成完成", **report.model_dump()}
        except ValueError as e:
            return {"error": str(e), "hint": "请检查报告格式是否符合契约要求"}


class LiteratureSpecifyPaperTool(_BaseTool):
    """通过标题精确查找一篇论文，返回完整数据供直接分析。"""
    name = "literature_specify_paper"
    description = (
        "Search for a specific paper by its exact title and return complete paper data. "
        "Use this when the user asks you to analyze a specific paper (e.g. '分析这篇文章：<title>'). "
        "This tool searches academic databases, finds the best match, and returns all available metadata. "
        "After receiving the result, proceed directly with analysis — do NOT call other search tools."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "The exact paper title to search for"},
        },
        "required": ["title"]
    }
    read_only = True

    @staticmethod
    def _normalize(s: str) -> str:
        """Normalize a string for loose title comparison."""
        import re as _re
        s = s.lower().strip()
        # Remove common punctuation, collapse whitespace
        s = _re.sub(r'[^\w\s]', '', s)
        s = _re.sub(r'\s+', ' ', s)
        return s.strip()

    def run(self, inp: dict) -> dict:
        title = inp.get("title", "").strip()
        if not title:
            return {"error": "title is required", "found": False}

        # Search ALL sources, looking for the best title match
        all_papers: list[tuple[dict, str]] = []  # (paper, source_name)
        for src_name, src_fn in _SOURCES:
            try:
                papers = src_fn(title, limit=10)
                if papers:
                    for p in papers:
                        all_papers.append((p, src_name))
            except Exception:
                continue

        if not all_papers:
            return {"found": False, "note": f"No papers found matching '{title}'"}

        # Score each paper by title similarity
        qt_norm = self._normalize(title)

        def score(p: dict) -> float:
            pt = self._normalize(p.get("title") or "")
            if not pt:
                return 0.0
            # Exact match after normalization = highest score
            if pt == qt_norm:
                return 100.0
            # One contains the other
            if pt == qt_norm or pt.startswith(qt_norm) or qt_norm.startswith(pt):
                return 90.0
            # Word overlap ratio
            q_words = set(qt_norm.split())
            p_words = set(pt.split())
            if q_words and p_words:
                overlap = len(q_words & p_words) / max(len(q_words), len(p_words))
                return overlap * 50.0
            return 0.0

        # Sort by score descending, pick the best
        all_papers.sort(key=lambda x: score(x[0]), reverse=True)
        best, best_src = all_papers[0]
        best_score = score(best)

        # If the best match has empty abstract, try to enrich from other sources
        if not best.get("abstract", "").strip() and best.get("doi"):
            doi = best["doi"]
            for p, src in all_papers:
                if p.get("abstract", "").strip() and (
                    p.get("doi") == doi
                    or self._normalize(p.get("title", "")) == self._normalize(best.get("title", ""))
                ):
                    best["abstract"] = p["abstract"]
                    if not best.get("impact_factor") and p.get("impact_factor"):
                        best["impact_factor"] = p["impact_factor"]
                    if not best.get("citation_count") and p.get("citation_count"):
                        best["citation_count"] = p["citation_count"]
                    best_src = src
                    break

        # If still empty, try direct fetch by DOI from Semantic Scholar (known to have abstracts)
        if not best.get("abstract", "").strip() and best.get("doi"):
            try:
                import urllib.request as _req
                import json as _json
                s2_url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{best['doi']}?fields=title,abstract,authors,year,journal,citationCount,externalIds,url"
                s2_resp = _req.urlopen(s2_url, timeout=10)
                s2_data = _json.loads(s2_resp.read())
                if s2_data.get("abstract"):
                    best["abstract"] = s2_data["abstract"]
                if not best.get("citation_count") and s2_data.get("citationCount"):
                    best["citation_count"] = s2_data["citationCount"]
                if not best.get("impact_factor"):
                    best["impact_factor"] = s2_data.get("impactFactor")
            except Exception:
                pass

        # If still empty, try OpenAlex by DOI
        if not best.get("abstract", "").strip() and best.get("doi"):
            try:
                import urllib.request as _req
                import json as _json
                oa_url = f"https://api.openalex.org/works/doi:{best['doi']}"
                oa_resp = _req.urlopen(oa_url, timeout=10)
                oa_data = _json.loads(oa_resp.read())
                if oa_data.get("abstract_inverted_index"):
                    # OpenAlex stores abstracts as inverted index
                    words = []
                    for word, positions in oa_data["abstract_inverted_index"].items():
                        for pos in positions:
                            words.append((pos, word))
                    words.sort()
                    best["abstract"] = " ".join(w for _, w in words)
                if not best.get("citation_count") and oa_data.get("cited_by_count"):
                    best["citation_count"] = oa_data["cited_by_count"]
            except Exception:
                pass

        # If even the best match is poor, note it
        match_note = ""
        if best_score < 50:
            match_note = (
                f"Note: Could not find an exact match for '{title}'. "
                f"The closest result from {best_src} is shown below. "
                "If this is not the right paper, try alternative search terms."
            )

        # Translate title to Chinese if missing
        if best.get("title") and not best.get("title_cn"):
            cn = _translate_title(best["title"])
            if cn:
                best["title_cn"] = cn

        # Emit progress card so frontend shows it
        try:
            global _literature_progress_callback
            if _literature_progress_callback:
                _literature_progress_callback(ProgressUpdate(
                    phase=ResearchPhase.SEARCHING,
                    message=f"找到论文: {best.get('title', title)[:60]}",
                    data={
                        "dimension_id": "_found_" + str(int(__import__('time').time())),
                        "dimension_name": best.get("title", title)[:40],
                        "paper_count": 1,
                        "papers": [best],
                    }
                ))
        except Exception:
            pass

        result = {
            "found": True,
            "source": best_src,
            "note": "Paper found. Proceed with analysis directly using this data — do NOT search again.",
            **best
        }
        if match_note:
            result["_match_note"] = match_note

        return result


class LiteratureSimilarSearchTool(_BaseTool):
    """根据一篇已知文献查找相似/相关文献。"""
    name = "literature_similar_search"
    description = (
        "Find academic papers similar to a given paper. "
        "Use this when the user wants to discover related work, compare papers, "
        "or find alternatives to a specific paper they uploaded or specified. "
        "Provide the original paper's DOI if known, or just the title. "
        "Uses Semantic Scholar recommendations API for accurate similarity matching. "
        "After receiving results, compare each found paper with the original."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "The title of the original paper"},
            "abstract": {"type": "string", "description": "Abstract or brief description of the original paper (optional, helps with relevance filtering)"},
            "doi": {"type": "string", "description": "DOI of the original paper (optional but recommended for better results)"},
            "keywords": {"type": "string", "description": "Extra keywords to guide the search (optional)"},
        },
        "required": ["title"]
    }
    read_only = True

    @staticmethod
    def _word_overlap(a: str, b: str) -> float:
        """Compute word overlap ratio between two strings (0.0 - 1.0)."""
        import re as _re
        def _tokenize(s):
            s = s.lower().strip()
            s = _re.sub(r'[^\w\s]', '', s)
            return set(_re.sub(r'\s+', ' ', s).split())
        ta = _tokenize(a)
        tb = _tokenize(b)
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / max(len(ta), len(tb))

    @staticmethod
    def _compute_relevance(p_title: str, p_abstract: str, orig_title: str, orig_abstract: str, orig_keywords: str = "") -> float:
        """Score how relevant a candidate paper is to the original (0.0 - 1.0)."""
        score = 0.0
        # Title overlap (most important)
        score += LiteratureSimilarSearchTool._word_overlap(p_title, orig_title) * 0.5
        # Abstract overlap (if available)
        if p_abstract and orig_abstract:
            score += LiteratureSimilarSearchTool._word_overlap(p_abstract, orig_abstract) * 0.3
        # Keyword overlap
        if orig_keywords:
            kw_lower = orig_keywords.lower()
            text = (p_title + " " + p_abstract).lower()
            kw_matches = sum(1 for kw in kw_lower.split() if kw in text)
            if kw_lower.split():
                score += (kw_matches / len(kw_lower.split())) * 0.2
        return min(score, 1.0)

    def run(self, inp: dict) -> dict:
        title = inp.get("title", "").strip()
        doi = inp.get("doi", "").strip()
        abstract = inp.get("abstract", "").strip()
        keywords = inp.get("keywords", "").strip()
        if not title and not doi:
            return {"error": "title or doi is required", "found": False}

        import urllib.request as _req
        import json as _json
        import re as _re
        import time as _time

        papers = []
        s2_id = None

        # ── Helper: normalize title ──
        def _normalize(s):
            s = s.lower().strip()
            s = _re.sub(r'[^\w\s]', '', s)
            return _re.sub(r'\s+', ' ', s).strip()

        # ── Strategy 1: Semantic Scholar ──
        try:
            # If DOI provided, use it directly
            if doi:
                s2_url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?fields=paperId,title,externalIds"
                s2_resp = _req.urlopen(s2_url, timeout=10)
                s2_data = _json.loads(s2_resp.read())
                s2_id = s2_data.get("paperId")

            # Otherwise, look up by title with word-overlap matching
            if not s2_id and title:
                q = _urllib_parse.quote(title)
                s2_url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={q}&limit=10&fields=title,paperId,externalIds"
                s2_resp = _req.urlopen(s2_url, timeout=10)
                s2_data = _json.loads(s2_resp.read())
                best_score = 0.35  # minimum word overlap threshold
                best_item = None
                qt_norm = _normalize(title)
                for item in s2_data.get("data", []):
                    pt = _normalize(item.get("title") or "")
                    overlap = self._word_overlap(pt, qt_norm)
                    if overlap > best_score:
                        best_score = overlap
                        best_item = item
                if best_item:
                    s2_id = best_item.get("paperId")
                    if not doi:
                        doi = best_item.get("externalIds", {}).get("DOI", "")

            # Get recommendations
            if s2_id:
                _time.sleep(1.0)  # rate limit
                rec_url = f"https://api.semanticscholar.org/graph/v1/paper/{s2_id}/recommendations?limit=15&fields=title,abstract,url,authors,year,journal,externalIds,citationCount"
                rec_resp = _req.urlopen(rec_url, timeout=15)
                rec_data = _json.loads(rec_resp.read())
                for item in rec_data.get("recommendations", []):
                    authors = item.get("authors") or []
                    papers.append({
                        "title": item.get("title", ""),
                        "abstract": item.get("abstract") or "",
                        "url": item.get("url") or "",
                        "authors": ", ".join(a.get("name", "") for a in authors),
                        "journal": item.get("journal", {}).get("name", "") if isinstance(item.get("journal"), dict) else "",
                        "year": item.get("year"),
                        "doi": item.get("externalIds", {}).get("DOI", ""),
                        "citation_count": item.get("citationCount"),
                        "_source": "Semantic Scholar",
                    })
        except Exception:
            pass

        # ── Strategy 2: OpenAlex related_works by DOI ──
        if len(papers) < 5 and doi:
            try:
                _time.sleep(0.3)
                oa_url = f"https://api.openalex.org/works/doi:{doi}?select=id,related_works"
                oa_resp = _req.urlopen(oa_url, timeout=10)
                oa_data = _json.loads(oa_resp.read())
                related_ids = oa_data.get("related_works", [])[:10]
                if related_ids:
                    ids_param = "|".join(related_ids)
                    oa_batch = f"https://api.openalex.org/works?filter=openalex:{ids_param}&select=title,abstract_inverted_index,authorships,publication_year,primary_location,cited_by_count,doi,id&per_page=10"
                    _time.sleep(0.3)
                    batch_resp = _req.urlopen(oa_batch, timeout=15)
                    batch_data = _json.loads(batch_resp.read())
                    for item in batch_data.get("results", []):
                        item_doi = item.get("doi", "")
                        if item_doi and any(p.get("doi") == item_doi for p in papers):
                            continue
                        abstract = ""
                        if item.get("abstract_inverted_index"):
                            words = []
                            for word, positions in item["abstract_inverted_index"].items():
                                for pos in positions:
                                    words.append((pos, word))
                            words.sort()
                            abstract = " ".join(w for _, w in words)
                        loc = item.get("primary_location") or {}
                        source = loc.get("source") or {}
                        papers.append({
                            "title": item.get("title", ""),
                            "abstract": abstract,
                            "url": item.get("doi", ""),
                            "authors": ", ".join(a.get("author", {}).get("display_name", "") for a in item.get("authorships", [])),
                            "journal": source.get("display_name", ""),
                            "year": item.get("publication_year"),
                            "doi": item_doi,
                            "citation_count": item.get("cited_by_count"),
                            "_source": "OpenAlex",
                        })
            except Exception:
                pass

        # ── Strategy 3: Keyword search fallback (only if still empty) ──
        if not papers:
            kw = keywords or title
            if kw:
                kw_result = search_academic_databases(kw, limit=10)
                kw_papers = kw_result.get("papers", [])
                qt_norm = _normalize(title) if title else ""
                for p in kw_papers:
                    # Skip the original paper itself
                    if qt_norm and _normalize(p.get("title", "")) == qt_norm:
                        continue
                    if p.get("doi") and any(ex.get("doi") == p["doi"] for ex in papers):
                        continue
                    p["_source"] = kw_result.get("source", "keyword")
                    papers.append(p)

        if not papers:
            return {"found": False, "note": f"No similar papers found for '{title}'", "papers": []}

        # ── Relevance scoring & filtering ──
        orig_text_for_score = title
        if abstract:
            orig_text_for_score += " " + abstract
        scored = []
        for p in papers:
            p_abs = p.get("abstract") or ""
            score = self._compute_relevance(
                p.get("title", ""), p_abs, title, abstract, keywords
            )
            scored.append((score, p))

        # Sort by relevance descending
        scored.sort(key=lambda x: x[0], reverse=True)
        # Filter: keep papers with score >= 0.10 (very low bar to remove only clearly irrelevant)
        filtered = [p for s, p in scored if s >= 0.10]

        if not filtered:
            # Keep top 3 even if below threshold (better than returning nothing)
            filtered = [p for _, p in scored[:3]]

        # Keep top 10 max
        filtered = filtered[:10]

        # ── Translate missing Chinese titles ──
        for p in filtered:
            if p.get("title") and not p.get("title_cn"):
                try:
                    cn = _translate_title(p["title"])
                    if cn:
                        p["title_cn"] = cn
                except Exception:
                    pass

        # ── Emit literature cards ──
        try:
            global _literature_progress_callback
            if _literature_progress_callback:
                _literature_progress_callback(ProgressUpdate(
                    phase=ResearchPhase.SEARCHING,
                    message=f"找到 {len(filtered)} 篇相似文献",
                    data={
                        "dimension_id": "_similar_" + str(int(_time.time())),
                        "dimension_name": "相似文献推荐",
                        "paper_count": len(filtered),
                        "papers": filtered,
                    }
                ))
        except Exception:
            pass

        # Append relevance scores for LLM reference (not shown in cards)
        enriched = list(filtered)
        for i, p in enumerate(enriched):
            p["_relevance"] = round(scored[i][0], 2) if i < len(scored) else 0.0

        return {
            "found": True,
            "paper_count": len(enriched),
            "original_title": title,
            "papers": enriched,
            "note": f"Found {len(enriched)} similar papers (sorted by relevance). Briefly introduce each one and compare with the original paper.",
        }


if __name__ == "__main__":
    # 示例：验证数据契约
    agent = LiteratureAgent()
    
    # 测试 Plan 阶段
    print("=" * 60)
    print("测试 Phase 1: Planning")
    print("=" * 60)
    test_dimensions = [
        {
            "dimension_id": "dim_1",
            "dimension_name": "深度学习基础理论",
            "search_keywords": "deep learning convergence optimization",
            "target_count": 15,
            "year_range": "2020-2025"
        }
    ]
    validated_dims = agent.validate_plan(test_dimensions)
    print(f"✓ 验证通过，共 {len(validated_dims)} 个维度")
    
    # 测试 Search 阶段
    print("\n" + "=" * 60)
    print("测试 Phase 2: Searching")
    print("=" * 60)
    test_papers = [
        {
            "title": "Attention Is All You Need",
            "abstract": "The dominant sequence transduction models are based on...",
            "url": "https://arxiv.org/abs/1706.03762",
            "year": 2017,
            "citation_count": 98234
        }
    ]
    result = agent.validate_search_results("dim_1", "深度学习基础理论", test_papers)
    print(f"✓ 验证通过，检索到 {len(result.papers)} 篇文献")
    
    # 测试 Generate 阶段
    print("\n" + "=" * 60)
    print("测试 Phase 3: Generating")
    print("=" * 60)
    test_report = {
        "title": "深度学习研究综述",
        "html_content": "<html><body><h1>综述</h1><p>引用示例：<a href='https://arxiv.org/abs/1706.03762'>[Vaswani, 2017]</a></p></body></html>",
        "summary": "本综述分析了深度学习的最新进展...",
        "total_papers_reviewed": 15
    }
    report = agent.validate_report(test_report)
    print(f"✓ 验证通过，综述了 {report.total_papers_reviewed} 篇文献")
    
    print("\n" + "=" * 60)
    print("所有测试通过！契约定义正确。")
    print("=" * 60)