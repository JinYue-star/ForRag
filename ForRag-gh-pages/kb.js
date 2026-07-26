/**
 * Knowledge base: categories, note attachments (download / teacher remove & delete).
 */
function resolveDefaultApiBase() {
    if (window.HKUAuth && typeof window.HKUAuth.resolveApiBase === 'function') {
        return window.HKUAuth.resolveApiBase();
    }
    if (window.location.hostname.endsWith('github.io')) return '';
    if (window.location.protocol === 'file:') return 'http://127.0.0.1:8000';
    const port = window.location.port;
    const devFrontendPorts = new Set(['5500', '5501', '3000', '5173', '4173', '8080']);
    if (port && devFrontendPorts.has(port)) {
        return `${window.location.protocol}//${window.location.hostname}:8000`;
    }
    return window.location.origin;
}

const CONFIG = {
    API_BASE: String((window.HKUAuth && window.HKUAuth.API_BASE) || resolveDefaultApiBase()).replace(
        /\/+$/,
        ''
    ),
    API_SESSIONS: '/api/v1/sessions',
    SESSION_ID_KEY: 'RAG_SESSION_ID',
    SESSION_SECRET_KEY: 'RAG_SESSION_SECRET'
};

const state = {
    categories: [],
    notes: [],
    attachments: [],
    attachmentsNoteId: null,
    exercises: [],
    currentCategoryId: null,
    currentNoteId: null,
    saving: false
};

function fileDownloadUrl(noteId, fileId) {
    return `${noteUrl(noteId)}/files/${encodeURIComponent(fileId)}`;
}

function text(en, zh, vars) {
    if (window.HKU && typeof window.HKU.text === 'function') {
        return window.HKU.text(en, zh, vars);
    }
    let value = en;
    Object.keys(vars || {}).forEach((key) => {
        value = value.replace(new RegExp(`\\{${key}\\}`, 'g'), String(vars[key]));
    });
    return value;
}

function currentRole() {
    return (localStorage.getItem('hku_role') || '').trim();
}

function isTeacher() {
    // In no-auth local mode role may be empty → allow writing (single-user dev).
    const r = currentRole();
    return r === 'teacher' || r === '';
}

function authHeaders() {
    const h = {};
    const access =
        (window.HKUAuth && typeof window.HKUAuth.token === 'function' && window.HKUAuth.token()) ||
        (localStorage.getItem('HKU_LOGIN_TOKEN') || '').trim() ||
        (localStorage.getItem('RAG_ACCESS_TOKEN') || '').trim();
    if (access) {
        h.Authorization = `Bearer ${access}`;
    }
    const sec = (localStorage.getItem(CONFIG.SESSION_SECRET_KEY) || '').trim();
    if (sec) {
        h['X-Session-Secret'] = sec;
    }
    return h;
}

function getSessionId() {
    return (localStorage.getItem(CONFIG.SESSION_ID_KEY) || '').trim();
}

function showToast(el, message, isError) {
    const toast = el || document.getElementById('toast');
    if (!toast) return;
    toast.querySelector('.toast-message').textContent = message;
    toast.classList.toggle('error', !!isError);
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 3200);
}

async function getErrorMessage(response) {
    try {
        const data = await response.clone().json();
        const detail = data && data.detail;
        if (typeof detail === 'string' && detail.trim()) return detail;
        if (Array.isArray(detail) && detail[0] && detail[0].msg) return String(detail[0].msg);
    } catch (_) {
        /* ignore */
    }
    return text('Request failed ({status}).', '请求失败（状态码：{status}）。', {
        status: response.status
    });
}

async function ensureSession() {
    const sid = getSessionId();
    const sec = (localStorage.getItem(CONFIG.SESSION_SECRET_KEY) || '').trim();
    if (sid && sec) {
        const check = await fetch(
            `${CONFIG.API_BASE}${CONFIG.API_SESSIONS}/${encodeURIComponent(sid)}/files`,
            { headers: authHeaders() }
        );
        if (check.ok) return;
    }
    const res = await fetch(`${CONFIG.API_BASE}${CONFIG.API_SESSIONS}`, {
        method: 'POST',
        headers: authHeaders()
    });
    if (!res.ok) {
        throw new Error(await getErrorMessage(res));
    }
    const data = await res.json();
    localStorage.setItem(CONFIG.SESSION_ID_KEY, data.session_id);
    localStorage.setItem(CONFIG.SESSION_SECRET_KEY, data.session_secret);
}

