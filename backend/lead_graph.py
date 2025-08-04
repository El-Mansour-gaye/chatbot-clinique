from typing import Optional
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field
import os
import logging
import langchain
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client
from langchain_core.tools import tool
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.schema import HumanMessage
from langchain_community.cache import SQLiteCache
from langchain.memory import ConversationBufferMemory
from dateutil.parser import parse as parse_datetime, parserinfo
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import traceback
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import re
import time
import threading

# Définition du fuseau horaire du Sénégal (UTC+0)
SENEGAL_TIMEZONE = timezone(timedelta(hours=0))

class FrenchParserInfo(parserinfo):
    MONTHS = [
        ("jan", "janvier"), ("fév", "février"), ("mar", "mars"), ("avr", "avril"),
        ("mai", "mai"), ("juin", "juin"), ("jul", "juillet"), ("aoû", "août"),
        ("sep", "septembre"), ("oct", "octobre"), ("nov", "novembre"), ("déc", "décembre")
    ]

# --- Contenu de google_calendar.py ---
SERVICE_ACCOUNT_FILE = os.path.join(os.path.dirname(__file__), 'service_account.json')
SCOPES = ['https://www.googleapis.com/auth/calendar']
CALENDAR_ID = os.getenv('GOOGLE_CALENDAR_ID', 'primary') 

def get_calendar_service():
    """Crée et retourne un service Google Calendar authentifié."""
    try:
        logger.info(f"Tentative de chargement des credentials depuis : {SERVICE_ACCOUNT_FILE}")
        if not os.path.exists(SERVICE_ACCOUNT_FILE):
            logger.error("Fichier service_account.json INTROUVABLE à l'emplacement attendu.")
            return None
        
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        
        if creds and creds.valid:
            logger.info("Credentials Google chargés et valides.")
        else:
            # Cette situation est souvent normale pour les comptes de service.
            logger.info("La propriété 'valid' des credentials est False. Ceci est souvent normal pour les comptes de service qui rafraîchissent les tokens à la volée.")

        service = build('calendar', 'v3', credentials=creds)
        logger.info("Service Google Calendar créé avec succès.")
        return service
    except Exception as e:
        logger.error(f"Erreur critique lors de la création du service Calendar : {e}")
        logger.error(traceback.format_exc()) # Affiche la pile d'appel complète de l'erreur
        return None

def check_availability(start_dt: datetime, end_dt: datetime) -> bool:
    service = get_calendar_service()
    if not service: return False
    events = service.events().list(calendarId=CALENDAR_ID, timeMin=start_dt.isoformat(), timeMax=end_dt.isoformat(), singleEvents=True).execute().get('items', [])
    return not bool(events)

def create_event(start_dt: datetime, end_dt: datetime, summary: str, client_email: str) -> dict:
    service = get_calendar_service()
    if not service: return {"error": "Service Calendar indisponible"}
    
    logger.info(f"[CALENDAR_DEBUG] Tentative de création d'événement dans le calendrier: {CALENDAR_ID}")
    logger.info(f"[CALENDAR_DEBUG] Résumé: {summary}")
    logger.info(f"[CALENDAR_DEBUG] Début: {start_dt.isoformat()}")
    logger.info(f"[CALENDAR_DEBUG] Fin: {end_dt.isoformat()}")
    
    description = f"Détails du rendez-vous.\nClient: {summary}\nEmail: {client_email}"
    
    event = {
        'summary': summary,
        'description': description,
        'start': {'dateTime': start_dt.isoformat(), 'timeZone': str(start_dt.tzinfo)},
        'end': {'dateTime': end_dt.isoformat(), 'timeZone': str(end_dt.tzinfo)},
        # 'attendees' a été retiré pour éviter l'erreur de délégation de domaine (forbiddenForServiceAccounts).
        'reminders': {'useDefault': True},
        # Rendre l'événement public pour que le client puisse y accéder
        'visibility': 'public',
        'transparency': 'opaque'
    }
    
    try:
        t0 = time.time()
        result = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        logger.info(f"[PERF] Google Calendar event creation took {time.time() - t0:.2f} seconds")
        logger.info(f"[CALENDAR_SUCCESS] Événement créé avec succès. ID: {result.get('id')}")
        logger.info(f"[CALENDAR_SUCCESS] Lien: {result.get('htmlLink')}")
        return result
    except Exception as e:
        logger.error(f"[CALENDAR_ERROR] Erreur lors de la création de l'événement: {e}")
        logger.error(f"[CALENDAR_ERROR] Calendar ID utilisé: {CALENDAR_ID}")
        logger.error(f"[CALENDAR_ERROR] Traceback: {traceback.format_exc()}")
        return {"error": f"Erreur lors de la création de l'événement: {str(e)}"}

