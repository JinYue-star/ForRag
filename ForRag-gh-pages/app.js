/**
 * RAG chat UI: sidebar, chat history, async Q&A, quiz handoff.
 *
 * If the API requires an access token, run in the console:
 *   localStorage.setItem('RAG_ACCESS_TOKEN', '<same value as server RAG_ACCESS_TOKEN>')
 * then reload. If unset, requests omit Authorization (server must allow anonymous access).
 */

const isGitHubPages = window.location.hostname.endsWith('github.io');

/** Same origin as the API when served together; dev front-end ports default to localhost:8000. */
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
const normalizedApiBase = String(runtimeApiBase).replace(/\/+$/, '');

const CONFIG = {
    API_BASE: normalizedApiBase,
    API_HEALTH: '/health',
    API_SESSIONS: '/api/v1/sessions',
    SESSION_ID_KEY: 'RAG_SESSION_ID',
    SESSION_SECRET_KEY: 'RAG_SESSION_SECRET',
    LAST_QUIZ_KEY: 'RAG_LAST_QUIZ',
    CONVERSATIONS_KEY: 'RAG_CONVERSATIONS',
    FEEDBACK_KEY: 'RAG_MSG_FEEDBACK',
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
        'application/vnd.ms-powerpoint',
        'image/png',
        'image/jpeg',
        'image/webp',
        'image/gif',
        'image/bmp',
        'image/tiff'
    ],
    MAX_FILES: 5,
    POLL_MS: 450
};

const state = {
    serverFiles: [],
    isLoading: false,
    isUploading: false,
    chatMutating: false,
    messages: [],
    historyMenuAnchorSid: null
};

const elements = {
    uploadZone: document.getElementById('uploadZone'),
    fileInput: document.getElementById('fileInput'),
    fileList: document.getElementById('fileList'),
    questionInput: document.getElementById('questionInput'),
    topKSelect: document.getElementById('topKSelect'),
    submitBtn: document.getElementById('submitBtn'),
    chatMessages: document.getElementById('chatMessages'),
    newChatBtn: document.getElementById('newChatBtn'),
    chatHistoryList: document.getElementById('chatHistoryList'),
    composerDock: document.getElementById('composerDock'),
    composerAttachBtn: document.getElementById('composerAttachBtn'),
    composerImageBtn: document.getElementById('composerImageBtn'),
    composerMicBtn: document.getElementById('composerMicBtn'),
    composerFileInput: document.getElementById('composerFileInput'),
    composerImageInput: document.getElementById('composerImageInput'),
    generateBar: document.getElementById('generateBar'),
    generateQuizBtn: document.getElementById('generateQuizBtn'),
    toast: document.getElementById('toast'),
    sessionFilesTrigger: document.getElementById('sessionFilesTrigger'),
    sessionFilesBadge: document.getElementById('sessionFilesBadge'),
    sessionFilesPopoverList: document.getElementById('sessionFilesPopoverList'),
    sessionFilesPreview: document.getElementById('sessionFilesPreview')
};

function showToast(message, isError = false, isSuccess = false) {
    const toast = elements.toast;
    const messageEl = toast.querySelector('.toast-message');
    messageEl.textContent = message;
    
    // Reset toast styles
    toast.className = 'toast';
    
    if (isError) {
        toast.classList.add('error');
    } else if (isSuccess) {
        toast.classList.add('success');
    }
    
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 3500);
}

function getSessionId() {
    return (localStorage.getItem(CONFIG.SESSION_ID_KEY) || '').trim();
}

function getSessionSecret() {
    return (localStorage.getItem(CONFIG.SESSION_SECRET_KEY) || '').trim();
}

function clearSession() {
    localStorage.removeItem(CONFIG.SESSION_ID_KEY);
    localStorage.removeItem(CONFIG.SESSION_SECRET_KEY);
    state.messages = [];
    renderChat();
}

function loadConversations() {
    try {
        const raw = localStorage.getItem(CONFIG.CONVERSATIONS_KEY);
        if (!raw) return [];
        const data = JSON.parse(raw);
        return Array.isArray(data) ? data : [];
    } catch (_) {
        return [];
    }
}

function saveConversations(list) {
    localStorage.setItem(CONFIG.CONVERSATIONS_KEY, JSON.stringify(list));
}

function sortConversations(list) {
    return [...list].sort((a, b) => {
        if (!!a.pinned !== !!b.pinned) return a.pinned ? -1 : 1;
        return (b.updated_at || 0) - (a.updated_at || 0);
    });
}

function migrateConversations() {
    const sid = getSessionId();
    const sec = getSessionSecret();
    if (!sid || !sec) return;
    let list = loadConversations();
    if (!list.some((c) => c.session_id === sid)) {
        list.push({
            session_id: sid,
            session_secret: sec,
            title: 'New chat',
            pinned: false,
            updated_at: Date.now()
        });
        saveConversations(list);
    }
}

function touchCurrentConversation() {
    const sid = getSessionId();
    if (!sid) return;
    const list = loadConversations();
    const i = list.findIndex((c) => c.session_id === sid);
    if (i < 0) return;
    list[i].updated_at = Date.now();
    const firstUser = state.messages.find((m) => m.role === 'user');
    const t = firstUser ? String(firstUser.content || '').trim() : '';
    const defaultChatTitles = new Set(['新对话', 'New chat']);
    if (t && (!list[i].title || defaultChatTitles.has(list[i].title))) {
        list[i].title = t.length > 36 ? `${t.slice(0, 36)}…` : t;
    }
    saveConversations(list);
    renderHistoryList();
}

