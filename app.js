/**
 * 极简主义文档问答前端 - JavaScript
 * 
 * 功能：
 * 1. 文件上传（拖拽 + 点击）
 * 2. 问答交互
 * 3. 结果展示
 */

// ========================================
// 配置
// ========================================

const CONFIG = {
    API_BASE: 'http://localhost:8000',
    API_HEALTH: '/health',
    API_QA: '/api/v1/qa',
    ALLOWED_TYPES: [
        'application/pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/msword',
        'text/plain',
        'text/markdown',
        'text/html',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.ms-excel',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'application/vnd.ms-powerpoint'
    ],
    MAX_FILES: 20
};

// ========================================
// 状态管理
// ========================================

const state = {
    files: new DataTransfer().files, // 当前已上传文件列表
    isLoading: false
};

// ========================================
// DOM 元素
// ========================================

const elements = {
    uploadZone: document.getElementById('uploadZone'),
    fileInput: document.getElementById('fileInput'),
    fileList: document.getElementById('fileList'),
    questionInput: document.getElementById('questionInput'),
    topKSelect: document.getElementById('topKSelect'),
    submitBtn: document.getElementById('submitBtn'),
    resultSection: document.getElementById('resultSection'),
    answerContent: document.getElementById('answerContent'),
    sourcesCard: document.getElementById('sourcesCard'),
    sourcesHeader: document.getElementById('sourcesHeader'),
    sourcesBody: document.getElementById('sourcesBody'),
    sourcesList: document.getElementById('sourcesList'),
    sourcesCount: document.getElementById('sourcesCount'),
    toggleSources: document.getElementById('toggleSources'),
    toast: document.getElementById('toast')
};

// ========================================
// 工具函数
// ========================================

/**
 * 显示 Toast 提示
 */
function showToast(message, isError = false) {
    elements.toast.querySelector('.toast-message').textContent = message;
    elements.toast.classList.toggle('error', isError);
    elements.toast.classList.add('show');
    
    setTimeout(() => {
        elements.toast.classList.remove('show');
    }, 3000);
}

/**
 * 获取文件扩展名
 */
function getFileExtension(filename) {
    return filename.split('.').pop().toLowerCase();
}

/**
 * 获取文件类型图标 SVG
 */
