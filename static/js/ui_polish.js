(function () {
    const MAX_POLISH_BLOCKS = 80;

    function escapeHtml(value) {
        return value.replace(/[&<>"']/g, (char) => ({
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#039;",
        })[char]);
    }

    function highlightJson(raw) {
        const pretty = prettyJson(raw);
        return escapeHtml(pretty)
            .replace(/(&quot;[^&]*?&quot;)(\s*:)/g, '<span class="json-key">$1</span>$2')
            .replace(/: (&quot;.*?&quot;)/g, ': <span class="json-string">$1</span>')
            .replace(/: (-?\d+(?:\.\d+)?)/g, ': <span class="json-number">$1</span>')
            .replace(/: (true|false)/g, ': <span class="json-bool">$1</span>')
            .replace(/: (null)/g, ': <span class="json-null">$1</span>');
    }

    function prettyJson(raw) {
        const parsed = JSON.parse(String(raw || "").trim());
        return JSON.stringify(parsed, null, 2);
    }

    function looksJson(raw) {
        const trimmed = String(raw || "").trim();
        return Boolean(trimmed) && ["{", "["].includes(trimmed[0]);
    }

    function highlightShell(raw) {
        const escaped = escapeHtml(raw);
        return escaped.split("\n").map((line) => {
            if (/^\s*#/.test(line)) return `<span class="shell-comment">${line}</span>`;
            return line
                .replace(/^(\s*)(\$|#|&gt;)(\s+)/, '$1<span class="shell-prompt">$2</span>$3')
                .replace(/\b(sudo|ssh|scp|rsync|docker|kubectl|systemctl|journalctl|curl|python3?|bash|sh|ip|qm|pvesh|openstack|bkc-ssh)\b/g, '<span class="shell-command">$1</span>')
                .replace(/(--[a-zA-Z0-9][\w-]*)/g, '<span class="shell-option">$1</span>')
                .replace(/(&quot;.*?&quot;|'.*?')/g, '<span class="shell-string">$1</span>');
        }).join("\n");
    }

    function polishJsonPreviews() {
        Array.from(document.querySelectorAll(".json-preview")).slice(0, MAX_POLISH_BLOCKS).forEach((pre) => {
            if (pre.dataset.polished === "true") return;
            const raw = pre.textContent || "";
            if (!looksJson(raw)) return;
            try { pre.innerHTML = highlightJson(raw); } catch (err) { return; }
            pre.dataset.polished = "true";
        });
    }

    function polishTerminalLogs() {
        Array.from(document.querySelectorAll(".terminal-log")).slice(0, MAX_POLISH_BLOCKS).forEach((pre) => {
            if (pre.dataset.polished === "true") return;
            const raw = pre.textContent || "";
            if (!raw.trim()) return;
            if (looksJson(raw)) {
                try {
                    pre.classList.add("json-preview");
                    pre.innerHTML = highlightJson(raw);
                    pre.dataset.polished = "true";
                    return;
                } catch (err) {
                    // Fall back to shell-ish highlighting.
                }
            }
            pre.innerHTML = highlightShell(raw);
            pre.dataset.polished = "true";
        });
    }

    function polishTextareaPreviews() {
        const selector = [
            "textarea[name$='_json']",
            "textarea[name*='json']",
            "textarea[readonly]",
        ].join(",");
        Array.from(document.querySelectorAll(selector)).slice(0, MAX_POLISH_BLOCKS).forEach((textarea) => {
            if (textarea.dataset.previewed === "true") return;
            const raw = textarea.value || textarea.textContent || "";
            const isJsonName = /json/i.test(textarea.name || "");
            const isReadonly = textarea.hasAttribute("readonly");
            const isJson = looksJson(raw);
            if (!isJsonName && !isReadonly) return;
            if (!isJson && !isReadonly) return;

            const details = document.createElement("details");
            details.className = "code-preview-panel";
            if (isJson) details.open = true;
            const summary = document.createElement("summary");
            summary.textContent = isJson ? "Pretty JSON preview" : "Rendered text preview";
            const pre = document.createElement("pre");
            pre.className = isJson ? "json-preview compact-code-preview" : "terminal-log compact-terminal compact-code-preview";
            if (isJson) {
                try {
                    pre.innerHTML = highlightJson(raw);
                    pre.dataset.polished = "true";
                } catch (err) {
                    pre.textContent = raw;
                }
            } else {
                pre.innerHTML = highlightShell(raw);
                pre.dataset.polished = "true";
            }
            details.append(summary, pre);
            textarea.insertAdjacentElement("afterend", details);
            textarea.dataset.previewed = "true";
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", () => {
            polishJsonPreviews();
            polishTerminalLogs();
            polishTextareaPreviews();
        });
    } else {
        polishJsonPreviews();
        polishTerminalLogs();
        polishTextareaPreviews();
    }
})();