function getFeedbackMap() {
    try {
        return JSON.parse(localStorage.getItem(CONFIG.FEEDBACK_KEY) || '{}');
    } catch (_) {
        return {};
    }
}

function setFeedback(messageId, val) {
    const m = getFeedbackMap();
    if (val == null) delete m[messageId];
    else m[messageId] = val;
    localStorage.setItem(CONFIG.FEEDBACK_KEY, JSON.stringify(m));
}

function renderHistoryList() {
    const el = elements.chatHistoryList;
    if (!el) return;
    const sid = getSessionId();
    const list = sortConversations(loadConversations());
    el.innerHTML = '';
    if (list.length === 0) {
        const p = document.createElement('p');
        p.className = 'chat-history-empty';
        p.textContent = 'No chats yet. Click New chat to begin.';
        el.appendChild(p);
        return;
    }
    list.forEach((c) => {
        const row = document.createElement('div');
        row.className = `chat-history-item${c.session_id === sid ? ' is-active' : ''}`;
        row.dataset.sessionId = c.session_id;
        row.setAttribute('role', 'listitem');

        const titleBtn = document.createElement('button');
        titleBtn.type = 'button';
        titleBtn.className = 'chat-history-title';
        titleBtn.title = c.title || 'Chat';
        titleBtn.textContent = c.title || 'New chat';

        const menuWrap = document.createElement('div');
        menuWrap.className = 'chat-history-menu-wrap';
        const menuBtn = document.createElement('button');
        menuBtn.type = 'button';
        menuBtn.className = 'chat-history-menu-btn';
        menuBtn.setAttribute('aria-label', 'Chat actions');
        menuBtn.dataset.sessionId = c.session_id;
        menuBtn.innerHTML =
            '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>';

        menuWrap.appendChild(menuBtn);
        row.appendChild(titleBtn);
        row.appendChild(menuWrap);
        el.appendChild(row);

        titleBtn.addEventListener('click', () => {
            switchConversation(c.session_id);
        });
        menuBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            openHistoryMenu(menuBtn, c);
        });
    });
}

function ensureHistoryMenuEl() {
    let el = document.getElementById('historyContextMenu');
    if (!el) {
        el = document.createElement('div');
        el.id = 'historyContextMenu';
        el.className = 'chat-history-floatmenu';
        el.hidden = true;
        document.body.appendChild(el);
    }
    return el;
}

function closeHistoryMenu() {
    const el = ensureHistoryMenuEl();
    el.hidden = true;
    el.innerHTML = '';
    state.historyMenuAnchorSid = null;
}

function openHistoryMenu(anchorBtn, conv) {
    const el = ensureHistoryMenuEl();
    state.historyMenuAnchorSid = conv.session_id;
    el.innerHTML = `
        <button type="button" data-action="pin">${conv.pinned ? 'Unpin' : 'Pin'}</button>
        <button type="button" data-action="rename">Rename</button>
        <button type="button" data-action="delete" class="is-danger">Delete</button>
    `;
    el.querySelectorAll('button').forEach((b) => {
        b.addEventListener('click', (e) => {
            e.stopPropagation();
            const action = b.dataset.action;
            closeHistoryMenu();
            if (action === 'pin') togglePinConversation(conv.session_id);
            else if (action === 'rename') renameConversation(conv.session_id);
            else if (action === 'delete') void deleteConversation(conv.session_id);
        });
    });

    const r = anchorBtn.getBoundingClientRect();
    const desiredTop = r.bottom + 6;
    const desiredLeft = Math.min(r.right - 160, window.innerWidth - 12 - 160);
    el.style.top = `${Math.min(desiredTop, window.innerHeight - 12)}px`;
    el.style.left = `${Math.max(12, desiredLeft)}px`;
    el.hidden = false;
}

function togglePinConversation(sessionId) {
    let list = loadConversations();
    const i = list.findIndex((x) => x.session_id === sessionId);
    if (i < 0) return;
    list[i].pinned = !list[i].pinned;
    saveConversations(list);
    renderHistoryList();
}

function renameConversation(sessionId) {
    const list = loadConversations();
    const entry = list.find((x) => x.session_id === sessionId);
    if (!entry) return;
    const n = window.prompt('Chat title', entry.title || '');
    if (n == null) return;
    const t = n.trim();
    if (!t) return;
    entry.title = t.length > 80 ? t.slice(0, 80) : t;
    saveConversations(list);
    renderHistoryList();
}

async function deleteConversation(sessionId) {
    if (!window.confirm('Delete this chat? Session uploads, messages, and related data will be removed. This cannot be undone.')) {
        return;
    }
    const list = loadConversations();
    const entry = list.find((x) => x.session_id === sessionId);
    if (!entry) return;
    const wasCurrent = getSessionId() === sessionId;
    try {
        const res = await fetch(
            `${CONFIG.API_BASE}${CONFIG.API_SESSIONS}/${encodeURIComponent(sessionId)}`,
            {
                method: 'DELETE',
                headers: {
                    ...authHeaders(),
                    'X-Session-Secret': entry.session_secret
                }
            }
        );
        if (!res.ok && res.status !== 404) {
            throw new Error(await getErrorMessage(res));
        }
    } catch (err) {
        console.error(err);
        showToast(err.message || 'Delete failed.', true);
        return;
    }
    const newList = list.filter((x) => x.session_id !== sessionId);
    saveConversations(newList);
    if (wasCurrent) {
        if (newList.length > 0) {
            await switchConversation(newList[0].session_id);
        } else {
            await createFreshSessionAndList();
        }
    }
    renderHistoryList();
    showToast('Chat deleted.', false, true);
}