function getFileIcon() {
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
        <polyline points="14,2 14,8 20,8"/>
    </svg>`;
}

/**
 * 验证文件类型
 */
function isValidFile(file) {
    const validExtensions = ['pdf', 'docx', 'doc', 'txt', 'md', 'html', 'xlsx', 'xls', 'pptx', 'ppt'];
    const extension = getFileExtension(file.name);
    return validExtensions.includes(extension);
}

/**
 * 更新文件列表显示
 */
function updateFileList() {
    elements.fileList.innerHTML = '';
    
    const fileArray = Array.from(state.files);
    fileArray.forEach((file, index) => {
        const fileItem = document.createElement('div');
        fileItem.className = 'file-item';
        fileItem.innerHTML = `
            <span class="file-icon">${getFileIcon()}</span>
            <span class="file-name" title="${file.name}">${file.name}</span>
            <button class="remove-btn" data-index="${index}" title="移除文件">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <line x1="18" y1="6" x2="6" y2="18"/>
                    <line x1="6" y1="6" x2="18" y2="18"/>
                </svg>
            </button>
        `;
        elements.fileList.appendChild(fileItem);
    });

    // 绑定删除事件
    elements.fileList.querySelectorAll('.remove-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            removeFile(parseInt(btn.dataset.index));
        });
    });

    updateSubmitButton();
}

/**
 * 添加文件
 */
function addFiles(files) {
    const fileArray = Array.from(files);
    
    // 过滤无效文件
    const validFiles = fileArray.filter(isValidFile);
    const invalidCount = fileArray.length - validFiles.length;
    
    if (invalidCount > 0) {
        showToast(`${invalidCount} 个无效文件已被过滤`);
    }

    if (validFiles.length === 0) {
        return;
    }

    // 检查文件数量限制
    if (state.files.length + validFiles.length > CONFIG.MAX_FILES) {
        showToast(`最多只能上传 ${CONFIG.MAX_FILES} 个文件`, true);
        return;
    }

    // 合并到现有文件
    const dt = new DataTransfer();
    Array.from(state.files).forEach(f => dt.items.add(f));
    validFiles.forEach(f => dt.items.add(f));
    state.files = dt.files;
    
    updateFileList();
}

/**
 * 移除文件
 */
function removeFile(index) {
    const dt = new DataTransfer();
    Array.from(state.files).forEach((f, i) => {
        if (i !== index) dt.items.add(f);
    });
    state.files = dt.files;
    updateFileList();
}

/**
 * 更新提交按钮状态
 */
function updateSubmitButton() {
    const hasFiles = state.files.length > 0;
    const hasQuestion = elements.questionInput.value.trim().length > 0;
    elements.submitBtn.disabled = !hasFiles || !hasQuestion || state.isLoading;
}

// ========================================
// 事件绑定
// ========================================

/**
 * 初始化事件监听
 */
function initEventListeners() {
    // 上传区域点击
    elements.uploadZone.addEventListener('click', () => {
        if (!state.isLoading) {
            elements.fileInput.click();
        }
    });

    // 文件选择
    elements.fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            addFiles(e.target.files);
            e.target.value = ''; // 重置以便重复选择同一文件
        }
    });

    // 拖拽上传
    elements.uploadZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        if (!state.isLoading) {
            elements.uploadZone.classList.add('dragover');
        }
    });

    elements.uploadZone.addEventListener('dragleave', (e) => {
        e.preventDefault();
        elements.uploadZone.classList.remove('dragover');
    });

    elements.uploadZone.addEventListener('drop', (e) => {
        e.preventDefault();
        elements.uploadZone.classList.remove('dragover');
        if (!state.isLoading && e.dataTransfer.files.length > 0) {
            addFiles(e.dataTransfer.files);
        }
    });

    // 问题输入
    elements.questionInput.addEventListener('input', updateSubmitButton);

    // 提交问答
    elements.submitBtn.addEventListener('click', submitQuestion);

    // 切换文档片段显示
    elements.toggleSources.addEventListener('click', () => {
        elements.sourcesCard.classList.toggle('collapsed');
    });
}

// ========================================
// API 交互
// ========================================

/**
 * 提交问答
 */
async function submitQuestion() {
    const question = elements.questionInput.value.trim();
    
    if (!question) {
        showToast('请输入问题', true);
        return;
    }

    if (state.files.length === 0) {
        showToast('请上传文件', true);
        return;
    }

    // 设置加载状态
    state.isLoading = true;
    elements.submitBtn.classList.add('loading');
    elements.submitBtn.disabled = true;
    
    // 显示结果区域
    elements.resultSection.classList.add('show');
    elements.answerContent.textContent = '';
    elements.sourcesList.innerHTML = '';
    elements.sourcesCount.textContent = '';
    
    // 滚动到结果区域
    elements.resultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

    try {
        // 构建 FormData
        const formData = new FormData();
        formData.append('question', question);
        formData.append('top_k', parseInt(elements.topKSelect.value));
        
        // 添加文件
        Array.from(state.files).forEach(file => {
            formData.append('files', file);
        });

        // 发送请求
        const response = await fetch(`${CONFIG.API_BASE}${CONFIG.API_QA}`, {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(errorText || `请求失败 (${response.status})`);
        }

        const data = await response.json();
        
        // 渲染答案
        elements.answerContent.textContent = data.answer || '抱歉，未能生成有效答案';
        
        // 渲染文档片段
        if (data.hits && data.hits.length > 0) {
            elements.sourcesCount.textContent = `(${data.hits.length})`;
            
            data.hits.forEach((hit, index) => {
                const sourceItem = document.createElement('div');
                sourceItem.className = 'source-item';
                sourceItem.style.animationDelay = `${index * 100}ms`;
                
                sourceItem.innerHTML = `
                    <div class="source-meta">
                        <span class="source-score">${(hit.score * 100).toFixed(1)}%</span>
                        <span class="source-page">${hit.page_label || hit.source || '未知来源'}</span>
                    </div>
                    <div class="source-content">${hit.meta?.text || hit.content || ''}</div>
                `;
                
                elements.sourcesList.appendChild(sourceItem);
            });
            
            // 默认展开
            elements.sourcesCard.classList.remove('collapsed');
        } else {
            elements.sourcesCard.style.display = 'none';
        }

    } catch (error) {
        console.error('问答请求失败:', error);
        elements.answerContent.textContent = `请求失败: ${error.message}`;
        showToast(error.message, true);
    } finally {
        state.isLoading = false;
        elements.submitBtn.classList.remove('loading');
        updateSubmitButton();
    }
}

// ========================================
// 初始化
// ========================================

/**
 * 应用初始化
 */
function init() {
    initEventListeners();
    updateSubmitButton();
    console.log('文档问答前端初始化完成');
}

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', init);
