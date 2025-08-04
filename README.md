# Chatbot Spécial Clinique

## Description
Ce projet est un chatbot conçu pour interagir avec les utilisateurs via WhatsApp. Il permet de gérer les demandes de rendez-vous et d'extraire des informations utilisateur à partir des conversations.

## Structure du projet
```
chatbot_spe_clinique
├── backend
│   ├── app.py                  # Fichier principal de l'application Flask
│   ├── whatsapp_webhook.py     # Gestion du webhook WhatsApp
│   ├── lead_graph.py           # Gestion des leads et intégration de services externes
│   ├── requirements.txt         # Dépendances Python requises
│   └── static
│       └── index.html          # Interface frontend du chatbot
├── .env                         # Variables d'environnement
└── README.md                   # Documentation du projet
```

## Installation
1. Clonez le dépôt :
   ```
   git clone <URL_DU_DEPOT>
   cd chatbot_spe_clinique
   ```

2. Installez les dépendances :
   ```
   pip install -r backend/requirements.txt
   ```

3. Configurez les variables d'environnement dans le fichier `.env`.

## Utilisation
1. Lancez l'application Flask :
   ```
   python backend/app.py
   ```

## Contribuer
Les contributions sont les bienvenues ! Veuillez soumettre une demande de tirage pour toute amélioration ou correction de bogue.

## License
Ce projet est sous licence MIT. Veuillez consulter le fichier LICENSE pour plus de détails.