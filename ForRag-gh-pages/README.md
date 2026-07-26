# ForRag-gh-pages — 静态前端说明

本目录是 **ForRag** 项目的**纯静态前端**（HTML / CSS / JavaScript），与仓库根目录下的 **FastAPI 后端**（`rag_api/`、`fastapi_service.py`）配合使用，实现基于 **RAG（检索增强生成）** 的文档问答、会话知识库与测验巩固。

---

## 功能概览

| 页面 | 文件 | 作用 |
|------|------|------|
| **AI 助手** | `index.html` + `app.js` | 按会话上传文档（PDF、DOCX、PPTX、表格、图片等）、多轮对话、可配置检索片段数（Top-K）、展示答案与命中片段；支持异步问答轮询；可勾选助手消息生成测验并跳转测验页。 |
| **知识库** | `kb.html` + `kb.js` | 同一会话内：类目、Markdown 笔记、附件；笔记与附件可纳入后续检索。 |
| **测验** | `quiz.html` | 在助手页生成测验后在此答题、提交批改、查看解析与小结。 |
| **样式** | `styles.css` | 全局布局与组件样式（侧栏、聊天区、测验等）。 |

后端能力（由 `rag_api/routes.py` 等实现）包括但不限于：`POST /api/v1/sessions` 创建会话、文件上传、同步/异步问答、聊天记录、测验生成与批改、知识库 CRUD。检索侧在服务端会调用 `doc_qa_assistant.py` 与可选的 `rag_pipeline.py`（混合检索、重排等，见根目录环境变量说明）。

---

## 与后端如何连接

1. 浏览器加载本目录下的页面后，脚本会向 **`API_BASE`** 发请求，例如：
   - `GET /health`
   - `POST /api/v1/sessions`
   - `POST /api/v1/sessions/{id}/files`
   - `POST /api/v1/sessions/{id}/qa` 或 `.../qa/async` 等。

2. **`API_BASE` 的默认规则**（与 `app.js`、`kb.js` 中 `resolveDefaultApiBase()` 一致）：
   - **页面在 `*.github.io` 上**：默认指向占位公网地址；**部署到 GitHub Pages 时，你必须把 API 指到自己的后端**（见下文「配置」）。
   - **`file://` 打开本地文件**：默认 `http://127.0.0.1:8000`。
   - **常见本地前端端口**（如 Live Server 的 `5500`、`3000`、`5173` 等）：默认同一主机上的 **`http(s)://主机:8000`**，即假定后端跑在 8000 端口。
   - **与后端同源打开**（例如用 uvicorn 挂载本目录、直接访问 `http://127.0.0.1:8000/`）：使用 **`window.location.origin`**，无需改配置。

3. **访问令牌**：若服务端设置了 `RAG_ACCESS_TOKEN`，浏览器需在 **localStorage** 中写入与服务器相同的令牌，请求才会带上 `Authorization: Bearer ...`。可在控制台执行：
   ```js
   localStorage.setItem('RAG_ACCESS_TOKEN', '<与服务器环境变量相同的值>');
   location.reload();
   ```
   未设置令牌且服务端允许匿名时，请求可不带头（取决于后端 `RAG_REQUIRE_ACCESS_TOKEN` 等配置）。

4. **会话**：首次使用会创建会话，`session_id` 与 `X-Session-Secret` 存在 **localStorage**（键名见下节）。同一浏览器内多页（助手 / 知识库 / 测验）共享该会话。启用登录鉴权时，服务端还会校验当前用户是否为会话 `owner`，不同学生之间的会话互不可见。侧栏多会话列表按登录用户名分区存储。

---

## 本地开发与调试

### 方式 A（推荐）：后端挂载本目录，同源访问

在仓库根目录启动（示例）：

```bash
py -3.12 -m uvicorn fastapi_service:app --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000/` 即可打开 `index.html`，**API 与页面同源**，一般无 CORS 问题。更完整的环境变量说明见根目录 `fastapi_service.py` 文件顶部的文档字符串。

