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

  await window.Clerk.load({
    afterSignInUrl: "/",
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
