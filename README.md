# 文档问答前端

极简主义风格的文档问答前端页面，基于 RAG 后端服务。

## 功能特性

- 📄 多文件上传（支持拖拽和点击上传）
- 🔍 智能问答交互
- 📋 相关文档片段展示
- 🎨 极简主义设计风格

## 支持的文件格式

- PDF
- Word (DOCX, DOC)
- 文本 (TXT, MD)
- HTML
- Excel (XLSX, XLS)
- PowerPoint (PPTX, PPT)

## 快速开始

### 1. 启动后端服务

确保后端服务已在 `http://localhost:8000` 运行：

```bash
# 进入后端目录
cd ForRag

# 启动服务
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 2. 启动前端

**方式一：直接打开**

直接用浏览器打开 `index.html` 文件：

```bash
# macOS
open ForRag/frontend/index.html

# Windows
start ForRag/frontend/index.html

# Linux
xdg-open ForRag/frontend/index.html
```

**方式二：使用本地服务器**

```bash
# Python 3
cd ForRag/frontend
python -m http.server 3000

# 然后访问 http://localhost:3000
```

**方式三：VS Code Live Server**

如果你使用 VS Code，可以安装 Live Server 插件，然后右键点击 `index.html` 选择 "Open with Live Server"。

## 使用说明

1. **上传文件**：将文件拖拽到上传区域，或点击选择文件
2. **输入问题**：在文本框中输入您的问题
3. **调整参数**：可选择检索片段数量（默认3）
4. **提交问答**：点击"提问"按钮获取答案
5. **查看结果**：在结果区域查看答案和相关文档片段

## 项目结构

```
ForRag/frontend/
├── index.html    # 主页面
├── styles.css    # 样式表
├── app.js        # 交互逻辑
└── README.md     # 说明文档
```

## API 说明

### 健康检查

```
GET /health
```

### 问答接口

```
POST /api/v1/qa
Content-Type: multipart/form-data
```

**请求参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| question | string | 是 | 用户问题 |
| files | File[] | 是 | 上传的文件列表 |
| top_k | int | 否 | 检索片段数，默认3 |

**响应格式：**

```json
{
    "answer": "生成的答案内容",
    "route": "api" | "local",
    "hits": [
        {
            "score": 0.95,
            "source": "文件名.pdf",
            "page_label": "Page 1",
            "meta": {
                "text": "文档片段内容..."
            }
        }
    ]
}
```

## 技术栈

- 纯 HTML5 + CSS3 + JavaScript
- 无框架依赖
- 响应式设计
- CSS 变量管理主题

## 浏览器兼容性

- Chrome 80+
- Firefox 75+
- Safari 13+
- Edge 80+

## License

MIT
