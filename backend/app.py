import os
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from functools import wraps
from dotenv import load_dotenv
from whatsapp_webhook import whatsapp
import traceback
from langchain.memory import ConversationBufferMemory
import threading
from lead_graph import get_agent_executor, TicketData, process_appointment_backend, get_supabase_client
import re
from datetime import datetime, timedelta

load_dotenv()
print("[APP_INIT] Load dotenv complete.")
print(f"[APP_INIT] GROQ_API_KEY loaded: {os.getenv('GROQ_API_KEY') is not None}")

from langchain_core.messages import HumanMessage, AIMessage
print("[APP_INIT] Successfully imported all necessary modules.")

web_user_memories = {}

print(f"!!! [FLASK CWD CHECK] Le répertoire de travail est : {os.getcwd()}")

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_FOLDER_PATH = os.path.join(APP_DIR, 'static')
app = Flask(__name__, static_folder=STATIC_FOLDER_PATH, static_url_path='')
CORS(app)
app.register_blueprint(whatsapp, url_prefix='/whatsapp')

def extract_user_data_from_memory(memory):
    messages = memory.chat_memory.messages
    user_data = {"name": "", "email": "", "phone": "", "service_type": "", "proposed_date": "", "proposed_time": ""}

    print(f"[DEBUG] Extraction des données utilisateur depuis {len(messages)} messages")
    
    for i, msg in enumerate(reversed(messages)):
        content = msg.content.lower()
        print(f"[DEBUG] Message {i}: {content[:100]}...")
        
        if not user_data["email"] and "@" in content:
            user_data["email"] = extract_email(content)
            print(f"[DEBUG] Email extrait: {user_data['email']}")
            
        if not user_data["phone"] and any(x in content for x in ["77", "tel", "tél", "+"]):
            user_data["phone"] = extract_phone(content)
            print(f"[DEBUG] Téléphone extrait: {user_data['phone']}")
            
        if not user_data["name"] and ("je m'appelle" in content or "nom" in content):
            user_data["name"] = extract_name(content)
            print(f"[DEBUG] Nom extrait: {user_data['name']}")
            
        if not user_data["service_type"]:
            extracted_service = extract_service_type(content)
            if extracted_service and extracted_service != "Consultation":
                user_data["service_type"] = extracted_service
                print(f"[DEBUG] Type de soin extrait: {user_data['service_type']}")
                
        if not user_data["proposed_time"] and "h" in content:
            user_data["proposed_time"] = extract_time(content)
            print(f"[DEBUG] Heure extraite: {user_data['proposed_time']}")
            
        if not user_data["proposed_date"] and any(x in content for x in ["demain", "/", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]):
            user_data["proposed_date"] = extract_date(content)
            print(f"[DEBUG] Date extraite: {user_data['proposed_date']}")
    
    print(f"[DEBUG] Données finales extraites: {user_data}")
    return user_data

def extract_email(text):
    match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", text)
    return match.group(0) if match else ""

def extract_phone(text):
    match = re.search(r"(?:\+221)?\s*(\d{2,3}[\s\-]?\d{3}[\s\-]?\d{3,4})", text)
    return match.group(1).replace(" ", "").replace("-", "") if match else ""

