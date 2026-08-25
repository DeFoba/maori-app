let isRegisterMode = false;
let CHAT_ID = "general";
let pollTimer = null;

const authModal = document.getElementById("authModal");
const msgArea = document.getElementById("messagesArea");

// Проверка сохраненной сессии
function init() {
  const token = localStorage.getItem("tg_token");
  const user = localStorage.getItem("tg_username");
  
  if (token && user) {
    authModal.style.display = "none";
    document.getElementById("myUsernameDisplay").textContent = `@${user}`;
    startChat();
  } else {
    authModal.style.display = "flex";
  }
}

function toggleAuthMode() {
  isRegisterMode = !isRegisterMode;
  document.getElementById("authTitle").textContent = isRegisterMode ? "Регистрация" : "Вход в Telegram";
  document.getElementById("authSubmitBtn").textContent = isRegisterMode ? "Создать аккаунт" : "Войти";
  document.getElementById("authSwitch").textContent = isRegisterMode ? "Уже есть аккаунт? Войти" : "Нет аккаунта? Зарегистрироваться";
}

async function handleAuth() {
  const u = document.getElementById("authUsername").value.trim();
  const p = document.getElementById("authPassword").value.trim();
  if (!u || !p) return alert("Заполните логин и пароль");

  const endpoint = isRegisterMode ? "/api/v1/auth/register" : "/api/v1/auth/login";
  try {
    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: u, password: p })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Ошибка авторизации");

    // Сохраняем сессию
    localStorage.setItem("tg_token", data.token);
    localStorage.setItem("tg_username", data.username);
    
    authModal.style.display = "none";
    document.getElementById("myUsernameDisplay").textContent = `@${data.username}`;
    startChat();
  } catch (err) {
    alert(err.message);
  }
}

function logout() {
  localStorage.removeItem("tg_token");
  localStorage.removeItem("tg_username");
  clearInterval(pollTimer);
  location.reload();
}

function openChat(id) {
  CHAT_ID = id;
  document.body.classList.add("in-chat");
  loadMessages();
}

function closeChat() {
  document.body.classList.remove("in-chat");
}

function startChat() {
  loadMessages();
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(loadMessages, 1500);
}

async function loadMessages() {
  const token = localStorage.getItem("tg_token");
  const myUser = localStorage.getItem("tg_username");
  if (!token) return;

  try {
    const res = await fetch(`/api/v1/messages/${CHAT_ID}`, {
      headers: { "Authorization": `Bearer ${token}` }
    });
    if (res.status === 401) return logout();
    if (!res.ok) return;

    const messages = await res.json();
    const shouldScroll = msgArea.scrollTop + msgArea.clientHeight >= msgArea.scrollHeight - 60;
    
    msgArea.innerHTML = "";
    messages.forEach(msg => {
      const isMe = msg.sender_id === myUser;
      const bubble = document.createElement("div");
      bubble.className = `msg-bubble ${isMe ? "msg-outgoing" : "msg-incoming"}`;
      bubble.innerHTML = `
        <span class="msg-sender">${escapeHtml(msg.sender_id)}</span>
        <span>${escapeHtml(msg.text)}</span>
        <span class="msg-time">${msg.created_at.slice(11, 16)}</span>
      `;
      msgArea.appendChild(bubble);
    });

    if (shouldScroll) {
      msgArea.scrollTop = msgArea.scrollHeight;
    }
  } catch (err) {
    console.error(err);
  }
}

async function sendMsg() {
  const input = document.getElementById("msgInput");
  const text = input.value.trim();
  const token = localStorage.getItem("tg_token");
  if (!text || !token) return;

  input.value = "";
  try {
    const res = await fetch("/api/v1/messages/send", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${token}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ chat_id: CHAT_ID, text: text })
    });
    if (res.ok) {
      await loadMessages();
      msgArea.scrollTop = msgArea.scrollHeight;
    }
  } catch (err) {
    alert("Не удалось отправить сообщение");
  }
}

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, m => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
  }[m]));
}

init();
