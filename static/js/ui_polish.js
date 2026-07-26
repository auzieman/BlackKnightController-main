(function () {
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
        return escapeHtml(raw)
            .replace(/(&quot;[^&]*?&quot;)(\s*:)/g, '<span class="json-key">$1</span>$2')
            .replace(/: (&quot;.*?&quot;)/g, ': <span class="json-string">$1</span>')
            .replace(/: (-?\d+(?:\.\d+)?)/g, ': <span class="json-number">$1</span>')
            .replace(/: (true|false)/g, ': <span class="json-bool">$1</span>')
            .replace(/: (null)/g, ': <span class="json-null">$1</span>');
    }

    function polishJsonPreviews() {
        document.querySelectorAll(".json-preview").forEach((pre) => {
            if (pre.dataset.polished === "true") return;
            const raw = pre.textContent || "";
            const trimmed = raw.trim();
            if (!trimmed || !["{", "["].includes(trimmed[0])) return;
            try {
                JSON.parse(trimmed);
            } catch (err) {
                return;
            }
            pre.innerHTML = highlightJson(raw);
            pre.dataset.polished = "true";
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", polishJsonPreviews);
    } else {
        polishJsonPreviews();
    }
})();
