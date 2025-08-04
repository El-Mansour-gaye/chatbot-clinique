// Configuration
const API_ENDPOINT = '/api/chat';
const TYPING_DELAY = 1000;
const REASONING_DISPLAY_DURATION = 1800; // Durée d'affichage de la réflexion en ms

// Éléments DOM
const chatbox = document.getElementById('chatbox');
const userInput = document.getElementById('userInput');
const sendBtn = document.getElementById('sendBtn');
const startBtn = document.getElementById('startBtn');
const chatCloseBtn = document.getElementById('chatCloseBtn');
const chatWidgetContainer = document.getElementById('chatWidgetContainer');
const chatTeaser = document.getElementById('chatTeaser');

// État du chat
let isTyping = false;
let history = [];
let sessionId = null;

// Fonctions utilitaires
function getCurrentTime() {
  return new Date().toLocaleTimeString('fr-FR', { 
    hour: '2-digit', 
    minute: '2-digit' 
  });
}

function formatMarkdown(text) {
  // Liens cliquables [texte](url)
  text = text.replace(/\[(.*?)\]\((https?:\/\/[^\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  // Gras **texte**
  text = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  // Italique *texte*
  text = text.replace(/\*(.*?)\*/g, '<em>$1</em>');
  // Retours à la ligne
  text = text.replace(/\n/g, '<br>');
  return text;
}

function createMessageElement(content, isUser = false) {
  const messageDiv = document.createElement('div');
  messageDiv.className = `message ${isUser ? 'user-message' : 'bot-message'}`;
  
  const messageContent = document.createElement('div');
  messageContent.className = 'message-content';
  
  if (isUser) {
    const formattedContent = formatMarkdown(content);
    messageContent.innerHTML = formattedContent;
    messageDiv.appendChild(messageContent);
    const messageTime = document.createElement('div');
    messageTime.className = 'message-time';
    messageTime.textContent = getCurrentTime();
    messageDiv.appendChild(messageTime);
    return messageDiv;
  }

  // Affichage standard du message du bot (plus de réflexion séparée)
  const formattedContent = formatMarkdown(content);
  messageContent.innerHTML = formattedContent;
  messageDiv.appendChild(messageContent);
  
  const messageTime = document.createElement('div');
  messageTime.className = 'message-time';
  messageTime.textContent = getCurrentTime();
  messageDiv.appendChild(messageTime);
  return messageDiv;
}

function showTypingIndicator() {
  const indicator = document.createElement('div');
  indicator.className = 'typing-indicator';
  indicator.innerHTML = `
    <div class="typing-dot"></div>
    <div class="typing-dot"></div>
    <div class="typing-dot"></div>
  `;
  chatbox.appendChild(indicator);
  chatbox.scrollTop = chatbox.scrollHeight;
  return indicator;
}

function removeTypingIndicator(indicator) {
  if (indicator && indicator.parentNode) {
    indicator.parentNode.removeChild(indicator);
  }
}

// Gestion des messages
async function sendMessage() {
  const message = userInput.value.trim();
  if (!message || isTyping) return;
  
  history.push({ role: 'user', content: message });
  const userMessage = createMessageElement(message, true);
  chatbox.appendChild(userMessage);
  chatbox.scrollTop = chatbox.scrollHeight;
  
  userInput.value = '';
  userInput.style.height = 'auto';
  isTyping = true;
  sendBtn.disabled = true;
  userInput.disabled = true;
  
  const typingIndicator = showTypingIndicator();
  
  try {
    const response = await fetch(API_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 
        history: history,
        session_id: sessionId
      }),
    });
    
    if (!response.ok) {
      const errorData = await response.json().catch(() => null);
      const errorMessage = errorData ? errorData.response : 'Erreur réseau';
      throw new Error(errorMessage);
    }
    
    const data = await response.json();
    
    if (data && data.response) {
      history.push({ role: 'assistant', content: data.response });
      await new Promise(resolve => setTimeout(resolve, TYPING_DELAY));
      const botMessage = createMessageElement(data.response);
      chatbox.appendChild(botMessage);

      if (data.response.includes("Votre demande est en cours de traitement")) {
        // Extraire l'email utilisateur depuis l'historique ou le dernier message utilisateur
        // Exemple naïf :
        let email = null;
        for (let i = history.length - 1; i >= 0; i--) {
          const match = history[i].content && history[i].content.match(/[\w\.-]+@[\w\.-]+\.\w+/);
          if (match) { email = match[0]; break; }
        }
        if (email) checkTicketStatus(email);
      }

    } else {
      throw new Error("La réponse du serveur est mal formée.");
    }

  } catch (error) {
    console.error('Erreur:', error);
    const errorMessage = createMessageElement(
      `Désolé, une erreur est survenue : ${error.message}`
    );
    chatbox.appendChild(errorMessage);
  } finally {
    removeTypingIndicator(typingIndicator);
    chatbox.scrollTop = chatbox.scrollHeight;
    isTyping = false;
    sendBtn.disabled = false;
    userInput.disabled = false;
    userInput.focus();
  }
}

// Gestion des événements
sendBtn.addEventListener('click', sendMessage);

userInput.addEventListener('keypress', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// Gestion de la croix de fermeture
if (chatCloseBtn) {
  chatCloseBtn.addEventListener('click', closeWidget);
}

startBtn.addEventListener('click', () => {
  chatbox.innerHTML = '';
  userInput.value = '';
  userInput.style.height = 'auto';
  history = [];
  
  sessionId = `web-session-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
  console.log(`Nouvelle conversation démarrée avec l'ID de session : ${sessionId}`);
  
  const welcomeText = 'Bonjour ! Je suis l\'assistant virtuel de la Clinique Dentaire St Dominique. Comment puis-je vous aider aujourd\'hui ? 🦷';
  const welcomeMessage = createMessageElement(welcomeText);
  chatbox.appendChild(welcomeMessage);
  history.push({ role: 'assistant', content: welcomeText });
});

// Fonction pour ajouter le bouton de redimensionnement
function addResizeHandle() {
  const chatContainer = document.querySelector('.chat-container');
  if (!chatContainer) return;
  
  const resizeHandle = document.createElement('button');
  resizeHandle.className = 'resize-handle';
  resizeHandle.title = 'Redimensionner';
  chatContainer.appendChild(resizeHandle);
  
  let isResizing = false;
  let startX, startY, startWidth, startHeight;
  
  resizeHandle.addEventListener('mousedown', (e) => {
    isResizing = true;
    startX = e.clientX;
    startY = e.clientY;
    startWidth = chatContainer.offsetWidth;
    startHeight = chatContainer.offsetHeight;
    
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    e.preventDefault();
  });
  
  function handleMouseMove(e) {
    if (!isResizing) return;
    
    const deltaX = startX - e.clientX;
    const deltaY = startY - e.clientY;
    
    const newWidth = Math.max(300, Math.min(window.innerWidth * 0.9, startWidth + deltaX));
    const newHeight = Math.max(400, Math.min(window.innerHeight * 0.9, startHeight + deltaY));
    
    chatContainer.style.width = newWidth + 'px';
    chatContainer.style.height = newHeight + 'px';
  }
  
  function handleMouseUp() {
    isResizing = false;
    document.removeEventListener('mousemove', handleMouseMove);
    document.removeEventListener('mouseup', handleMouseUp);
  }
}

// Auto-grow du textarea
userInput.addEventListener('input', function() {
  this.style.height = 'auto';
  const maxRows = 3;
  const lineHeight = parseInt(window.getComputedStyle(this).lineHeight) || 22;
  const maxHeight = lineHeight * maxRows;
  this.style.height = Math.min(this.scrollHeight, maxHeight) + 'px';
});

// Initialisation
window.addEventListener('load', () => {
  startBtn.click();
  addResizeHandle();
  if (chatTeaser) chatTeaser.style.display = 'none';
  if (chatWidgetContainer) chatWidgetContainer.style.display = 'flex';
});

// Fonction pour fermer le widget
function closeWidget() {
  if (chatWidgetContainer) {
    chatWidgetContainer.style.display = 'none';
  }
  if (chatTeaser) {
    chatTeaser.style.display = 'flex';
  }
}

// Fonction pour ouvrir le widget
function openWidget() {
  if (chatWidgetContainer) {
    chatWidgetContainer.style.display = 'flex';
  }
  if (chatTeaser) {
    chatTeaser.style.display = 'none';
  }
}

// Gestion du bouton teaser flottant
if (chatTeaser) {
  chatTeaser.addEventListener('click', openWidget);
}

// Ajout du bouton Envoyer à côté de Nouvelle conversation
const inputContainer = document.querySelector('.input-container');
if (inputContainer) {
  // On cherche le bouton Nouvelle conversation existant
  const newConvBtn = document.getElementById('startBtn');
  const sendBtn = document.getElementById('sendBtn');
  if (newConvBtn && sendBtn) {
    // Création du conteneur flex pour les deux boutons
    const btnRow = document.createElement('div');
    btnRow.style.display = 'flex';
    btnRow.style.gap = '0.5rem';
    btnRow.style.marginTop = '0.3rem';
    btnRow.style.justifyContent = 'space-between';
    btnRow.style.width = '100%';

    // On déplace le bouton Envoyer et Nouvelle conversation dans le conteneur
    btnRow.appendChild(sendBtn);
    btnRow.appendChild(newConvBtn);

    // On insère le conteneur juste après la zone d'input
    inputContainer.appendChild(btnRow);
  }
  // Suppression du bouton Accueil s'il existe
  const homeBtn = Array.from(inputContainer.querySelectorAll('button')).find(b => b.textContent.includes('Accueil'));
  if (homeBtn) {
    homeBtn.remove();
  }
}

// Ajout de la vérification du ticket après confirmation
function checkTicketStatus(email, retryCount = 0) {
  setTimeout(() => {
    fetch(`/api/check_ticket?email=${encodeURIComponent(email)}`)
      .then(res => res.json())
      .then(data => {
        if (data.status === "success" && data.found) {
          // Affiche le résultat dans la chatbox
          const confirmMsg = `✅ Ticket confirmé !<br>- ID : <strong>${data.ticket_id}</strong><br>- Soin : <strong>${data.service_type}</strong><br>- Date : <strong>${data.date}</strong> à <strong>${data.time}</strong>`;
          const botMessage = createMessageElement(confirmMsg, false);
          chatbox.appendChild(botMessage);
          chatbox.scrollTop = chatbox.scrollHeight;
        } else {
          // Affiche un message d'attente et un bouton pour relancer
          const waitMsg = document.createElement('div');
          waitMsg.className = 'message bot-message';
          waitMsg.innerHTML = `<div class="message-content">⏳ Ticket encore en cours...<br>Réessayez dans quelques secondes.</div>`;
          if (retryCount < 3) {
            const retryBtn = document.createElement('button');
            retryBtn.textContent = 'Re-vérifier';
            retryBtn.style.marginTop = '8px';
            retryBtn.onclick = function() {
              waitMsg.remove();
              checkTicketStatus(email, retryCount + 1);
            };
            waitMsg.appendChild(retryBtn);
          }
          chatbox.appendChild(waitMsg);
          chatbox.scrollTop = chatbox.scrollHeight;
        }
      })
      .catch(err => {
        console.error("Erreur lors du check ticket :", err);
        const errorMsg = createMessageElement("❌ Erreur lors de la vérification du ticket.", false);
        chatbox.appendChild(errorMsg);
        chatbox.scrollTop = chatbox.scrollHeight;
      });
  }, retryCount === 0 ? 7000 : 4000); // 7s la première fois, 4s ensuite
}
