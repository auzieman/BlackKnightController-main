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
            if (isJsonName) textarea.classList.add("textarea-with-preview");
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

    function isLongStructuredValue(raw) {
        const value = String(raw || "").trim();
        if (value.length < 80) return false;
        return looksJson(value) || value.includes("', '") || value.includes('", "') || /[{[]['"]?\w/.test(value);
    }

    function normalizePythonishValue(raw) {
        return String(raw || "")
            .trim()
            .replace(/\bNone\b/g, "null")
            .replace(/\bTrue\b/g, "true")
            .replace(/\bFalse\b/g, "false")
            .replace(/'/g, '"');
    }

    function renderStructuredValue(raw) {
        const value = String(raw || "").trim();
        if (looksJson(value)) {
            try { return highlightJson(value); } catch (err) { /* fall through */ }
        }
        try { return highlightJson(normalizePythonishValue(value)); } catch (err) { /* fall through */ }
        return highlightShell(value);
    }

    function polishInlineStructuredValues() {
        const selector = ".fact-list dd, .ops-fact-list dd, td";
        Array.from(document.querySelectorAll(selector)).slice(0, MAX_POLISH_BLOCKS * 3).forEach((node) => {
            if (node.dataset.polished === "true") return;
            if (node.querySelector("a, button, input, select, textarea, pre, code")) return;
            const raw = node.textContent || "";
            if (!isLongStructuredValue(raw)) return;
            const details = document.createElement("details");
            details.className = "inline-code-preview";
            const summary = document.createElement("summary");
            summary.textContent = "Structured value";
            const pre = document.createElement("pre");
            pre.className = "json-preview compact-code-preview";
            pre.innerHTML = renderStructuredValue(raw);
            pre.dataset.polished = "true";
            details.append(summary, pre);
            node.textContent = "";
            node.appendChild(details);
            node.dataset.polished = "true";
        });
    }

    function codeBlockLabel(pre) {
        if (pre.classList.contains("json-preview")) return "View formatted JSON";
        if (pre.classList.contains("terminal-log")) return "View formatted command/log";
        return "View formatted text";
    }

    function shouldCollapseCodeBlock(pre) {
        if (pre.closest("details")) return false;
        if (pre.closest(".beta-cy, .node-popover")) return false;
        const raw = pre.textContent || "";
        if (raw.trim().length < 260) return false;
        if (pre.closest("td, dd, .detail-grid, .inspector, .run-stage-card, .pipeline-stage-card")) return true;
        return pre.classList.contains("compact-terminal") || pre.classList.contains("compact-code-preview");
    }

    function collapseCrowdedCodeBlocks() {
        const selector = "pre.json-preview, pre.terminal-log";
        Array.from(document.querySelectorAll(selector)).slice(0, MAX_POLISH_BLOCKS).forEach((pre) => {
            if (pre.dataset.collapsible === "true") return;
            if (!shouldCollapseCodeBlock(pre)) return;
            const details = document.createElement("details");
            details.className = "code-preview-panel collapsed-code-panel";
            const summary = document.createElement("summary");
            summary.textContent = codeBlockLabel(pre);
            pre.classList.add("compact-code-preview");
            pre.parentNode.insertBefore(details, pre);
            details.append(summary, pre);
            pre.dataset.collapsible = "true";
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", () => {
            polishJsonPreviews();
            polishTerminalLogs();
            polishTextareaPreviews();
            polishInlineStructuredValues();
            collapseCrowdedCodeBlocks();
        });
    } else {
        polishJsonPreviews();
        polishTerminalLogs();
        polishTextareaPreviews();
        polishInlineStructuredValues();
        collapseCrowdedCodeBlocks();
    }
})();