# --- NOUVEL ENVOI D'EMAIL AVEC SMTPLIB (GMAIL) ---
def send_ticket_email(ticket_data: dict, to_email: str):
    """Envoie un e-mail de confirmation via SMTP (conçu pour Gmail)."""
    sender_email = os.getenv("SENDER_EMAIL")
    sender_password = os.getenv("SENDER_APP_PASSWORD")
    manager_email = os.getenv("MANAGER_EMAIL")

    if not sender_email or not sender_password:
        logger.error("[EMAIL-SMTP] SENDER_EMAIL ou SENDER_APP_PASSWORD manquant dans .env.")
        raise ValueError("Les credentials pour l'envoi d'e-mail ne sont pas configurés.")

    # Dictionnaire de traduction pour les champs de l'e-mail
    labels = {
        "service_type": "Type de soin demandé",
        "proposed_date": "Date proposée",
        "proposed_time": "Heure proposée",
        "issue_type": "Type de problème",
        "description": "Description"
    }

    # Construction du message
    message = MIMEMultipart("alternative")
    subject = f"[Clinique St Dominique] Confirmation de votre demande : Ticket {ticket_data.get('ticket_id')}"
    message["Subject"] = subject
    message["From"] = f"Clinique Dentaire St Dominique <{sender_email}>"
    message["To"] = to_email
    
    recipients = [to_email]
    if manager_email:
        message["Cc"] = manager_email
        recipients.append(manager_email)

    # Génération des détails pour l'e-mail
    details_html = ""
    # On itère sur une liste définie pour contrôler l'ordre et les champs affichés au client
    for key in ["service_type", "proposed_date", "proposed_time", "issue_type", "description"]:
        if ticket_data.get(key):
            details_html += f"<li><strong>{labels.get(key, key.replace('_', ' ').title())}:</strong> {ticket_data[key]}</li>"

    # Contenu HTML de l'email (amélioré et sans lien Calendar)
    body_html = f"""
    <html>
      <head>
        <style>
          body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #333; line-height: 1.6; }}
          .container {{ max-width: 600px; margin: 20px auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px; background-color: #f9f9f9; }}
          .header {{ font-size: 24px; font-weight: 600; color: #004a99; margin-bottom: 20px; }}
          p {{ margin: 10px 0; }}
          ul {{ list-style-type: none; padding-left: 0; }}
          li {{ background-color: #ffffff; margin-bottom: 8px; padding: 10px; border-left: 4px solid #0056b3; }}
          strong {{ color: #004a99; }}
          .footer {{ font-size: 12px; color: #777; margin-top: 20px; text-align: center; }}
        </style>
      </head>
      <body>
        <div class="container">
          <p class="header">Confirmation de votre demande</p>
          <p>Bonjour {ticket_data.get('name', 'patient')},</p>
          <p>Votre demande a bien été enregistrée à la Clinique Dentaire St Dominique sous le numéro de ticket <strong>{ticket_data.get('ticket_id')}</strong>. Voici un résumé des informations que vous nous avez fournies :</p>
          <ul>
            {details_html}
          </ul>
          <p>Un membre de notre équipe vous contactera dans les plus brefs délais pour confirmer votre rendez-vous ou donner suite à votre demande.</p>
          <p>Cordialement,<br><strong>L'équipe de la Clinique Dentaire St Dominique</strong></p>
        </div>
        <div class="footer">
          <p>Ceci est un e-mail automatique, merci de ne pas y répondre directement.</p>
        </div>
      </body>
    </html>
    """
    
    message.attach(MIMEText(body_html, "html"))

    # Connexion au serveur SMTP et envoi
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()  # Sécurise la connexion
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipients, message.as_string())
            logger.info(f"Email SMTP envoyé avec succès pour le ticket {ticket_data.get('ticket_id')}")
    except smtplib.SMTPAuthenticationError:
        logger.error("[EMAIL-SMTP] Échec de l'authentification. Vérifiez SENDER_EMAIL et SENDER_APP_PASSWORD.")
        raise
    except Exception as e:
        logger.error(f"[EMAIL-SMTP] Erreur lors de l'envoi : {e}")
        raise

