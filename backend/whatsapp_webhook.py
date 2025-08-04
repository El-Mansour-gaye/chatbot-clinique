from flask import Blueprint, request, jsonify
import json

whatsapp = Blueprint('whatsapp', __name__)

@whatsapp.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    
    if not data:
        return jsonify({'status': 'error', 'message': 'No data received'}), 400

    # Process the incoming message
    message = data.get('message')
    sender = data.get('sender')

    if not message or not sender:
        return jsonify({'status': 'error', 'message': 'Missing message or sender'}), 400

    # Here you would typically process the message and generate a response
    response_message = process_message(message)

    # Send the response back to the sender
    return jsonify({'status': 'success', 'response': response_message}), 200

def process_message(message):
    # Placeholder for message processing logic
    # This function should contain the logic to handle the incoming message
    return f"Received your message: {message}"