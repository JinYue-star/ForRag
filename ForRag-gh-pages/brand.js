/* HKU Teacher-student Co-learning (SOLO) Bot
 * Shared brand bar + lightweight i18n (default English, switchable Chinese).
 * Include on every page: <script src="brand.js"></script>
 */
(function () {
    "use strict";

    var LANG_KEY = "hku_lang";
    var PRODUCT_NAME = "HKU Teacher-student Co-learning (SOLO) Bot";

    // ---- i18n dictionary (en is the source of truth; zh mirrors keys) ----
    var I18N = {
        en: {
            "product.name": PRODUCT_NAME,
            "product.short": "SOLO Bot",
            "lang.toggle": "CN",
            "role.teacher": "Teacher",
            "role.student": "Student",

            "landing.title": "HKU Teacher-student Co-learning (SOLO) Bot",
            "landing.teacher.name": "Teacher",
            "landing.teacher.desc": "Upload course materials and questions, manage the shared knowledge base, and export student questions and quiz data.",
            "landing.student.name": "Student",
            "landing.student.desc": "Ask questions based on the course materials, upload temporary files for a session, and generate quizzes to review.",
            "landing.footer": "The University of Hong Kong · Department of Electrical & Computer Engineering",

            "nav.assistant": "AI Assistant",
            "nav.kb": "Knowledge Base",
            "nav.quiz": "Quiz",
            "nav.export": "Export",
            "nav.back": "Back",
            "nav.back.aria": "Back one level",

            "assistant.title": "AI Assistant",
            "assistant.subtitle": "Retrieval-augmented answers from the course knowledge base and this session's files.",
            "kb.title": "Knowledge Base",
            "kb.subtitle.teacher": "Course materials shared with all students. You can add categories, notes and attachments.",
            "kb.subtitle.student": "Course materials shared by your teacher (read-only). These are included in retrieval.",
            "quiz.title": "Quiz",

            "newchat": "New chat",
            "chats": "Chats",
            "composer.placeholder": "Ask the assistant… (Shift+Enter for newline)",
            "generatequiz": "Generate quiz",

            "auth.login": "Sign in",
            "auth.register": "Register",
            "auth.logout": "Sign out",
            "auth.username": "Username",
            "auth.password": "Password",
            "auth.displayname": "Display name",
            "auth.studentno": "Student number (optional)",
            "auth.regcode": "Course registration code",
            "auth.teacher.title": "Teacher sign in",
            "auth.student.title": "Student sign in",
            "auth.toRegister": "New student? Create an account",
            "auth.toLogin": "Already have an account? Sign in",
            "auth.submit.login": "Sign in",
            "auth.submit.register": "Register & sign in",
            "auth.back": "Back",

            "teacher.title": "Teacher Console",
            "teacher.subtitle": "Manage course materials, students and exports.",
            "teacher.card.kb": "Course Knowledge Base",
            "teacher.card.kb.desc": "Upload course materials and questions shared with all students.",
            "teacher.card.students": "Students",
            "teacher.card.students.desc": "Create student accounts or share the registration code.",
            "teacher.card.export": "Export",
            "teacher.card.export.desc": "Export student questions and quiz scores.",
            "teacher.open": "Open",
            "teacher.regcode.label": "Registration code",
            "teacher.regcode.rotate": "Regenerate",
            "teacher.students.new": "New student",
            "teacher.students.empty": "No student accounts yet.",
            "teacher.teachers.new": "New teacher",
            "common.create": "Create",
            "common.cancel": "Cancel",
            "common.delete": "Delete",
            "common.role": "Role",
            "common.name": "Name",

            "export.title": "Export",
            "export.subtitle": "Filter and download student questions and quiz scores.",
            "export.filters": "Filters",
            "export.range": "Time range",
            "export.range.all": "All time",
            "export.range.today": "Today",
            "export.range.7": "Last 7 days",
            "export.range.30": "Last 30 days",
            "export.range.custom": "Custom",
            "export.start": "Start",
            "export.end": "End",
            "export.mod.questions": "Student questions",
            "export.mod.answers": "System answer summaries (optional)",
            "export.mod.quiz": "Quiz",
            "export.preview": "Preview",
            "export.previewTitle": "Preview",
            "export.download": "Export",
            "export.pickModule": "Select at least one module.",
            "export.moreRows": "…and {n} more rows will be included in the file."
        },
        zh: {
            "product.name": "港大师生共学 (SOLO) 机器人",
            "product.short": "SOLO Bot",
            "lang.toggle": "EN",
            "role.teacher": "教师",
            "role.student": "学生",

            "landing.title": "港大师生共学 (SOLO) 机器人",
            "landing.teacher.name": "教师",
            "landing.teacher.desc": "上传课件与题目，管理共享知识库，并导出学生提问与测验数据。",
            "landing.student.name": "学生",
            "landing.student.desc": "基于课程资料提问，可临时上传本次会话文件，并生成测验复习。",
            "landing.footer": "香港大学 · 电机电子工程系",

            "nav.assistant": "AI 助手",
            "nav.kb": "知识库",
            "nav.quiz": "测验",
            "nav.export": "导出",
            "nav.back": "返回",
            "nav.back.aria": "返回上一级",

            "assistant.title": "AI 助手",
            "assistant.subtitle": "基于课程知识库与本会话文件的检索增强问答。",
            "kb.title": "知识库",
            "kb.subtitle.teacher": "面向全体学生共享的课程资料。你可以新增类目、笔记与附件。",
            "kb.subtitle.student": "教师共享的课程资料（只读），会一同参与检索。",
            "quiz.title": "测验",

            "newchat": "新建对话",
            "chats": "对话",
            "composer.placeholder": "向助手提问……（Shift+Enter 换行）",
            "generatequiz": "生成测验",

            "auth.login": "登录",
            "auth.register": "注册",
            "auth.logout": "退出登录",
            "auth.username": "用户名",
            "auth.password": "密码",
            "auth.displayname": "显示名称",
            "auth.studentno": "学号（可选）",
            "auth.regcode": "课程注册码",
            "auth.teacher.title": "教师登录",
            "auth.student.title": "学生登录",
            "auth.toRegister": "新同学？创建账号",
            "auth.toLogin": "已有账号？去登录",
            "auth.submit.login": "登录",
            "auth.submit.register": "注册并登录",
            "auth.back": "返回",

            "teacher.title": "教师控制台",
            "teacher.subtitle": "管理课程资料、学生与数据导出。",
            "teacher.card.kb": "课程知识库",
            "teacher.card.kb.desc": "上传面向全体学生共享的课件与题目。",
            "teacher.card.students": "学生管理",
            "teacher.card.students.desc": "创建学生账号或分享注册码。",
            "teacher.card.export": "导出",
            "teacher.card.export.desc": "导出学生提问与测验成绩。",
            "teacher.open": "打开",
            "teacher.regcode.label": "注册码",
            "teacher.regcode.rotate": "重新生成",
            "teacher.students.new": "新增学生",
            "teacher.students.empty": "暂无学生账号。",
            "teacher.teachers.new": "新增教师",
            "common.create": "创建",
            "common.cancel": "取消",
            "common.delete": "删除",
            "common.role": "角色",
            "common.name": "姓名",

            "export.title": "导出",
            "export.subtitle": "筛选并下载学生提问与测验成绩。",
            "export.filters": "筛选条件",
            "export.range": "时间范围",
            "export.range.all": "全部时间",
            "export.range.today": "今天",
            "export.range.7": "近 7 天",
            "export.range.30": "近 30 天",
            "export.range.custom": "自定义",
            "export.start": "开始",
            "export.end": "结束",
            "export.mod.questions": "学生提问",
            "export.mod.answers": "系统回答摘要（可选）",
            "export.mod.quiz": "测验",
            "export.preview": "预览",
            "export.previewTitle": "预览",
            "export.download": "导出",
            "export.pickModule": "请至少选择一个模块。",
            "export.moreRows": "……文件中还将包含另外 {n} 行。"
        }
    };

    function getLang() {
        var v = null;
        try { v = localStorage.getItem(LANG_KEY); } catch (e) {}
        return v === "zh" ? "zh" : "en";
    }

    function setLang(lang) {
        try { localStorage.setItem(LANG_KEY, lang); } catch (e) {}
    }

    function t(key) {
        var lang = getLang();
        var table = I18N[lang] || I18N.en;
        if (Object.prototype.hasOwnProperty.call(table, key)) return table[key];
        if (Object.prototype.hasOwnProperty.call(I18N.en, key)) return I18N.en[key];
        return key;
    }

    function text(en, zh, vars) {
        var value = getLang() === "zh" ? zh : en;
        Object.keys(vars || {}).forEach(function (key) {
            value = value.replace(new RegExp("\\{" + key + "\\}", "g"), String(vars[key]));
        });
        return value;
    }

    // Apply translations to any element carrying data-i18n* attributes.
    function applyTranslations(root) {
        var scope = root || document;
        scope.querySelectorAll("[data-i18n]").forEach(function (el) {
            el.textContent = t(el.getAttribute("data-i18n"));
        });
        scope.querySelectorAll("[data-i18n-placeholder]").forEach(function (el) {
            el.setAttribute("placeholder", t(el.getAttribute("data-i18n-placeholder")));
        });
        scope.querySelectorAll("[data-i18n-title]").forEach(function (el) {
            el.setAttribute("title", t(el.getAttribute("data-i18n-title")));
        });
        scope.querySelectorAll("[data-i18n-aria-label]").forEach(function (el) {
            el.setAttribute("aria-label", t(el.getAttribute("data-i18n-aria-label")));
        });
        scope.querySelectorAll("[data-en][data-zh]").forEach(function (el) {
            el.textContent = text(el.getAttribute("data-en"), el.getAttribute("data-zh"));
        });
        ["placeholder", "title", "aria-label", "alt"].forEach(function (attr) {
            scope.querySelectorAll("[data-" + attr + "-en][data-" + attr + "-zh]").forEach(function (el) {
                el.setAttribute(attr, text(
                    el.getAttribute("data-" + attr + "-en"),
                    el.getAttribute("data-" + attr + "-zh")
                ));
            });
        });
        var title = document.querySelector("title[data-en][data-zh]");
        if (title) document.title = text(title.getAttribute("data-en"), title.getAttribute("data-zh"));
        document.querySelectorAll("meta[data-content-en][data-content-zh]").forEach(function (el) {
            el.setAttribute("content", text(el.getAttribute("data-content-en"), el.getAttribute("data-content-zh")));
        });
        document.documentElement.setAttribute("lang", getLang() === "zh" ? "zh-CN" : "en");
    }

    function currentPage() {
        var path = (window.location.pathname.split("/").pop() || "").toLowerCase();
        return path || "index.html";
    }

    function isTeacherRole() {
        try {
            if ((localStorage.getItem("hku_role") || "").trim() === "teacher") return true;
        } catch (e) {}
        return document.body.getAttribute("data-hku-role") === "teacher";
    }

    // Hierarchical parent for “back one level” (not browser history).
    function parentPage() {
        var page = currentPage();
        var params = new URLSearchParams(window.location.search || "");
        switch (page) {
            case "landing.html":
                return null;
            case "login.html":
                return "landing.html";
            case "teacher.html":
                return "landing.html";
            case "export.html":
                return "teacher.html";
            case "kb.html":
                return isTeacherRole() ? "teacher.html" : "index.html";
            case "quiz.html":
                if (params.get("quiz_id") || params.get("mode") === "class") return "kb.html";
                return "index.html";
            case "index.html":
                return isTeacherRole() ? "teacher.html" : "landing.html";
            default:
                return "landing.html";
        }
    }

    function buildTopbar() {
        if (document.querySelector(".hku-topbar")) return;

        var bar = document.createElement("header");
        bar.className = "hku-topbar";
        bar.setAttribute("role", "banner");

        var logos = document.createElement("div");
        logos.className = "hku-topbar__logos";
        var hku = document.createElement("img");
        hku.className = "hku-topbar__logo hku-topbar__logo--hku";
        hku.src = "assets/hku-logo.png";
        hku.alt = text("The University of Hong Kong", "香港大学");
        hku.setAttribute("data-alt-en", "The University of Hong Kong");
        hku.setAttribute("data-alt-zh", "香港大学");
        var ece = document.createElement("img");
        ece.className = "hku-topbar__logo hku-topbar__logo--ece";
        ece.src = "assets/ece-logo.png";
        ece.alt = text("Department of Electrical & Computer Engineering", "电机电子工程系");
        ece.setAttribute("data-alt-en", "Department of Electrical & Computer Engineering");
        ece.setAttribute("data-alt-zh", "电机电子工程系");
        logos.appendChild(hku);
        logos.appendChild(ece);

        // Product name intentionally omitted from the bar (logos carry the brand).
        var spacer = document.createElement("div");
        spacer.className = "hku-topbar__spacer";

        bar.appendChild(logos);
        bar.appendChild(spacer);

        var up = parentPage();
        if (up) {
            var back = document.createElement("button");
            back.type = "button";
            back.className = "hku-lang-toggle";
            back.setAttribute("data-i18n", "nav.back");
            back.setAttribute("data-i18n-aria-label", "nav.back.aria");
            back.setAttribute("data-i18n-title", "nav.back.aria");
            back.setAttribute("aria-label", t("nav.back.aria"));
            back.setAttribute("title", t("nav.back.aria"));
            back.textContent = t("nav.back");
            back.addEventListener("click", function () {
                window.location.href = up;
            });
            bar.appendChild(back);
        }

        var toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "hku-lang-toggle";
        toggle.setAttribute("aria-label", text("Switch language", "切换语言"));
        toggle.setAttribute("data-aria-label-en", "Switch language");
        toggle.setAttribute("data-aria-label-zh", "切换语言");
        toggle.setAttribute("data-i18n", "lang.toggle");
        toggle.textContent = t("lang.toggle");
        toggle.addEventListener("click", function () {
            setLang(getLang() === "en" ? "zh" : "en");
            applyTranslations(document);
            toggle.textContent = t("lang.toggle");
            document.dispatchEvent(new CustomEvent("hku:langchange", { detail: { lang: getLang() } }));
        });
        bar.appendChild(toggle);

        // Show logout when an auth client is present and logged in.
        // (Signed-in display name omitted — it was display-only, not interactive.)
        if (window.HKUAuth && window.HKUAuth.isLoggedIn && window.HKUAuth.isLoggedIn()) {
            var logout = document.createElement("button");
            logout.type = "button";
            logout.className = "hku-lang-toggle";
            logout.setAttribute("data-i18n", "auth.logout");
            logout.textContent = t("auth.logout");
            logout.addEventListener("click", function () {
                window.HKUAuth.logout().then(function () { window.location.href = "landing.html"; });
            });
            bar.appendChild(logout);
        }

        document.body.insertBefore(bar, document.body.firstChild);
        document.body.classList.add("has-hku-topbar");
    }

    // Expose a tiny API for page scripts (dynamic strings).
    window.HKU = {
        t: t,
        text: text,
        lang: getLang,
        setLang: function (l) { setLang(l); applyTranslations(document); },
        apply: applyTranslations
    };

    function init() {
        buildTopbar();
        applyTranslations(document);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