# --- Fin de la section email ---

# Configuration du logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Configuration du cache Langchain
langchain.llm_cache = SQLiteCache(database_path=os.path.join(os.path.dirname(__file__), ".langchain.db"))

def get_supabase_client() -> Optional[Client]:
    """Crée un client Supabase."""
    try:
        supabase_url = os.getenv('SUPABASE_URL')
        supabase_key = os.getenv('SUPABASE_KEY')
        
        if not supabase_url or not supabase_key:
            logger.error("SUPABASE_URL ou SUPABASE_KEY non configurés")
            return None
            
        client = create_client(supabase_url, supabase_key)
        logger.info("Client Supabase créé avec succès")
        return client
    except Exception as e:
        logger.error(f"Erreur lors de la création du client Supabase: {str(e)}")
        return None

# --- Nouvelle classe unifiée pour les données de ticket ---
class TicketData(BaseModel):
    type: str = Field(description="Type de ticket : 'appointment' ou 'support'")
    name: str = Field(description="Nom de l'utilisateur")
    email: str = Field(description="Email de l'utilisateur")
    phone: str = Field(description="Téléphone de l'utilisateur")

    # Champs pour rendez-vous
    service_type: Optional[str] = Field(None, description="Type de service demandé (si type=appointment)")
    proposed_date: Optional[str] = Field(None, description="Date souhaitée (si rendez-vous)")
    proposed_time: Optional[str] = Field(None, description="Heure souhaitée (si rendez-vous)")

    # Champs pour support
    issue_type: Optional[str] = Field(None, description="Type de problème (si support)")
    description: Optional[str] = Field(None, description="Description du problème (si support)")
    google_event_link: Optional[str] = Field(None, description="Lien de l'événement Google Calendar si un RDV a été créé")

# --- Traitement asynchrone du rendez-vous (à placer après TicketData) ---
def process_appointment_backend(ticket_data: TicketData):
    try:
        # 1. Créer l'événement Google Calendar
        start_time_str = f"{ticket_data.proposed_date} {ticket_data.proposed_time}"
        summary = f"RDV Dentaire - {ticket_data.name}"
        event_result = create_calendar_event_backend(  # 🔁 utiliser la version backend, pas l'outil
            start_time_str=start_time_str,
            summary=summary,
            client_email=ticket_data.email,
            duration_minutes=60
        )

        google_event_link = None
        if "Lien est :" in event_result:
            google_event_link = event_result.split("Lien est :")[-1].strip()
        ticket_data.google_event_link = google_event_link

        # 2. Créer le ticket
        save_ticket(ticket_data)
    except Exception as e:
        logger.error(f"[BACKEND] Erreur lors du traitement asynchrone du rendez-vous : {e}")


