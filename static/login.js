// SkullMaster iQ — login / first-run password setup
const $ = (sel) => document.querySelector(sel);

// Match the app's theme preference so the two screens feel like one product.
document.documentElement.dataset.theme =
  localStorage.getItem("skullmaster-theme") || "dark";

let setupMode = false;
let submitting = false;
let minLength = 8;

function showError(message) {
  const el = $("#login-error");
  el.textContent = message;
  el.hidden = !message;
}

function applyLoginMode() {
  setupMode = false;
  $("#login-heading").textContent = "Sign in";
  $("#login-sub").textContent = "Enter your password to open your notebooks.";
  $("#password").setAttribute("autocomplete", "current-password");
  $("#confirm-field").hidden = true;
  $("#confirm").value = "";
  $("#login-submit").textContent = "Sign in";
  $("#login-alt").textContent = "Create account";
  $("#login-note").textContent =
    "Your password is stored only on this machine as a salted hash, and never leaves it.";
  showError("");
  $("#password").focus();
}

function applySetupMode(length = minLength) {
  minLength = length;
  setupMode = true;
  $("#login-heading").textContent = "Create your password";
  $("#login-sub").textContent =
    `Choose a password to protect your notebooks (at least ${minLength} characters).`;
  $("#password").setAttribute("autocomplete", "new-password");
  $("#confirm-field").hidden = false;
  $("#login-submit").textContent = "Create account";
  $("#login-alt").textContent = "Return to logon";
  $("#login-note").textContent =
    "There is no default password and no recovery: this is stored only on this machine, as a salted hash.";
  showError("");
  $("#password").focus();
}

async function init() {
  try {
    const res = await fetch("/api/auth/status");
    const status = await res.json();
    if (status.authenticated) {
      window.location.replace("/");
      return;
    }
    minLength = status.min_password_length || minLength;
    // First run goes straight to account creation; otherwise sign in first.
    if (status.setup_required) applySetupMode(minLength);
    else applyLoginMode();
  } catch {
    showError("Cannot reach the SkullMaster iQ server. Is it still running?");
  }
}

// Toggle between "Sign in" and "Create account" (and back to logon).
$("#login-alt").addEventListener("click", () => {
  if (submitting) return;
  if (setupMode) applyLoginMode();
  else applySetupMode(minLength);
});

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (submitting) return;

  const password = $("#password").value;
  if (!password) return showError("Enter your password.");
  if (setupMode && password !== $("#confirm").value) {
    return showError("The two passwords don't match.");
  }

  submitting = true;
  const btn = $("#login-submit");
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = setupMode ? "Creating…" : "Signing in…";
  showError("");

  try {
    const res = await fetch(setupMode ? "/api/auth/setup" : "/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (!res.ok) {
      const detail = (await res.json().catch(() => ({}))).detail || res.statusText;
      throw new Error(detail);
    }
    window.location.replace("/");
    return;
  } catch (err) {
    showError(err.message);
    $("#password").value = "";
    if (setupMode) $("#confirm").value = "";
    $("#password").focus();
  } finally {
    submitting = false;
    btn.disabled = false;
    btn.textContent = label;
  }
});

init();
