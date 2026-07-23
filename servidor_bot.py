"""
Bot DK Partners - Servidor con webhook (v7)
- Vehiculo (marca+modelo+año) en un solo campo
- Pregunta de repuestos mas abierta (permite varios)
- Boton "Corregir" reinicia desde repuestos, no desde el nombre
- Mensaje final al cliente con datos de contacto de asesores
- Mensaje al jefe mediante PLANTILLA (requiere plantilla aprobada en Meta)
"""

from flask import Flask, request, jsonify
import requests
import time
from config import ACCESS_TOKEN, PHONE_NUMBER_ID, API_VERSION, VERIFY_TOKEN, NUMERO_JEFE

app = Flask(__name__)

TIMEOUT_SEGUNDOS = 10 * 60  # 10 minutos de inactividad -> se reinicia

PREGUNTANDO = "preguntando"
CONFIRMANDO = "confirmando"

conversaciones = {}

# Nombre de la plantilla que debes crear y aprobar en WhatsApp Manager
NOMBRE_PLANTILLA_JEFE = "nuevo_pedido_dk"
IDIOMA_PLANTILLA = "es"

# Numeros de contacto de asesores (se muestran al cliente al final)
ASESORES = ["945 133 442", "947 114 330"]

CAMPOS = [
    ("nombre", "Para empezar, ¿cuál es tu *nombre*?", "👤"),
    ("repuestos", "Gracias, {nombre}. ¿Qué *repuestos* necesitas?", "🔧"),
    ("vehiculo", "Cuéntame sobre tu vehículo: *marca, modelo y año* (ej. Toyota Hilux 2019)", "🚗"),
    ("placa", "Por último, ¿cuál es la *placa* del vehículo?", "🔖"),
]

# Indice del primer campo al que se vuelve cuando el usuario pide "corregir"
# (0 = nombre, 1 = repuestos). Como el nombre ya se sabe, se vuelve a repuestos.
PASO_CORRECCION = 1


def _post(payload):
    url = f"https://graph.facebook.com/{API_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    r = requests.post(url, headers=headers, json=payload)
    if r.status_code != 200:
        print(f"[ERROR] {r.status_code}: {r.text}")
    return r


def enviar_texto(destinatario, texto):
    _post({
        "messaging_product": "whatsapp",
        "to": destinatario,
        "type": "text",
        "text": {"body": texto},
    })


def enviar_botones(destinatario, texto, botones):
    _post({
        "messaging_product": "whatsapp",
        "to": destinatario,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": texto},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": bid, "title": titulo}}
                    for bid, titulo in botones
                ]
            },
        },
    })


def enviar_plantilla_jefe(destinatario, numero_cliente, nombre, repuestos, vehiculo, placa):
    """
    Manda el resumen del pedido al jefe usando una plantilla aprobada.
    Necesario porque el jefe nunca le escribe primero al bot (no hay
    ventana de 24h abierta), asi que un mensaje de texto libre fallaria.
    """
    _post({
        "messaging_product": "whatsapp",
        "to": destinatario,
        "type": "template",
        "template": {
            "name": NOMBRE_PLANTILLA_JEFE,
            "language": {"code": IDIOMA_PLANTILLA},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": f"+{numero_cliente}"},
                        {"type": "text", "text": nombre},
                        {"type": "text", "text": repuestos},
                        {"type": "text", "text": vehiculo},
                        {"type": "text", "text": placa},
                    ],
                }
            ],
        },
    })


def validar_campo(nombre_campo, valor):
    v = (valor or "").strip()
    if len(v) == 0:
        return False, "No recibí ningún texto. Intenta de nuevo:"
    if len(v) < 2:
        return False, "Esa respuesta es muy corta, ¿puedes darme un poco más de detalle?"
    return True, None


def armar_resumen(datos):
    lineas = []
    for campo, _, emoji in CAMPOS:
        if campo in datos:
            etiquetas = {
                "nombre": "Nombre",
                "repuestos": "Repuestos",
                "vehiculo": "Vehículo",
                "placa": "Placa",
            }
            lineas.append(f"{emoji} {etiquetas[campo]}: {datos[campo]}")
    return "\n".join(lineas)


def mensaje_final_cliente():
    contactos = " y ".join(ASESORES)
    return (
        "✅ ¡Gracias! Tu pedido fue registrado correctamente.\n"
        "Uno de nuestros asesores se pondrá en contacto contigo muy pronto. 🙌\n\n"
        f"Para más información, comunícate directamente con nuestros asesores: {contactos}"
    )


def conversacion_activa(numero):
    estado = conversaciones.get(numero)
    if estado is None:
        return None
    if time.time() - estado["ultima_actividad"] > TIMEOUT_SEGUNDOS:
        del conversaciones[numero]
        return None
    return estado