def save_ticket(ticket_data: TicketData) -> str:
    """Sauvegarde un ticket dans Supabase, envoie un email de confirmation, et retourne son ID."""
    try:
        client = get_supabase_client()
        if not client:
            return "Erreur : client Supabase introuvable."

        ticket_id = f"TICKET-{os.urandom(4).hex().upper()}"
        data = {
            "ticket_id": ticket_id,
            "type": ticket_data.type,
            "name": ticket_data.name,
            "email": ticket_data.email,
            "phone": ticket_data.phone,
            "service_type": ticket_data.service_type,
            "proposed_date": ticket_data.proposed_date,
            "proposed_time": ticket_data.proposed_time,
            "issue_type": ticket_data.issue_type,
            "description": ticket_data.description,
            "google_event_link": ticket_data.google_event_link,
            "created_at": datetime.now(SENEGAL_TIMEZONE).isoformat()
        }

        t0 = time.time()
        client.table("tickets").insert(data).execute()
        logger.info(f"[PERF] Supabase insert took {time.time() - t0:.2f} seconds")
        
        # --- ENVOI DE L'EMAIL DE CONFIRMATION ---
        email_notification_message = ""
        logger.info(f"[EMAIL] Début de la tentative d'envoi d'email pour le ticket {ticket_id}")
        try:
            logger.info(f"[EMAIL] Variables d'environnement - SENDER_EMAIL: {os.getenv('SENDER_EMAIL') is not None}, SENDER_APP_PASSWORD: {os.getenv('SENDER_APP_PASSWORD') is not None}")
            t1 = time.time()
            send_ticket_email(data, ticket_data.email)
            logger.info(f"[PERF] Email sending took {time.time() - t1:.2f} seconds")
            logger.info(f"[EMAIL] Email envoyé avec succès pour le ticket {ticket_id} à {ticket_data.email}")
            email_notification_message = " Un e-mail de confirmation vous a été envoyé."
        except Exception as email_error:
            logger.error(f"[EMAIL] Erreur lors de la tentative d'envoi d'email: {email_error}")
            logger.error(f"[EMAIL] Traceback complet: {traceback.format_exc()}")
            email_notification_message = " L'envoi de l'e-mail de confirmation a échoué."

        return "Votre demande est en cours de traitement. Vous recevrez une confirmation par e-mail sous peu."

    except Exception as e:
        logger.error(f"Erreur lors de l'enregistrement du ticket : {str(e)}")
        error_str = str(e)
        if 'violates row-level security policy' in error_str:
            logger.error("--- ERREUR DE POLITIQUE SUPABASE (RLS) ---")
            logger.error("La table 'tickets' bloque l'écriture. Allez sur Supabase > Policies et créez une politique d'INSERT pour la table 'tickets'.")
            return "ERREUR: Le ticket n'a pas pu être sauvegardé à cause d'un problème de permissions dans la base de données."
        
        if "Could not find the 'google_event_link' column" in error_str:
            logger.error("--- ERREUR DE SCHEMA SUPABASE ---")
            logger.error("La colonne 'google_event_link' est manquante dans la table 'tickets'. Veuillez l'ajouter (type: text).")
            return "ERREUR: Le ticket n'a pas pu être sauvegardé car la base de données n'est pas à jour. La colonne 'google_event_link' est manquante."

        return "ERREUR: Une erreur interne est survenue lors de la création du ticket. Le ticket n'a PAS été créé."

# --- MODÈLES LLM ---

# Modèle principal pour la conversation
llm = ChatGroq(model="qwen/qwen3-32b", temperature=0, groq_api_key=os.getenv("GROQ_API_KEY") or "...", timeout=30)
logger.info(f"LLM conversationnel initialisé : {llm.model_name}")

# Modèle de garde pour la modération de contenu
llama_guard = ChatGroq(model="meta-llama/llama-guard-4-12b", temperature=0, groq_api_key=os.getenv("GROQ_API_KEY") or "...", timeout=30)
logger.info(f"LLM de modération initialisé : {llama_guard.model_name}")

