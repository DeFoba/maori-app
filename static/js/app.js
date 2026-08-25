let isRegisterMode = false;
let CHAT_ID = "general";
let pollTimer = null;

let oldestMessageId = null;
let newestMessageId = null;
let isLoadingHistory = false;
let hasMoreHistory = true;

let selectedColor = "#5288c1";
let selectedAvatarFile = null;

const authModal = document.getElementById("authModal");
const editProfileModal = document.getElementById("editProfileModal");
const viewUserModal = document.getElementById("viewUserModal");
const msgArea = document.getElementById("messagesArea");

async function init() {
  const token = localStorage.getItem("tg_token");
  if (token) {
    authModal.style.display = "none";
    await loadMyProfile();
    startChat();
  } else {
    authModal.style.display = "flex";
  }
}

async function loadMyProfile() {
  const token = localStorage.getItem("tg_token");
  try {
    const res = await fetch("/api/v1/users/me", {
      headers: { "Authorization": `Bearer ${token}` }
    });
    if (res.status === 401) return logout();
    const user = await res.json();
    
    document.getElementById("myDisplayNameDisplay").textContent = user.display_name;
    document.getElementById("editUsername").value = `@${user.username}`;
    document.getElementById("editDisplayName").value = user.display_name;
    selectedColor = user.name_color || "#5288c1";
    
    // Подсветка цвета
    document.querySelectorAll(".color-circle").forEach(el => {
      el.classList.toggle("active", el.dataset.color === selectedColor);
    });

    // Аватарка в хедере
    const headerImg = document.getElementById("myHeaderAvatarImg");
    const headerPlaceholder = document.getElementById("myHeaderAvatarPlaceholder");
    if (user.avatar_url) {
      headerImg.src = user.avatar_url;
      headerImg.style.display = "block";
      headerPlaceholder.style.display = "none";
    } else {
      headerImg.style.display = "none";
      headerPlaceholder.style.display = "flex";
      headerPlaceholder.textContent = (user.display_name || user.username)[0].toUpperCase();
    }
  } catch (err) {
    console.error(err);
  }
}

function openEditProfile() {
  editProfileModal.style.display = "flex";
  const headerImg = document.getElementById("myHeaderAvatarImg");
  const modalImg = document.getElementById("myProfileAvatarImg");
  const modalPlaceholder = document.getElementById("myProfileAvatarPlaceholder");

  if (headerImg.style.display === "block") {
    modalImg.src = headerImg.src;
    modalImg.style.display = "block";
    modalPlaceholder.style.display = "none";
  } else {
    modalImg.style.display = "none";
    modalPlaceholder.style.display = "flex";
    modalPlaceholder.textContent = document.getElementById("myDisplayNameDisplay").textContent[0].toUpperCase();
  }
}

function closeEditProfile() {
  editProfileModal.style.display = "none";
}

function selectColor(elem) {
  document.querySelectorAll(".color-circle").forEach(el => el.classList.remove("active"));
  elem.classList.add("active");
  selectedColor = elem.dataset.color;
}

function previewAvatar(e) {
  const file = e.target.files[0];
  if (!file) return;
  selectedAvatarFile = file;

  const reader = new FileReader();
  reader.onload = function(evt) {
    const modalImg = document.getElementById("myProfileAvatarImg");
    modalImg.src = evt.target.result;
    modalImg.style.display = "block";
    document.getElementById("myProfileAvatarPlaceholder").style.display = "none";
  };
  reader.readAsDataURL(file);
}

async function saveProfile() {
  const token = localStorage.getItem("tg_token");
  const displayName = document.getElementById("editDisplayName").value.trim();
  if (!displayName) return alert("Введите отображаемое имя");

  const formData = new FormData();
  formData.append("display_name", displayName);
  formData.append("name_color", selectedColor);
  if (selectedAvatarFile) {
    formData.append("avatar", selectedAvatarFile);
  }

  try {
    const res = await fetch("/api/v1/users/update_profile", {
      method: "POST",
      headers: { "Authorization": `Bearer ${token}` },
      body: formData
    });
    if (!res.ok) throw new Error("Не удалось сохранить профиль");
    
    closeEditProfile();
    await loadMyProfile();
    startChat(); // Перерисовать чат с новыми данными
  } catch (err) {
    alert(err.message);
  }
}

async function openUserProfile(username) {
  const token = localStorage.getItem("tg_token");
  try {
    const res = await fetch(`/api/v1/users/${username}`, {
      headers: { "Authorization": `Bearer ${token}` }
    });
    if (!res.ok) return;
    const user = await res.json();

    const avatarImg = document.getElementById("viewUserAvatar");
    const placeholder = document.getElementById("viewUserAvatarPlaceholder");
    if (user.avatar_url) {
      avatarImg.src = user.avatar_url;
      avatarImg.style.display = "block";
      placeholder.style.display = "none";
    } else {
      avatarImg.style.display = "none";
      placeholder.style.display = "flex";
      placeholder.textContent = user.display_name[0].toUpperCase();
      placeholder.style.background = user.name_color || "#4a89dc";
    }

    const titleElem = document.getElementById("viewUserDisplayName");
    titleElem.textContent = user.display_name;
    titleElem.style.color = user.name_color || "#fff";

    document.getElementById("viewUserLogin").textContent = `@${user.username}`;
    viewUserModal.style.display = "flex";
  } catch (err) {
    console.error(err);
  }
}

// --- Сообщения и рендеринг ---
function renderMessageElement(msg) {
  const myUser = localStorage.getItem("tg_username");
  const isMe = msg.sender_id === myUser;
  
  const row = document.createElement("div");
  row.id = `msg-${msg.id}`;
  row.className = `msg-row ${isMe ? "outgoing" : "incoming"}`;

  let avatarHtml = "";
  if (!isMe) {
    if (msg.sender_avatar_url) {
      avatarHtml = `<img src="${msg.sender_avatar_url}" class="avatar-small" onclick="openUserProfile('${msg.sender_id}')" />`;
    } else {
      const letter = (msg.sender_display_name || msg.sender_id)[0].toUpperCase();
      avatarHtml = `<div class="avatar-small" style="background:${msg.sender_name_color};" onclick="openUserProfile('${msg.sender_id}')">${letter}</div>`;
    }
  }

  const bubble = document.createElement("div");
  bubble.className = `msg-bubble ${isMe ? "msg-outgoing" : "msg-incoming"}`;
  bubble.innerHTML = `
    ${!isMe ? `<span class="msg-sender" style="color:${msg.sender_name_color};" onclick="openUserProfile('${msg.sender_id}')">${escapeHtml(msg.sender_display_name)}</span>` : ""}
    <span>${escapeHtml(msg.text)}</span>
    <span class="msg-time">${msg.created_at.slice(11, 16)}</span>
  `;

  if (!isMe) row.innerHTML = avatarHtml;
  row.appendChild(bubble);
  return row;
}

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
    const previousHeight = msgArea.scrollHeight;
    const fragment = document.createDocumentFragment();
    
    olderMessages.forEach(msg => fragment.appendChild(renderMessageElement(msg)));
    msgArea.prepend(fragment);
    msgArea.scrollTop = msgArea.scrollHeight - previousHeight;
  } catch (err) {
    console.error(err);
  } finally {
    isLoadingHistory = false;
  }
}

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

msgArea.addEventListener("scroll", () => {
  if (msgArea.scrollTop <= 40) loadOlderMessages();
});

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
    alert("Не удалось отправить сообщение");
  }
}

// --- Auth Helpers ---
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
    await loadMyProfile();
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

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, m => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
  }[m]));
}

init();
