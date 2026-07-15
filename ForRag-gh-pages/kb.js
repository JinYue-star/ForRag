/**
 * Knowledge base: categories, Markdown notes, attachments (session-scoped).
 */
const isGitHubPages = window.location.hostname.endsWith('github.io');

function resolveDefaultApiBase() {
    if (isGitHubPages) {
        return 'https://kitty-collapse-ivory-vol.trycloudflare.com';
    }
    if (window.location.protocol === 'file:') {
        return 'http://127.0.0.1:8000';
    }
    const port = window.location.port;
    const devFrontendPorts = new Set(['5500', '5501', '3000', '5173', '4173', '8080']);
    if (port && devFrontendPorts.has(port)) {
        return `${window.location.protocol}//${window.location.hostname}:8000`;
    }
    return window.location.origin;
}

const DEFAULT_API_BASE = resolveDefaultApiBase();
const storedApiBase = localStorage.getItem('RAG_API_BASE');
const staleApiBases = new Set([
    'http://35.77.38.184:8000',
    'http://35.77.38.184:8001',
    'https://kitty-collapse-ivory-vol.trycloudflare.com'
]);
const runtimeApiBase =
    window.__API_BASE__ ||
    (storedApiBase && !staleApiBases.has(storedApiBase) ? storedApiBase : DEFAULT_API_BASE);
const CONFIG = {
    API_BASE: String(runtimeApiBase).replace(/\/+$/, ''),
    API_SESSIONS: '/api/v1/sessions',
    SESSION_ID_KEY: 'RAG_SESSION_ID',
    SESSION_SECRET_KEY: 'RAG_SESSION_SECRET'
};

const state = {
    categories: [],
    notes: [],
    currentCategoryId: null,
    currentNoteId: null,
    saving: false
};

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
    const access = (localStorage.getItem('RAG_ACCESS_TOKEN') || '').trim();
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
        const data = await response.json();
        if (data && typeof data.detail === 'string' && data.detail.trim()) {
            return data.detail;
        }
        if (data && Array.isArray(data.detail)) {
            return data.detail.map((d) => (d && d.msg) || JSON.stringify(d)).join('; ');
        }
    } catch (_) {
        /* ignore */
    }
    return `Request failed (${response.status})`;
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
    const sel = document.getElementById('categorySelect');
    if (!sel) return;
    sel.innerHTML = '';
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
    state.notes.forEach((n) => {
        const li = document.createElement('li');
        li.className = 'kb-note-item' + (n.id === state.currentNoteId ? ' active' : '');
        li.textContent = n.title || 'Untitled';
        li.dataset.id = n.id;
        li.addEventListener('click', () => selectNote(n.id));
        ul.appendChild(li);
    });
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
    document.getElementById('noteBody').value = n.body_markdown || '';
    await loadAttachments(noteId);
}

async function loadAttachments(noteId) {
    const res = await fetch(`${noteUrl(noteId)}/files`, { headers: authHeaders() });
    if (!res.ok) return;
    const files = await res.json();
    const box = document.getElementById('attachList');
    if (!box) return;
    box.innerHTML = '';
    files.forEach((f) => {
        const row = document.createElement('div');
        row.className = 'kb-attach-row';
        if (isTeacher()) {
            row.innerHTML = `<span>${f.original_name}</span><button type="button" data-fid="${f.id}">Remove</button>`;
            row.querySelector('button').addEventListener('click', () => deleteAttach(noteId, f.id));
        } else {
            row.innerHTML = `<span>${f.original_name}</span>`;
        }
        box.appendChild(row);
    });
}

async function deleteAttach(noteId, fid) {
    const res = await fetch(`${noteUrl(noteId)}/files/${encodeURIComponent(fid)}`, {
        method: 'DELETE',
        headers: authHeaders()
    });
    if (!res.ok) {
        showToast(document.getElementById('toast'), await getErrorMessage(res), true);
        return;
    }
    await loadAttachments(noteId);
    showToast(document.getElementById('toast'), 'Attachment removed.');
}