def moderate_content(text_to_moderate: str) -> bool:
    """
    Vérifie si un texte est sûr en utilisant Llama Guard.
    Retourne True si le texte est sûr, False sinon.
    """
    if not text_to_moderate:
        return True # Considérer une chaîne vide comme sûre

    try:
        # Prompt de classification simple pour Llama Guard
        prompt = f"""
        Task: Check if the user message is safe to process for a customer service agent.
        The agent's task is to book appointments for a dental clinic.
        Unsafe content includes self-harm, hate speech, violence, and sexually explicit material.
        A simple question, even if out of scope, is safe.

        <begin>
        {text_to_moderate}
        <end>

        Is the above message safe or unsafe? Answer with a single word.
        """
        
        response = llama_guard.invoke(prompt)
        
        # Llama Guard est entraîné à répondre par "safe" ou "unsafe".
        # On vérifie la présence du mot "unsafe" dans la réponse.
        answer = response.content.strip().lower()
        logger.info(f"[MODERATION] Texte: '{text_to_moderate[:50]}...' -> Réponse Guard: '{answer}'")
        
        if "unsafe" in answer:
            return False # Le contenu est jugé dangereux
        
        return True # Le contenu est sûr
        
    except Exception as e:
        logger.error(f"[MODERATION] Erreur lors de la modération du contenu : {e}")
        return False # Par précaution, considérer comme non sûr en cas d'erreur

# --- OUTILS DE L'AGENT ---
def create_calendar_event_backend(start_time_str: str, summary: str, client_email: str, duration_minutes: int = 60) -> str:
    try:
        time_str_for_parsing = start_time_str.replace('h', ':')
        start_time = parse_datetime(time_str_for_parsing, parserinfo=FrenchParserInfo())
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=SENEGAL_TIMEZONE)
        end_time = start_time + timedelta(minutes=duration_minutes)
        event = create_event(start_time, end_time, summary, client_email)
        if "error" in event:
            return f"Échec de la création de l'événement: {event['error']}"
        else:
            return f"Lien est : {event.get('htmlLink', 'Non disponible')}"
    except Exception as e:
        logger.error(f"[BACKEND_CALENDAR] Erreur : {e}")
        return "Erreur lors de la création de l'événement (backend)."

def normalize_french_time(text):
    # Remplace "16h" par "16:00", "16h30" par "16:30", etc.
    return re.sub(r'(\d{1,2})h(\d{0,2})', lambda m: f"{m.group(1)}:{m.group(2) or '00'}", text)

@tool
def check_calendar_availability(start_time_str: str, duration_minutes: int = 60) -> str:
    """
    Vérifie la disponibilité dans l'agenda pour un créneau donné.
    Utilisez cet outil AVANT de tenter de créer un événement.
    Args:
        start_time_str (str): La date et l'heure de début souhaitées, au format ISO ou en langage naturel (ex: "demain à 14h", "25 décembre 2024 10:00").
        duration_minutes (int): La durée du rendez-vous en minutes. Par défaut 60.
    Returns:
        str: "Le créneau est disponible." ou "Le créneau est malheureusement déjà occupé."
    """
    try:
        time_str_for_parsing = normalize_french_time(start_time_str)
        start_time = parse_datetime(time_str_for_parsing, parserinfo=FrenchParserInfo())
        # Assurer que le datetime est "aware" (avec timezone)
        if start_time.tzinfo is None:
            # Si aucune timezone n'est spécifiée, on utilise le fuseau horaire du Sénégal
            start_time = start_time.replace(tzinfo=SENEGAL_TIMEZONE)

        end_time = start_time + timedelta(minutes=duration_minutes)
        if check_availability(start_time, end_time):
            return "Le créneau est disponible."
        else:
            return "Le créneau est malheureusement déjà occupé. Proposez une alternative à l'utilisateur."
    except HttpError as e:
        if e.resp.status == 403 and 'accessNotConfigured' in str(e):
             logger.error(f"ERREUR API GOOGLE: L'API Calendar n'est pas activée. {e}")
             return "Erreur de configuration: L'API Google Calendar n'est pas activée. Un administrateur doit l'activer dans la console Google Cloud. Impossible de continuer."
        else:
             logger.error(f"Erreur HttpError dans check_calendar_availability: {e}")
             return "Une erreur de communication avec l'agenda est survenue. Veuillez réessayer plus tard."
    except Exception as e:
        logger.error(f"Erreur dans check_calendar_availability: {e}")
        return (
            "Erreur technique lors de la vérification de la disponibilité du calendrier. "
            "Veuillez réessayer plus tard ou contactez la clinique par téléphone au 77 510 02 06. "
            "Désolé pour la gêne occasionnée."
        )

