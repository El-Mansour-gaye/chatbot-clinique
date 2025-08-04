import os
import json
import requests
import logging
from flask import Blueprint, request, jsonify
from dotenv import load_dotenv
import traceback
from langchain.memory import ConversationBufferMemory

# Import de la nouvelle architecture (l'agent) et des types de messages
from lead_graph import get_agent_executor, moderate_content
from langchain_core.messages import HumanMessage, AIMessage
print("[WHATSAPP_WEBHOOK_INIT] Successfully imported AGENT components from lead_graph.")

load_dotenv()
whatsapp = Blueprint('whatsapp', __name__)

WHATSAPP_TOKEN = os.getenv('WHATSAPP_TOKEN')
WHATSAPP_PHONE_ID = os.getenv('WHATSAPP_PHONE_ID')
VERIFY_TOKEN = os.getenv('VERIFY_TOKEN')

# Dictionnaire pour stocker l'historique de conversation de chaque utilisateur
# Va maintenant stocker des objets ConversationBufferMemory
user_memories = {}

# Configuration du logging
logger = logging.getLogger(__name__)

print(f"[CONFIG] WhatsApp Phone ID: '{WHATSAPP_PHONE_ID}'")
print(f"[CONFIG] Verify Token: '{VERIFY_TOKEN}'")
print(f"[CONFIG] WhatsApp Token: {'✅ Présent' if WHATSAPP_TOKEN else '❌ Manquant'}")

def format_whatsapp_response(response_text: str) -> str:
    """Formate la réponse pour WhatsApp en gardant la réflexion lisible."""
    # Détecter si le message contient une réflexion structurée
    import re
    reasoning_match = re.search(r'🤔\s*\*\*Ma réflexion\s*:\*\*(.*?)💬\s*\*\*Ma réponse\s*:\*\*(.*)', response_text, re.DOTALL)
    
    if reasoning_match:
        reasoning_text = reasoning_match.group(1).strip()
        response_text_final = reasoning_match.group(2).strip()
        
        # Extraire les étapes de réflexion
        reasoning_steps = []
        for line in reasoning_text.split('\n'):
            line = line.strip()
            if line.startswith('•'):
                reasoning_steps.append(line[1:].strip())
        
        # Construire le message formaté pour WhatsApp
        formatted_message = "🤔 *Ma réflexion :*\n"
        
        if reasoning_steps:
            for i, step in enumerate(reasoning_steps, 1):
                formatted_message += f"{i}. {step}\n"
        else:
            # Si pas d'étapes détectées, afficher le texte brut
            formatted_message += reasoning_text.replace('\n', '\n') + "\n"
        
        formatted_message += f"\n💬 *Ma réponse :*\n{response_text_final}"
        
        return formatted_message
    else:
        # Retourner le message original si pas de formatage spécial
        return response_text

def process_message(message_body: str, phone_number: str) -> str:
    """Traite un message entrant en utilisant l'agent et retourne la réponse."""
    
    # --- 1. Modération du message entrant ---
    if not moderate_content(message_body):
        logger.warning(f"[MODERATION] Message entrant de {phone_number} bloqué : '{message_body}'")
        return "Je ne peux pas répondre à cette demande. Ma mission est de vous assister pour les prises de rendez-vous à la clinique."

    # --- 2. Gestion de la mémoire par numéro de téléphone ---
    if phone_number not in user_memories:
        print(f"[WHATSAPP_PROCESS] Création d'une nouvelle mémoire pour : {phone_number}")
        user_memories[phone_number] = ConversationBufferMemory(memory_key="chat_history", return_messages=True)
        # Ajouter le message de bienvenue à la mémoire pour le contexte initial
        welcome_text = "Bonjour ! Je suis l'assistant virtuel de la Clinique Dentaire St Dominique. Comment puis-je vous aider ?"
        user_memories[phone_number].save_context({"input": "start"}, {"output": welcome_text})
        
    memory = user_memories[phone_number]
    
    # Message d'erreur par défaut
    response_text = "Je rencontre un problème technique. Veuillez réessayer plus tard." 

    if not callable(get_agent_executor):
        print("[PROCESS_MESSAGE] Critical: agent executor not available.")
        memory.chat_memory.add_ai_message(response_text) # Sauvegarder l'erreur dans la mémoire
        return response_text

    try:
        # La logique de prompt est maintenant gérée dans lead_graph.py
        agent_executor = get_agent_executor(memory=memory)
                
        # Invoquer l'agent avec juste le nouvel input. La mémoire gère le reste.
        result = agent_executor.invoke({
            "input": message_body
        })
        
        response_text = result.get('output', "Désolé, je n'ai pas pu générer de réponse.")
        
        # --- 3. Modération de la réponse sortante ---
        if not moderate_content(response_text):
            logger.warning(f"[MODERATION] Réponse de l'agent bloquée : '{response_text}'")
            return "Je ne suis pas en mesure de répondre à cette question. Comment puis-je vous aider avec les services de la clinique ?"

        # Formater la réponse pour WhatsApp
        formatted_response = format_whatsapp_response(response_text)
        
    except Exception as e:
        print(f"[PROCESS_MESSAGE] Error invoking agent: '{e}'\n{traceback.format_exc()}")
        # La réponse sera déjà dans la mémoire, on retourne juste le message d'erreur
        formatted_response = response_text
    
    return formatted_response

