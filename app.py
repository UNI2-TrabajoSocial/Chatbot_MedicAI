# app.py
import os
from flask import Flask, request
import sett
import services

app = Flask(__name__)

# 👉 Inicia el scheduler APENAS se levanta la app (sin depender de __main__)
services.start_reminder_scheduler()

@app.route('/bienvenido', methods=['GET'])
def bienvenido():
    return 'Hola, soy MedicAI, tu asistente virtual. ¿En qué puedo ayudarte?'

@app.route('/webhook', methods=['GET'])
def verificar_token():
    mode      = request.args.get('hub.mode')
    token     = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    if mode == 'subscribe' and token == sett.VERIFY_TOKEN and challenge:
        return challenge, 200
    return 'Token inválido', 403

@app.route('/webhook', methods=['POST'])
def recibir_mensaje():
    try:
        body = request.get_json(force=True)
        print("💥 LLEGÓ WEBHOOK:", body)

        entry   = body['entry'][0]
        changes = entry['changes'][0]
        value   = changes['value']

        if 'messages' not in value:
            print("⚠️ Sin campo 'messages', ignorado")
            return 'Ignorado', 200

        message   = value['messages'][0]
        number    = message['from']
        messageId = message['id']
        name      = value['contacts'][0]['profile']['name']

        # --- EXTRAER SI VIENE interactive ---
        if 'interactive' in message:
            inter = message['interactive']
            if inter['type'] == 'button_reply':
                text = inter['button_reply']['id']
            elif inter['type'] == 'list_reply':
                text = inter['list_reply']['id']
            else:
                text = services.obtener_Mensaje_whatsapp(message)
        else:
            # Soporta text, image, document, button, etc.
            text = services.obtener_Mensaje_whatsapp(message)
        # --------------------------------------

        print(f"📨 Mensaje de {number} ({name}): {text}")
        services.administrar_chatbot(text, number, messageId, name)
        return 'Enviado', 200

    except KeyError as e:
        print("❌ KeyError procesando webhook:", e)
        return f'KeyError: {e}', 400
    except Exception as e:
        print("❌ ERROR procesando webhook:", e)
        return str(e), 500

if __name__ == '__main__':
    # Para entorno local
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