@tool
def create_calendar_event(start_time_str: str, summary: str, client_email: str, duration_minutes: int = 60) -> str:
    """
    Crée un événement dans Google Calendar. OBLIGATOIRE pour les rendez-vous.
    Utilisez cet outil IMMÉDIATEMENT après avoir reçu les informations du client (nom, email, téléphone).
    Utilisez cet outil SEULEMENT après avoir vérifié la disponibilité et obtenu l'accord de l'utilisateur.
    Args:
        start_time_str (str): L'heure de début de l'événement, au format ISO ou en langage naturel.
        summary (str): Le titre de l'événement (ex: "RDV Dentaire - Jean Dupont").
        client_email (str): L'e-mail du client, qui sera ajouté à la description de l'événement.
        duration_minutes (int): La durée en minutes.
    Returns:
        str: Une confirmation avec le lien de l'événement, ou un message d'erreur.
    """
    try:
        time_str_for_parsing = start_time_str.replace('h', ':')
        start_time = parse_datetime(time_str_for_parsing, parserinfo=FrenchParserInfo())
        # Assurer que le datetime est "aware" (avec timezone)
        if start_time.tzinfo is None:
            # Si aucune timezone n'est spécifiée, on utilise le fuseau horaire du Sénégal
            start_time = start_time.replace(tzinfo=SENEGAL_TIMEZONE)
            
        end_time = start_time + timedelta(minutes=duration_minutes)
        event = create_event(start_time, end_time, summary, client_email)
        if "error" in event:
            return f"Échec de la création de l'événement: {event['error']}"
        else:
            link = event.get('htmlLink', 'Lien non disponible')
            return f"Événement créé avec succès. Le lien est : {link}"
    except HttpError as e:
        if e.resp.status == 403 and 'forbiddenForServiceAccounts' in str(e):
             logger.error(f"ERREUR API GOOGLE: Le compte de service ne peut pas inviter de participants. {e}")
             return "Erreur de configuration: Le compte de service n'est pas autorisé à inviter des participants à un événement. L'événement n'a pas été créé."
        if e.resp.status == 403 and 'accessNotConfigured' in str(e):
             logger.error(f"ERREUR API GOOGLE: L'API Calendar n'est pas activée. {e}")
             return "Erreur de configuration: L'API Google Calendar n'est pas activée. Impossible de créer l'événement."
        else:
             logger.error(f"Erreur HttpError dans create_calendar_event: {e}")
             return "Une erreur de communication avec l'agenda est survenue lors de la création de l'événement."
    except Exception as e:
        logger.error(f"Erreur dans create_calendar_event: {e}")
        return "Erreur lors de la création de l'événement."

# --- Nouvel outil unifié ---
@tool
def create_ticket(
    type: str,
    name: str,
    email: str,
    phone: str,
    service_type: Optional[str] = None,
    proposed_date: Optional[str] = None,
    proposed_time: Optional[str] = None,
    issue_type: Optional[str] = None,
    description: Optional[str] = None,
    google_event_link: Optional[str] = None,
) -> str:
    """
    Crée un ticket de support ou de rendez-vous.
    OBLIGATOIRE : Utilisez cet outil IMMÉDIATEMENT après avoir reçu les informations du client (nom, email, téléphone).
    - Pour TOUS les tickets, il faut : nom, email, téléphone, et type ('appointment' ou 'support').
    - Si type='appointment', il faut EN PLUS : service_type, proposed_date, et proposed_time.
    - Si type='support', il faut EN PLUS : issue_type et description.
    NE JAMAIS TERMINER LA CONVERSATION SANS APPELER CET OUTIL.
    """
    logger.info(f"[CREATE_TICKET] Début de création du ticket - Type: {type}, Nom: {name}, Email: {email}")
    ticket_data = TicketData(
        type=type,
        name=name,
        email=email,
        phone=phone,
        service_type=service_type,
        proposed_date=proposed_date,
        proposed_time=proposed_time,
        issue_type=issue_type,
        description=description,
        google_event_link=google_event_link,
    )
    print(f"[TOOL CALLED] Ticket : {ticket_data.model_dump(exclude_none=True)}")
    result = save_ticket(ticket_data)
    logger.info(f"[CREATE_TICKET] Résultat de création du ticket : {result}")
    return result

