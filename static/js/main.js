(function () {
  "use strict";

  // Cover image picker: upload as soon as a file is chosen, no separate
  // submit button. The <noscript> fallback button in the template covers
  // the case where JS is unavailable.
  document.querySelectorAll("input.cover-auto-submit").forEach(function (input) {
    input.addEventListener("change", function () {
      if (!input.files || input.files.length === 0) return;
      var form = input.form;
      if (!form) return;
      if (form.requestSubmit) {
        form.requestSubmit();
      } else {
        form.submit();
      }
    });
  });

  // Copy a recorded file path to the clipboard.
  document.querySelectorAll(".copy-path-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var path = btn.getAttribute("data-path");
      if (!path) return;
      var done = function () {
        var original = btn.textContent;
        btn.textContent = "Copied";
        setTimeout(function () { btn.textContent = original; }, 1200);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(path).then(done).catch(function () {
          fallbackCopy(path, done);
        });
      } else {
        fallbackCopy(path, done);
      }
    });
  });

  function fallbackCopy(text, done) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); done(); } catch (e) { /* no-op */ }
    document.body.removeChild(ta);
  }

  // Confirm before any destructive form submit.
  document.querySelectorAll("form.confirm-delete").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      var msg = form.getAttribute("data-confirm") || "Are you sure?";
      if (!window.confirm(msg)) {
        e.preventDefault();
      }
    });
  });

  // Optional drag-to-reorder for the backlog list. Rows are only draggable
  // in the full, unfiltered rank view (the server also rejects a partial
  // order). The up/down buttons are plain form posts and need no JS.
  var list = document.getElementById("backlog-list");
  if (list) {
    var dragEl = null;

    list.querySelectorAll("li[draggable='true']").forEach(function (li) {
      li.addEventListener("dragstart", function () {
        dragEl = li;
        li.classList.add("dragging");
      });
      li.addEventListener("dragend", function () {
        li.classList.remove("dragging");
        persistOrder();
      });
      li.addEventListener("dragover", function (e) {
        e.preventDefault();
        var after = getDragAfterElement(list, e.clientY);
        if (after == null) {
          list.appendChild(dragEl);
        } else {
          list.insertBefore(dragEl, after);
        }
      });
    });

    function getDragAfterElement(container, y) {
      var items = Array.prototype.slice.call(
        container.querySelectorAll("li[draggable='true']:not(.dragging)")
      );
      return items.reduce(function (closest, child) {
        var box = child.getBoundingClientRect();
        var offset = y - box.top - box.height / 2;
        if (offset < 0 && offset > closest.offset) {
          return { offset: offset, element: child };
        } else {
          return closest;
        }
      }, { offset: Number.NEGATIVE_INFINITY, element: null }).element;
    }

    function persistOrder() {
      var ids = Array.prototype.slice
        .call(list.querySelectorAll("li[draggable='true']"))
        .map(function (li) { return li.getAttribute("data-backlog-id"); });
      fetch("/backlog/reorder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ order: ids }),
      }).then(function (resp) {
        if (!resp.ok) {
          window.alert("Couldn't save the new order — reloading the list.");
        }
        // Reload so ranks, the top-10 divider, and highlighting all reflect
        // what the server actually saved.
        window.location.reload();
      }).catch(function () {
        window.alert("Couldn't reach the server — reloading the list.");
        window.location.reload();
      });
    }
  }
})();