function applyConversation(entry) {
    localStorage.setItem(CONFIG.SESSION_ID_KEY, entry.session_id);
    localStorage.setItem(CONFIG.SESSION_SECRET_KEY, entry.session_secret);
    state.messages = [];
    renderChat();
}

async function createFreshSessionAndList() {
    clearSession();
    await ensureSession();
    migrateConversations();
    await refreshServerFiles();
    await loadChatMessages();
    touchCurrentConversation();
    renderHistoryList();
}

async function switchConversation(sessionId) {
    if (sessionId === getSessionId()) return;
    const list = loadConversations();
    const entry = list.find((x) => x.session_id === sessionId);
    if (!entry) return;
    applyConversation(entry);
    await refreshServerFiles();
    await loadChatMessages();
    touchCurrentConversation();
    renderHistoryList();
    closeHistoryMenu();
}

async function startNewConversation() {
    if (state.isLoading || state.isUploading || state.chatMutating) {
        showToast('Please wait for the current action to finish.', true);
        return;
    }
    try {
        const res = await fetch(`${CONFIG.API_BASE}${CONFIG.API_SESSIONS}`, {
            method: 'POST',
            headers: authHeaders()
        });
        if (!res.ok) {
            throw new Error(await getErrorMessage(res));
        }
        const data = await res.json();
        const entry = {
            session_id: data.session_id,
            session_secret: data.session_secret,
            title: 'New chat',
            pinned: false,
            updated_at: Date.now()
        };
        let list = loadConversations();
        list.unshift(entry);
        saveConversations(list);
        applyConversation(entry);
        await refreshServerFiles();
        await loadChatMessages();
        renderHistoryList();
        showToast('New chat started.', false, true);
    } catch (err) {
        console.error(err);
        showToast(err.message || 'Could not create session.', true);
    }
}

function authHeaders() {
    const h = {};
    const access = (localStorage.getItem('RAG_ACCESS_TOKEN') || '').trim();
    if (access) {
        h['Authorization'] = `Bearer ${access}`;
    }
    const sec = getSessionSecret();
    if (sec) {
        h['X-Session-Secret'] = sec;
    }
    return h;
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
    try {
        const text = await response.text();
        if (text.trim()) {
            return text;
        }
    } catch (_) {
        /* ignore */
    }
    return `Request failed (${response.status})`;
}

async function ensureSession() {
    if (getSessionId() && getSessionSecret()) {
        const check = await fetch(
            `${CONFIG.API_BASE}${CONFIG.API_SESSIONS}/${encodeURIComponent(getSessionId())}/files`,
            { headers: authHeaders() }
        );
        if (check.ok) {
            return;
        }
        if (check.status === 401) {
            throw new Error('Invalid access token.');
        }
        clearSession();
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
    let convs = loadConversations();
    if (!convs.some((c) => c.session_id === data.session_id)) {
        convs.unshift({
            session_id: data.session_id,
            session_secret: data.session_secret,
            title: 'New chat',
            pinned: false,
            updated_at: Date.now()
        });
        saveConversations(convs);
    }
}

function normalizeSessionFilesPayload(data) {
    if (Array.isArray(data)) return data;
    if (data && typeof data === 'object' && Array.isArray(data.files)) return data.files;
    return [];
}

async function refreshServerFiles() {
    if (!getSessionId() || !getSessionSecret()) {
        state.serverFiles = [];
        updateFileList();
        return;
    }
    const res = await fetch(
        `${CONFIG.API_BASE}${CONFIG.API_SESSIONS}/${encodeURIComponent(getSessionId())}/files`,
        { headers: authHeaders() }
    );
    if (!res.ok) {
        if (res.status === 403 || res.status === 404) {
            clearSession();
        }
        state.serverFiles = [];
        updateFileList();
        return;
    }
    try {
        const data = await res.json();
        state.serverFiles = normalizeSessionFilesPayload(data);
    } catch (_) {
        state.serverFiles = [];
    }
    updateFileList();
}

async function loadChatMessages() {
    if (!getSessionId() || !getSessionSecret()) {
        state.messages = [];
        renderChat();
        return;
    }
    const res = await fetch(
        `${CONFIG.API_BASE}${CONFIG.API_SESSIONS}/${encodeURIComponent(getSessionId())}/messages`,
        { headers: authHeaders() }
    );
    if (!res.ok) {
        state.messages = [];
        renderChat();
        return;
    }
    state.messages = await res.json();
    renderChat();
    touchCurrentConversation();
}

async function deleteChatMessage(messageId) {
    if (!messageId || !getSessionId() || !getSessionSecret()) {
        return;
    }
    state.chatMutating = true;
    updateNewChatButton();
    updateGenerateButtonState();
    updateSubmitButton();
    try {
        const res = await fetch(
            `${CONFIG.API_BASE}${CONFIG.API_SESSIONS}/${encodeURIComponent(getSessionId())}/messages/${encodeURIComponent(messageId)}`,
            { method: 'DELETE', headers: authHeaders() }
        );
        if (!res.ok) {
            throw new Error(await getErrorMessage(res));
        }
        await loadChatMessages();
        showToast('Message deleted.', false, true);
    } catch (err) {
        console.error(err);
        showToast(err.message || 'Delete failed.', true);
    } finally {
        state.chatMutating = false;
        updateNewChatButton();
        updateGenerateButtonState();
        updateSubmitButton();
    }
}

async function clearAllChatMessages() {
    if (!getSessionId() || !getSessionSecret() || state.messages.length === 0) {
        return;
    }
    if (!window.confirm('Clear all messages in this chat? This cannot be undone.')) {
        return;
    }
    state.chatMutating = true;
    updateNewChatButton();
    updateGenerateButtonState();
    updateSubmitButton();
    try {
        const res = await fetch(
            `${CONFIG.API_BASE}${CONFIG.API_SESSIONS}/${encodeURIComponent(getSessionId())}/messages`,
            { method: 'DELETE', headers: authHeaders() }
        );
        if (!res.ok) {
            throw new Error(await getErrorMessage(res));
        }
        await loadChatMessages();
        showToast('Chat cleared.', false, true);
    } catch (err) {
        console.error(err);
        showToast(err.message || 'Could not clear chat.', true);
    } finally {
        state.chatMutating = false;
        updateNewChatButton();
        updateGenerateButtonState();
        updateSubmitButton();
    }
}

function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
}

