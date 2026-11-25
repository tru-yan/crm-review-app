// Clickable queue rows, one submit per page, and the confirmation dialog before a write.
// Every page works without this file: the server then shows its own confirmation page instead.
(function () {
  "use strict";
  var form = document.getElementById("decide");
  var dialog = form && form.dataset.confirm ? document.getElementById(form.dataset.confirm) : null;
  var sent = false;

  // A page restored from the back/forward cache keeps its old state (buttons disabled, perhaps a stale status):
  // load it fresh instead.
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) { window.location.reload(); }
  });

  document.querySelectorAll('form[method="post"]').forEach(function (f) {
    f.addEventListener("submit", function (event) {
      if (sent) {
        event.preventDefault();  // one decision per page load
        return;
      }
      var approving = f === form && (!event.submitter || event.submitter.id === "approve");
      if (approving && dialog && form.elements.confirmed.value !== "yes") {
        event.preventDefault();  // ask first; the dialog's button submits again with confirmed=yes
        dialog.showModal();
        return;
      }
      sent = true;
      setTimeout(function () {
        f.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
      }, 0);
    });
  });

  if (dialog) {
    dialog.querySelector("[data-close]").addEventListener("click", function () { dialog.close(); });
    dialog.querySelector("[data-confirm-yes]").addEventListener("click", function () {
      form.elements.confirmed.value = "yes";
      dialog.close();
      form.requestSubmit(document.getElementById("approve"));
      form.elements.confirmed.value = "";  // the form data is already built; leave nothing for Back to restore
    });
  }

  document.querySelectorAll("tr[data-href]").forEach(function (row) {
    row.addEventListener("click", function (event) {
      if (!event.target.closest("a, button, input, select")) { window.location.href = row.dataset.href; }
    });
  });
})();
