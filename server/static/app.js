/**
 * 文献检索进度可视化 - 契约式设计版本
 * ========================================
 * 
 * 核心理念：
 * - 前端只关心数据格式（契约），不关心数据来源
 * - 支持 Plan/Search/Generate 三个阶段的实时更新
 * - 完全解耦：Agent 怎么搜的与前端无关
 */

class LiteratureProgressVisualizer {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        if (!this.container) {
            console.error(`容器 #${containerId} 不存在`);
            return;
        }
        
        this.state = {
            phase: 'planning',  // planning | searching | generating | completed
            dimensions: [],
            searchResults: {},  // { dimension_id: { papers: [...], summary: "..." } }
            report: null
        };
        
        this.init();
    }
    
    init() {
        this.container.className = 'literature-progress-container';
        this.render();
    }
    
    // ========================================================================
    // 公开 API - 接收后端的进度更新
    // ========================================================================
    
    /**
     * 更新进度（后端调用此方法）
     * @param {Object} update - { phase, message, data }
     */
    updateProgress(update) {
        console.log('[Progress Update]', update);
        
        const { phase, message, data } = update;
        this.state.phase = phase;
        
        switch (phase) {
            case 'planning':
                if (data && data.dimensions) {
                    this.state.dimensions = data.dimensions;
                }
                break;
                
            case 'searching':
                if (data && data.dimension_id) {
                    this.state.searchResults[data.dimension_id] = {
                        dimension_name: data.dimension_name,
                        papers: data.papers || [],
                        paper_count: data.paper_count || 0
                    };
                }
                break;
                
            case 'generating':
                // 生成中暂时不需要特殊处理
                break;
                
            case 'completed':
                if (data) {
                    this.state.report = data;
                }
                break;
        }
        
        this.render();
    }
    
    // ========================================================================
    // 渲染主函数
    // ========================================================================
    
    render() {
        this.container.innerHTML = `
            ${this.renderOverallProgress()}
            ${this.renderPhaseDetails()}
        `;
    }
    
    // ========================================================================
    // 总体进度指示器
    // ========================================================================
    
    renderOverallProgress() {
        const phases = [
            { id: 'planning', icon: '📋', label: '制定计划', status: 'Plan' },
            { id: 'searching', icon: '🔍', label: '检索文献', status: 'Search' },
            { id: 'generating', icon: '📝', label: '生成报告', status: 'Generate' },
            { id: 'completed', icon: '✅', label: '完成', status: 'Done' }
        ];
        
        const currentPhaseIndex = phases.findIndex(p => p.id === this.state.phase);
        
        return `
            <div class="literature-overall-progress">
                <div class="progress-steps">
                    ${phases.map((phase, index) => {
                        const isActive = index === currentPhaseIndex;
                        const isCompleted = index < currentPhaseIndex;
                        const stateClass = isCompleted ? 'completed' : (isActive ? 'active' : '');
                        
                        return `
                            <div class="progress-step ${stateClass}">
                                <div class="step-icon">${phase.icon}</div>
                                <div class="step-label">${phase.label}</div>
                                <div class="step-status">${phase.status}</div>
                            </div>
                            ${index < phases.length - 1 ? '<div class="progress-connector"></div>' : ''}
                        `;
                    }).join('')}
                </div>
            </div>
        `;
    }
    
    // ========================================================================
    // 阶段详细信息
    // ========================================================================
    
    renderPhaseDetails() {
        const { phase, dimensions, searchResults, report } = this.state;
        
        let content = '';
        
        // Phase 1: Planning - 显示维度卡片
        if (dimensions.length > 0) {
            content += this.renderPlanningPhase(dimensions);
        }
        
        // Phase 2: Searching - 显示文献表格
        if (Object.keys(searchResults).length > 0) {
            content += this.renderSearchingPhase(searchResults);
        }
        
        // Phase 3: Completed - 显示报告信息
        if (phase === 'completed' && report) {
            content += this.renderCompletedPhase(report);
        }
        
        return `<div class="literature-details-container">${content}</div>`;
    }
    
    // ========================================================================
    // Phase 1: Planning - 维度展示
    // ========================================================================
    
    renderPlanningPhase(dimensions) {
        return `
            <div class="phase-section plan-section">
                <div class="phase-header">
                    <span class="phase-icon">📋</span>
                    <h3 class="phase-title">检索计划</h3>
                    <span class="phase-badge">${dimensions.length} 个维度</span>
                </div>
                <div class="dimensions-list">
                    ${dimensions.map((dim, index) => this.renderDimensionCard(dim, index + 1)).join('')}
                </div>
            </div>
        `;
    }
    
    renderDimensionCard(dimension, number) {
        const { 
            dimension_name, 
            search_keywords, 
            target_count, 
            quality_requirement, 
            year_range 
        } = dimension;
        
        return `
            <div class="dimension-card">
                <div class="dimension-header">
                    <div class="dimension-number">${number}</div>
                    <div class="dimension-name">${dimension_name}</div>
                    <div class="dimension-meta">
                        <span class="dimension-limit">目标: ${target_count} 篇</span>
                        ${quality_requirement ? `<span class="dimension-quality">${quality_requirement}</span>` : ''}
                    </div>
                </div>
                <div class="dimension-query">
                    <strong>检索词:</strong>
                    <code>${search_keywords}</code>
                    ${year_range ? `<div style="margin-top: 4px; font-size: 11px; color: #666;">年份范围: ${year_range}</div>` : ''}
                </div>
            </div>
        `;
    }
    
    // ========================================================================
    // Phase 2: Searching - 文献表格
    // ========================================================================
    
    renderSearchingPhase(searchResults) {
        return `
            <div class="phase-section search-section">
                <div class="phase-header">
                    <span class="phase-icon">🔍</span>
                    <h3 class="phase-title">检索结果</h3>
                    <span class="phase-badge">${Object.keys(searchResults).length} 个维度已完成</span>
                </div>
                <div class="search-results-container">
                    ${Object.entries(searchResults).map(([dimId, result]) => 
                        this.renderDimensionResults(result)
                    ).join('')}
                </div>
            </div>
        `;
    }
    
    renderDimensionResults(result) {
        const { dimension_name, papers, paper_count } = result;
        
        return `
            <div class="dimension-result-section">
                <div class="dimension-result-header">
                    <h4>${dimension_name}</h4>
                    <span class="paper-count-badge">${paper_count || papers.length} 篇文献</span>
                </div>
                ${papers.length > 0 ? this.renderPapersTable(papers) : '<p class="no-papers-msg">暂无文献数据</p>'}
            </div>
        `;
    }
    
    renderPapersTable(papers) {
        return `
            <div class="papers-table-wrap">
                <table class="papers-table">
                    <thead>
                        <tr>
                            <th class="col-number">#</th>
                            <th class="col-title">标题</th>
                            <th class="col-journal">期刊/会议</th>
                            <th class="col-year">年份</th>
                            <th class="col-if">IF/引用</th>
                            <th class="col-link">链接</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${papers.map((paper, index) => this.renderPaperRow(paper, index + 1)).join('')}
                    </tbody>
                </table>
            </div>
        `;
    }
    
    renderPaperRow(paper, number) {
        const { 
            title, 
            journal = '未知', 
            year = '-', 
            url, 
            impact_factor, 
            citation_count 
        } = paper;
        
        // 影响因子/引用数显示逻辑
        let ifDisplay = '-';
        let ifClass = '';
        if (impact_factor) {
            ifDisplay = `IF: ${impact_factor.toFixed(1)}`;
            ifClass = impact_factor >= 10 ? 'if-high' : (impact_factor >= 5 ? 'if-medium' : '');
        } else if (citation_count) {
            ifDisplay = `引用: ${citation_count}`;
        }
        
        return `
            <tr>
                <td class="col-number">${number}</td>
                <td class="col-title">
                    <div class="paper-title-cell" title="${title}">${title}</div>
                </td>
                <td class="col-journal">${journal}</td>
                <td class="col-year">${year}</td>
                <td class="col-if">
                    <span class="${ifClass}">${ifDisplay}</span>
                </td>
                <td class="col-link">
                    <a href="${url}" target="_blank" class="paper-link">查看原文</a>
                </td>
            </tr>
        `;
    }
    
    // ========================================================================
    // Phase 3: Completed - 报告展示
    // ========================================================================
    
    renderCompletedPhase(report) {
        const { title, summary, total_papers, has_html, has_markdown } = report;
        
        return `
            <div class="phase-section report-section">
                <div class="phase-header">
                    <span class="phase-icon">📝</span>
                    <h3 class="phase-title">综述报告</h3>
                    <span class="phase-badge">已完成</span>
                </div>
                <div class="report-info">
                    <p><strong>报告标题：</strong>${title}</p>
                    <p><strong>综述文献数：</strong>${total_papers} 篇</p>
                    <p><strong>核心结论：</strong>${summary}</p>
                    <p style="margin-top: 12px;">
                        ${has_html ? '<a href="#" class="report-link" onclick="window.openReport(\'html\')">📄 查看 HTML 报告</a>' : ''}
                        ${has_markdown ? '<a href="#" class="report-link" onclick="window.openReport(\'markdown\')" style="margin-left: 8px;">📝 查看 Markdown 报告</a>' : ''}
                    </p>
                </div>
            </div>
        `;
    }
}