function getFileExtension(filename) {
    return filename.split('.').pop().toLowerCase();
}

function getFileIcon(filename) {
    const ext = getFileExtension(filename);
    const icons = {
        pdf: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14,2 14,8 20,8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10,9 9,9 8,9"/></svg>',
        docx: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14,2 14,8 20,8"/><path d="M8 12h8"/><path d="M8 16h8"/><path d="M16 10v4"/></svg>',
        doc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14,2 14,8 20,8"/><path d="M8 12h8"/><path d="M8 16h8"/><path d="M16 10v4"/></svg>',
        txt: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14,2 14,8 20,8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><line x1="10" y1="9" x2="14" y2="9"/></svg>',
        md: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14,2 14,8 20,8"/><path d="M9 13h6"/><path d="M9 17h6"/><circle cx="12" cy="9" r="2"/></svg>',
        xlsx: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14,2 14,8 20,8"/><path d="M16 13v-1a2 2 0 0 0-2-2H9m5 4v-1a2 2 0 0 0-2-2H9m0 5h6"/><line x1="8" y1="12" x2="16" y2="12"/><line x1="8" y1="16" x2="16" y2="16"/></svg>',
        xls: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14,2 14,8 20,8"/><path d="M16 13v-1a2 2 0 0 0-2-2H9m5 4v-1a2 2 0 0 0-2-2H9m0 5h6"/><line x1="8" y1="12" x2="16" y2="12"/><line x1="8" y1="16" x2="16" y2="16"/></svg>',
        pptx: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14,2 14,8 20,8"/><rect x="8" y="12" width="8" height="3" rx="1"/><path d="M8 12H9a2 2 0 0 1 2 2v3H8z"/><path d="M16 12h1a2 2 0 0 1 2 2v3h-4z"/></svg>',
        ppt: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14,2 14,8 20,8"/><rect x="8" y="12" width="8" height="3" rx="1"/><path d="M8 12H9a2 2 0 0 1 2 2v3H8z"/><path d="M16 12h1a2 2 0 0 1 2 2v3h-4z"/></svg>',
        png: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5"/></svg>',
        jpg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5"/></svg>',
        jpeg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5"/></svg>',
        webp: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5"/><path d="M12 12v8"/><path d="M12 12h8"/></svg>',
        bmp: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5"/><rect x="12" y="12" width="8" height="8" rx="1"/></svg>',
        gif: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5"/><path d="M16 12v4h-4"/><path d="M16 16h-4"/></svg>',
        tiff: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5"/><path d="M12 16h8"/><path d="M16 12v4"/></svg>'
    };
    return icons[ext] || icons.txt;
}

function isValidFile(file) {
    const validExtensions = [
        'pdf',
        'docx',
        'doc',
        'txt',
        'md',
        'html',
        'xlsx',
        'xls',
        'pptx',
        'ppt',
        'png',
        'jpg',
        'jpeg',
        'webp',
        'bmp',
        'gif',
        'tif',
        'tiff'
    ];
    return validExtensions.includes(getFileExtension(file.name));
}