async function saveCurrentNote() {
    if (!state.currentNoteId || state.saving) return;
    state.saving = true;
    const title = document.getElementById('noteTitle').value.trim() || 'Untitled';
    const body = document.getElementById('noteBody').value;
    try {
        const res = await fetch(noteUrl(state.currentNoteId), {
            method: 'PATCH',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, body_markdown: body })
        });
        if (!res.ok) {
            throw new Error(await getErrorMessage(res));
        }
        showToast(document.getElementById('toast'), 'Saved.');
        await loadNotes();
    } catch (e) {
        showToast(document.getElementById('toast'), e.message || 'Save failed.', true);
    } finally {
        state.saving = false;
    }
}

async function createCategory() {
    const name = prompt('New category name');
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
    showToast(document.getElementById('toast'), 'Category created.');
}

async function createNote() {
    if (!state.currentCategoryId) {
        showToast(document.getElementById('toast'), 'Select a category first.', true);
        return;
    }
    await ensureSession();
    const res = await fetch(`${kbBase(state.currentCategoryId)}/notes`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: 'New note', body_markdown: '' })
    });
    if (!res.ok) {
        showToast(document.getElementById('toast'), await getErrorMessage(res), true);
        return;
    }
    const row = await res.json();
    await loadNotes();
    await selectNote(row.id);
    showToast(document.getElementById('toast'), 'Note created.');
}

async function uploadAttach(fileInput) {
    if (!state.currentNoteId || !fileInput.files || !fileInput.files[0]) return;
    const fd = new FormData();
    fd.append('file', fileInput.files[0]);
    const res = await fetch(`${noteUrl(state.currentNoteId)}/files`, {
        method: 'POST',
        headers: authHeaders(),
        body: fd
    });
    fileInput.value = '';
    if (!res.ok) {
        showToast(document.getElementById('toast'), await getErrorMessage(res), true);
        return;
    }
    await loadAttachments(state.currentNoteId);
    showToast(document.getElementById('toast'), 'Uploaded.');
}