def extract_name(text):
    match = re.search(r"(?:je m'appelle|nom est|je suis)\s*([A-Za-zÀ-ÿ\- ]+)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r"nom[:\s]+([A-Za-zÀ-ÿ\- ]+)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    words = text.strip().split()
    if len(words) == 1 and len(words[0]) > 2 and not re.search(r"@|tel|mail|soin|rdv|rendez-vous|demain|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|\d", words[0], re.IGNORECASE):
        return words[0]
    return ""

def extract_service_type(text):
    match = re.search(r"(détartrage|extraction|consultation|orthodontie|blanchiment|carie[s]?|prothèse[s]?|parodontologie|cavité[s]?|douleur[s]?|mal de dents?)", text, re.IGNORECASE)
    if match:
        service = match.group(1).capitalize()
        if service.lower() in ["carie", "caries", "cavité", "cavités"]:
            return "Carie"
        elif service.lower() in ["douleur", "douleurs", "mal de dents"]:
            return "Douleur"
        else:
            return service
    return "Consultation"

def extract_time(text):
    match = re.search(r"(\d{1,2})h(\d{0,2})", text)
    if match:
        return f"{match.group(1)}h{match.group(2) if match.group(2) else '00'}"
    return ""

def extract_date(text):
    import datetime
    import re
    from dateutil.relativedelta import relativedelta, MO, TU, WE, TH, FR, SA, SU
    
    text = text.lower()
    today = datetime.date.today()
    jours = {
        'lundi': 0, 'mardi': 1, 'mercredi': 2, 'jeudi': 3, 'vendredi': 4, 'samedi': 5, 'dimanche': 6
    }
    if 'après-demain' in text or 'apres-demain' in text:
        return (today + datetime.timedelta(days=2)).strftime('%Y-%m-%d')
    if 'demain' in text:
        return (today + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    match = re.search(r'(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche) prochain', text)
    if match:
        jour = match.group(1)
        target = jours[jour]
        days_ahead = (target - today.weekday() + 7) % 7
        if days_ahead == 0:
            days_ahead = 7
        return (today + datetime.timedelta(days=days_ahead)).strftime('%Y-%m-%d')
    match = re.search(r'(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)', text)
    if match:
        jour = match.group(1)
        target = jours[jour]
        days_ahead = (target - today.weekday() + 7) % 7
        if days_ahead == 0:
            days_ahead = 7
        return (today + datetime.timedelta(days=days_ahead)).strftime('%Y-%m-%d')
    match = re.search(r'(\d{1,2})/(\d{1,2})/(\d{2,4})', text)
    if match:
        day, month, year = match.groups()
        if len(year) == 2:
            year = '20' + year
        try:
            date = datetime.date(int(year), int(month), int(day))
            return date.strftime('%Y-%m-%d')
        except:
            return ''
    return ''

@app.route('/')
def root():
    return send_from_directory(app.static_folder, 'index.html')

def log_requests(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated_function

@app.route("/api/chat", methods=["POST"])
@log_requests
def chat():
    data = request.get_json()
    history = data.get("history", []) 
    session_id = data.get("session_id", "default_web_session")

    if not history:
        return jsonify({"status": "error", "response": "L'historique de conversation est vide"}), 400

    try:
        if session_id not in web_user_memories:
            web_user_memories[session_id] = ConversationBufferMemory(memory_key="chat_history", return_messages=True)

        memory = web_user_memories[session_id]
        user_input = history[-1].get("content")
        if not user_input:
            return jsonify({"status": "error", "response": "Message utilisateur vide"}), 400

        agent_executor = get_agent_executor(memory=memory)
        response = agent_executor["invoke"]({"input": user_input})
        bot_reply = response['output']

        if "[CONFIRM_APPOINTMENT]" in bot_reply:
            print("[INFO] Confirmation détectée. Traitement asynchrone lancé.")
            
            user_data = extract_user_data_from_memory(memory)

            print(f"[DEBUG] Nom extrait pour le ticket : {user_data['name']}")

            ticket_data = TicketData(
                type="appointment",
                name=user_data["name"],
                email=user_data["email"],
                phone=user_data["phone"],
                service_type=user_data["service_type"],
                proposed_date=user_data["proposed_date"],
                proposed_time=user_data["proposed_time"]
            )

            threading.Thread(target=process_appointment_backend, args=(ticket_data,)).start()

            return jsonify({
                "status": "success",
                "response": "Votre demande est en cours de traitement. Vous recevrez une confirmation par e-mail sous peu."
            })

        return jsonify({"status": "success", "response": bot_reply})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "response": "Une erreur interne est survenue."}), 500

@app.route("/health")
def health():
    return jsonify({"status": "healthy"}), 200

@app.route("/api/check_ticket", methods=["GET"])
def check_ticket():
    email = request.args.get("email")
    if not email:
        return jsonify({"status": "error", "message": "Email manquant"}), 400

    try:
        client = get_supabase_client()
        if not client:
            return jsonify({"status": "error", "message": "Erreur interne Supabase"}), 500

        time_limit = (datetime.utcnow() - timedelta(minutes=2)).isoformat()

        result = client.table("tickets").select("*").eq("email", email).gte("created_at", time_limit).execute()
        tickets = result.data if hasattr(result, 'data') else result

        if tickets and len(tickets) > 0:
            return jsonify({
                "status": "success",
                "found": True,
                "ticket_id": tickets[0].get("ticket_id"),
                "service_type": tickets[0].get("service_type"),
                "date": tickets[0].get("proposed_date"),
                "time": tickets[0].get("proposed_time"),
            })
        else:
            return jsonify({"status": "success", "found": False})

    except Exception as e:
        print("[CHECK_TICKET] ERREUR:", e)
        return jsonify({"status": "error", "message": "Erreur interne"}), 500

if __name__ == '__main__':
    print("--- Lancement du serveur en mode direct (python app.py) ---")
    app.run(host="0.0.0.0", port=5000, debug=True)