function sessionKbPrefix() {
    return `${CONFIG.API_BASE}${CONFIG.API_SESSIONS}/${encodeURIComponent(getSessionId())}/kb`;
}

function kbBase(catId) {
    return `${sessionKbPrefix()}/categories/${encodeURIComponent(catId)}`;
}

function noteUrl(noteId) {
    return `${sessionKbPrefix()}/notes/${encodeURIComponent(noteId)}`;
}

async function loadCategories() {
    await ensureSession();
    const res = await fetch(`${sessionKbPrefix()}/categories`, { headers: authHeaders() });
    if (!res.ok) {
        throw new Error(await getErrorMessage(res));
    }
    state.categories = await res.json();
    renderCategoryList();
}

function renderCategoryList() {
    const sel = document.getElementById('categorySelect');
    if (!sel) return;
    sel.innerHTML = '';
    if (!state.categories.length) {
        const empty = document.createElement('option');
        empty.value = '';
        empty.textContent = text('No categories', '暂无类别');
        sel.appendChild(empty);
        state.currentCategoryId = null;
        return;
    }
    state.categories.forEach((c) => {
        const opt = document.createElement('option');
        opt.value = c.id;
        opt.textContent = c.name;
        sel.appendChild(opt);
    });
    if (!state.currentCategoryId && state.categories.length) {
        state.currentCategoryId = state.categories[0].id;
        sel.value = state.currentCategoryId;
    } else if (state.currentCategoryId) {
        sel.value = state.currentCategoryId;
    }
}

async function loadNotes() {
    if (!state.currentCategoryId) {
        state.notes = [];
        renderNoteList();
        return;
    }
    const res = await fetch(`${kbBase(state.currentCategoryId)}/notes`, { headers: authHeaders() });
    if (!res.ok) {
        throw new Error(await getErrorMessage(res));
    }
    state.notes = await res.json();
    renderNoteList();
}

function renderNoteList() {
    const ul = document.getElementById('noteList');
    if (!ul) return;
    ul.innerHTML = '';
    if (!state.notes.length) {
        const empty = document.createElement('li');
        empty.className = 'kb-note-item';
        empty.textContent = state.currentCategoryId
            ? text('No notes in this category.', '此类别中暂无笔记。')
            : text('Select or create a category.', '请选择或新建类别。');
        ul.appendChild(empty);
        return;
    }
    state.notes.forEach((n) => {
        const li = document.createElement('li');
        li.className = 'kb-note-item' + (n.id === state.currentNoteId ? ' active' : '');
        li.textContent = n.title || text('Untitled', '无标题');
        li.dataset.id = n.id;
        li.addEventListener('click', () => selectNote(n.id));
        ul.appendChild(li);
    });
}

async function downloadAttach(noteId, fileMeta) {
    if (!noteId || !fileMeta || !fileMeta.id) {
        showToast(
            document.getElementById('toast'),
            text('No file selected.', '未选择文件。'),
            true
        );
        return;
    }
    try {
        await ensureSession();
        const res = await fetch(fileDownloadUrl(noteId, fileMeta.id), {
            headers: authHeaders()
        });
        if (!res.ok) {
            throw new Error(await getErrorMessage(res));
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = fileMeta.original_name || 'download';
        a.rel = 'noopener';
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 2000);
        showToast(document.getElementById('toast'), text('Download started.', '开始下载。'));
    } catch (e) {
        showToast(
            document.getElementById('toast'),
            (e && e.message) || text('Download failed.', '下载失败。'),
            true
        );
    }
}

