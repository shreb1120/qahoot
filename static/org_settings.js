/**
 * org_settings.js — invite link generation on the Team Settings page.
 */
document.addEventListener("DOMContentLoaded", function () {
  const inviteBtn = document.getElementById("invite-btn");
  const inviteResult = document.getElementById("invite-result");
  const inviteLinkInput = document.getElementById("invite-link");
  const inviteError = document.getElementById("invite-error");
  const copyBtn = document.getElementById("copy-btn");
  const copyStatus = document.getElementById("copy-status");
  const emailInput = document.getElementById("invite-email");

  if (!inviteBtn) return;

  const ALERT_ICON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
    '<path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z"/></svg>';

  function setError(message) {
    if (!inviteError) return;
    inviteError.innerHTML = message ? ALERT_ICON + "<span>" + message + "</span>" : "";
  }

  // The email is only a label for the admin's own reference, but a typo that
  // reaches the invite list is confusing later — catch it before the request.
  function emailProblem(value) {
    if (!value) return "";
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
      return "That doesn't look like an email address. Leave it blank if you just want a link.";
    }
    return "";
  }

  inviteBtn.addEventListener("click", async function () {
    const email = emailInput ? emailInput.value.trim() : "";

    const problem = emailProblem(email);
    if (problem) {
      setError(problem);
      if (emailInput) emailInput.focus();
      return;
    }

    setError("");
    inviteBtn.disabled = true;
    inviteBtn.setAttribute("aria-busy", "true");
    inviteBtn.textContent = "Generating…";

    const body = new FormData();
    if (email) body.append("email", email);

    try {
      const resp = await fetch("/org/invite", {
        method: "POST",
        body: body,
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });

      if (resp.status === 401 || resp.status === 403) {
        setError("Your session expired, or you no longer have admin access. Reload the page and try again.");
        return;
      }
      if (resp.status === 429) {
        setError("Too many invite links generated just now. Wait a minute and try again.");
        return;
      }
      if (!resp.ok) {
        setError("Could not generate an invite link (error " + resp.status + "). Try again.");
        return;
      }

      const data = await resp.json();
      if (!data || !data.invite_url) {
        setError("The server did not return an invite link. Try again.");
        return;
      }

      inviteLinkInput.value = data.invite_url;
      inviteResult.style.display = "block";
      inviteLinkInput.focus();
      inviteLinkInput.select();
    } catch (err) {
      setError("Could not reach the server. Check your connection and try again.");
      console.error(err);
    } finally {
      inviteBtn.disabled = false;
      inviteBtn.removeAttribute("aria-busy");
      inviteBtn.textContent = "Generate invite link";
    }
  });

  if (copyBtn) {
    copyBtn.addEventListener("click", async function () {
      function report(message) {
        copyBtn.textContent = message;
        if (copyStatus) copyStatus.textContent = message;
        setTimeout(function () {
          copyBtn.textContent = "Copy";
          if (copyStatus) copyStatus.textContent = "";
        }, 2500);
      }

      // navigator.clipboard needs a secure context; execCommand is the fallback
      // for plain-HTTP local development, and can itself refuse.
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(inviteLinkInput.value);
          report("Copied!");
          return;
        }
        inviteLinkInput.select();
        if (document.execCommand("copy")) report("Copied!");
        else report("Press Ctrl+C");
      } catch (err) {
        inviteLinkInput.select();
        report("Press Ctrl+C");
      }
    });
  }
});