// ============================================================================
// 全局实例（可选）
// ============================================================================

window.LiteratureProgressVisualizer = LiteratureProgressVisualizer;

// 示例：如何在后端触发进度更新
/*
// 在你的 server.py 或 WebSocket handler 中：

const visualizer = new LiteratureProgressVisualizer('literature-container');

// Phase 1: Planning 完成
visualizer.updateProgress({
    phase: 'planning',
    message: '检索计划已制定',
    data: {
        dimensions: [
            {
                dimension_id: 'dim_1',
                dimension_name: '深度学习基础理论',
                search_keywords: 'deep learning convergence',
                target_count: 15,
                year_range: '2020-2025'
            }
        ]
    }
});

// Phase 2: Searching 某个维度完成
visualizer.updateProgress({
    phase: 'searching',
    message: '维度 [深度学习基础理论] 检索完成',
    data: {
        dimension_id: 'dim_1',
        dimension_name: '深度学习基础理论',
        paper_count: 18,
        papers: [
            {
                title: 'Attention Is All You Need',
                journal: 'NeurIPS',
                year: 2017,
                url: 'https://arxiv.org/abs/1706.03762',
                citation_count: 98234
            }
        ]
    }
});

// Phase 3: Completed
visualizer.updateProgress({
    phase: 'completed',
    message: '综述报告生成完成',
    data: {
        title: '深度学习研究综述',
        summary: '本综述分析了...',
        total_papers: 45,
        has_html: true,
        has_markdown: false
    }
});
*/