@whatsapp.route('/webhook', methods=['GET'])
def verify_webhook():
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    print(f"[WEBHOOK_VERIFY] Mode: '{mode}', Token: '{token}', Expected: '{VERIFY_TOKEN}'") 
    if mode == 'subscribe' and token == VERIFY_TOKEN:
        print("[WEBHOOK_VERIFY] Success.")
        return challenge, 200
    else:
        print("[WEBHOOK_VERIFY] Failed.")
        return 'Forbidden', 403

@whatsapp.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    try:
        if data.get('object') == 'whatsapp_business_account':
            for entry in data.get('entry', []):
                for change in entry.get('changes', []):
                    value = change.get('value', {})
                    if value.get('messages'):
                        for msg_obj in value.get('messages', []):
                            from_number_val = msg_obj.get('from') 
                            msg_type = msg_obj.get('type')
                            if from_number_val and msg_type == 'text':
                                msg_body = msg_obj['text']['body']
                                print(f'[WEBHOOK_POST] Processing text message from {from_number_val}: "{msg_body}"') 
                                response_text_val = process_message(msg_body, from_number_val) 
                                print(f'[WEBHOOK_POST] Generated response for {from_number_val}: "{response_text_val}"') 
                                if response_text_val:
                                    send_whatsapp_message(from_number_val, response_text_val)
                                else:
                                    print(f"[WEBHOOK_POST] No response for {from_number_val}.")
                            elif from_number_val:
                                print(f"[WEBHOOK_POST] Non-text type '{msg_type}' from {from_number_val}.") 
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        print(f"[WEBHOOK_POST] Error: '{str(e)}'\n{traceback.format_exc()}") 
        return jsonify({'status': 'error', 'message': "Internal server error"}), 500

def send_whatsapp_message(to_number: str, message_text: str): 
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
        print("[WHATSAPP_SEND] CRITICAL: Token/PhoneID missing.")
        return {"error": "Server WhatsApp config error."}
    url = f"https://graph.facebook.com/v17.0/{WHATSAPP_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to_number, "type": "text", "text": {"body": message_text}}
    
    print(f'[WHATSAPP_SEND] To {to_number}: "{message_text}"') 
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        result = response.json()
        return result
    except requests.exceptions.Timeout:
        print(f"[WHATSAPP_SEND] Error: Timeout for {to_number}")
        return {"error": "Timeout sending."}
    except requests.exceptions.HTTPError as err:
        print(f"[WHATSAPP_SEND] HTTP error for {to_number}: {err}") 
        if err.response is not None: print(f"[WHATSAPP_SEND] API Error ({err.response.status_code}): {err.response.text}")
        return {"error": f"HTTP {err.response.status_code}."} 
    except requests.exceptions.RequestException as err:
        print(f"[WHATSAPP_SEND] Request error for {to_number}: {err}") 
        return {"error": f"Request error: {err}"} 
    except Exception as e:
        print(f"[WHATSAPP_SEND] Unexpected exception for {to_number}: '{e}'\n{traceback.format_exc()}") 
        return {"error": "Unexpected server error."}
