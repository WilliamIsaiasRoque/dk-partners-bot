"""
Bot DK Partners - Servidor con webhook (v5)
- Pide todos los datos de una sola vez
- Si faltan campos no críticos, avisa y deja elegir si enviar igual o completar
- Reenvía al jefe con nombre + número del cliente
"""

from flask import Flask, request, jsonify
import requests
from config import ACCESS_TOKEN, PHONE_NUMBER_ID, API_VERSION, VERIFY_TOKEN, NUMERO_JEFE

app = Flask(__name__)

ESPERANDO_DATOS = "esperando_datos"
CONFIRMANDO = "confirmando"

conversaciones = {}

FORMATO_DATOS = (
    "Nombre: Juan Pérez\n"
    "Repuesto: Filtro de aceite\n"
    "Marca: Toyota\n"
    "Modelo: Hilux\n"
    "Año: 2019\n"
    "Placa: ABC-123"
)

MENSAJE_BIENVENIDA = (
    "¡Hola! 👋 Bienvenido a *DK Partners*.\n\n"
    "Para registrar tu pedido, responde con tus datos en este formato:\n\n"
    f"{FORMATO_DATOS}\n\n"
    "Copia el formato, completa tus datos y envíalo 👆"
)

# Campos mínimos sin los cuales no tiene sentido procesar el pedido
CAMPOS_MINIMOS = ["repuesto", "marca"]

# Todos los campos ideales
CAMPOS_TODOS = ["nombre", "repuesto", "marca", "modelo", "año", "placa"]

ALIASES = {
    "año": ["año", "anio", "anño", "year"],
    "repuesto": ["repuesto", "parte", "pieza"],
    "nombre": ["nombre", "name"],
    "marca": ["marca", "brand"],
    "modelo": ["modelo", "model"],
    "placa": ["placa", "plate", "matricula"],
}

EMOJIS = {
    "nombre": "👤",
    "repuesto": "🔧",
    "marca": "🚗",
    "modelo": "📦",
    "año": "📅",
    "placa": "🔖",
}


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


def parsear_datos(texto):
    datos = {}
    for linea in texto.strip().split("\n"):
        if ":" not in linea:
            continue
        partes = linea.split(":", 1)
        clave_raw = partes[0].strip().lower()
        valor = partes[1].strip()
        if not valor:
            continue
        for campo, aliases in ALIASES.items():
            if clave_raw in aliases:
                datos[campo] = valor
                break
    return datos


def armar_resumen(datos):
    lineas = []
    for campo in CAMPOS_TODOS:
        if campo in datos:
            emoji = EMOJIS.get(campo, "•")
            lineas.append(f"{emoji} {campo.capitalize()}: {datos[campo]}")
    return "\n".join(lineas)


def armar_mensaje_jefe(numero_cliente, datos):
    numero_limpio = numero_cliente.replace("@c.us", "")
    resumen = armar_resumen(datos)
    faltantes = [c for c in CAMPOS_TODOS if c not in datos]
    nota = ""
    if faltantes:
        faltantes_str = ", ".join(c.capitalize() for c in faltantes)
        nota = f"\n\n⚠️ _Campos no completados: {faltantes_str}_"
    return (
        f"📋 *Nuevo pedido - DK Partners*\n\n"
        f"📱 Número: +{numero_limpio}\n\n"
        f"{resumen}"
        f"{nota}"
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
    estado = conversaciones.get(numero)

    if boton_id == "btn_confirmar":
        if not estado or estado["estado"] != CONFIRMANDO:
            return
        enviar_texto(numero, "✅ ¡Gracias! Tu pedido fue registrado. En breve te contactamos.")
        enviar_texto(NUMERO_JEFE, armar_mensaje_jefe(numero, estado["datos"]))
        del conversaciones[numero]

    elif boton_id == "btn_completar":
        conversaciones[numero] = {"estado": ESPERANDO_DATOS, "datos": {}}
        enviar_texto(
            numero,
            "Perfecto, vuelve a enviar tus datos completos:\n\n" + FORMATO_DATOS
        )

    elif boton_id == "btn_corregir":
        conversaciones[numero] = {"estado": ESPERANDO_DATOS, "datos": {}}
        enviar_texto(
            numero,
            "Sin problema, vuelve a enviar tus datos:\n\n" + FORMATO_DATOS
        )

    elif boton_id in {"btn_cancelar", "btn_nuevo"}:
        if numero in conversaciones:
            del conversaciones[numero]
        if boton_id == "btn_cancelar":
            enviar_botones(
                numero,
                "Pedido cancelado. ¿Deseas iniciar uno nuevo?",
                [("btn_nuevo", "🚀 Nuevo pedido")],
            )
        else:
            conversaciones[numero] = {"estado": ESPERANDO_DATOS, "datos": {}}
            enviar_texto(numero, MENSAJE_BIENVENIDA)


def manejar_texto(numero, texto):
    texto_lower = texto.lower()

    if texto_lower in {"cancelar", "salir", "cancel"}:
        if numero in conversaciones:
            del conversaciones[numero]
        enviar_botones(
            numero,
            "Pedido cancelado. ¿Deseas iniciar uno nuevo?",
            [("btn_nuevo", "🚀 Nuevo pedido")],
        )
        return

    if numero not in conversaciones:
        conversaciones[numero] = {"estado": ESPERANDO_DATOS, "datos": {}}
        enviar_texto(numero, MENSAJE_BIENVENIDA)
        return

    estado = conversaciones[numero]

    if estado["estado"] == ESPERANDO_DATOS:
        datos = parsear_datos(texto)
        minimos_presentes = all(c in datos for c in CAMPOS_MINIMOS)

        if not minimos_presentes:
            # Sin repuesto ni marca no hay pedido posible
            faltantes = [c for c in CAMPOS_MINIMOS if c not in datos]
            faltantes_str = " y ".join(c.capitalize() for c in faltantes)
            enviar_texto(
                numero,
                f"Para procesar tu pedido necesito al menos *{faltantes_str}*.\n\n"
                f"Vuelve a enviar tus datos:\n\n{FORMATO_DATOS}"
            )
            return

        faltantes = [c for c in CAMPOS_TODOS if c not in datos]
        estado["datos"] = datos
        estado["estado"] = CONFIRMANDO
        resumen = armar_resumen(datos)

        if faltantes:
            # Tiene lo mínimo pero faltan campos opcionales
            faltantes_str = ", ".join(c.capitalize() for c in faltantes)
            enviar_botones(
                numero,
                f"Recibí tu pedido:\n\n{resumen}\n\n"
                f"⚠️ Algunos datos no fueron completados: *{faltantes_str}*.\n\n"
                "¿Seguro que quieres enviar tu cotización así, o prefieres completar los datos?",
                [("btn_confirmar", "✅ Enviar así"), ("btn_completar", "✏️ Completar datos")],
            )
        else:
            # Todo completo
            enviar_botones(
                numero,
                f"Estos son tus datos:\n\n{resumen}\n\n¿Todo correcto?",
                [("btn_confirmar", "✅ Confirmar"), ("btn_corregir", "✏️ Corregir")],
            )
        return

    if estado["estado"] == CONFIRMANDO:
        enviar_botones(
            numero,
            "Usa los botones para continuar 👇",
            [("btn_confirmar", "✅ Confirmar"), ("btn_completar", "✏️ Completar datos")],
        )


if __name__ == "__main__":
    app.run(port=5000, debug=False)
