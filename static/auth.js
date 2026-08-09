/**
 * auth.js — Clerk JS component mounting for login and signup pages.
 *
 * The Clerk script tag (loaded in base.html) exposes window.Clerk after it
 * initialises.  We wait for the 'load' event, call Clerk.load(), then mount
 * whichever component the page requests via [data-mode] on #clerk-component.
 *
 * Supported data-mode values: "sign-in", "sign-up"
 */
window.addEventListener("load", async function () {
  const container = document.getElementById("clerk-component");
  if (!container || !window.Clerk) return;

  // Where the visitor was actually heading, validated server-side by
  // safe_next() before it ever reached this attribute. Without it, a token that
  // was not ready turns every deep link into a trip to the dashboard.
  const next = container.dataset.next || "";
  await window.Clerk.load({
    afterSignInUrl: next || "/",
    afterSignUpUrl: "/org/setup",
    afterSignOutUrl: "/auth/login",
  });

  const mode = container.dataset.mode;
  if (mode === "sign-in") {
    window.Clerk.mountSignIn(container);
  } else if (mode === "sign-up") {
    window.Clerk.mountSignUp(container);
  }
});