function formatBytes(n) {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function updateSessionFilesPreview() {
    const trigger = elements.sessionFilesTrigger;
    const badge = elements.sessionFilesBadge;
    const listEl = elements.sessionFilesPopoverList;
    if (!trigger || !badge || !listEl) {
        return;
    }

    if (!Array.isArray(state.serverFiles)) {
        state.serverFiles = [];
    }

    const count = state.serverFiles.length;

    badge.textContent = String(count > 9 ? '9+' : count);
    if (count > 0) {
        badge.hidden = false;
        trigger.setAttribute(
            'aria-label',
            `本会话已上传 ${count} 个文件，悬停可查看全部文件名。`
        );
    } else {
        badge.hidden = true;
        trigger.setAttribute('aria-label', '本会话尚未上传文件。');
    }

    listEl.innerHTML = '';
    if (state.serverFiles.length === 0) {
        const empty = document.createElement('p');
        empty.className = 'session-files-popover-empty';
        empty.textContent = '本会话尚未上传文件。';
        listEl.appendChild(empty);
        return;
    }

    const list = document.createElement('ul');
    list.className = 'session-files-popover-list';

    state.serverFiles.forEach((file) => {
        if (!file || typeof file !== 'object') return;
        const pending = String(file.id || '').startsWith('temp-');
        const li = document.createElement('li');
        const name =
            file.original_name ||
            file.name ||
            file.filename ||
            String(file.stored_name || file.stored_rel || '').split(/[/\\]/).pop() ||
            '（未命名）';
        li.textContent = pending ? `${name}（上传中…）` : name;
        li.title = name;
        list.appendChild(li);
    });

    listEl.appendChild(list);
}

function updateFileList() {
    if (elements.fileList) {
        elements.fileList.innerHTML = '';

        if (state.serverFiles.length === 0) {
            const emptyState = document.createElement('div');
            emptyState.className = 'file-item empty-state';
            emptyState.style.justifyContent = 'center';
            emptyState.style.color = 'var(--color-gray-400)';
            emptyState.style.background = 'transparent';
            emptyState.style.border = 'none';
            emptyState.style.boxShadow = 'none';
            emptyState.textContent = 'No files uploaded yet.';
            elements.fileList.appendChild(emptyState);
        } else {
            state.serverFiles.forEach((file) => {
                const fileItem = document.createElement('div');
                fileItem.className = 'file-item';
                fileItem.innerHTML = `
            <span class="file-icon">${getFileIcon(file.original_name)}</span>
            <span class="file-name" title="${file.original_name}">${file.original_name}</span>
            <span class="file-size">${formatBytes(file.size_bytes)}</span>
            <button type="button" class="remove-btn" data-file-id="${file.id}" title="Remove file">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <line x1="18" y1="6" x2="6" y2="18"/>
                    <line x1="6" y1="6" x2="18" y2="18"/>
                </svg>
            </button>
        `;
                elements.fileList.appendChild(fileItem);
            });
            elements.fileList.querySelectorAll('.remove-btn').forEach((btn) => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    deleteServerFile(btn.dataset.fileId);
                });
            });
        }
    }

    updateSessionFilesPreview();
    updateSubmitButton();
}

function updateNewChatButton() {
    if (!elements.newChatBtn) return;
    elements.newChatBtn.disabled = state.isLoading || state.isUploading || state.chatMutating;
}

const ASSISTANT_AVATAR_SVG = `<svg class="chat-avatar-icon" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path fill="currentColor" d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/></svg>`;

