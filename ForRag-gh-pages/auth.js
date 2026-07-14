/* HKU SOLO Bot — frontend auth client.
 * Stores the login token under RAG_ACCESS_TOKEN so all existing fetch()
 * calls (which send it as a Bearer header) authenticate automatically.
 */
(function () {
    "use strict";

    var TOKEN_KEY = "RAG_ACCESS_TOKEN";
    var ROLE_KEY = "hku_role";
    var NAME_KEY = "hku_user_name";
    var USERNAME_KEY = "hku_username";

    function resolveApiBase() {
        var isGitHubPages = window.location.hostname.endsWith("github.io");
        var stored = null;
        try { stored = localStorage.getItem("RAG_API_BASE"); } catch (e) {}
        if (window.__API_BASE__) return String(window.__API_BASE__).replace(/\/+$/, "");
        if (stored) return String(stored).replace(/\/+$/, "");
        if (isGitHubPages) return "https://kitty-collapse-ivory-vol.trycloudflare.com";
        if (window.location.protocol === "file:") return "http://127.0.0.1:8000";
        var port = window.location.port;
        var devPorts = { "5500": 1, "5501": 1, "3000": 1, "5173": 1, "4173": 1, "8080": 1 };
        if (port && devPorts[port]) return window.location.protocol + "//" + window.location.hostname + ":8000";
        return window.location.origin;
    }

    var API_BASE = resolveApiBase();

    function get(k) { try { return (localStorage.getItem(k) || "").trim(); } catch (e) { return ""; } }
    function set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
    function del(k) { try { localStorage.removeItem(k); } catch (e) {} }

    function token() { return get(TOKEN_KEY); }
    function role() { return get(ROLE_KEY); }
    function displayName() { return get(NAME_KEY); }

    function saveSession(data) {
        if (!data || !data.token) return;
        set(TOKEN_KEY, data.token);
        var u = data.user || {};
        set(ROLE_KEY, u.role || "");
        set(NAME_KEY, u.display_name || u.username || "");
        set(USERNAME_KEY, u.username || "");
    }

    function clearSession() {
        del(TOKEN_KEY);
        del(ROLE_KEY);
        del(NAME_KEY);
        del(USERNAME_KEY);
        // also drop the per-browser learning session so a new user starts clean
        del("RAG_SESSION_ID");
        del("RAG_SESSION_SECRET");
    }

    async function apiJson(path, opts) {
        opts = opts || {};
        var headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
        if (token()) headers.Authorization = "Bearer " + token();
        var res = await fetch(API_BASE + path, {
            method: opts.method || "GET",
            headers: headers,
            body: opts.body ? JSON.stringify(opts.body) : undefined
        });
        var data = null;
        try { data = await res.json(); } catch (e) {}
        if (!res.ok) {
            var msg = (data && data.detail) ? data.detail : ("Request failed (" + res.status + ")");
            var err = new Error(msg);
            err.status = res.status;
            throw err;
        }
        return data;
    }

    async function login(username, password) {
        var data = await apiJson("/api/v1/auth/login", {
            method: "POST",
            body: { username: username, password: password }
        });
        saveSession(data);
        return data;
    }

    async function register(payload) {
        var data = await apiJson("/api/v1/auth/register", { method: "POST", body: payload });
        saveSession(data);
        return data;
    }

    async function me() {
        return apiJson("/api/v1/auth/me");
    }

    async function logout() {
        try { await apiJson("/api/v1/auth/logout", { method: "POST" }); } catch (e) {}
        clearSession();
    }

    async function config() {
        try { return await apiJson("/api/v1/auth/config"); }
        catch (e) { return { auth_required: false }; }
    }

    // Guard: ensure a valid session with the required role; else redirect to login.
    // Returns the current user (or a stub in no-auth mode).
    async function requireRole(requiredRole) {
        var cfg = await config();
        if (!cfg.auth_required) {
            // no-auth local mode: allow through, remember chosen role if present
            if (requiredRole && !role()) set(ROLE_KEY, requiredRole);
            return { role: role() || requiredRole || "", display_name: displayName() };
        }
        if (!token()) {
            redirectToLogin(requiredRole);
            return null;
        }
        try {
            var user = await me();
            // Teachers may preview student pages (superset); students cannot open
            // teacher-only pages.
            var allowed = !requiredRole || user.role === requiredRole ||
                (requiredRole === "student" && user.role === "teacher");
            if (!allowed) {
                window.location.href = "login.html?role=" + encodeURIComponent(requiredRole) + "&switch=1";
                return null;
            }
            set(ROLE_KEY, user.role || "");
            set(NAME_KEY, user.display_name || user.username || "");
            return user;
        } catch (e) {
            clearSession();
            redirectToLogin(requiredRole);
            return null;
        }
    }

    function redirectToLogin(requiredRole) {
        var q = requiredRole ? ("?role=" + encodeURIComponent(requiredRole)) : "";
        window.location.href = "login.html" + q;
    }

    window.HKUAuth = {
        API_BASE: API_BASE,
        token: token,
        role: role,
        displayName: displayName,
        isLoggedIn: function () { return !!token(); },
        login: login,
        register: register,
        me: me,
        logout: logout,
        config: config,
        requireRole: requireRole,
        clearSession: clearSession
    };
})();
