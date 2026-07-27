/* auth.js -- login & register pages: password visibility toggle and
   live "passwords match" feedback before the form even submits. */

document.querySelectorAll(".password-toggle").forEach((btn) => {
  btn.addEventListener("click", () => {
    const input = document.getElementById(btn.dataset.target);
    if (!input) return;
    const showing = input.type === "text";
    input.type = showing ? "password" : "text";
    btn.textContent = showing ? "Show" : "Hide";
  });
});

const pw = document.getElementById("password");
const confirmPw = document.getElementById("confirm_password");
const matchHint = document.getElementById("confirm-hint");
const form = document.getElementById("register-form");

function checkMatch() {
  if (!confirmPw || !matchHint) return true;
  if (confirmPw.value === "") {
    matchHint.textContent = "";
    matchHint.className = "hint";
    return true;
  }
  if (pw.value === confirmPw.value) {
    matchHint.textContent = "Passwords match";
    matchHint.className = "hint ok";
    return true;
  }
  matchHint.textContent = "Passwords do not match";
  matchHint.className = "hint error";
  return false;
}

pw?.addEventListener("input", checkMatch);
confirmPw?.addEventListener("input", checkMatch);

form?.addEventListener("submit", (e) => {
  if (!checkMatch()) {
    e.preventDefault();
    confirmPw.focus();
    return;
  }
  if (pw.value.length < 6) {
    e.preventDefault();
    const pwHint = document.getElementById("password-hint");
    if (pwHint) { pwHint.textContent = "Password must be at least 6 characters"; pwHint.className = "hint error"; }
    pw.focus();
  }
});

const emailInput = document.getElementById("email");
const emailHint = document.getElementById("email-hint");
emailInput?.addEventListener("blur", () => {
  if (!emailInput.value) return;
  const ok = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(emailInput.value.trim());
  if (emailHint) {
    emailHint.textContent = ok ? "" : "Enter a valid email address";
    emailHint.className = ok ? "hint" : "hint error";
  }
});