function renderChat() {
    if (state.messages.length === 0) {
        elements.chatMessages.innerHTML = `
            <div class="chat-msg chat-msg--assistant">
                <div class="chat-msg-inner chat-msg-inner--assistant">
                    <div class="chat-avatar" aria-hidden="true">${ASSISTANT_AVATAR_SVG}</div>
                    <div class="chat-msg-main">
                        <div class="chat-bubble">
                            <p class="welcome-lead">Hi — type a question below to get started.</p>
                            <p class="welcome-muted">We retrieve over this session’s uploads and your Knowledge Base notes. Attach files from the toolbar, tune how many chunks to fetch, and turn assistant replies into a quiz.</p>
                        </div>
                    </div>
                </div>
            </div>
        `;
        updateGenerateButtonState();
        updateNewChatButton();
        return;
    }
    
    elements.chatMessages.innerHTML = '';
    const frag = document.createDocumentFragment();

    state.messages.forEach((m) => {
        const row = document.createElement('div');
        row.className = `chat-msg chat-msg--${m.role}`;
        row.dataset.messageId = m.id;

        const inner = document.createElement('div');
        inner.className =
            m.role === 'assistant' ? 'chat-msg-inner chat-msg-inner--assistant' : 'chat-msg-inner';

        if (m.role === 'assistant') {
            const av = document.createElement('div');
            av.className = 'chat-avatar';
            av.setAttribute('aria-hidden', 'true');
            av.innerHTML = ASSISTANT_AVATAR_SVG;
            inner.appendChild(av);
        }

        const main = document.createElement('div');
        main.className = 'chat-msg-main';

        const bubble = document.createElement('div');
        bubble.className = 'chat-bubble';
        bubble.textContent = m.content || '';
        main.appendChild(bubble);

        if (m.role === 'assistant') {
            const fb = getFeedbackMap()[m.id];
            const toolbar = document.createElement('div');
            toolbar.className = 'assistant-msg-toolbar';
            const mkBtn = (label, icon, fbVal) => {
                const b = document.createElement('button');
                b.type = 'button';
                b.className = 'msg-tb-btn' + (fb === fbVal ? ' is-active' : '');
                b.title = label;
                b.setAttribute('aria-label', label);
                b.dataset.fb = fbVal;
                b.textContent = icon;
                b.addEventListener('click', () => {
                    const cur = getFeedbackMap()[m.id];
                    setFeedback(m.id, cur === fbVal ? null : fbVal);
                    renderChat();
                });
                return b;
            };
            toolbar.appendChild(mkBtn('Helpful', '👍', 'up'));
            toolbar.appendChild(mkBtn('Needs work', '👎', 'down'));

            const copyB = document.createElement('button');
            copyB.type = 'button';
            copyB.className = 'msg-tb-btn';
            copyB.textContent = 'Copy';
            copyB.title = 'Copy reply';
            copyB.addEventListener('click', async () => {
                const text = m.content || '';
                try {
                    await navigator.clipboard.writeText(text);
                    showToast('Reply copied.', false, true);
                } catch (err) {
                    console.error(err);
                    showToast('Copy failed.', true);
                }
            });
            toolbar.appendChild(copyB);

            const shareB = document.createElement('button');
            shareB.type = 'button';
            shareB.className = 'msg-tb-btn';
            shareB.textContent = 'Share';
            shareB.title = 'Share or copy';
            shareB.addEventListener('click', async () => {
                const text = m.content || '';
                if (navigator.share) {
                    try {
                        await navigator.share({ text, title: 'Assistant reply' });
                    } catch (e) {
                        if (e && e.name !== 'AbortError') {
                            try {
                                await navigator.clipboard.writeText(text);
                                showToast('Copied to clipboard.', false, true);
                            } catch (_) {
                                showToast('Share canceled.', false, false);
                            }
                        }
                    }
                } else {
                    try {
                        await navigator.clipboard.writeText(text);
                        showToast('Copied to clipboard.', false, true);
                    } catch (err) {
                        showToast('Could not copy.', true);
                    }
                }
            });
            toolbar.appendChild(shareB);
            main.appendChild(toolbar);
        }

        const delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.className = 'chat-msg-delete';
        delBtn.title = 'Delete this message';
        delBtn.setAttribute('aria-label', 'Delete this message');
        delBtn.dataset.messageId = m.id;
        delBtn.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>`;
        main.appendChild(delBtn);
        inner.appendChild(main);
        row.appendChild(inner);

        if (m.role === 'assistant') {
            const pick = document.createElement('label');
            pick.className = 'quiz-pick';
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.className = 'quiz-pick-cb';
            cb.dataset.messageId = m.id;
            cb.checked = false;
            pick.appendChild(cb);
            pick.appendChild(document.createTextNode(' Include in quiz'));
            const countInput = document.createElement('input');
            countInput.type = 'number';
            countInput.className = 'quiz-count-input';
            countInput.min = '1';
            countInput.max = '20';
            countInput.value = '1';
            countInput.title = 'Number of questions from this reply';
            pick.appendChild(document.createTextNode(' Questions '));
            pick.appendChild(countInput);
            row.appendChild(pick);
        }

        frag.appendChild(row);
    });

    elements.chatMessages.appendChild(frag);
    elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
    updateGenerateButtonState();
    updateNewChatButton();

    elements.chatMessages.querySelectorAll('.chat-msg-delete').forEach((btn) => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            deleteChatMessage(btn.dataset.messageId);
        });
    });

    elements.chatMessages.querySelectorAll('.quiz-pick-cb').forEach((cb) => {
        cb.addEventListener('change', updateGenerateButtonState);
    });
    elements.chatMessages.querySelectorAll('.quiz-count-input').forEach((inp) => {
        inp.addEventListener('input', updateGenerateButtonState);
        inp.addEventListener('change', updateGenerateButtonState);
    });
}

function updateGenerateButtonState() {
    const anyAssistant = state.messages.some((m) => m.role === 'assistant');
    const checked = elements.chatMessages.querySelectorAll('.quiz-pick-cb:checked');
    elements.generateQuizBtn.disabled =
        !anyAssistant || checked.length === 0 || state.isLoading || state.chatMutating;
}

async function pollQaJob(jobId) {
    const sid = getSessionId();
    const url = `${CONFIG.API_BASE}${CONFIG.API_SESSIONS}/${encodeURIComponent(sid)}/qa/jobs/${encodeURIComponent(jobId)}`;
    for (;;) {
        const res = await fetch(url, { headers: authHeaders() });
        if (!res.ok) {
            throw new Error(await getErrorMessage(res));
        }
        const j = await res.json();
        if (j.status === 'done') {
            return j;
        }
        if (j.status === 'error') {
            throw new Error(j.detail || 'Q&A failed.');
        }
        await sleep(CONFIG.POLL_MS);
    }
}

async function submitQuestion() {
    const question = elements.questionInput.value.trim();
    if (!question) {
        showToast('Please enter a question.', true);
        return;
    }
    state.isLoading = true;
    elements.submitBtn.classList.add('loading');
    elements.submitBtn.disabled = true;
    updateGenerateButtonState();

    // Show user message immediately
    const userMessage = {
        id: 'temp-' + Date.now(),
        role: 'user',
        content: question
    };
    const tempMessages = [...state.messages, userMessage];
    state.messages = tempMessages;
    renderChat();

    try {
        await ensureSession();

        const formData = new FormData();
        formData.append('question', question);
        formData.append('top_k', parseInt(elements.topKSelect.value, 10));

        const start = await fetch(
            `${CONFIG.API_BASE}${CONFIG.API_SESSIONS}/${encodeURIComponent(getSessionId())}/qa/async`,
            { method: 'POST', headers: authHeaders(), body: formData }
        );
        if (!start.ok) {
            throw new Error(await getErrorMessage(start));
        }
        const { job_id: jobId } = await start.json();
        const job = await pollQaJob(jobId);
        const result = job.result;
        if (!result) {
            throw new Error('No answer received.');
        }

        if (result.no_kb_notice) {
            showToast(result.no_kb_notice, false, false);
        }
        await loadChatMessages();

        const lastAssistant = [...state.messages].reverse().find((m) => m.role === 'assistant');
        if (lastAssistant) {
            const el = elements.chatMessages.querySelector(
                `.quiz-pick-cb[data-message-id="${lastAssistant.id}"]`
            );
            if (el) {
                el.checked = true;
            }
        }
        updateGenerateButtonState();

        elements.questionInput.value = '';
        showToast('Answer ready.', false, true);
    } catch (err) {
        console.error(err);
        showToast(err.message || 'Request failed.', true);
        // Remove optimistic user bubble
        state.messages = state.messages.filter(m => !m.id.startsWith('temp-'));
        renderChat();
    } finally {
        state.isLoading = false;
        elements.submitBtn.classList.remove('loading');
        updateSubmitButton();
    }
}

async function generateQuizNavigate() {
    const checked = Array.from(elements.chatMessages.querySelectorAll('.quiz-pick-cb:checked'));
    if (checked.length === 0) {
        showToast('Select at least one assistant message.', true);
        return;
    }
    const segments = checked.map((cb) => {
        const row = cb.closest('.chat-msg');
        const num = row && row.querySelector('.quiz-count-input');
        let c = parseInt(num && num.value, 10);
        if (!Number.isFinite(c) || c < 1) c = 1;
        if (c > 20) c = 20;
        return { message_id: cb.dataset.messageId, count: c };
    });

    elements.generateQuizBtn.disabled = true;
    elements.generateQuizBtn.innerHTML = '<span class="btn-loading"><svg class="spinner" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" fill="none" stroke-dasharray="60" stroke-linecap="round"/></svg></span>';
    
    try {
        await ensureSession();
        const res = await fetch(
            `${CONFIG.API_BASE}${CONFIG.API_SESSIONS}/${encodeURIComponent(getSessionId())}/quiz/generate`,
            {
                method: 'POST',
                headers: {
                    ...authHeaders(),
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ segments })
            }
        );
        if (!res.ok) {
            throw new Error(await getErrorMessage(res));
        }
        const data = await res.json();
        sessionStorage.setItem(
            CONFIG.LAST_QUIZ_KEY,
            JSON.stringify({ quiz_id: data.quiz_id, items: data.items })
        );
        window.location.href = 'quiz.html';
    } catch (err) {
        console.error(err);
        showToast(err.message || 'Could not generate quiz.', true);
    } finally {
        elements.generateQuizBtn.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 19.5A2.5 2.5 016.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 014 19.5v-15A2.5 2.5 016.5 2z"/></svg><span>Generate quiz</span>`;
        updateGenerateButtonState();
    }
}

async function addFiles(fileList) {
    const fileArray = Array.from(fileList);
    const validFiles = fileArray.filter(isValidFile);
    
    if (fileArray.length - validFiles.length > 0) {
        showToast('Some file types were skipped.', false, false);
    }
    if (validFiles.length === 0) {
        return;
    }
    if (state.serverFiles.length + validFiles.length > CONFIG.MAX_FILES) {
        showToast(`You can upload at most ${CONFIG.MAX_FILES} files.`, true);
        return;
    }

    state.isUploading = true;
    updateSubmitButton();
    
    // Optimistic file list while uploading
    const tempFiles = validFiles.map(file => ({
        id: 'temp-' + Date.now() + Math.random(),
        original_name: file.name,
        size_bytes: file.size
    }));
    const tempServerFiles = [...state.serverFiles, ...tempFiles];
    state.serverFiles = tempServerFiles;
    updateFileList();

    try {
        await ensureSession();
        const formData = new FormData();
        validFiles.forEach((f) => formData.append('files', f));
        const res = await fetch(
            `${CONFIG.API_BASE}${CONFIG.API_SESSIONS}/${encodeURIComponent(getSessionId())}/files`,
            { method: 'POST', headers: authHeaders(), body: formData }
        );
        if (!res.ok) {
            throw new Error(await getErrorMessage(res));
        }
        await refreshServerFiles();
        showToast('Upload complete.', false, true);
    } catch (err) {
        console.error(err);
        showToast(err.message || 'Upload failed.', true);
        // Restore file list from server
        await refreshServerFiles();
    } finally {
        state.isUploading = false;
        updateSubmitButton();
    }
}

async function deleteServerFile(fileId) {
    if (!fileId) {
        return;
    }
    if (!getSessionId() || !getSessionSecret()) {
        showToast('Session invalid or expired.', true);
        return;
    }
    state.isUploading = true;
    updateSubmitButton();
    try {
        const res = await fetch(
            `${CONFIG.API_BASE}${CONFIG.API_SESSIONS}/${encodeURIComponent(getSessionId())}/files/${encodeURIComponent(fileId)}`,
            { method: 'DELETE', headers: authHeaders() }
        );
        if (!res.ok) {
            throw new Error(await getErrorMessage(res));
        }
        await refreshServerFiles();
        showToast('File removed.', false, true);
    } catch (err) {
        console.error(err);
        showToast(err.message || 'Delete failed.', true);
    } finally {
        state.isUploading = false;
        updateSubmitButton();
    }
}

function updateSubmitButton() {
    const hasQuestion = elements.questionInput.value.trim().length > 0;
    const sidebarListsFiles = !!elements.fileList;
    const hasFilesOrNoSidebar = !sidebarListsFiles || state.serverFiles.length > 0;
    elements.submitBtn.disabled =
        !hasQuestion ||
        !hasFilesOrNoSidebar ||
        state.isLoading ||
        state.isUploading ||
        state.chatMutating;
}

function initSessionFilesPopover() {
    const { sessionFilesTrigger, sessionFilesPreview } = elements;
    if (!sessionFilesTrigger || !sessionFilesPreview) return;

    sessionFilesTrigger.addEventListener('click', async (e) => {
        e.stopPropagation();
        e.preventDefault();
        const open = !sessionFilesPreview.classList.contains('is-open');
        sessionFilesPreview.classList.toggle('is-open', open);
        sessionFilesTrigger.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) {
            try {
                await refreshServerFiles();
            } catch (_) {
                /* keep existing state.serverFiles */
            }
            updateSessionFilesPreview();
        }
    });

    /* 延后到微任务，避免与按钮同一轮 document 冒泡竞争导致刚打开就被关掉 */
    document.addEventListener('click', (e) => {
        const t = e.target;
        queueMicrotask(() => {
            if (!sessionFilesPreview.classList.contains('is-open')) return;
            if (sessionFilesPreview.contains(t)) return;
            sessionFilesPreview.classList.remove('is-open');
            sessionFilesTrigger.setAttribute('aria-expanded', 'false');
        });
    });
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape' || !sessionFilesPreview.classList.contains('is-open')) return;
        sessionFilesPreview.classList.remove('is-open');
        sessionFilesTrigger.setAttribute('aria-expanded', 'false');
    });
}

