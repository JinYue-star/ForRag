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
        row.innerHTML = `<span>${f.original_name}</span><button type="button" data-fid="${f.id}">Remove</button>`;
        row.querySelector('button').addEventListener('click', () => deleteAttach(noteId, f.id));
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

function init() {
    const toast = document.getElementById('toast');
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

    loadCategories()
        .then(() => loadNotes())
        .catch((e) => showToast(toast, e.message || 'Failed to load.', true));
}

document.addEventListener('DOMContentLoaded', init);