async function selectNote(noteId) {
    state.currentNoteId = noteId;
    renderNoteList();
    const res = await fetch(noteUrl(noteId), { headers: authHeaders() });
    if (!res.ok) {
        showToast(document.getElementById('toast'), await getErrorMessage(res), true);
        return;
    }
    const n = await res.json();
    document.getElementById('noteTitle').value = n.title || '';
    await loadAttachments(noteId);
}

async function loadAttachments(noteId) {
    const res = await fetch(`${noteUrl(noteId)}/files`, { headers: authHeaders() });
    if (!res.ok) {
        showToast(document.getElementById('toast'), await getErrorMessage(res), true);
        return;
    }
    const files = await res.json();
    if (state.currentNoteId !== noteId) return;
    state.attachments = Array.isArray(files) ? files : [];
    state.attachmentsNoteId = noteId;
    renderAttachments();
}

function renderAttachments() {
    const box = document.getElementById('attachList');
    if (!box) return;
    box.innerHTML = '';
    if (!state.currentNoteId || state.attachmentsNoteId !== state.currentNoteId) return;
    if (!state.attachments.length) {
        const empty = document.createElement('div');
        empty.className = 'kb-attach-empty';
        empty.textContent = text(
            'No attachments yet. Teachers can upload a file.',
            '暂无附件。教师可上传文件。'
        );
        box.appendChild(empty);
        return;
    }
    state.attachments.forEach((f) => {
        const row = document.createElement('div');
        row.className = 'kb-attach-row';
        const name = document.createElement('span');
        name.className = 'kb-attach-name';
        name.textContent = f.original_name || f.id;
        row.appendChild(name);
        const actions = document.createElement('div');
        actions.className = 'kb-attach-actions';
        const dl = document.createElement('button');
        dl.type = 'button';
        dl.className = 'kb-attach-btn';
        dl.textContent = text('Download', '下载');
        dl.addEventListener('click', (ev) => {
            ev.preventDefault();
            ev.stopPropagation();
            downloadAttach(state.currentNoteId, f);
        });
        actions.appendChild(dl);
        if (isTeacher()) {
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'kb-attach-btn kb-attach-btn-danger';
            remove.textContent = text('Remove', '移除');
            remove.setAttribute(
                'aria-label',
                text('Remove attachment {name}', '移除附件 {name}', {
                    name: f.original_name || ''
                })
            );
            remove.addEventListener('click', (ev) => {
                ev.preventDefault();
                ev.stopPropagation();
                deleteAttach(state.currentNoteId, f.id);
            });
            actions.appendChild(remove);
        }
        row.appendChild(actions);
        box.appendChild(row);
    });
}

async function deleteAttach(noteId, fid) {
    if (!noteId || !fid) return;
    if (
        !window.confirm(
            text(
                'Remove this attachment from the note?',
                '从该笔记移除此附件？'
            )
        )
    ) {
        return;
    }
    try {
        await ensureSession();
        const res = await fetch(`${noteUrl(noteId)}/files/${encodeURIComponent(fid)}`, {
            method: 'DELETE',
            headers: authHeaders()
        });
        if (!res.ok) {
            throw new Error(await getErrorMessage(res));
        }
        await loadAttachments(noteId);
        showToast(document.getElementById('toast'), text('Attachment removed.', '附件已移除。'));
    } catch (e) {
        showToast(
            document.getElementById('toast'),
            (e && e.message) || text('Could not remove the attachment.', '无法移除附件。'),
            true
        );
    }
}

async function saveCurrentNote() {
    if (!state.currentNoteId || state.saving) return;
    state.saving = true;
    const title = document.getElementById('noteTitle').value.trim() || text('Untitled', '无标题');
    try {
        const res = await fetch(noteUrl(state.currentNoteId), {
            method: 'PATCH',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ title })
        });
        if (!res.ok) {
            throw new Error(await getErrorMessage(res));
        }
        showToast(document.getElementById('toast'), text('Saved.', '已保存。'));
        await loadNotes();
    } catch (e) {
        showToast(document.getElementById('toast'), text('Save failed.', '保存失败。'), true);
    } finally {
        state.saving = false;
    }
}