def tocar(numero):
    if numero in conversaciones:
        conversaciones[numero]["ultima_actividad"] = time.time()


def iniciar_conversacion(numero):
    conversaciones[numero] = {
        "paso": 0,
        "datos": {},
        "estado": PREGUNTANDO,
        "ultima_actividad": time.time(),
    }
    enviar_texto(
        numero,
        "¡Hola! 👋 Bienvenido a *DK Partners*.\n\n"
        "Soy el asistente virtual y te voy a ayudar a registrar tu pedido "
        "de repuestos para que uno de nuestros asesores te contacte a la brevedad.\n\n"
        "Vamos a ir paso a paso, no te preocupes 🙂"
    )
    preguntar_paso(numero, 0, {})


def preguntar_paso(numero, paso, datos):
    _, pregunta, _ = CAMPOS[paso]
    enviar_texto(numero, pregunta.format(**datos))


def pedir_confirmacion(numero, datos):
    resumen = armar_resumen(datos)
    enviar_botones(
        numero,
        f"Perfecto, estos son tus datos:\n\n{resumen}\n\n¿Está todo correcto?",
        [("btn_confirmar", "✅ Confirmar"), ("btn_corregir", "✏️ Corregir")],
    )


@app.route("/webhook", methods=["GET"])
def verificar_webhook():
    modo = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if modo == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Verificacion fallida", 403


@app.route("/webhook", methods=["POST"])
def recibir_mensaje():
    data = request.get_json()
    try:
        cambios = data["entry"][0]["changes"][0]["value"]
        mensajes = cambios.get("messages")
        if not mensajes:
            return jsonify(status="ok"), 200
        mensaje = mensajes[0]
        numero = mensaje["from"]
        tipo = mensaje.get("type")

        if tipo == "interactive":
            boton_id = mensaje["interactive"]["button_reply"]["id"]
            manejar_boton(numero, boton_id)
        elif tipo == "text":
            texto = mensaje.get("text", {}).get("body", "").strip()
            manejar_texto(numero, texto)

    except (KeyError, IndexError):
        pass

    return jsonify(status="ok"), 200


def manejar_boton(numero, boton_id):
    estado = conversacion_activa(numero)

    if boton_id == "btn_confirmar":
        if not estado or estado["estado"] != CONFIRMANDO:
            return
        datos = estado["datos"]
        enviar_texto(numero, mensaje_final_cliente())
        enviar_plantilla_jefe(
            NUMERO_JEFE,
            numero,
            datos.get("nombre", ""),
            datos.get("repuestos", ""),
            datos.get("vehiculo", ""),
            datos.get("placa", ""),
        )
        del conversaciones[numero]
        return

    if boton_id == "btn_corregir":
        if not estado:
            return
        # Vuelve a preguntar desde "repuestos", conserva el nombre ya dado
        nombre_guardado = estado["datos"].get("nombre", "")
        estado["datos"] = {"nombre": nombre_guardado} if nombre_guardado else {}
        estado["paso"] = PASO_CORRECCION
        estado["estado"] = PREGUNTANDO
        tocar(numero)
        enviar_texto(numero, "Sin problema, corrijamos desde aquí:")
        preguntar_paso(numero, PASO_CORRECCION, estado["datos"])
        return


def manejar_texto(numero, texto):
    estado = conversacion_activa(numero)
    texto_lower = texto.lower().strip()

    if texto_lower in {"cancelar", "salir", "cancel"}:
        if numero in conversaciones:
            del conversaciones[numero]
        enviar_texto(numero, "Tu pedido fue cancelado. Escríbenos cuando quieras iniciar uno nuevo 🙂")
        return

    if estado is None:
        iniciar_conversacion(numero)
        return

    tocar(numero)

    if estado["estado"] == PREGUNTANDO:
        paso = estado["paso"]
        campo_actual = CAMPOS[paso][0]

        es_valido, error = validar_campo(campo_actual, texto)
        if not es_valido:
            enviar_texto(numero, error)
            return

        estado["datos"][campo_actual] = texto.strip()
        estado["paso"] += 1

        if estado["paso"] < len(CAMPOS):
            preguntar_paso(numero, estado["paso"], estado["datos"])
        else:
            estado["estado"] = CONFIRMANDO
            pedir_confirmacion(numero, estado["datos"])
        return

    if estado["estado"] == CONFIRMANDO:
        enviar_botones(
            numero,
            "Usa los botones para confirmar o corregir tu pedido 👇",
            [("btn_confirmar", "✅ Confirmar"), ("btn_corregir", "✏️ Corregir")],
        )
        return


if __name__ == "__main__":
    app.run(port=5000, debug=False)
