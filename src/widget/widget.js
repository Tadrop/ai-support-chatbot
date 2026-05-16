/*!
 * GreenLeaf Chat Widget — vanilla JS, zero dependencies.
 * Embed with:
 *   <script src="https://your-host/widget.js" data-api="https://api.your-host" defer></script>
 *
 * Lifecycle:
 *   1. Renders a floating chat button (bottom-right).
 *   2. On click, opens a panel.
 *   3. On first open, shows the GDPR notice + name/email form.
 *   4. After consent, calls /session/start, stores session_id in sessionStorage.
 *   5. Each message POSTs to /chat and renders the answer with cited URLs.
 *   6. If the backend returns handoff=true, shows a "connected with our team" notice.
 */
(function () {
  "use strict";

  // ---------- Config ----------
  var currentScript = document.currentScript ||
    document.querySelector('script[src*="widget.js"]');
  var API_BASE = (currentScript && currentScript.getAttribute("data-api")) || "";
  var SESSION_KEY = "greenleaf_chat_session";

  // ---------- Styles ----------
  var STYLE = ""
    + "#gl-chat-btn{position:fixed;bottom:20px;right:20px;width:56px;height:56px;"
    + "border-radius:50%;background:#2E7D32;color:#fff;border:none;cursor:pointer;"
    + "font-size:24px;box-shadow:0 4px 12px rgba(0,0,0,.2);z-index:999999}"
    + "#gl-chat-panel{position:fixed;bottom:90px;right:20px;width:360px;max-width:calc(100vw - 40px);"
    + "height:520px;max-height:calc(100vh - 120px);background:#fff;border-radius:12px;"
    + "box-shadow:0 8px 24px rgba(0,0,0,.18);display:none;flex-direction:column;"
    + "font:14px/1.4 system-ui,sans-serif;color:#222;z-index:999999;overflow:hidden}"
    + "#gl-chat-panel.open{display:flex}"
    + "#gl-header{background:#2E7D32;color:#fff;padding:12px 16px;font-weight:600}"
    + "#gl-body{flex:1;overflow-y:auto;padding:12px 16px}"
    + "#gl-footer{border-top:1px solid #eee;padding:8px;display:flex;gap:8px}"
    + "#gl-input{flex:1;border:1px solid #ccc;border-radius:6px;padding:8px;font:inherit}"
    + "#gl-send{background:#2E7D32;color:#fff;border:none;border-radius:6px;padding:0 14px;cursor:pointer}"
    + ".gl-msg{margin:8px 0;padding:8px 12px;border-radius:8px;max-width:85%}"
    + ".gl-msg.user{background:#E8F5E9;align-self:flex-end;margin-left:auto}"
    + ".gl-msg.bot{background:#F5F5F5;align-self:flex-start}"
    + ".gl-msg.handoff{background:#FFF3E0;border:1px solid #FFB74D}"
    + ".gl-sources{margin-top:6px;font-size:12px;color:#555}"
    + ".gl-sources a{color:#2E7D32;text-decoration:underline;margin-right:8px}"
    + ".gl-gdpr{font-size:12px;color:#555;background:#FAFAFA;padding:10px;border-radius:6px;margin-bottom:10px}"
    + ".gl-form input{display:block;width:100%;box-sizing:border-box;margin:6px 0;padding:8px;border:1px solid #ccc;border-radius:6px;font:inherit}"
    + ".gl-form button{background:#2E7D32;color:#fff;border:none;border-radius:6px;padding:8px 12px;cursor:pointer;width:100%}"
    + ".gl-form label{font-size:12px;display:flex;gap:6px;align-items:center;margin:6px 0}";

  // ---------- DOM helpers ----------
  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (k === "class") node.className = attrs[k];
      else if (k === "html") node.innerHTML = attrs[k];
      else node.setAttribute(k, attrs[k]);
    }
    (children || []).forEach(function (c) {
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }

  function injectStyles() {
    var s = document.createElement("style");
    s.textContent = STYLE;
    document.head.appendChild(s);
  }

  // ---------- API ----------
  function api(path, body) {
    return fetch(API_BASE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) {
      if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || r.statusText); });
      return r.json();
    });
  }

  // ---------- Session bootstrap ----------
  function getSession() {
    try { return JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null"); }
    catch (e) { return null; }
  }
  function setSession(s) { sessionStorage.setItem(SESSION_KEY, JSON.stringify(s)); }

  // ---------- Rendering ----------
  var body;

  function addMsg(role, text, citedUrls, isHandoff) {
    var cls = "gl-msg " + role + (isHandoff ? " handoff" : "");
    var node = el("div", { class: cls }, [text]);
    if (citedUrls && citedUrls.length) {
      var sources = el("div", { class: "gl-sources" }, ["Sources: "]);
      citedUrls.forEach(function (u) {
        sources.appendChild(el("a", { href: u, target: "_blank", rel: "noopener" }, [linkLabel(u)]));
      });
      node.appendChild(sources);
    }
    body.appendChild(node);
    body.scrollTop = body.scrollHeight;
  }

  function linkLabel(u) {
    try { return new URL(u).pathname.replace(/^\//, "") || u; } catch (e) { return u; }
  }

  function showOnboarding(onSubmit) {
    body.innerHTML = "";
    var gdpr = el("div", { class: "gl-gdpr" }, [
      "Before we begin: GreenLeaf will store your name and email to follow up on your enquiry. "
      + "Your conversation may be reviewed by our team. By continuing you consent to this use.",
    ]);
    var form = el("form", { class: "gl-form" });
    var nameInput = el("input", { type: "text", placeholder: "Your name", required: "" });
    var emailInput = el("input", { type: "email", placeholder: "Your email", required: "" });
    var consentLabel = el("label", {});
    var consentInput = el("input", { type: "checkbox", required: "" });
    consentLabel.appendChild(consentInput);
    consentLabel.appendChild(document.createTextNode("I consent to the above"));
    var submit = el("button", { type: "submit" }, ["Start chat"]);
    form.appendChild(nameInput);
    form.appendChild(emailInput);
    form.appendChild(consentLabel);
    form.appendChild(submit);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (!consentInput.checked) return;
      submit.disabled = true; submit.textContent = "Starting…";
      onSubmit(nameInput.value.trim(), emailInput.value.trim());
    });
    body.appendChild(gdpr);
    body.appendChild(form);
  }

  function showChat(session) {
    body.innerHTML = "";
    addMsg("bot", "Hi " + session.name + "! Ask me about shipping, returns, products, or anything from our website.");
  }

  // ---------- Bootstrap ----------
  function send(session, msg) {
    addMsg("user", msg);
    api("/chat", { session_id: session.session_id, message: msg })
      .then(function (resp) { addMsg("bot", resp.answer, resp.cited_urls, resp.handoff); })
      .catch(function (err) { addMsg("bot", "Sorry — something went wrong. " + err.message, [], true); });
  }

  function init() {
    injectStyles();
    var btn = el("button", { id: "gl-chat-btn", "aria-label": "Open chat" }, ["💬"]);
    var panel = el("div", { id: "gl-chat-panel", role: "dialog", "aria-label": "GreenLeaf chat" });
    var header = el("div", { id: "gl-header" }, ["GreenLeaf Help"]);
    body = el("div", { id: "gl-body" });
    var footer = el("div", { id: "gl-footer" });
    var input = el("input", { id: "gl-input", placeholder: "Type a message…", "aria-label": "Message" });
    var sendBtn = el("button", { id: "gl-send" }, ["Send"]);
    footer.appendChild(input);
    footer.appendChild(sendBtn);
    panel.appendChild(header);
    panel.appendChild(body);
    panel.appendChild(footer);
    document.body.appendChild(btn);
    document.body.appendChild(panel);

    var session = getSession();

    btn.addEventListener("click", function () {
      panel.classList.toggle("open");
      if (panel.classList.contains("open") && !getSession()) {
        showOnboarding(function (name, email) {
          api("/session/start", { name: name, email: email, gdpr_consent: true })
            .then(function (resp) {
              session = { session_id: resp.session_id, name: name, email: email };
              setSession(session);
              showChat(session);
            })
            .catch(function (err) {
              body.innerHTML = "";
              addMsg("bot", "Couldn't start the session: " + err.message, [], true);
            });
        });
      } else if (panel.classList.contains("open") && session && body.children.length === 0) {
        showChat(session);
      }
    });

    function trigger() {
      var msg = input.value.trim();
      if (!msg) return;
      if (!session) return;
      input.value = "";
      send(session, msg);
    }
    sendBtn.addEventListener("click", trigger);
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") trigger(); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