function initEventListeners() {
    initSessionFilesPopover();
    if (elements.uploadZone && elements.fileInput) {
        elements.uploadZone.addEventListener('click', () => {
            if (!state.isLoading && !state.isUploading) {
                elements.fileInput.click();
            }
        });
        elements.fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                addFiles(e.target.files);
                e.target.value = '';
            }
        });
        elements.uploadZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            if (!state.isLoading && !state.isUploading) {
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
            if (!state.isLoading && !state.isUploading && e.dataTransfer.files.length > 0) {
                addFiles(e.dataTransfer.files);
            }
        });
    }

    // Composer: submit & shortcuts
    elements.questionInput.addEventListener('input', updateSubmitButton);
    elements.questionInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey && !elements.submitBtn.disabled) {
            e.preventDefault();
            submitQuestion();
        }
    });
    elements.submitBtn.addEventListener('click', submitQuestion);
    elements.generateQuizBtn.addEventListener('click', generateQuizNavigate);
    
    if (elements.newChatBtn) {
        elements.newChatBtn.addEventListener('click', () => startNewConversation());
    }

    if (elements.composerAttachBtn && elements.composerFileInput) {
        elements.composerAttachBtn.addEventListener('click', () => elements.composerFileInput.click());
        elements.composerFileInput.addEventListener('change', (e) => {
            if (e.target.files && e.target.files.length > 0) {
                addFiles(e.target.files);
                e.target.value = '';
            }
        });
    }
    if (elements.composerImageBtn && elements.composerImageInput) {
        elements.composerImageBtn.addEventListener('click', () => elements.composerImageInput.click());
        elements.composerImageInput.addEventListener('change', (e) => {
            if (e.target.files && e.target.files.length > 0) {
                addFiles(e.target.files);
                e.target.value = '';
            }
        });
    }
    if (elements.composerMicBtn) {
        elements.composerMicBtn.addEventListener('click', () => {
            const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SR) {
                showToast('Voice input is not supported in this browser.', true);
                return;
            }
            const rec = new SR();
            rec.lang = 'en-US';
            rec.onresult = (ev) => {
                const t = ev.results[0][0].transcript;
                const q = elements.questionInput;
                q.value = `${q.value} ${t}`.trim();
                updateSubmitButton();
            };
            rec.onerror = () => showToast('Speech recognition error.', true);
            try {
                rec.start();
                showToast('Listening…', false, false);
            } catch (err) {
                showToast('Could not start speech recognition.', true);
            }
        });
    }

    document.addEventListener('click', (e) => {
        const menu = document.getElementById('historyContextMenu');
        if (menu && !menu.hidden && !e.target.closest('#historyContextMenu')) {
            closeHistoryMenu();
        }
    });
    window.addEventListener('resize', () => closeHistoryMenu());
    window.addEventListener('scroll', () => closeHistoryMenu(), true);
    
    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
        // Ctrl/Cmd + Enter: send
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && !elements.submitBtn.disabled) {
            e.preventDefault();
            submitQuestion();
        }
        // Ctrl/Cmd + L: clear chat
        if ((e.ctrlKey || e.metaKey) && e.key === 'l' && !e.shiftKey) {
            e.preventDefault();
            clearAllChatMessages();
        }
        if ((e.ctrlKey || e.metaKey) && e.key === 'n') {
            e.preventDefault();
            startNewConversation();
        }
    });
}

// Optional API health check
async function checkApiHealth() {
    try {
        const res = await fetch(`${CONFIG.API_BASE}${CONFIG.API_HEALTH}`);
        if (!res.ok) {
            showToast('The API may be unreachable.', true);
        }
    } catch (err) {
        console.warn('API health check failed:', err);
    }
}

function init() {
    initEventListeners();
    updateSubmitButton();
    migrateConversations();
    renderHistoryList();

    // Ping API on load
    checkApiHealth().catch(() => {});

    refreshServerFiles()
        .then(() => loadChatMessages())
        .catch(() => {});
    
    console.log('RAG web UI loaded.');
    console.log('API:', CONFIG.API_BASE);
}

document.addEventListener('DOMContentLoaded', init);