function applyRoleGating() {
    // Subtitle reflects whether the KB is writable for this role.
    const sub = document.querySelector('.workspace-subtitle');
    if (sub) {
        sub.setAttribute('data-i18n', isTeacher() ? 'kb.subtitle.teacher' : 'kb.subtitle.student');
        if (window.HKU && window.HKU.apply) window.HKU.apply(document);
    }
    const teacherBar = document.getElementById('exercisesTeacherBar');
    const hint = document.getElementById('exercisesHint');
    if (teacherBar) teacherBar.style.display = isTeacher() ? 'flex' : 'none';
    if (hint) {
        hint.textContent = isTeacher()
            ? 'Upload a question bank (CSV/Excel), publish for students, or generate from a Student questions note.'
            : 'Published practice quizzes from your teacher.';
    }
    if (isTeacher()) return;
    // Student: read-only knowledge base — hide all write controls.
    const hideIds = ['btnNewCategory', 'btnNewNote', 'btnSaveNote', 'btnPickFile'];
    hideIds.forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    });
    const noteFile = document.getElementById('noteFile');
    if (noteFile) noteFile.disabled = true;
    ['noteTitle', 'noteBody'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.setAttribute('readonly', 'readonly');
    });
    const attachLabel = document.querySelector('.kb-attach-label');
    if (attachLabel) attachLabel.style.display = 'none';
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
    const rows = await res.json();
    const list = document.getElementById('exerciseList');
    if (!list) return;
    list.innerHTML = '';
    if (!rows.length) {
        const empty = document.createElement('li');
        empty.className = 'kb-exercise-empty';
        empty.textContent = isTeacher()
            ? 'No class exercises yet. Upload a CSV/Excel bank or generate from a note.'
            : 'No published exercises yet.';
        list.appendChild(empty);
        return;
    }
    rows.forEach((ex) => {
        const li = document.createElement('li');
        li.className = 'kb-exercise-row';
        const status = ex.status === 'published' ? 'Published' : 'Unpublished';
        const meta = `${ex.item_count || 0} Q · ${status}`;
        const title = document.createElement('div');
        title.className = 'kb-exercise-meta';
        title.innerHTML = `<strong>${escapeHtml(ex.title || 'Untitled')}</strong><span>${escapeHtml(meta)}</span>`;
        const actions = document.createElement('div');
        actions.className = 'kb-exercise-actions';
        const openBtn = document.createElement('a');
        openBtn.className = 'chat-toolbar-btn';
        openBtn.href = quizOpenUrl(ex.quiz_id);
        openBtn.textContent = isTeacher() ? 'Open' : 'Start';
        actions.appendChild(openBtn);
        if (isTeacher()) {
            const copyBtn = document.createElement('button');
            copyBtn.type = 'button';
            copyBtn.className = 'chat-toolbar-btn';
            copyBtn.textContent = 'Copy link';
            copyBtn.addEventListener('click', async () => {
                const url = new URL(quizOpenUrl(ex.quiz_id), window.location.href).href;
                try {
                    await navigator.clipboard.writeText(url);
                    showToast(document.getElementById('toast'), 'Link copied.');
                } catch (_) {
                    showToast(document.getElementById('toast'), url);
                }
            });
            const toggleBtn = document.createElement('button');
            toggleBtn.type = 'button';
            toggleBtn.className = 'chat-toolbar-btn';
            toggleBtn.textContent = ex.status === 'published' ? 'Unpublish' : 'Publish';
            toggleBtn.addEventListener('click', () => patchExercise(ex.id, {
                status: ex.status === 'published' ? 'unpublished' : 'published'
            }));
            const delBtn = document.createElement('button');
            delBtn.type = 'button';
            delBtn.className = 'chat-toolbar-btn';
            delBtn.textContent = 'Delete';
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

function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = String(s == null ? '' : s);
    return d.innerHTML;
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
    showToast(document.getElementById('toast'), 'Updated.');
}

async function deleteExercise(id) {
    if (!confirm('Delete this class exercise?')) return;
    const res = await fetch(exercisesApi(`/${encodeURIComponent(id)}`), {
        method: 'DELETE',
        headers: authHeaders()
    });
    if (!res.ok) {
        showToast(document.getElementById('toast'), await getErrorMessage(res), true);
        return;
    }
    await loadExercises();
    showToast(document.getElementById('toast'), 'Deleted.');
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
    showToast(document.getElementById('toast'), 'Exercise published.');
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
        showToast(document.getElementById('toast'), 'Open a Student questions note first.', true);
        return;
    }
    const n = prompt('How many questions to generate?', '5');
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
    showToast(document.getElementById('toast'), `Generated ${data.item_count || count} questions and published.`);
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
    document.getElementById('btnNewCategory')?.addEventListener('click', () => createCategory().catch((e) => showToast(toast, e.message, true)));
    document.getElementById('btnNewNote')?.addEventListener('click', () => createNote().catch((e) => showToast(toast, e.message, true)));
    document.getElementById('btnSaveNote')?.addEventListener('click', () => saveCurrentNote());
    document.getElementById('categorySelect')?.addEventListener('change', (e) => {
        state.currentCategoryId = e.target.value;
        state.currentNoteId = null;
        document.getElementById('noteTitle').value = '';
        document.getElementById('noteBody').value = '';
        document.getElementById('attachList').innerHTML = '';
        loadNotes().catch((err) => showToast(toast, err.message, true));
    });
    document.getElementById('noteFile')?.addEventListener('change', (e) => {
        uploadAttach(e.target).catch((err) => showToast(toast, err.message, true));
    });
    document.getElementById('btnExercisePick')?.addEventListener('click', () => document.getElementById('exerciseFile')?.click());
    document.getElementById('exerciseFile')?.addEventListener('change', (e) => {
        const f = e.target.files && e.target.files[0];
        e.target.value = '';
        if (f) uploadExercise(f).catch((err) => showToast(toast, err.message, true));
    });
    document.getElementById('btnTplCsv')?.addEventListener('click', (e) => {
        e.preventDefault();
        downloadTemplate('csv').catch((err) => showToast(toast, err.message, true));
    });
    document.getElementById('btnTplXlsx')?.addEventListener('click', (e) => {
        e.preventDefault();
        downloadTemplate('xlsx').catch((err) => showToast(toast, err.message, true));
    });
    document.getElementById('btnAiFromNote')?.addEventListener('click', () => {
        aiFromCurrentNote().catch((err) => showToast(toast, err.message, true));
    });

    loadCategories()
        .then(() => loadNotes())
        .then(() => loadExercises())
        .catch((e) => showToast(toast, e.message || 'Failed to load.', true));
}

document.addEventListener('DOMContentLoaded', init);