# La liste des outils est maintenant étendue
tools = [create_ticket, check_calendar_availability, create_calendar_event]

# Nouveau prompt système qui explique le workflow
BASE_SYSTEM_PROMPT = """
Vous êtes l'assistant conversationnel de la Clinique Dentaire St Dominique à Dakar.

### 🎯 Objectif :
Aider les patients à prendre rendez-vous ou poser des questions, **en évitant tout traitement lourd avant confirmation.**

---

### ✅ FLUX POUR UN RENDEZ-VOUS :

1. **Collectez les infos suivantes :**
   - type de soin
   - date souhaitée
   - heure souhaitée
   - nom, email, téléphone

2. **Quand toutes les infos sont collectées :**
   - Affichez un RÉCAPITULATIF clair.
   - Demandez à l’utilisateur de **confirmer** ("oui" ou "confirmer").

3. **Si l’utilisateur confirme :**
   - Répondez UNIQUEMENT par : `[CONFIRM_APPOINTMENT]`
   - **NE DITES RIEN D’AUTRE.**
   - **NE FAITES AUCUN APPEL D’OUTIL.**

4. **N’appelez `create_calendar_event` et `create_ticket` que si vous recevez le message spécial `[BACKEND_TRIGGER]`** (ce message est réservé au backend, vous ne l’utiliserez pas ici).

---

### ⚠️ RÈGLES STRICTES :

- **NE JAMAIS** appeler d’outil tant que l’utilisateur n’a pas confirmé.
- Quand l’utilisateur confirme, votre réponse doit être UNIQUEMENT `[CONFIRM_APPOINTMENT]`
- Les appels aux outils seront lancés par le serveur backend, vous n’avez pas à le faire.

---

### 🦷 Contexte clinique :

- Adresse : Avenue Cheikh Anta Diop, Dakar.
- Téléphone : +221 77 510 02 06
- Horaires : Lundi à Vendredi (9h-13h / 15h-18h30), Samedi (9h-12h)

"""


# Création de l'agent et de l'exécuteur (simplifié)
def get_agent_executor(memory) -> AgentExecutor:
    """
    Crée et retourne une instance de l'exécuteur d'agent.
    """
    # Ajout de la date du jour dynamiquement dans le prompt système avec le fuseau horaire du Sénégal
    current_date = datetime.now(SENEGAL_TIMEZONE).strftime('%A %d %B %Y')
    system_prompt = f"""
    Nous sommes le {current_date}.
    {BASE_SYSTEM_PROMPT}
    """
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ])
    
    agent = create_tool_calling_agent(llm, tools, prompt)
    
    # Intégration de la mémoire directement dans l'exécuteur
    agent_executor = AgentExecutor(
        agent=agent, 
        tools=tools, 
        memory=memory, 
        verbose=True
    )
    return agent_executor