async function deleteCurrentNote() {
    if (!state.currentNoteId || !isTeacher()) return;
    if (
        !window.confirm(
            text(
                'Delete this note and all its attachments? This cannot be undone.',
                '删除此笔记及其全部附件？此操作无法撤销。'
            )
        )
    ) {
        return;
    }
    const nid = state.currentNoteId;
    const res = await fetch(noteUrl(nid), {
        method: 'DELETE',
        headers: authHeaders()
    });
    if (!res.ok) {
        showToast(document.getElementById('toast'), await getErrorMessage(res), true);
        return;
    }
    state.currentNoteId = null;
    state.attachments = [];
    state.attachmentsNoteId = null;
    document.getElementById('noteTitle').value = '';
    const box = document.getElementById('attachList');
    if (box) box.innerHTML = '';
    await loadNotes();
    showToast(document.getElementById('toast'), text('Note deleted.', '笔记已删除。'));
}

function isDefaultNoteTitle(title) {
    const t = String(title || '').trim().toLowerCase();
    return !t || t === 'new note' || t === '新笔记' || t === 'untitled' || t === '无标题';
}

async function createCategory() {
    const name = prompt(text('New category name', '新类别名称'));
    if (!name || !name.trim()) return;
    await ensureSession();
    const res = await fetch(`${sessionKbPrefix()}/categories`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), sort_order: state.categories.length })
    });
    if (!res.ok) {
        showToast(document.getElementById('toast'), await getErrorMessage(res), true);
        return;
    }
    await loadCategories();
    showToast(document.getElementById('toast'), text('Category created.', '类别已创建。'));
}

async function createNote() {
    if (!state.currentCategoryId) {
        showToast(document.getElementById('toast'), text('Select a category first.', '请先选择类别。'), true);
        return;
    }
    await ensureSession();
    const res = await fetch(`${kbBase(state.currentCategoryId)}/notes`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: text('New note', '新笔记'), body_markdown: '' })
    });
    if (!res.ok) {
        showToast(document.getElementById('toast'), await getErrorMessage(res), true);
        return;
    }
    const row = await res.json();
    await loadNotes();
    await selectNote(row.id);
    showToast(document.getElementById('toast'), text('Note created.', '笔记已创建。'));
}

async function uploadAttach(fileInput) {
    if (!state.currentNoteId || !fileInput.files || !fileInput.files[0]) return;
    const file = fileInput.files[0];
    try {
        await ensureSession();
        const fd = new FormData();
        fd.append('file', file);
        const res = await fetch(`${noteUrl(state.currentNoteId)}/files`, {
            method: 'POST',
            headers: authHeaders(),
            body: fd
        });
        fileInput.value = '';
        if (!res.ok) {
            throw new Error(await getErrorMessage(res));
        }
        const titleEl = document.getElementById('noteTitle');
        if (titleEl && isDefaultNoteTitle(titleEl.value)) {
            const stem = String(file.name || '').replace(/\.[^.]+$/, '').trim();
            if (stem) {
                titleEl.value = stem;
                try {
                    await fetch(noteUrl(state.currentNoteId), {
                        method: 'PATCH',
                        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
                        body: JSON.stringify({ title: stem })
                    });
                    await loadNotes();
                } catch (_) {
                    /* ignore title sync failure */
                }
            }
        }
        await loadAttachments(state.currentNoteId);
        showToast(document.getElementById('toast'), text('Uploaded.', '上传成功。'));
    } catch (e) {
        fileInput.value = '';
        showToast(
            document.getElementById('toast'),
            (e && e.message) || text('Could not upload the attachment.', '无法上传附件。'),
            true
        );
    }
}

