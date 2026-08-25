const AUTH_TOKEN = prompt("Введите API Auth Token:", localStorage.getItem("tg_token") || "") || "";
localStorage.setItem("tg_token", AUTH_TOKEN);

const MY_USER_ID = localStorage.getItem("tg_user") || "user_" + Math.floor(Math.random() * 1000);
localStorage.setItem("tg_user", MY_USER_ID);

const CHAT_ID = "general";
const msgArea = document.getElementById("messagesArea");

async function loadMessages() {
  try {
    const res = await fetch(`/api/v1/messages/${CHAT_ID}`, {
      headers: { "Authorization": `Bearer ${AUTH_TOKEN}` }
    });
    if (!res.ok) throw new Error("Ошибка доступа");
    const messages = await res.json();
    
    msgArea.innerHTML = "";
    messages.forEach(msg => {
      const isMe = msg.sender_id === MY_USER_ID;
      const bubble = document.createElement("div");
      bubble.className = `msg-bubble ${isMe ? "msg-outgoing" : "msg-incoming"}`;
      bubble.innerHTML = `
        <span class="msg-sender">${escapeHtml(msg.sender_id)}</span>
        <span>${escapeHtml(msg.text)}</span>
        <span class="msg-time">${msg.created_at.slice(11, 16)}</span>
      `;
      msgArea.appendChild(bubble);
    });
    msgArea.scrollTop = msgArea.scrollHeight;
  } catch (err) {
    console.error(err);
  }
}

async function sendMsg() {
  const input = document.getElementById("msgInput");
  const text = input.value.trim();
  if (!text) return;

  input.value = "";
  try {
    const res = await fetch("/api/v1/messages/send", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${AUTH_TOKEN}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        sender_id: MY_USER_ID,
        chat_id: CHAT_ID,
        text: text
      })
    });
    if (res.ok) loadMessages();
  } catch (err) {
    alert("Не удалось отправить сообщение");
  }
}

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, m => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
  }[m]));
}

loadMessages();
setInterval(loadMessages, 2000);