def handle_appointment_dialogue(message, user_data):
    """
    Gère le dialogue de prise de rendez-vous avec confirmation utilisateur.
    user_data : dict contenant les infos collectées (nom, email, téléphone, soin, date, heure, confirmation_pending)
    message : message texte reçu de l'utilisateur
    Retourne la réponse à afficher à l'utilisateur.
    """
    # 1. Si on attend la confirmation
    if user_data.get("confirmation_pending"):
        if message.strip().lower() in ["oui", "confirmer", "ok", "yes"]:
            # Lancer le traitement asynchrone
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
            user_data["confirmation_pending"] = False
            # Ici, il faudrait sauvegarder user_data dans la session ou la base si besoin
            return "Votre demande est en cours de traitement. Vous recevrez une confirmation par e-mail sous peu."
        else:
            return "Merci de répondre par 'oui' ou 'confirmer' pour valider votre rendez-vous."
    # 2. Si on a toutes les infos mais pas encore demandé la confirmation
    infos_ok = all(user_data.get(k) for k in ["name", "email", "phone", "service_type", "proposed_date", "proposed_time"])
    if infos_ok and not user_data.get("confirmation_pending"):
        recap = (
            f"Merci, voici le récapitulatif de votre demande :\n"
            f"- Nom : {user_data['name']}\n"
            f"- Email : {user_data['email']}\n"
            f"- Téléphone : {user_data['phone']}\n"
            f"- Soin : {user_data['service_type']}\n"
            f"- Date : {user_data['proposed_date']} à {user_data['proposed_time']}\n\n"
            "Nous allons vérifier la disponibilité et finaliser votre rendez-vous.\n"
            "**Merci de confirmer pour continuer (répondez par 'oui' ou 'confirmer').**"
        )
        user_data["confirmation_pending"] = True
        # Ici, il faudrait sauvegarder user_data dans la session ou la base si besoin
        return recap
    # 3. Sinon, poursuis la collecte des infos (à intégrer dans ta logique principale)
    return None  # Signifie qu'il faut continuer la collecte

# --- Exemple d'intégration backend pour un traitement asynchrone instantané ---
# (À adapter à Flask, FastAPI, Django, etc.)
#
# def handle_confirmed_appointment(user_data):
#     """
#     Fonction à appeler dès que l'utilisateur a confirmé son rendez-vous.
#     user_data : dict contenant toutes les infos nécessaires (nom, email, téléphone, soin, date, heure)
#     """
#     from threading import Thread
#     ticket_data = TicketData(
#         type="appointment",
#         name=user_data["name"],
#         email=user_data["email"],
#         phone=user_data["phone"],
#         service_type=user_data["service_type"],
#         proposed_date=user_data["proposed_date"],
#         proposed_time=user_data["proposed_time"]
#     )
#     Thread(target=process_appointment_backend, args=(ticket_data,)).start()
#     # Répondre immédiatement à l'utilisateur :
#     return "Votre demande est en cours de traitement. Vous recevrez une confirmation par e-mail sous peu."
#
# --- Fin de l'exemple ---

if __name__ == "__main__":
    print("Testing lead_graph.py components with new ticket logic...")
    # Test de la modération
    print("\n--- Test de la modération ---")
    safe_text = "Bonjour, je voudrais prendre un rendez-vous."
    unsafe_text = "Je veux fabriquer une bombe."
    print(f"'{safe_text}' -> Sûr ? {moderate_content(safe_text)}")
    print(f"'{unsafe_text}' -> Sûr ? {moderate_content(unsafe_text)}")

    if not os.getenv("GROQ_API_KEY"): print("Warning: GROQ_API_KEY not set.")
    if not os.getenv("SUPABASE_URL"): print("Warning: SUPABASE_URL not set.")
    
    # Test de la création de ticket
    print("\n--- Ticket Creation Test ---")
    try:
        test_ticket = TicketData(
            type="appointment",
            name="Test User", 
            email="test@example.com", 
            phone="123456789",
            service_type="Traduction",
            proposed_date="demain",
            proposed_time="14h"
        )
        tool_result = create_ticket.invoke({"ticket_data": test_ticket})
        print(f"Tool `create_ticket` test result: {tool_result}")
    except Exception as e: 
        print(f"Error testing ticket creation: '{e}'")

    # Test de la création d'agent
    print("\n--- Agent Executor Creation Test ---")
    try:
        test_memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)
        test_executor = get_agent_executor(test_memory)
        print("Agent executor created successfully.")
        
        response = test_executor.invoke({"input": "Bonjour, je veux prendre un RDV pour une traduction demain à 10h. Mon nom est Jean, email jean@test.com, tel 0102030405."})
        print(f"Agent test response: {response['output']}")

    except Exception as e:
        print(f"Error testing agent executor creation: {e}")