### 方式 B：静态服务器 + 独立后端

用 VS Code Live Server、 `python -m http.server` 等打开本目录（例如 `http://127.0.0.1:5500`），脚本会默认把 API 指到 **`http://127.0.0.1:8000`**。请确保后端已在 8000 端口运行，且 CORS 允许该来源（见服务端 `RAG_ALLOWED_ORIGINS`、`RAG_CORS_STRICT`）。

### 方式 C：GitHub Pages 仅托管前端

前端可部署在 GitHub Pages，但 **API 必须部署在你可控的服务器**（或隧道到本机）。部署后务必在浏览器中设置：

```js
localStorage.setItem('RAG_API_BASE', 'https://你的后端域名');
location.reload();
```

（若曾保存过已过期的 `RAG_API_BASE`，请更新或清除后再设。）

---

## 常用 localStorage 键

| 键 | 含义 |
|----|------|
| `RAG_API_BASE` | 后端根 URL（可选覆盖默认推断）。 |
| `RAG_ACCESS_TOKEN` | 与服务器 `RAG_ACCESS_TOKEN` 一致的 Bearer 令牌。 |
| `RAG_SESSION_ID` / `RAG_SESSION_SECRET` | 当前会话 ID 与密钥。 |
| `RAG_CONVERSATIONS::<username>` | 该用户的侧栏多会话列表（明文含各会话 secret）；无用户名时回退键 `RAG_CONVERSATIONS`（遗留）。 |
| `RAG_LAST_QUIZ` | 最近一次测验载荷，供 `quiz.html` 使用。 |
| `hku_username` / `hku_role` 等 | 登录态（见 `auth.js`）；登出时清理当前会话键与遗留全局 `RAG_CONVERSATIONS`。 |

页面也可在构建/注入时设置全局变量 `window.__API_BASE__` 指定 API 根地址（优先级高于部分默认逻辑）。

---

## 使用提示

- **单会话上传文件数量上限**：前端 `app.js` 中 `MAX_FILES` 为 **5**（与后端默认 `RAG_MAX_FILES` 等配置应对齐；若改后端，请同步前端或接受服务端校验错误）。
- **异步问答**：助手页对长时间推理使用轮询任务接口（`POLL_MS` 等，见 `app.js`）。
- **快捷键**（以 `app.js` / `quiz.html` 为准）：
  - **Ctrl/Cmd + Enter**：发送问题 / 提交测验批改。
  - **Ctrl/Cmd + L**：清空当前聊天（若页面提供该快捷键）。
  - **Shift + Ctrl/Cmd + R**（测验页）：重置答题状态。

---

## 目录结构

```
ForRag-gh-pages/
├── index.html      # AI 助手（主界面）
├── app.js          # 助手页逻辑（会话、上传、问答、测验入口）
├── kb.html         # 知识库
├── kb.js           # 知识库逻辑
├── quiz.html       # 测验（内联脚本 + 页面结构）
├── styles.css      # 全局样式
└── README.md       # 本说明
```

---

## 故障排查简表

| 现象 | 可能原因 |
|------|----------|
| 跨域被拦截 | 后端未允许当前页面 Origin；检查 `RAG_ALLOWED_ORIGINS` / `RAG_CORS_STRICT`。 |
| 401 / 缺少令牌 | 服务端要求 Bearer；设置 `localStorage.RAG_ACCESS_TOKEN` 与服务器一致。 |
| 连不上 API | `RAG_API_BASE` 错误或后端未启动；GitHub Pages 场景必须显式配置后端地址。 |
| 上传失败 / 413 | 文件过大或超过 `RAG_MAX_FILE_MB`；参考服务端环境变量。 |

---

## License

若仓库根目录存在 `LICENSE`，以该文件为准；否则与主项目保持一致。
