## 背景与目标

当前实现将“知识库（KB）”与 `session_id` 绑定：KB 的 SQLite 表含 `session_id` 列，KB 笔记正文/附件也存放在 `.uploads/<session_id>/kb/...`。这会导致开启新对话（新 session）后，无法复用旧对话里构建的知识库。

本次改造目标（单用户本地应用）：

- **全局知识库附件/笔记**：作为长期资料库，**所有对话共用**（不随 `session_id` 变化而隔离/丢失）。
- **对话问答输入框上传文件**：作为会话临时材料，**仅与当前对话/会话绑定**（删除会话时随会话删除）。

采用方案 A：引入全局 `kb_id="default"`（未来可扩展为 workspace/user 维度），KB 数据与文件路径均不再与 `session_id` 绑定；会话文件仍按 session 走原逻辑。


## 术语

- **会话文件（session files）**：通过对话页上传到 `/sessions/{session_id}/files` 的文件。
- **知识库（KB）**：通过 KB 页面管理的类目、笔记正文、笔记附件。
- **kb_id**：全局知识库归属键；单用户本地版固定为 `"default"`。


## 现状（需改变点）

- `kb.sqlite` 表：`kb_categories / kb_notes / kb_note_files` 均含 `session_id`，并以其为过滤条件。
- KB 文件路径：`.uploads/<session_id>/kb/notes/<note_id>.md` 与 `.uploads/<session_id>/kb/files/<file_id>_<safe_name>`
- 会话删除：`DELETE /sessions/{id}` 会级联清理该 session 的 KB（需避免误删全局 KB）。
- 检索聚合：`collect_qa_index_inputs(session_id, kb_scope, category_ids)` 的 KB 部分当前同样按 session 过滤。


## 目标架构

### 数据模型（SQLite）

将 KB 三张表从 `session_id` 迁移为 `kb_id`：

- `kb_categories(kb_id, ...)`
- `kb_notes(kb_id, category_id, ...)`
- `kb_note_files(kb_id, note_id, stored_rel, ...)`

约束：

- `kb_id` 对单用户固定为 `"default"`。
- 旧字段 `session_id` 将通过迁移脚本写入到新字段后可保留一段时间（兼容旧数据/回滚），最终可删除。

索引：

- `idx_kb_cat_kb` on `kb_categories(kb_id)`
- `idx_kb_notes_kb` on `kb_notes(kb_id)`
- `idx_kb_nf_kb` on `kb_note_files(kb_id)`


### 文件存储

KB 文件统一迁至全局目录，不再置于 `.uploads/<session_id>/...`：

- KB 笔记正文：`.uploads/kb/default/notes/<note_id>.md`
- KB 附件：`.uploads/kb/default/files/<file_id>_<safe_name>`

会话文件保持不变：

- 会话文件：`.uploads/<session_id>/<file_id>_<safe_name>`

`stored_rel` 规则：

- KB：`kb/default/notes/<note_id>.md`、`kb/default/files/<file_id>_<safe_name>`
- 会话文件：`<session_id>/<file_id>_<safe_name>`（保持原逻辑）


### API 设计（后端）

为减少前端改动，提供两层 API：

1) **新增全局 KB 路由（推荐使用）**

- `GET /api/v1/kb/categories`
- `POST /api/v1/kb/categories`
- `PATCH /api/v1/kb/categories/{category_id}`
- `DELETE /api/v1/kb/categories/{category_id}`
- `GET /api/v1/kb/categories/{category_id}/notes`
- `POST /api/v1/kb/categories/{category_id}/notes`
- `GET /api/v1/kb/notes/{note_id}`
- `PATCH /api/v1/kb/notes/{note_id}`
- `DELETE /api/v1/kb/notes/{note_id}`
- `GET /api/v1/kb/notes/{note_id}/files`
- `POST /api/v1/kb/notes/{note_id}/files`
- `DELETE /api/v1/kb/notes/{note_id}/files/{file_id}`

2) **保留旧的 session 级 KB 路由但改为代理到全局 KB**

现有 `/sessions/{session_id}/kb/...` 路由继续存在，但内部不再按 session 过滤；仅用于前端未升级时的兼容。


### 检索聚合（RAG）

`collect_qa_index_inputs(session_id, kb_scope, category_ids)` 改造：

- `session_files`：仍读取 `chroma_store.file_list(session_id)`（会话上传文件）
- `kb_only/union`：读取 `kb_id="default"` 的 KB 笔记正文与附件（不随 session 变化）
- `category_ids`：仅作用于 KB 侧（过滤全局 KB 的类目），不影响 session files

这样默认 `kb_scope="union"` 会实现：

- **全局 KB** + **当前会话文件** 一起参与检索


### 会话删除语义

`DELETE /sessions/{id}` 的删除范围调整为：

- 删除会话上传文件（`.uploads/<session_id>/...`）+ 会话消息/测验等会话数据
- **不删除全局 KB**（不再调用 `session_delete_all_kb`）

新增（或保留）KB 清理能力：

- `DELETE /api/v1/kb/reset`（可选）：显式清空全局 KB（类目/笔记/附件/文件）


## 迁移方案（从当前数据到全局 KB）

迁移分为两部分：SQLite 与文件系统。

### 1) SQLite 迁移

在 `init_kb_db()` 里做轻量迁移：

- 若表缺 `kb_id` 列：`ALTER TABLE ... ADD COLUMN kb_id TEXT`
- 将 `kb_id` 置为 `"default"`（对所有行）：
  - `UPDATE kb_categories SET kb_id='default' WHERE kb_id IS NULL OR kb_id=''`
  - `UPDATE kb_notes SET kb_id='default' ...`
  - `UPDATE kb_note_files SET kb_id='default' ...`
- 新增索引 `idx_*_kb`（并保留旧的 `idx_*_session` 一段时间）

读写逻辑改为优先使用 `kb_id`，不再依赖 `session_id`。


### 2) 文件迁移

将所有 `.uploads/<sid>/kb/notes/*.md` 与 `.uploads/<sid>/kb/files/*` 合并到：

- `.uploads/kb/default/notes/`
- `.uploads/kb/default/files/`

冲突策略：

- `notes/<note_id>.md`：以数据库中的 note_id 为准（理论上唯一）。如存在重名冲突，保留最新修改时间并记录日志。
- `files/<file_id>_<safe_name>`：以 `file_id` 前缀确保唯一。若已存在同名，追加后缀 `_dupN`。

同时更新 `kb_note_files.stored_rel`（以及笔记正文的 `stored_rel` 若引入）以指向新相对路径。


## 前端改动点（最小）

- `kb.js` 从调用 `/sessions/{session_id}/kb/...` 改为调用 `/kb/...`（全局）。如暂不改，后端兼容代理也可工作。
- 对话页无需修改即可使用全局 KB（后端默认 union 包含 KB）。


## 验收标准

- 新建对话（新 session）后，KB 页面中的类目/笔记/附件仍可见且不丢失。
- 新对话问答在未上传会话文件的情况下，也能从 KB 命中检索片段（hits 非空或 `kb_relevant=true`）。
- 删除某个 session 不会删除 KB 内容与 `.uploads/kb/default/**` 文件。
- 删除 KB 附件/笔记会正确删除全局文件，并在后续检索中不再出现。


## 回滚策略

- 迁移期保留 `session_id` 列与旧索引；如需回滚，可将查询逻辑切回 `session_id`（但文件已迁移时需同步恢复路径或增加兼容路径解析）。
- 文件迁移可先“复制”再切换引用（两阶段），确认无误后再删除旧目录。

