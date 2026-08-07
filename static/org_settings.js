/**
 * org_settings.js — invite link generation on the Team Settings page.
 */
document.addEventListener("DOMContentLoaded", function () {
  const inviteBtn = document.getElementById("invite-btn");
  const inviteResult = document.getElementById("invite-result");
  const inviteLinkInput = document.getElementById("invite-link");
  const copyBtn = document.getElementById("copy-btn");
  const emailInput = document.getElementById("invite-email");

  if (!inviteBtn) return;

  inviteBtn.addEventListener("click", async function () {
    inviteBtn.disabled = true;
    inviteBtn.textContent = "Generating…";

    const body = new FormData();
    if (emailInput.value.trim()) {
      body.append("email", emailInput.value.trim());
    }

    try {
      const resp = await fetch("/org/invite", {
        method: "POST",
        body: body,
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });

      if (!resp.ok) {
        throw new Error("Server returned " + resp.status);
      }

      const data = await resp.json();
      inviteLinkInput.value = data.invite_url;
      inviteResult.style.display = "block";
    } catch (err) {
      alert("Failed to generate invite link. Please try again.");
      console.error(err);
    } finally {
      inviteBtn.disabled = false;
      inviteBtn.textContent = "Generate invite link";
    }
  });

  if (copyBtn) {
    copyBtn.addEventListener("click", function () {
      inviteLinkInput.select();
      document.execCommand("copy");
      copyBtn.textContent = "Copied!";
      setTimeout(function () {
        copyBtn.textContent = "Copy";
      }, 2000);
    });
  }
});