function applyRoleGating() {
    // Subtitle reflects whether the KB is writable for this role.
    const sub = document.querySelector('.workspace-subtitle');
    if (sub) {
        const en = isTeacher()
            ? 'Manage notes, attachments, and class exercises. These are included in retrieval.'
            : 'Course materials shared by your teacher (read-only). These are included in retrieval.';
        const zh = isTeacher()
            ? '管理笔记、附件和课堂练习；这些内容将用于检索。'
            : '教师共享的课程资料（只读），将用于检索。';
        sub.setAttribute('data-en', en);
        sub.setAttribute('data-zh', zh);
        sub.textContent = text(en, zh);
    }
    const teacherBar = document.getElementById('exercisesTeacherBar');
    const hint = document.getElementById('exercisesHint');
    if (teacherBar) teacherBar.style.display = isTeacher() ? 'flex' : 'none';
    if (hint) {
        hint.textContent = isTeacher()
            ? text(
                'Upload a question bank (CSV/Excel), publish it for students, or generate one from a Student questions note.',
                '上传题库（CSV/Excel）并发布给学生，或根据“学生问题”笔记生成题库。'
            )
            : text('Published practice quizzes from your teacher.', '教师发布的练习测验。');
    }
    if (isTeacher()) return;
    // Student: read-only knowledge base — hide all write controls.
    const hideIds = [
        'btnNewCategory',
        'btnNewNote',
        'btnSaveNote',
        'btnDeleteNote',
        'btnPickFile'
    ];
    hideIds.forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    });
    const noteFile = document.getElementById('noteFile');
    if (noteFile) noteFile.disabled = true;
    const titleEl = document.getElementById('noteTitle');
    if (titleEl) titleEl.setAttribute('readonly', 'readonly');
    const toolbar = document.querySelector('.kb-attach-toolbar');
    if (toolbar) {
        const pick = document.getElementById('btnPickFile');
        if (pick) pick.style.display = 'none';
    }
}

function exercisesApi(path) {
    return `${CONFIG.API_BASE}/api/v1/kb/exercises${path || ''}`;
}

function quizOpenUrl(quizId) {
    return `quiz.html?quiz_id=${encodeURIComponent(quizId)}`;
}

async function loadExercises() {
    const res = await fetch(exercisesApi(''), { headers: authHeaders() });
    if (!res.ok) {
        showToast(document.getElementById('toast'), await getErrorMessage(res), true);
        return;
    }
    state.exercises = await res.json();
    renderExercises();
}

function renderExercises() {
    const list = document.getElementById('exerciseList');
    if (!list) return;
    list.innerHTML = '';
    if (!state.exercises.length) {
        const empty = document.createElement('li');
        empty.className = 'kb-exercise-empty';
        empty.textContent = isTeacher()
            ? text(
                'No class exercises yet. Upload a CSV/Excel bank or generate one from a note.',
                '暂无课堂练习。请上传 CSV/Excel 题库或从笔记生成。'
            )
            : text('No published exercises yet.', '暂无已发布的练习。');
        list.appendChild(empty);
        return;
    }
    state.exercises.forEach((ex) => {
        const li = document.createElement('li');
        li.className = 'kb-exercise-row';
        const status = ex.status === 'published'
            ? text('Published', '已发布')
            : text('Unpublished', '未发布');
        const meta = text('{count} questions · {status}', '{count} 题 · {status}', {
            count: ex.item_count || 0,
            status
        });
        const title = document.createElement('div');
        title.className = 'kb-exercise-meta';
        const strong = document.createElement('strong');
        strong.textContent = ex.title || text('Untitled', '无标题');
        const metaText = document.createElement('span');
        metaText.textContent = meta;
        title.appendChild(strong);
        title.appendChild(metaText);
        const actions = document.createElement('div');
        actions.className = 'kb-exercise-actions';
        const openBtn = document.createElement('a');
        openBtn.className = 'chat-toolbar-btn';
        openBtn.href = quizOpenUrl(ex.quiz_id);
        openBtn.textContent = isTeacher() ? text('Open', '打开') : text('Start', '开始');
        actions.appendChild(openBtn);
        if (isTeacher()) {
            const copyBtn = document.createElement('button');
            copyBtn.type = 'button';
            copyBtn.className = 'chat-toolbar-btn';
            copyBtn.textContent = text('Copy link', '复制链接');
            copyBtn.addEventListener('click', async () => {
                const url = new URL(quizOpenUrl(ex.quiz_id), window.location.href).href;
                try {
                    await navigator.clipboard.writeText(url);
                    showToast(document.getElementById('toast'), text('Link copied.', '链接已复制。'));
                } catch (_) {
                    showToast(document.getElementById('toast'), url);
                }
            });
            const toggleBtn = document.createElement('button');
            toggleBtn.type = 'button';
            toggleBtn.className = 'chat-toolbar-btn';
            toggleBtn.textContent = ex.status === 'published'
                ? text('Unpublish', '取消发布')
                : text('Publish', '发布');
            toggleBtn.addEventListener('click', () => patchExercise(ex.id, {
                status: ex.status === 'published' ? 'unpublished' : 'published'
            }));
            const delBtn = document.createElement('button');
            delBtn.type = 'button';
            delBtn.className = 'chat-toolbar-btn';
            delBtn.textContent = text('Delete', '删除');
            delBtn.addEventListener('click', () => deleteExercise(ex.id));
            actions.appendChild(copyBtn);
            actions.appendChild(toggleBtn);
            actions.appendChild(delBtn);
        }
        li.appendChild(title);
        li.appendChild(actions);
        list.appendChild(li);
    });
}

