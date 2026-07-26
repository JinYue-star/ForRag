/* HKU SOLO Bot — frontend auth client.
 *
 * Login tokens are stored under HKU_LOGIN_TOKEN (migrated from legacy RAG_ACCESS_TOKEN).
 * Server env RAG_ACCESS_TOKEN is a separate optional machine/service gate used only when
 * login auth is disabled — never write the env static token into HKU_LOGIN_TOKEN.
 */
(function () {
    "use strict";

    var TOKEN_KEY = "HKU_LOGIN_TOKEN";
    var LEGACY_TOKEN_KEY = "RAG_ACCESS_TOKEN";
    var ROLE_KEY = "hku_role";
    var NAME_KEY = "hku_user_name";
    var USERNAME_KEY = "hku_username";

    /** Known-dead / demo tunnels — never use as API base. */
    var STALE_API_BASES = {
        "http://35.77.38.184:8000": 1,
        "http://35.77.38.184:8001": 1,
        "https://kitty-collapse-ivory-vol.trycloudflare.com": 1
    };

    function resolveApiBase() {
        if (window.__API_BASE__) {
            return String(window.__API_BASE__).replace(/\/+$/, "");
        }
        var stored = null;
        try {
            stored = localStorage.getItem("RAG_API_BASE");
        } catch (e) {}
        if (stored && STALE_API_BASES[String(stored).replace(/\/+$/, "")]) {
            try {
                localStorage.removeItem("RAG_API_BASE");
            } catch (e) {}
            stored = null;
        }
        if (stored) {
            return String(stored).replace(/\/+$/, "");
        }
        // GitHub Pages: no baked-in tunnel. Set window.__API_BASE__ or localStorage RAG_API_BASE.
        if (window.location.hostname.endsWith("github.io")) {
            return "";
        }
        if (window.location.protocol === "file:") {
            return "http://127.0.0.1:8000";
        }
        var port = window.location.port;
        var devPorts = { "5500": 1, "5501": 1, "3000": 1, "5173": 1, "4173": 1, "8080": 1 };
        if (port && devPorts[port]) {
            return window.location.protocol + "//" + window.location.hostname + ":8000";
        }
        return window.location.origin;
    }

    var API_BASE = resolveApiBase();

    function get(k) {
        try {
            return (localStorage.getItem(k) || "").trim();
        } catch (e) {
            return "";
        }
    }
    function set(k, v) {
        try {
            localStorage.setItem(k, v);
        } catch (e) {}
    }
    function del(k) {
        try {
            localStorage.removeItem(k);
        } catch (e) {}
    }

    function token() {
        var t = get(TOKEN_KEY);
        if (t) return t;
        // One-time migrate legacy key (was overloaded with server RAG_ACCESS_TOKEN name).
        var legacy = get(LEGACY_TOKEN_KEY);
        if (legacy) {
            set(TOKEN_KEY, legacy);
            del(LEGACY_TOKEN_KEY);
            return legacy;
        }
        return "";
    }

    function role() {
        return get(ROLE_KEY);
    }
    function displayName() {
        return get(NAME_KEY);
    }

    function localizedText(en, zh, vars) {
        if (window.HKU && window.HKU.text) return window.HKU.text(en, zh, vars);
        var lang = get("hku_lang") === "zh" ? "zh" : "en";
        var value = lang === "zh" ? zh : en;
        Object.keys(vars || {}).forEach(function (key) {
            value = value.replace(new RegExp("\\{" + key + "\\}", "g"), String(vars[key]));
        });
        return value;
    }

    function localizedError(en, zh, status) {
        var err = new Error(localizedText(en, zh));
        err.messageEn = en;
        err.messageZh = zh;
        if (status != null) err.status = status;
        return err;
    }

    function apiError(detail, status) {
        var messages = {
            "用户名或密码错误": ["Incorrect username or password.", "用户名或密码错误。"],
            "注册码无效": ["The registration code is invalid.", "注册码无效。"],
            "请输入用户名": ["Enter a username.", "请输入用户名。"],
            "密码至少 6 位": ["The password must be at least 6 characters.", "密码至少需要 6 个字符。"],
            "用户名已被占用": ["That username is already in use.", "用户名已被占用。"],
            "非法角色": ["The selected role is invalid.", "所选角色无效。"],
            "创建用户失败": ["Failed to create the user.", "创建用户失败。"],
            "未登录": ["You are not signed in.", "您尚未登录。"],
            "请先登录": ["Please sign in first.", "请先登录。"],
            "登录已过期或无效，请重新登录": [
                "Your session has expired or is invalid. Please sign in again.",
                "登录已过期或无效，请重新登录。"
            ],
            "账号已被禁用": ["This account has been disabled.", "账号已被禁用。"],
            "仅教师可执行该操作": ["Only teachers can perform this action.", "仅教师可执行该操作。"],
            "请求过于频繁，请稍后再试": [
                "Too many requests. Please try again later.",
                "请求过于频繁，请稍后再试。"
            ],
            "缺少访问令牌": ["The access token is missing.", "缺少访问令牌。"],
            "访问令牌无效": ["The access token is invalid.", "访问令牌无效。"]
        };
        var known = messages[String(detail || "")];
        if (known) return localizedError(known[0], known[1], status);
        if (detail) return localizedError(String(detail), String(detail), status);
        return localizedError(
            "Request failed (" + status + ").",
            "请求失败（状态码 " + status + "）。",
            status
        );
    }

    function saveSession(data) {
        if (!data || !data.token) return;
        set(TOKEN_KEY, data.token);
        del(LEGACY_TOKEN_KEY);
        var u = data.user || {};
        set(ROLE_KEY, u.role || "");
        set(NAME_KEY, u.display_name || u.username || "");
        set(USERNAME_KEY, u.username || "");
    }

    function clearSession() {
        // Clear auth + current learning session only.
        // Keep RAG_CONVERSATIONS::<username>: wiping it on logout /me failure
        // erased student chat sidebars with no recovery path.
        del(TOKEN_KEY);
        del(LEGACY_TOKEN_KEY);
        del(ROLE_KEY);
        del(NAME_KEY);
        del(USERNAME_KEY);
        del("RAG_SESSION_ID");
        del("RAG_SESSION_SECRET");
    }

    function username() {
        return get(USERNAME_KEY);
    }

    function ensureApiBase() {
        if (API_BASE) return API_BASE;
        throw localizedError(
            "API base is not configured. On GitHub Pages set localStorage RAG_API_BASE or window.__API_BASE__ to your backend URL.",
            "未配置 API 地址。在 GitHub Pages 上请设置 localStorage.RAG_API_BASE 或 window.__API_BASE__ 为后端地址。"
        );
    }

    async function apiJson(path, opts) {
        opts = opts || {};
        var base = ensureApiBase();
        var headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
        if (token()) headers.Authorization = "Bearer " + token();
        var res;
        try {
            res = await fetch(base + path, {
                method: opts.method || "GET",
                headers: headers,
                body: opts.body ? JSON.stringify(opts.body) : undefined
            });
        } catch (e) {
            throw localizedError(
                "Unable to reach the server. Check your connection and try again.",
                "无法连接服务器，请检查网络后重试。"
            );
        }
        var data = null;
        try {
            data = await res.json();
        } catch (e) {}
        if (!res.ok) {
            throw apiError(data && data.detail, res.status);
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
        try {
            await apiJson("/api/v1/auth/logout", { method: "POST" });
        } catch (e) {}
        clearSession();
    }

    async function config() {
        if (!API_BASE) {
            return { auth_required: true, api_base_missing: true };
        }
        try {
            return await apiJson("/api/v1/auth/config");
        } catch (e) {
            return { auth_required: false };
        }
    }

    async function requireRole(requiredRole) {
        var cfg = await config();
        if (cfg.api_base_missing) {
            redirectToLogin(requiredRole);
            return null;
        }
        if (!cfg.auth_required) {
            if (requiredRole && !role()) set(ROLE_KEY, requiredRole);
            return { role: role() || requiredRole || "", display_name: displayName() };
        }
        if (!token()) {
            redirectToLogin(requiredRole);
            return null;
        }
        try {
            var user = await me();
            var allowed =
                !requiredRole ||
                user.role === requiredRole ||
                (requiredRole === "student" && user.role === "teacher");
            if (!allowed) {
                window.location.href =
                    "login.html?role=" + encodeURIComponent(requiredRole) + "&switch=1";
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
        var q = requiredRole ? "?role=" + encodeURIComponent(requiredRole) : "";
        window.location.href = "login.html" + q;
    }

    window.HKUAuth = {
        API_BASE: API_BASE,
        resolveApiBase: resolveApiBase,
        staleApiBases: STALE_API_BASES,
        token: token,
        role: role,
        displayName: displayName,
        username: username,
        isLoggedIn: function () {
            return !!token();
        },
        login: login,
        register: register,
        me: me,
        logout: logout,
        config: config,
        requireRole: requireRole,
        clearSession: clearSession
    };
})();
