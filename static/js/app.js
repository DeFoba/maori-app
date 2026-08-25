let isRegisterMode = false;
let CHAT_ID = "general";
let pollTimer = null;

let oldestMessageId = null;
let newestMessageId = null;
let isLoadingHistory = false;
let hasMoreHistory = true;

const authModal = document.getElementById("authModal");
const msgArea = document.getElementById("messagesArea");

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
  startChat();
}

function closeChat() {
  document.body.classList.remove("in-chat");
}

function renderMessageElement(msg) {
  const myUser = localStorage.getItem("tg_username");
  const isMe = msg.sender_id === myUser;
  const bubble = document.createElement("div");
  bubble.id = `msg-${msg.id}`;
  bubble.className = `msg-bubble ${isMe ? "msg-outgoing" : "msg-incoming"}`;
  bubble.innerHTML = `
    <span class="msg-sender">${escapeHtml(msg.sender_id)}</span>
    <span>${escapeHtml(msg.text)}</span>
    <span class="msg-time">${msg.created_at.slice(11, 16)}</span>
  `;
  return bubble;
}

// 1. Первичная загрузка чата
async function startChat() {
  msgArea.innerHTML = "";
  oldestMessageId = null;
  newestMessageId = null;
  hasMoreHistory = true;
  isLoadingHistory = false;

  const token = localStorage.getItem("tg_token");
  if (!token) return;

  try {
    const res = await fetch(`/api/v1/messages/${CHAT_ID}?limit=30`, {
      headers: { "Authorization": `Bearer ${token}` }
    });
    if (res.status === 401) return logout();
    if (!res.ok) return;

    const messages = await res.json();
    if (messages.length > 0) {
      oldestMessageId = messages[0].id;
      newestMessageId = messages[messages.length - 1].id;
      
      const fragment = document.createDocumentFragment();
      messages.forEach(msg => fragment.appendChild(renderMessageElement(msg)));
      msgArea.appendChild(fragment);
      msgArea.scrollTop = msgArea.scrollHeight;
    }
  } catch (err) {
    console.error(err);
  }

  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollNewMessages, 1500);
}

// 2. Подгрузка старых сообщений при скролле вверх
async function loadOlderMessages() {
  if (isLoadingHistory || !hasMoreHistory || !oldestMessageId) return;
  isLoadingHistory = true;

  const token = localStorage.getItem("tg_token");
  try {
    const res = await fetch(`/api/v1/messages/${CHAT_ID}?limit=30&before_id=${oldestMessageId}`, {
      headers: { "Authorization": `Bearer ${token}` }
    });
    if (!res.ok) return;

    const olderMessages = await res.json();
    if (olderMessages.length === 0) {
      hasMoreHistory = false;
      return;
    }

    oldestMessageId = olderMessages[0].id;

    // Сохраняем положение скролла, чтобы экран не дёргался
    const previousHeight = msgArea.scrollHeight;
    const fragment = document.createDocumentFragment();
    
    olderMessages.forEach(msg => fragment.appendChild(renderMessageElement(msg)));
    msgArea.prepend(fragment);

    // Восстанавливаем позицию скролла
    msgArea.scrollTop = msgArea.scrollHeight - previousHeight;
  } catch (err) {
    console.error("Ошибка загрузки истории:", err);
  } finally {
    isLoadingHistory = false;
  }
}

// 3. Получение только новых сообщений без перезагрузки всего DOM
async function pollNewMessages() {
  const token = localStorage.getItem("tg_token");
  if (!token || !newestMessageId) return;

  try {
    const res = await fetch(`/api/v1/messages/${CHAT_ID}?after_id=${newestMessageId}`, {
      headers: { "Authorization": `Bearer ${token}` }
    });
    if (res.status === 401) return logout();
    if (!res.ok) return;

    const newMessages = await res.json();
    if (newMessages.length > 0) {
      const isNearBottom = msgArea.scrollTop + msgArea.clientHeight >= msgArea.scrollHeight - 80;
      
      newMessages.forEach(msg => {
        if (!document.getElementById(`msg-${msg.id}`)) {
          msgArea.appendChild(renderMessageElement(msg));
          newestMessageId = msg.id;
        }
      });

      if (isNearBottom) {
        msgArea.scrollTop = msgArea.scrollHeight;
      }
    }
  } catch (err) {
    console.error(err);
  }
}

// Событие скролла для подгрузки истории
msgArea.addEventListener("scroll", () => {
  if (msgArea.scrollTop <= 40) {
    loadOlderMessages();
  }
});

// Отправка сообщений
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
      await pollNewMessages();
      msgArea.scrollTop = msgArea.scrollHeight;
    }
  } catch (err) {
    alert("Не удалось отправить: " + err.message);
  }
}

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, m => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
  }[m]));
}

init();