async function patchExercise(id, body) {
    const res = await fetch(exercisesApi(`/${encodeURIComponent(id)}`), {
        method: 'PATCH',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
    if (!res.ok) {
        showToast(document.getElementById('toast'), await getErrorMessage(res), true);
        return;
    }
    await loadExercises();
    showToast(document.getElementById('toast'), text('Updated.', '更新成功。'));
}

async function deleteExercise(id) {
    if (!confirm(text('Delete this class exercise?', '确定删除这项课堂练习吗？'))) return;
    const res = await fetch(exercisesApi(`/${encodeURIComponent(id)}`), {
        method: 'DELETE',
        headers: authHeaders()
    });
    if (!res.ok) {
        showToast(document.getElementById('toast'), await getErrorMessage(res), true);
        return;
    }
    await loadExercises();
    showToast(document.getElementById('toast'), text('Deleted.', '已删除。'));
}

async function uploadExercise(file) {
    const title = (document.getElementById('exerciseTitle')?.value || '').trim() || file.name;
    const fd = new FormData();
    fd.append('file', file);
    fd.append('title', title);
    fd.append('status', 'published');
    const res = await fetch(exercisesApi('/import'), {
        method: 'POST',
        headers: authHeaders(),
        body: fd
    });
    if (!res.ok) {
        showToast(document.getElementById('toast'), await getErrorMessage(res), true);
        return;
    }
    document.getElementById('exerciseTitle').value = '';
    await loadExercises();
    showToast(document.getElementById('toast'), text('Exercise published.', '练习已发布。'));
}

async function downloadTemplate(kind) {
    const path = kind === 'xlsx' ? '/template.xlsx' : '/template.csv';
    const res = await fetch(exercisesApi(path), { headers: authHeaders() });
    if (!res.ok) {
        showToast(document.getElementById('toast'), await getErrorMessage(res), true);
        return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = kind === 'xlsx' ? 'class-exercise-template.xlsx' : 'class-exercise-template.csv';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

async function aiFromCurrentNote() {
    if (!state.currentNoteId) {
        showToast(
            document.getElementById('toast'),
            text('Open a Student questions note first.', '请先打开一篇“学生问题”笔记。'),
            true
        );
        return;
    }
    const n = prompt(text('How many questions should be generated?', '要生成多少道题？'), '5');
    if (n == null) return;
    const count = Math.max(1, Math.min(20, parseInt(n, 10) || 5));
    const res = await fetch(exercisesApi('/generate-from-note'), {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({
            note_id: state.currentNoteId,
            n: count,
            publish: true,
            title: (document.getElementById('noteTitle')?.value || '').trim() || undefined
        })
    });
    if (!res.ok) {
        showToast(document.getElementById('toast'), await getErrorMessage(res), true);
        return;
    }
    const data = await res.json();
    await loadExercises();
    showToast(document.getElementById('toast'), text(
        'Generated and published {count} questions.',
        '已生成并发布 {count} 道题。',
        { count: data.item_count || count }
    ));
    // Also offer bank downloads
    if (data.items && data.items.length) {
        for (const fmt of ['csv', 'xlsx']) {
            const er = await fetch(exercisesApi('/export-bank'), {
                method: 'POST',
                headers: { ...authHeaders(), 'Content-Type': 'application/json' },
                body: JSON.stringify({ format: fmt, items: data.items })
            });
            if (!er.ok) continue;
            const blob = await er.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `quiz-bank.${fmt === 'xlsx' ? 'xlsx' : 'csv'}`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
        }
    }
}

function init() {
    const toast = document.getElementById('toast');
    applyRoleGating();
    document.getElementById('btnNewCategory')?.addEventListener('click', () => {
        createCategory().catch(() => showToast(
            toast,
            text('Could not create the category.', '无法创建类别。'),
            true
        ));
    });
    document.getElementById('btnNewNote')?.addEventListener('click', () => {
        createNote().catch(() => showToast(
            toast,
            text('Could not create the note.', '无法创建笔记。'),
            true
        ));
    });
    document.getElementById('btnSaveNote')?.addEventListener('click', () => saveCurrentNote());
    document.getElementById('btnDeleteNote')?.addEventListener('click', () => {
        deleteCurrentNote().catch(() => showToast(
            toast,
            text('Could not delete the note.', '无法删除笔记。'),
            true
        ));
    });
    document.getElementById('categorySelect')?.addEventListener('change', (e) => {
        state.currentCategoryId = e.target.value;
        state.currentNoteId = null;
        state.attachments = [];
        state.attachmentsNoteId = null;
        document.getElementById('noteTitle').value = '';
        const attachList = document.getElementById('attachList');
        if (attachList) attachList.innerHTML = '';
        loadNotes().catch(() => showToast(
            toast,
            text('Could not load notes.', '无法加载笔记。'),
            true
        ));
    });
    document.getElementById('noteFile')?.addEventListener('change', (e) => {
        uploadAttach(e.target).catch(() => showToast(
            toast,
            text('Could not upload the attachment.', '无法上传附件。'),
            true
        ));
    });
    document.getElementById('btnExercisePick')?.addEventListener('click', () => document.getElementById('exerciseFile')?.click());
    document.getElementById('exerciseFile')?.addEventListener('change', (e) => {
        const f = e.target.files && e.target.files[0];
        e.target.value = '';
        if (f) uploadExercise(f).catch(() => showToast(
            toast,
            text('Could not upload the exercise.', '无法上传练习。'),
            true
        ));
    });
    document.getElementById('btnTplCsv')?.addEventListener('click', (e) => {
        e.preventDefault();
        downloadTemplate('csv').catch(() => showToast(
            toast,
            text('Could not download the CSV template.', '无法下载 CSV 模板。'),
            true
        ));
    });
    document.getElementById('btnTplXlsx')?.addEventListener('click', (e) => {
        e.preventDefault();
        downloadTemplate('xlsx').catch(() => showToast(
            toast,
            text('Could not download the Excel template.', '无法下载 Excel 模板。'),
            true
        ));
    });
    document.getElementById('btnAiFromNote')?.addEventListener('click', () => {
        aiFromCurrentNote().catch(() => showToast(
            toast,
            text('Could not generate the exercise.', '无法生成练习。'),
            true
        ));
    });

    loadCategories()
        .then(() => loadNotes())
        .then(() => loadExercises())
        .catch(() => showToast(
            toast,
            text('Could not load the knowledge base.', '无法加载知识库。'),
            true
        ));
}

document.addEventListener('hku:langchange', () => {
    applyRoleGating();
    renderCategoryList();
    renderNoteList();
    renderAttachments();
    renderExercises();
});

document.addEventListener('DOMContentLoaded', init);
