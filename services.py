import requests
import sett
import json
import time
import random
import unicodedata
from datetime import datetime, timezone
import threading
import os
import sqlite3

# --- Zona horaria robusta ---
# 1) Usa env APP_TZ si está presente; si no, America/Santiago
DEFAULT_TZ = os.getenv("APP_TZ", "America/Santiago")

# 2) Intenta zoneinfo (builtin en Python 3.9+). Si no, intenta pytz. Si no, cae a UTC.
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None

try:
    import pytz  # opcional
except Exception:
    pytz = None

def _now_hhmm_local(tz_name: str = DEFAULT_TZ) -> str:
    """
    Devuelve HH:MM en la zona horaria indicada.
    Prioriza zoneinfo (si está), luego pytz. 
    Si nada está disponible, usa UTC para que sea determinístico.
    """
    try:
        if ZoneInfo is not None:
            return datetime.now(ZoneInfo(tz_name)).strftime("%H:%M")
        if pytz is not None:
            return datetime.now(pytz.timezone(tz_name)).strftime("%H:%M")
    except Exception:
        pass  # si falla el tz_name, cae a UTC
    return datetime.now(timezone.utc).strftime("%H:%M")

# ===================================================================
# BASE DE DATOS - STOCK Y RETIROS
# ===================================================================

DB_PATH = os.getenv("MEDICAI_DB", "medicai.db")

def db_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def db_init():
    with db_conn() as cx:
        cx.execute(
            """
            CREATE TABLE IF NOT EXISTS meds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE COLLATE NOCASE,
                stock INTEGER DEFAULT 0,
                location TEXT,
                price INTEGER
            )
            """
        )
        cx.execute(
            """
            CREATE TABLE IF NOT EXISTS pickups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number TEXT,
                drug TEXT,
                date TEXT,
                hour TEXT,
                freq_days INTEGER,
                status TEXT,
                created_at TEXT
            )
            """
        )
        cx.execute("CREATE INDEX IF NOT EXISTS idx_meds_name ON meds(name)")
        cx.execute("CREATE INDEX IF NOT EXISTS idx_pickups_num ON pickups(number)")
        cx.execute("CREATE INDEX IF NOT EXISTS idx_pickups_date ON pickups(date)")
        print("🗄️ DB lista:", DB_PATH)

# Inicializa DB al cargar el módulo
db_init()

def normalize_text(t: str) -> str:
    t = t.lower()
    t = ''.join(c for c in unicodedata.normalize('NFD', t)
                if unicodedata.category(c) != 'Mn')
    return t

# -----------------------------------------------------------
# Estado para Guía de Ruta / Derivaciones
# -----------------------------------------------------------
global route_sessions
route_sessions = {}  # { number: {"step": "...", "doc_type": "", "ges": "", "edad": int|None, "embarazada": bool|None} }

# ==================== GUÍA DE RUTA: HELPERS ====================
def start_route_flow(number, messageId):
    body = (
        "🏥 *¡Bienvenido a la Guía de Ruta Médica!*\n\n"
        "📋 Te ayudo a entender y gestionar tus documentos médicos paso a paso.\n\n"
        "¿Qué tipo de documento recibiste de tu médico o profesional de la salud?"
    )
    footer = "Guía de Ruta"
    options = [
        "📄 Interconsulta médica",
        "🧾 Orden de exámenes / procedimiento",
        "💊 Receta o indicación de tratamiento",
        "🚨 Derivación urgente",
        "❓ No estoy seguro/a",
    ]
    route_sessions[number] = {"step": "choose_type"}
    return listReply_Message(number, options, body, footer, "route_type", messageId)

def ask_ges(number, messageId):
    body = "¿Tu interconsulta está cubierta por el GES (Garantías Explícitas en Salud)?"
    footer = "Interconsulta"
    options = ["Sí, es GES", "No, no es GES", "No lo sé"]
    route_sessions[number]["step"] = "ask_ges"
    return listReply_Message(number, options, body, footer, "route_ges", messageId)

def interconsulta_instructions(ges_option):
    if ges_option == "Sí, es GES":
        return (
            "✅ *INTERCONSULTA GES (Garantías Explícitas en Salud)*\n\n"
            "📋 **¿Qué es?** Una derivación a especialista con cobertura garantizada por ley.\n\n"
            "📝 **Pasos a seguir:**\n"
            "1️⃣ Lleva tu interconsulta al *SOME del CESFAM* donde estás inscrito\n"
            "2️⃣ Solicita el *número de seguimiento GES* (muy importante)\n"
            "3️⃣ Te contactarán dentro de los plazos GES para coordinar:\n"
            "   • Cita con especialista\n"
            "   • Exámenes previos si se requieren\n"
            "   • Tratamiento garantizado\n\n"
            "⏰ **Plazos GES:** Varían según patología (desde 24h hasta 90 días)\n\n"
            "💡 **Tip:** Guarda tu número de seguimiento para consultar estado.\n\n"
            "¿Quieres configurar un recordatorio de *revisión de estado GES*?"
        )
    else:
        return (
            "ℹ️ *INTERCONSULTA NO GES o sin confirmar*\n\n"
            "📋 **¿Qué es?** Derivación a especialista sin cobertura GES específica.\n\n"
            "📝 **Pasos a seguir:**\n"
            "1️⃣ Lleva la interconsulta al *SOME del CESFAM* donde estás inscrito\n"
            "2️⃣ Confirma que quede *correctamente ingresada* en el sistema\n"
            "3️⃣ Pregunta si necesitas *exámenes previos* antes de la cita\n"
            "4️⃣ Solicita un *número de contacto* para hacer seguimiento\n"
            "5️⃣ Pregunta por los *tiempos de espera estimados*\n\n"
            "⚠️ **Importante:** Los tiempos pueden ser variables (no están garantizados como en GES)\n\n"
            "💡 **Tip:** Si tu condición empeora mientras esperas, consulta nuevamente.\n\n"
            "¿Te indico en qué sede del CESFAM hacer el trámite?"
        )

def exams_steps():
    return (
        "🧪 *ORDEN DE EXÁMENES / PROCEDIMIENTOS*\n\n"
        "📋 **¿Qué es?** Solicitud médica para realizar estudios diagnósticos.\n\n"
        "📝 **Pasos a seguir:**\n"
        "1️⃣ *Agenda tu hora:*\n"
        "   • En SOME del CESFAM (exámenes básicos)\n"
        "   • En laboratorio externo (si así se indica)\n"
        "   • Llamando al número que aparece en la orden\n\n"
        "2️⃣ *Antes de ir, verifica:*\n"
        "   • Si requiere *ayuno* (8-12 horas sin comer)\n"
        "   • Horarios de atención del laboratorio\n"
        "   • Si necesitas suspender algún medicamento\n\n"
        "3️⃣ *El día del examen lleva:*\n"
        "   • Cédula de identidad\n"
        "   • Orden médica original\n"
        "   • Credencial de salud (si tienes)\n\n"
        "4️⃣ *Después del examen:*\n"
        "   • Pregunta cuándo estarán los resultados\n"
        "   • Retira los resultados en la fecha indicada\n"
        "   • Agenda control con tu médico tratante\n\n"
        "💡 **Tip:** Algunos exámenes como glicemia, colesterol, triglicéridos requieren ayuno.\n\n"
        "¿Quieres que revisemos si tu examen requiere *ayuno*?"
    )

def urgent_referral_steps():
    return (
        "🚨 *DERIVACIÓN URGENTE*\n\n"
        "📋 **¿Qué es?** Referencia médica para atención inmediata en servicios de urgencia.\n\n"
        "⚠️ **ACCIÓN INMEDIATA REQUERIDA:**\n"
        "1️⃣ *Dirígete de inmediato* al servicio indicado:\n"
        "   • SAPU (Servicio de Atención Primaria de Urgencia)\n"
        "   • SAR (Servicio de Alta Resolución)\n"
        "   • Urgencia hospitalaria\n\n"
        "2️⃣ *Si tu estado empeora en el trayecto:*\n"
        "   • Llama al 131 (SAMU) inmediatamente\n"
        "   • No esperes, busca el centro de salud más cercano\n\n"
        "3️⃣ *Lleva contigo:*\n"
        "   • Cédula de identidad\n"
        "   • Derivación urgente (papel que te dieron)\n"
        "   • Medicamentos que tomas habitualmente\n"
        "   • Exámenes recientes (si los tienes)\n\n"
        "📞 **Números de emergencia:**\n"
        "   • SAMU: 131\n"
        "   • Bomberos: 132\n"
        "   • Carabineros: 133\n\n"
        "💡 **Importante:** En urgencias médicas reales, NO esperes respuesta del chatbot.\n\n"
        "¿Te indico el SAPU más cercano si me das tu comuna?"
    )

def req_docs_steps():
    return (
        "🧾 *CHECKLIST DE DOCUMENTOS Y REQUISITOS*\n\n"
        "📋 **Documentos básicos que siempre debes llevar:**\n\n"
        "🆔 **Obligatorios:**\n"
        "   • Cédula de identidad vigente\n"
        "   • Orden/interconsulta/receta original\n"
        "   • Credencial del sistema de salud (FONASA/ISAPRE)\n\n"
        "📄 **Documentos adicionales según el caso:**\n"
        "   • Exámenes previos relacionados (últimos 6 meses)\n"
        "   • Cartola del Registro Social de Hogares (para algunos trámites)\n"
        "   • Lista de medicamentos actuales\n"
        "   • Informes médicos anteriores\n"
        "   • Autorización del tutor (menores de edad)\n\n"
        "💡 **Tips importantes:**\n"
        "   • Siempre lleva originales Y fotocopias\n"
        "   • Si eres adulto mayor, puedes ir acompañado\n"
        "   • Anota preguntas que quieras hacer al profesional\n"
        "   • Llega 15 minutos antes de tu hora\n\n"
        "📱 **Recordatorio:** Puedes tomar foto de tus documentos como respaldo.\n\n"
        "¿Quieres que lo guarde y te envíe *recordatorios* personalizados?"
    )
# ==================== FIN HELPERS GUÍA DE RUTA ====================

# Única definición de estado de sesión
global session_states
session_states = {}

global appointment_sessions
appointment_sessions = {}

# -----------------------------------------------------------
# Estado para recordatorio y monitoreo de medicamentos
# -----------------------------------------------------------
global medication_sessions
medication_sessions = {}

# -----------------------------------------------------------
# Sistema de recordatorios de medicamentos
# -----------------------------------------------------------
global MED_REMINDERS
MED_REMINDERS = {}  # { number: [{"name": "med", "times": ["08:00", "20:00"], "last": ""}] }

global REMINDERS_LOCK
REMINDERS_LOCK = threading.Lock()

global REMINDER_THREAD_STARTED
REMINDER_THREAD_STARTED = False

# -----------------------------------------------------------
# Estado para Stock & Retiros
# -----------------------------------------------------------
global stock_sessions
stock_sessions = {}  # { number: { step, drug_name, freq_days, hour, ... } }

# Vinculación retiro -> adherencia
global LAST_RETIRED_DRUG
LAST_RETIRED_DRUG = {}  # { number: "Nombre del medicamento" }

# -----------------------------------------------------------
# Ejemplos de síntomas personalizados por categoría
# -----------------------------------------------------------
EJEMPLOS_SINTOMAS = {
    "respiratorio":    "tos seca, fiebre alta, dificultad para respirar",
    "bucal":           "dolor punzante en muela, sensibilidad al frío, sangrado de encías",
    "infeccioso":      "ardor al orinar, fiebre, orina frecuente",
    "cardiovascular":  "dolor en el pecho al esfuerzo, palpitaciones, mareos",
    "metabolico":      "sed excesiva, orina frecuentemente, pérdida de peso",
    "neurologico":     "dolor de cabeza pulsátil, náuseas, fotofobia",
    "musculoesqueletico": "dolor en espalda baja al levantarte, rigidez",
    "saludmental":     "ansiedad constante, insomnio, aislamiento social",
    "dermatologico":   "granos en cara, picazón intensa, enrojecimiento",
    "otorrinolaringologico": "ojos rojos, picazón ocular, secreción",
    "ginecologico":    "dolor pélvico durante menstruación, flujo anormal",
    "digestivo":       "diarrea, dolor abdominal inferior, gases"
}

# -----------------------------------------------------------
# Recomendaciones generales adaptadas por categoría
# -----------------------------------------------------------
RECOMENDACIONES_GENERALES = {
    "respiratorio": (
        "• Mantén reposo y buena hidratación.\n"
        "• Humidifica el ambiente y ventílalo a diario.\n"
        "• Usa mascarilla si convives con personas de riesgo.\n"
        "• Evita irritantes como humo, polvo o polución.\n"
        "• Controla tu temperatura cada 6 h.\n"
        "Si empeoras o la fiebre supera 39 °C, consulta a un profesional."
    ),
    "bucal": (
        "• Cepíllate los dientes al menos dos veces al día.\n"
        "• Usa hilo dental y enjuagues antisépticos.\n"
        "• Evita alimentos muy ácidos, azúcares o demasiado fríos/calientes.\n"
        "• Controla sangrados o mal aliento persistente.\n"
        "• Programa limpieza dental profesional anualmente.\n"
        "Si el dolor o sangrado continúa, visita a tu odontólogo."
    ),
    "infeccioso": (
        "• Guarda reposo e hidrátate con frecuencia.\n"
        "• Lávate las manos y desinfecta superficies de alto contacto.\n"
        "• Aísla si tu patología puede contagiar (fiebre, erupciones).\n"
        "• Usa mascarilla para no infectar a otros.\n"
        "• Observa tu temperatura y forúnculos si los hubiera.\n"
        "Si persiste la fiebre o hay sangre en secreciones, acude al médico."
    ),
    "cardiovascular": (
        "• Controla tu presión arterial regularmente.\n"
        "• Sigue una dieta baja en sal y grasas saturadas.\n"
        "• Realiza ejercicio moderado (30 min diarios) si tu médico lo autoriza.\n"
        "• Evita tabaco y consumo excesivo de alcohol.\n"
        "• Vigila dolores torácicos, palpitaciones o hinchazón.\n"
        "Si aparece dolor en el pecho o disnea, busca ayuda inmediata."
    ),
    "metabolico": (
        "• Mantén dieta equilibrada y controla los carbohidratos.\n"
        "• Realiza actividad física regular (mín. 150 min/semana).\n"
        "• Mide glucosa/lípidos según pauta médica.\n"
        "• Toma la medicación tal como te la recetaron.\n"
        "• Evita azúcares refinados y grasas trans.\n"
        "Si notas hipoglucemia (sudor, temblores) o hiperglucemia grave, consulta hoy."
    ),
    "neurologico": (
        "• Descansa en ambientes oscuros y silenciosos.\n"
        "• Identifica desencadenantes (estrés, luces, ruido).\n"
        "• Practica técnicas de respiración o relajación.\n"
        "• Lleva un diario de frecuencia y severidad de tus síntomas.\n"
        "• Mantente bien hidratado.\n"
        "Si aparecen déficit neurológicos (desorientación, debilidad), acude al neurólogo."
    ),
    "musculoesqueletico": (
        "• Aplica frío o calor local según indicación.\n"
        "• Realiza estiramientos suaves y evita movimientos bruscos.\n"
        "• Mantén reposo relativo, sin inmovilizar en exceso.\n"
        "• Considera fisioterapia o kinesiterapia.\n"
        "• Analgésicos de venta libre según prospecto.\n"
        "Si el dolor impide tu marcha o persiste más de 72 h, consulta al traumatólogo."
    ),
    "saludmental": (
        "• Practica respiración diafragmática y mindfulness.\n"
        "• Mantén rutina de sueño regular.\n"
        "• Realiza actividad física o caminatas diarias.\n"
        "• Comparte con tu red de apoyo (familia/amigos).\n"
        "• Considera terapia psicológica si los síntomas persisten.\n"
        "Si hay riesgo de daño a ti o a otros, busca ayuda de urgencia."
    ),
    "dermatologico": (
        "• Hidrata la piel con emolientes adecuados.\n"
        "• Evita jabones o detergentes agresivos.\n"
        "• No rasques lesiones ni uses remedios caseros.\n"
        "• Protege tu piel del sol con FPS ≥ 30.\n"
        "• Identifica y evita alérgenos o irritantes.\n"
        "Si notas pus, fiebre o expansión de la lesión, consulta a dermatología."
    ),
    "otorrinolaringologico": (
        "• Realiza lavados nasales y oculares con solución salina.\n"
        "• Evita rascarte o hurgarte en oído y nariz.\n"
        "• Controla exposición a alérgenos (polvo, pólenes).\n"
        "• No automediques antibióticos; sigue prescripción.\n"
        "• Descansa la voz y evita ambientes ruidosos.\n"
        "Si hay dolor intenso, secreción purulenta o pérdida auditiva, acude al ORL."
    ),
    "ginecologico": (
        "• Mantén higiene íntima con productos suaves.\n"
        "• Usa ropa interior de algodón y cambia con frecuencia.\n"
        "• Controla cualquier flujo anormal o sangrado intenso.\n"
        "• Alivia dolor menstrual con calor local y analgésicos según prospecto.\n"
        "• Programa chequeos ginecológicos anuales.\n"
        "Si hay fiebre, dolor severo o sangrado fuera de ciclo, busca atención médica."
    ),
    "digestivo": (
        "• Sigue dieta rica en fibra (frutas, verduras, cereales integrales).\n"
        "• Hidrátate agua o soluciones de rehidratación oral.\n"
        "• Evita comidas muy grasas, picantes o irritantes.\n"
        "• Come despacio y mastica bien.\n"
        "• Controla gases con caminatas suaves.\n"
        "Si observas sangre en heces o dolor abdominal muy intenso, consulta urgente."
    ),
    "default": (
        "• Mantén reposo e hidratación.\n"
        "• Observa tus síntomas a diario.\n"
        "• Consulta a un profesional si empeoras."
    ),
}


# -----------------------------------------------------------
# Funciones de mensajería y parsing de WhatsApp
# -----------------------------------------------------------
def obtener_Mensaje_whatsapp(message):
    """Obtiene el texto o el ID de respuesta de un mensaje de WhatsApp."""
    if 'type' not in message:
        return 'mensaje no reconocido'
    t = message['type']
    if t == 'text':
        return message['text']['body']
    elif t == 'button':
        return message['button']['text']
    elif t == 'interactive':
        interactive = message['interactive']
        if interactive['type'] == 'list_reply':
            return interactive['list_reply']['id']
        elif interactive['type'] == 'button_reply':
            return interactive['button_reply']['id']
    return 'mensaje no procesado'

# ===================================================================
# HELPERS DE NEGOCIO - STOCK & PICKUPS
# ===================================================================

# ============ STOCK ============
def stock_add_or_update(name: str, qty: int, location: str = None, price: int = None):
    with db_conn() as cx:
        cur = cx.execute("SELECT id FROM meds WHERE name=?", (name,))
        if cur.fetchone():
            cx.execute(
                "UPDATE meds SET stock = stock + ?, location=COALESCE(?,location), price=COALESCE(?,price) WHERE name=?",
                (qty, location, price, name)
            )
        else:
            cx.execute(
                "INSERT INTO meds(name, stock, location, price) VALUES(?,?,?,?)",
                (name, max(0, qty), location, price)
            )

def stock_get(name: str):
    with db_conn() as cx:
        cur = cx.execute(
            "SELECT name, stock, COALESCE(location,''), COALESCE(price,0) FROM meds WHERE name=?",
            (name,)
        )
        return cur.fetchone()  # None | (name, stock, location, price)

def stock_decrement(name: str, qty: int):
    with db_conn() as cx:
        cx.execute(
            "UPDATE meds SET stock = CASE WHEN stock-? < 0 THEN 0 ELSE stock-? END WHERE name=?",
            (qty, qty, name)
        )

# ============ PICKUPS (retiros) ============
def pickup_schedule_day(number: str, drug: str, date_iso: str, hour_hhmm: str):
    with db_conn() as cx:
        cx.execute(
            """INSERT INTO pickups(number,drug,date,hour,freq_days,status,created_at)
               VALUES(?,?,?,?,NULL,'pending',datetime('now'))""",
            (number, drug, date_iso, hour_hhmm)
        )

def pickup_schedule_cycle(number: str, drug: str, first_date: str, hour_hhmm: str, freq_days: int):
    with db_conn() as cx:
        cx.execute(
            """INSERT INTO pickups(number,drug,date,hour,freq_days,status,created_at)
               VALUES(?,?,?,?,?,'pending',datetime('now'))""",
            (number, drug, first_date, hour_hhmm, int(freq_days))
        )

def pickup_next_for(number: str, drug: str):
    with db_conn() as cx:
        cur = cx.execute(
            """SELECT id, drug, date, hour, COALESCE(freq_days,0), status
               FROM pickups
               WHERE number=? AND drug=? AND status='pending'
               ORDER BY date ASC LIMIT 1""",
            (number, drug)
        )
        return cur.fetchone()

def pickup_mark(number: str, drug: str, done: bool):
    with db_conn() as cx:
        cur = cx.execute(
            """SELECT id, date, hour, COALESCE(freq_days,0)
               FROM pickups
               WHERE number=? AND drug=? AND status='pending'
               ORDER BY date ASC LIMIT 1""",
            (number, drug)
        )
        row = cur.fetchone()
        if not row:
            return False
        
        pid, date_iso, hour, freq = row
        
        if done and freq > 0:
            # cerrar actual y crear siguiente
            cx.execute("UPDATE pickups SET status='done' WHERE id=?", (pid,))
            from datetime import datetime as _dt, timedelta as _td
            nxt = (_dt.fromisoformat(date_iso).date() + _td(days=freq)).isoformat()
            cx.execute(
                """INSERT INTO pickups(number,drug,date,hour,freq_days,status,created_at)
                   VALUES(?,?,?,?,?,'pending',datetime('now'))""",
                (number, drug, nxt, hour, freq)
            )
            return True
        else:
            cx.execute("UPDATE pickups SET status=? WHERE id=?", ('done' if done else 'missed', pid))
            return True

def pickup_list(number: str):
    with db_conn() as cx:
        cur = cx.execute(
            """SELECT drug, date, hour, COALESCE(freq_days,0), status
               FROM pickups
               WHERE number=?
               ORDER BY date ASC""",
            (number,)
        )
        return cur.fetchall()

# ============ HELPERS DEL FLUJO ============
def _parse_freq_to_days(txt: str) -> int:
    t = normalize_text(txt)
    if "30" in t:
        return 30
    if "15" in t:
        return 15
    import re
    m = re.search(r"(\d+)\s*d(i|í)as", t)
    if m:
        return max(1, int(m.group(1)))
    return 30

def _safe_today_tz(tz_name: str = DEFAULT_TZ):
    try:
        if ZoneInfo is not None:
            return datetime.now(ZoneInfo(tz_name)).date()
        if pytz is not None:
            return datetime.now(pytz.timezone(tz_name)).date()
    except Exception:
        pass
    return datetime.now(timezone.utc).date()

def _hhmm_or_default(txt: str, default="08:00") -> str:
    import re
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", txt)
    if not m:
        return default
    return f"{m.group(1).zfill(2)}:{m.group(2)}"

def check_stock_api(drug_name: str) -> str:
    """
    Stub de conexión. Retorna: 'available' | 'low' | 'none' | 'unknown'.
    Integra aquí Rayen/Medipro cuando tengas endpoint.
    """
    name = normalize_text(drug_name)
    if any(k in name for k in ["paracetamol", "metformina", "losartan", "losartán"]):
        return "available"
    if "amoxicilina" in name:
        return "low"
    return "unknown"


def enviar_Mensaje_whatsapp(data):
    """Envía un payload JSON a la API de WhatsApp."""
    try:
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f"Bearer {sett.WHATSAPP_TOKEN}"
        }
        print("--- Enviando JSON ---")
        try:
            print(json.dumps(json.loads(data), indent=2, ensure_ascii=False))
        except:
            print(data)
        print("---------------------")
        resp = requests.post(sett.WHATSAPP_URL, headers=headers, data=data)
        if resp.status_code == 200:
            print("Mensaje enviado correctamente")
        else:
            print(f"Error {resp.status_code}: {resp.text}")
        return resp.text, resp.status_code
    except Exception as e:
        print(f"Excepción al enviar mensaje: {e}")
        return str(e), 403


def text_Message(number, text):
    return json.dumps({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": number,
        "type": "text",
        "text": {"body": text}
    })


def buttonReply_Message(number, options, body, footer, sedd, messageId):
    buttons = [
        {"type": "reply", "reply": {"id": f"{sedd}_btn_{i+1}", "title": opt if len(opt) <= 20 else opt[:20]}}
        for i, opt in enumerate(options)
    ]
    return json.dumps({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": number,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "footer": {"text": footer},
            "action": {"buttons": buttons}
        }
    })


def listReply_Message(number, options, body, footer, sedd, messageId):
    rows = []
    for i, opt in enumerate(options):
        title = opt if len(opt) <= 24 else opt[:24]
        desc = "" if len(opt) <= 24 else opt
        rows.append({"id": f"{sedd}_row_{i+1}", "title": title, "description": desc})
    return json.dumps({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": number,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body},
            "footer": {"text": footer},
            "action": {"button": "Ver Opciones", "sections": [{"title": "Secciones", "rows": rows}]}
        }
    })


def replyReaction_Message(number, messageId, emoji):
    return json.dumps({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": number,
        "type": "reaction",
        "reaction": {"message_id": messageId, "emoji": emoji}
    })


def markRead_Message(messageId):
    return json.dumps({
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": messageId
    })

# -----------------------------------------------------------
# Funciones para determinar diagnóstico según cada categoría
# -----------------------------------------------------------
def diagnostico_respiratorio(respuestas):
    respuestas = respuestas.lower()
    if (
        "tos leve" in respuestas
        and "estornudos" in respuestas
        and "congestion nasal" in respuestas
    ):
        return (
            "Resfriado común",
            "Autocuidado en casa",
            "Mantén reposo e hidratación, aprovecha líquidos calientes y, si tienes congestión, usa solución salina nasal. Usa mascarilla si estás con personas de riesgo."
        )
    elif (
        "tos seca" in respuestas
        and "fiebre" in respuestas
        and "dolores musculares" in respuestas
    ):
        return (
            "Gripe (influenza)",
            "Autocuidado + control",
            "Reposa, mantén una buena hidratación y utiliza paracetamol o ibuprofeno según prospecto. Controla tu temperatura cada 6 h."
        )
    elif (
        "dolor al tragar" in respuestas
        and "fiebre" in respuestas
        and "garganta inflamada" in respuestas
    ):
        return (
            "Faringitis / Amigdalitis / Laringitis",
            "Requiere atención si persiste",
            "Haz gárgaras con agua tibia y sal, hidratación abundante. Si el dolor dura más de 48 h o hay placas en la garganta, consulta al médico para posible tratamiento antibiótico."
        )
    elif (
        "tos persistente" in respuestas
        and "flema" in respuestas
        and "pecho apretado" in respuestas
    ):
        return (
            "Bronquitis",
            "Medir gravedad",
            "Evita irritantes (humo, polvo), mantente hidratado y usa expectorantes de venta libre. Si empeora la dificultad para respirar o la fiebre persiste, acude al médico."
        )
    elif (
        "fiebre alta" in respuestas
        and "dificultad respiratoria" in respuestas
    ):
        return (
            "Neumonía",
            "Urgencia médica",
            "Esta combinación sugiere neumonía: acude de inmediato a un servicio de urgencias u hospital."
        )
    elif (
        "opresión torácica" in respuestas
        and "silbidos" in respuestas
    ):
        return (
            "Asma",
            "Evaluar crisis",
            "Si tienes salbutamol, úsalo según indicaciones. Si no mejora en 15 min o empeora la respiración, llama al 131 o acude a urgencias."
        )
    elif (
        "estornudos" in respuestas
        and "congestión nasal" in respuestas
        and "picazón" in respuestas
    ):
        return (
            "Rinitis alérgica",
            "Tratamiento ambulatorio",
            "Evita alérgenos (polvo, pólenes), antihistamínicos orales y lavados nasales con solución salina. Consulta a tu alergólogo si persiste."
        )
    elif (
        "tos seca" in respuestas
        and "fiebre" in respuestas
        and "pérdida de olfato" in respuestas
    ):
        return (
            "COVID-19",
            "Sospecha, test y aislamiento",
            "Aíslate y haz prueba PCR lo antes posible. Monitorea tus síntomas cada día y consulta si aparece dificultad respiratoria."
        )
    else:
        return None, None, None


def diagnostico_bucal(respuestas):
    respuestas = respuestas.lower()
    if (
        "dolor punzante" in respuestas
        and "sensibilidad" in respuestas
    ):
        return (
            "Caries",
            "Requiere atención odontológica",
            "Mantén una higiene bucal rigurosa (cepillado y uso de hilo dental), evita alimentos muy ácidos o muy fríos/calientes y consulta a un odontólogo para tratar la cavidad."
        )
    elif (
        "encías inflamadas" in respuestas
        and "sangrado" in respuestas
        and "mal aliento" in respuestas
    ):
        return (
            "Gingivitis",
            "Higiene mejorada + control",
            "Mejora tu higiene bucal con cepillado suave dos veces al día, uso de hilo dental y enjuagues antisépticos. Si los síntomas persisten tras una semana, visita a tu dentista."
        )
    elif (
        "encías retraídas" in respuestas
        and "dolor al masticar" in respuestas
        and "movilidad" in respuestas
    ):
        return (
            "Periodontitis",
            "Atención odontológica urgente",
            "Acude al odontólogo de inmediato; podrías necesitar raspado y alisado radicular para frenar la pérdida de tejido periodontal."
        )
    elif (
        "llagas" in respuestas
        and "pequeñas" in respuestas
        and "dolorosas" in respuestas
    ):
        return (
            "Aftas bucales",
            "Manejo local + observar",
            "Evita alimentos ácidos o picantes, enjuaga con agua tibia y sal, y utiliza gel o crema tópica para aliviar el dolor. Si duran más de 2 semanas, consulta a tu dentista."
        )
    elif (
        "dolor mandibular" in respuestas
        and "tensión" in respuestas
        and "rechinar" in respuestas
    ):
        return (
            "Bruxismo",
            "Uso de férula / evaluación",
            "Considera usar una férula de descarga nocturna, técnicas de relajación y fisioterapia mandibular. Evalúa con un odontólogo o especialista en ATM."
        )
    else:
        return None, None, None


def diagnostico_infeccioso(respuestas):
    respuestas = respuestas.lower()
    if (
        "ardor al orinar" in respuestas
        and "fiebre" in respuestas
        and "orina frecuente" in respuestas
    ):
        return (
            "Infección urinaria",
            "Atención médica no urgente",
            "Hidrátate abundantemente, evita irritantes (café, alcohol) y consulta al médico si persiste o hay sangre en la orina."
        )
    elif (
        "diarrea" in respuestas
        and "vómitos" in respuestas
        and "dolor abdominal" in respuestas
    ):
        return (
            "Gastroenteritis",
            "Hidratación + reposo",
            "Mantén reposo, usa soluciones de rehidratación oral y observa si hay signos de deshidratación. Acude al médico si empeora."
        )
    elif (
        "dolor estomacal persistente" in respuestas
        and "náuseas" in respuestas
    ):
        return (
            "Infección por Helicobacter pylori",
            "Evaluación médica necesaria",
            "Solicita pruebas de H. pylori y consulta con tu médico para iniciar tratamiento antibiótico y protector gástrico."
        )
    elif (
        "fiebre" in respuestas
        and "erupción" in respuestas
        and "ampollas" in respuestas
    ):
        return (
            "Varicela",
            "Reposo + aislamiento",
            "Mantén reposo, controla la fiebre con paracetamol y evita rascarte. Aísla hasta que todas las ampollas se sequen."
        )
    elif (
        "manchas rojas" in respuestas
        and "tos" in respuestas
        and "conjuntivitis" in respuestas
    ):
        return (
            "Sarampión",
            "Evaluación médica urgente",
            "Acude de inmediato al médico, confirma tu estado de vacunación y evita el contacto con personas susceptibles."
        )
    elif (
        "erupción leve" in respuestas
        and "inflamación ganglionar" in respuestas
    ):
        return (
            "Rubéola",
            "Observación + test",
            "Realiza prueba de rubéola y evita el contacto con embarazadas. Sigue las indicaciones de tu médico."
        )
    elif (
        "dolor en mejillas" in respuestas
        and "fiebre" in respuestas
    ):
        return (
            "Paperas",
            "Cuidado en casa + control",
            "Aplica calor suave en la zona, toma analgésicos según indicación y descansa. Consulta si hay complicaciones."
        )
    elif (
        "cansancio" in respuestas
        and "piel amarilla" in respuestas
        and "fiebre" in respuestas
    ):
        return (
            "Hepatitis A/B/C",
            "Evaluación inmediata y pruebas de laboratorio",
            "Solicita pruebas de función hepática y marcadores virales. Acude al médico cuanto antes."
        )
    else:
        return None, None, None


def diagnostico_cardiovascular(respuestas):
    respuestas = respuestas.lower()
    if (("presion" in respuestas or "presión" in respuestas)
        and ("sin síntomas" in respuestas or "alta" in respuestas)):
        return (
            "Hipertensión arterial",
            "Control ambulatorio",
            "Controla tu presión arterial regularmente, lleva una dieta baja en sal, haz ejercicio moderado y sigue las indicaciones de tu médico."
        )
    elif ("cansancio" in respuestas
          and "falta de aire" in respuestas
          and "hinchaz" in respuestas):
        return (
            "Insuficiencia cardíaca",
            "Evaluación clínica pronta",
            "Monitorea tu peso y la hinchazón, reduce la ingesta de líquidos si está indicado y consulta a un cardiólogo lo antes posible."
        )
    elif "palpitaciones" in respuestas:
        return (
            "Arritmias",
            "Requiere electrocardiograma",
            "Agenda un electrocardiograma y consulta con un especialista en cardiología para evaluar tu ritmo cardíaco."
        )
    elif ("dolor en el pecho" in respuestas
          and "brazo izquierdo" in respuestas
          and ("sudor frio" in respuestas or "sudor frío" in respuestas)):
        return (
            "Infarto agudo al miocardio",
            "Urgencia médica inmediata",
            "Llama a emergencias (SAMU 131) de inmediato o acude al hospital más cercano. No esperes."
        )
    elif ("dolor al caminar" in respuestas
          and "desaparece" in respuestas):
        return (
            "Aterosclerosis (angina)",
            "Evaluación médica en menos de 24 hrs",
            "Evita esfuerzos intensos hasta la valoración, y consulta con un cardiólogo para pruebas de perfusión o angiografía."
        )
    else:
        return None, None, None


def diagnostico_metabolico(respuestas):
    respuestas = respuestas.lower()
    if ("sed excesiva" in respuestas
        and "orina frecuentemente" in respuestas
        and "pérdida de peso" in respuestas):
        return (
            "Diabetes tipo 1",
            "Evaluación médica urgente",
            "Acude a un centro de salud para medición de glucosa en sangre y valoración endocrinológica inmediata."
        )
    elif ("cansancio" in respuestas
          and "visión borrosa" in respuestas
          and "sobrepeso" in respuestas):
        return (
            "Diabetes tipo 2",
            "Control y exámenes de laboratorio",
            "Realiza un hemograma de glucosa y HbA1c, ajusta dieta y actividad física, y programa consulta con endocrinología."
        )
    elif ("piel seca" in respuestas
          and ("intolerancia al frio" in respuestas or "frío" in respuestas)):
        return (
            "Hipotiroidismo",
            "Control endocrinológico",
            "Solicita perfil de tiroides (TSH, T4) y ajusta tu tratamiento si ya estás en seguimiento."
        )
    elif (("nerviosismo" in respuestas
           and ("sudoracion" in respuestas or "sudoración" in respuestas))
          and "pérdida de peso" in respuestas):
        return (
            "Hipertiroidismo",
            "Evaluación clínica y TSH",
            "Pide análisis de tiroides y consulta con endocrinólogo para manejo con antitiroideos o terapia con yodo."
        )
    elif ("circunferencia abdominal" in respuestas
          and ("presion alta" in respuestas or "presión alta" in respuestas)):
        return (
            "Síndrome metabólico",
            "Evaluación de riesgo cardiovascular",
            "Controla tu peso, presión y lípidos. Programa un chequeo cardiovascular completo."
        )
    elif "colesterol" in respuestas and "antecedentes" in respuestas:
        return (
            "Colesterol alto",
            "Prevención + examen de perfil lipídico",
            "Realiza un perfil de lípidos, ajusta dieta baja en grasas saturadas y considera estatinas si lo indica tu médico."
        )
    elif "dolor en la articulación" in respuestas and "dedo gordo" in respuestas:
        return (
            "Gota",
            "Evaluación médica ambulatoria",
            "Confirma con ácido úrico en sangre, modera el consumo de purinas y consulta con reumatología."
        )
    else:
        return None, None, None


def diagnostico_neurologico(respuestas):
    respuestas = respuestas.lower()
    if ("dolor de cabeza" in respuestas
        and ("pulsatil" in respuestas or "pulsátil" in respuestas)
        and ("nauseas" in respuestas or "náuseas" in respuestas)
        and "fotofobia" in respuestas):
        return (
            "Migraña",
            "Manejo con analgésicos + control",
            "Descansa en ambiente oscuro, utiliza triptanes o analgésicos según prescripción y lleva un diario de desencadenantes."
        )
    elif ("dolor de cabeza" in respuestas
          and "estrés" in respuestas):
        return (
            "Cefalea tensional",
            "Autocuidado + relajación",
            "Aplica compresas frías o calientes, practica técnicas de relajación y corrige postura."
        )
    elif ("sacudidas" in respuestas
          and "desmayo" in respuestas
          and ("confusion" in respuestas or "confusión" in respuestas)):
        return (
            "Epilepsia",  
            "Evaluación neurológica urgente",
            "Registra los episodios y consulta con neurología para EEG y ajuste de medicación anticonvulsivante."
        )
    elif ("temblores" in respuestas
          and "lentitud" in respuestas
          and "rigidez" in respuestas):
        return (
            "Parkinson",
            "Evaluación neurológica",
            "Agrega fisioterapia y consulta con neurología para iniciar tratamiento con levodopa o agonistas."
        )
    elif (("perdida de memoria" in respuestas or "pérdida de memoria" in respuestas)
          and "desorientación" in respuestas):
        return (
            "Alzheimer",
            "Evaluación por especialista",
            "Realiza pruebas cognitivas y consulta con neurología o geriatría para manejo multidisciplinario."
        )
    elif ("fatiga" in respuestas
          and "hormigueos" in respuestas
          and ("vision borrosa" in respuestas or "visión borrosa" in respuestas)):
        return (
            "Esclerosis múltiple",
            "Derivación neurológica",
            "Consulta con neurología para RMN cerebral y lumbar y comenzar terapia modificadora de enfermedad."
        )
    elif ("dolor facial" in respuestas
          and "punzante" in respuestas):
        return (
            "Neuralgia del trigémino",
            "Tratamiento farmacológico",
            "Inicia carbamazepina o gabapentina según indicación médica y valora bloqueo del nervio si persiste."
        )
    else:
        return None, None, None

def diagnostico_musculoesqueletico(respuestas):
    respuestas = respuestas.lower()
    if (
        "dolor en espalda baja" in respuestas
        and "sin golpe" in respuestas
    ):
        return (
            "Lumbalgia",
            "Reposo + fisioterapia",
            "Aplica calor local, evita levantar pesos y realiza estiramientos suaves con guía de kinesiología."
        )
    elif (
        "dolor articular" in respuestas
        and ("inflamacion" in respuestas or "inflamación" in respuestas)
        and "rigidez" in respuestas
    ):
        return (
            "Artritis",
            "Evaluación médica reumatológica",
            "Solicita marcadores inflamatorios (VSG, PCR) y consulta con reumatología para manejo con AINEs o DMARDs."
        )
    elif (
        "dolor articular" in respuestas
        and "uso" in respuestas
        and ("sin inflamacion" in respuestas or "sin inflamación" in respuestas)
    ):
        return (
            "Artrosis",
            "Ejercicio suave + control",
            "Refuerza musculatura con ejercicios de bajo impacto y considera condroprotectores si lo indica tu médico."
        )
    elif (
        "dolor muscular generalizado" in respuestas
        and "fatiga" in respuestas
    ):
        return (
            "Fibromialgia",
            "Manejo crónico integral",
            "Combina ejercicio aeróbico suave, terapia cognitivo‑conductual y manejo del dolor con tu médico."
        )
    elif (
        "dolor al mover" in respuestas
        and "sobreuso" in respuestas
    ):
        return (
            "Tendinitis",
            "Reposo local + analgésicos",
            "Aplica hielo, inmoviliza la zona en reposo y toma AINEs según indicación médica."
        )
    elif (
        "dolor localizado" in respuestas
        and "bursa" in respuestas
    ):
        return (
            "Bursitis",
            "Reposo + hielo + evaluación",
            "Aplica frío local y consulta con ortopedia o fisiatría si persiste para posible infiltración."
        )
    elif "torcedura" in respuestas:
        return (
            "Esguince",
            "Reposo, hielo, compresión, elevación (RICE)",
            "Sujeta con venda elástica, eleva la zona y reevalúa en 48 h con un profesional."
        )
    else:
        return None, None, None


def diagnostico_salud_mental(respuestas):
    respuestas = respuestas.lower()
    if (
        "ansiedad" in respuestas
        and "dificultad para relajarse" in respuestas
    ):
        return (
            "Ansiedad generalizada",
            "Apoyo psicoemocional + técnicas de autorregulación",
            "Práctica respiración diafragmática, mindfulness y considera terapia cognitivo‑conductual."
        )
    elif (
        "tristeza persistente" in respuestas
        and "pérdida de interés" in respuestas
        and "fatiga" in respuestas
    ):
        return (
            "Depresión",
            "Apoyo clínico + evaluación emocional",
            "Consulta con psiquiatría o psicología para evaluar terapia y, si es necesario, antidepresivos."
        )
    elif (
        "cambios extremos" in respuestas
        and "hiperactividad" in respuestas
    ):
        return (
            "Trastorno bipolar",
            "Evaluación profesional integral",
            "Valora estabilizadores del ánimo con psiquiatría y seguimiento estrecho."
        )
    elif (
        "ataques de pánico" in respuestas
        and "miedo a morir" in respuestas
    ):
        return (
            "Trastorno de pánico",
            "Manejo con técnicas de respiración + orientación",
            "Aprende respiración controlada y considera ISRS o benzodiacepinas en pauta corta."
        )
    elif (
        "flashbacks" in respuestas
        and "hipervigilancia" in respuestas
    ):
        return (
            "TEPT",
            "Acompañamiento psicológico",
            "Terapia de exposición y EMDR con psicólogo especializado."
        )
    elif (
        "compulsiones" in respuestas
        or "pensamientos repetitivos" in respuestas
    ):
        return (
            "TOC",
            "Detección temprana + derivación especializada",
            "Terapia cognitivo‑conductual con ERP y, si hace falta, ISRS a dosis altas."
        )
    else:
        return None, None, None


def diagnostico_dermatologico(respuestas):
    respuestas = respuestas.lower()
    if (
        "granos" in respuestas
        and ("cara" in respuestas or "pecho" in respuestas or "espalda" in respuestas)
    ):
        return (
            "Acné",
            "Manejo domiciliario + higiene",
            "Limpia con jabón suave, evita productos comedogénicos y consulta dermatología si persiste."
        )
    elif (
        "piel seca" in respuestas
        and "enrojecida" in respuestas
        and ("picazon" in respuestas or "picazón" in respuestas)
    ):
        return (
            "Dermatitis atópica",
            "Hidratación + evitar alérgenos",
            "Emuslivos frecuentes, evita jabones agresivos y considera corticoides tópicos si lo indica tu médico."
        )
    elif (
        "placas rojas" in respuestas
        and "escamas" in respuestas
        and "engrosadas" in respuestas
    ):
        return (
            "Psoriasis",
            "Evaluación dermatológica",
            "Consulta dermatológica para valorar calcipotriol o fototerapia."
        )
    elif (
        "ronchas" in respuestas
        and "aparecen" in respuestas
        and ("rapido" in respuestas or "rápido" in respuestas)
    ):
        return (
            "Urticaria",
            "Posible alergia / estrés",
            "Antihistamínicos orales y evita desencadenantes identificados."
        )
    elif (
        ("lesion redonda" in respuestas or "lesión redonda" in respuestas)
        and "borde rojo" in respuestas
    ):
        return (
            "Tiña",
            "Antimicótico tópico",
            "Aplica clotrimazol o terbinafina localmente durante 2 semanas."
        )
    elif (
        "ampolla" in respuestas
        and ("labio" in respuestas or "genitales" in respuestas)
    ):
        return (
            "Herpes simple",
            "Antiviral tópico u oral",
            "Inicia aciclovir tópico o valaciclovir oral según prescripción."
        )
    elif (
        "bultos" in respuestas
        and "duros" in respuestas
    ):
        return (
            "Verrugas",
            "Tratamiento tópico o crioterapia",
            "Aplica ácido salicílico o valora crioterapia con dermatólogo."
        )
    else:
        return None, None, None


def diagnostico_otorrinolaringologico(respuestas):
    respuestas = respuestas.lower()
    if (
        "ojos rojos" in respuestas
        and ("picazon" in respuestas or "picazón" in respuestas)
        and "secrecion" in respuestas
    ):
        return (
            "Conjuntivitis",
            "Higiene + evitar contacto",
            "Lava con soluciones salinas y evita frotar. Consulta si hay secreción purulenta."
        )
    elif (
        ("dolor de oido" in respuestas or "dolor de oído" in respuestas)
        and "fiebre" in respuestas
        and "tapado" in respuestas
    ):
        return (
            "Otitis",
            "Evaluación médica (especialmente en niños)",
            "Consulta pronto para antibióticos si está indicado y analgésicos para el dolor."
        )
    elif (
        "presion en cara" in respuestas
        and "secrecion nasal espesa" in respuestas
        and "dolor de cabeza" in respuestas
    ):
        return (
            "Sinusitis",
            "Tratamiento ambulatorio",
            "Descongestionantes y antibiótico si persiste más de 10 días."
        )
    elif (
        ("vision borrosa" in respuestas or "visión borrosa" in respuestas)
        and "halos" in respuestas
        and "dolor ocular" in respuestas
    ):
        return (
            "Glaucoma",
            "Evaluación urgente",
            "Agudeza visual y presión intraocular con oftalmólogo de inmediato."
        )
    elif (
        "dificultad para ver" in respuestas
        and ("vision nublada" in respuestas or "visión nublada" in respuestas)
    ):
        return (
            "Cataratas",
            "Derivación oftalmológica",
            "Consulta oftalmológica para valorar cirugía de cataratas."
        )
    elif (
        "zumbido" in respuestas
        or "disminucion auditiva" in respuestas
        or "disminución auditiva" in respuestas
    ):
        return (
            "Pérdida auditiva",
            "Evaluación ORL o audiometría",
            "Realiza audiometría y consulta con otorrinolaringólogo para rehabilitación auditiva."
        )
    else:
        return None, None, None



def diagnostico_ginecologico(respuestas):
    respuestas = respuestas.lower()
    if (
        "dolor al orinar" in respuestas
        and ("orina turbia" in respuestas or "turbia" in respuestas)
        and "fiebre" in respuestas
    ):
        return (
            "Cistitis",
            "Hidratación + atención médica si persiste",
            "Bebe abundante agua y consulta si hay sangre o dolor severo."
        )
    elif (
        "flujo anormal" in respuestas
        and ("picazon" in respuestas or "picazón" in respuestas or "ardor" in respuestas)
    ):
        return (
            "Vaginitis",
            "Evaluación ginecológica ambulatoria",
            "Toma muestra de flujo y pide tratamiento según cultivo."
        )
    elif (
        ("dolor pelvico" in respuestas or "dolor pélvico" in respuestas)
        and ("menstruacion dolorosa" in respuestas or "menstruación dolorosa" in respuestas)
    ):
        return (
            "Endometriosis",
            "Control ginecológico recomendado",
            "Ultrasonido pélvico y manejo hormonal con tu ginecólogo."
        )
    elif (
        "irritabilidad" in respuestas
        and "dolor mamario" in respuestas
        and "cambios premenstruales" in respuestas
    ):
        return (
            "Síndrome premenstrual (SPM)",
            "Manejo con hábitos y control hormonal",
            "Lleva registro de tu ciclo, dieta equilibrada y valora anticonceptivos hormonales."
        )
    elif (
        "dolor testicular" in respuestas
        or ("dolor" in respuestas and "perineal" in respuestas)
    ):
        return (
            "Prostatitis",
            "Evaluación médica inmediata (urología)",
            "Antibióticos según urocultivo y manejo del dolor con antiinflamatorios."
        )
    else:
        return None, None, None


def diagnostico_digestivo(respuestas):
    respuestas = respuestas.lower()
    if (
        "acidez" in respuestas
        and "ardor" in respuestas
        and ("comer" in respuestas or "aliment" in respuestas)
    ):
        return (
            "Reflujo gastroesofágico (ERGE)",
            "Control dietético + posible medicación",
            "Evita alimentos grasos, eleva la cabecera de la cama y considera IBP según médico."
        )
    elif (
        "diarrea" in respuestas
        and "dolor abdominal" in respuestas
    ):
        return (
            "Colitis",
            "Observación + evitar irritantes",
            "Hidratación con sales y dieta BRAT. Consulta si hay sangre o fiebre alta."
        )
    elif (
        ("evacuaciones dificiles" in respuestas or "evacuaciones difíciles" in respuestas)
        and "dolor abdominal" in respuestas
    ):
        return (
            "Estreñimiento",
            "Hidratación + fibra + hábitos",
            "Aumenta fibra y agua, realiza ejercicio y valora laxantes suaves."
        )
    elif (
        "dolor al evacuar" in respuestas
        and ("sangrado" in respuestas or "sangre" in respuestas)
        and ("picazon" in respuestas or "picazón" in respuestas)
    ):
        return (
            "Hemorroides",
            "Higiene + dieta + evaluación médica si persiste",
            "Baños de asiento, crema de hidrocortisona y dieta rica en fibra."
        )
    elif (
        "gases" in respuestas
        and ("hinchazon" in respuestas or "hinchazón" in respuestas)
        and "diarrea" in respuestas
        and ("lacteos" in respuestas or "lácteos" in respuestas)
    ):
        return (
            "Intolerancia a la lactosa",
            "Evitar lácteos + prueba de tolerancia",
            "Sustituye por leches sin lactosa y realiza test de hidrógeno espirado."
        )
    else:
        return None, None, None

diagnostico_saludmental = diagnostico_salud_mental

def handle_orientacion(text, number, messageId):
    parts = text.split(":", 1)
    if len(parts) < 2:
        return text_Message(
            number,
            "Por favor, proporciona la información en el formato:\n"
            "orientacion_<categoria>_<paso>:<tus síntomas>"
        )

    header, content = parts[0], parts[1].strip()
    hp = header.split("_")
    if len(hp) < 3 or hp[0] != "orientacion":
        return text_Message(number, "Formato incorrecto para orientación de síntomas.")
    categoria, paso = hp[1], hp[2]

    known = {
        "respiratorio": [
            "tos leve", "tos seca", "tos persistente", "tos",
            "fiebre", "fiebre alta", "estornudos", "congestion nasal", "congestión nasal",
            "dolor de garganta", "dolor al tragar", "garganta inflamada",
            "cansancio", "dolores musculares", "dolor en el pecho", "pecho apretado",
            "flema", "silbidos", "picazón", "picazon", "pérdida de olfato",
            "opresión torácica", "opresion toracica"
        ],
        "bucal": [
            "dolor punzante", "sensibilidad",
            "encías inflamadas", "encías retraídas",
            "sangrado", "mal aliento",
            "llagas", "pequeñas", "dolorosas",
            "dolor al masticar", "tensión mandibular",
            "movilidad", "dolor mandibular", "rechinar"
        ],
        "infeccioso": [
            "ardor al orinar", "fiebre", "orina frecuente",
            "diarrea", "vómitos", "dolor abdominal",
            "manchas", "picazón", "picazon", "ictericia"
        ],
        "cardiovascular": [
            "dolor en el pecho", "palpitaciones", "cansancio", "mareos",
            "falta de aire", "hinchazón", "hinchazon", "sudor frío", "sudor frio",
            "náuseas", "presión", "presion",
            "dolor al caminar", "desaparece", "brazo izquierdo"
        ],
        "metabolico": [
            "sed excesiva", "orina frecuentemente", "pérdida de peso", "aumento de peso",
            "cansancio", "visión borrosa", "vision borrosa", "colesterol", "antecedentes",
            "nerviosismo", "sudoración", "sudoracion", "circunferencia abdominal",
            "sobrepeso", "piel seca", "intolerancia al frio", "intolerancia al frío"
        ],
        "neurologico": [
            "dolor de cabeza", "pulsatil", "pulsátil", "náuseas", "nauseas",
            "fotofobia", "estrés", "estres", "tensión", "tension",
            "temblores", "lentitud", "rigidez", "sacudidas", "desmayo",
            "confusión", "confusion", "pérdida de memoria", "perdida de memoria",
            "desorientación", "desorientacion",
            "hormigueo", "fatiga", "dolor facial", "punzante"
        ],
        "musculoesqueletico": [
            "dolor en espalda baja", "dolor articular", "inflamación",
            "rigidez", "dolor muscular", "fatiga", "torcedura", "bursa"
        ],
        "saludmental": [
            "ansiedad", "dificultad para relajarse", "tristeza persistente",
            "pérdida de interés", "fatiga", "cambios extremos", "hiperactividad",
            "ataques de pánico", "miedo a morir", "flashbacks", "hipervigilancia",
            "compulsiones", "pensamientos repetitivos"
        ],
        "dermatologico": [
            "granos", "picazón", "picazon", "erupción", "erupcion",
            "escamas", "engrosadas", "ampolla", "ronchas", "aparecen",
            "lesión redonda", "lesion redonda", "borde rojo", "bultos", "duros"
        ],
        "otorrinolaringologico": [
            "ojos rojos", "picazón", "picazon", "secreción", "secrecion",
            "dolor de oído", "dolor de oido", "fiebre", "tapado",
            "presion en cara", "presión en cara", "secrecion nasal espesa",
            "zumbido", "visión borrosa", "vision borrosa", "halos",
            "dificultad para ver", "vision nublada", "visión nublada"
        ],
        "ginecologico": [
            "dolor al orinar", "orina turbia", "turbia", "fiebre",
            "flujo anormal", "picazón", "picazon", "ardor",
            "dolor pélvico", "dolor pelvico", "menstruación dolorosa",
            "menstruacion dolorosa", "sangrado menstrual",
            "irritabilidad", "dolor mamario", "cambios premenstruales",
            "dolor testicular", "perineal"
        ],
        "digestivo": [
            "acidez", "ardor", "comer", "aliment", "diarrea",
            "estreñimiento", "evacuaciones difíciles", "evacuaciones dificiles",
            "dolor abdominal", "dolor al evacuar", "gases", "hinchazón",
            "hinchazon", "sangrado", "lacteos", "lácteos"
        ],
    }



    # Paso 1: extracción → confirmación con botones
    if paso == "extraccion":
        sym_list = known.get(categoria, [])
        detectados = [s for s in sym_list if s in content.lower()]
        session_states[number]["texto_inicial"] = content

        body = (
            f"🩺 He detectado estos síntomas de *{categoria}*:\n"
            + "\n".join(f"- {d}" for d in (detectados or ["(ninguno)"]))
        )
        footer = "¿Es correcto?"
        buttons = ["Si ✅", "No ❌"]
        return buttonReply_Message(
            number,
            buttons,
            body,
            footer,
            f"orientacion_{categoria}_confirmacion",
            messageId
        )

    # Paso 2: confirmación y diagnóstico
    if paso == "confirmacion":
        # 1) si vino de un botón, content será algo_btn_1 o algo_btn_2
        if content.endswith("_btn_1"):
            respuesta = "si"
        elif content.endswith("_btn_2"):
            respuesta = "no"
        else:
            # 2) si no, quizá vino por texto libre
            respuesta = content.lower().split()[0]

        if respuesta == "si":
            original = session_states[number].get("texto_inicial", "")
            func = globals().get(f"diagnostico_{categoria}")
            if not func:
                cuerpo = "Categoría no reconocida para diagnóstico."
            else:
                salida = func(original)
                if len(salida) == 3:
                    diag, nivel, reco = salida
                else:
                    diag, nivel = salida
                    reco = ""
                if diag:
                    cierre_texto = RECOMENDACIONES_GENERALES.get(
                        categoria,
                        RECOMENDACIONES_GENERALES["default"]
                    )
                    cierre_general = f"\n\nRecomendaciones generales:\n{cierre_texto}"
                    cuerpo = (
                        f"Basado en tus síntomas, podrías tener: *{diag}*.\n"
                        f"Nivel de alerta: *{nivel}*.\n\n"
                        f"{reco}"
                        f"{cierre_general}"
                    )
                else:
                    cuerpo = (
                        "No se pudo determinar un diagnóstico con la información proporcionada. "
                        "Te recomiendo acudir a un profesional para una evaluación completa."
                    )
            session_states.pop(number, None)
            return text_Message(number, cuerpo)
        else:
            session_states[number]["paso"] = "extraccion"
            return text_Message(number, "Entendido. Por favor describe nuevamente tus síntomas.")



# -----------------------------------------------------------
# Función principal del chatbot
# -----------------------------------------------------------

def administrar_chatbot(text, number, messageId, name):
    # Normaliza texto
    text = normalize_text(text)
    
    # 1) marcar leído y reacción inicial
    enviar_Mensaje_whatsapp(markRead_Message(messageId))
    enviar_Mensaje_whatsapp(replyReaction_Message(number, messageId, "🩺"))

    # 👉 INICIALIZA list_responses AQUÍ
    list_responses = []
    

# 2) Mapeo de IDs de botones (button_reply) y filas de lista (list_reply)
    ui_mapping = {
        # ----- Guía de Ruta: mapeo de listas/botones -----
        # Selección de tipo de documento
        "route_type_row_1": "interconsulta",
        "route_type_row_2": "examenes",
        "route_type_row_3": "receta",
        "route_type_row_4": "derivacion_urgente",
        "route_type_row_5": "no_seguro",

        # Pregunta GES
        "route_ges_row_1": "ges_si",
        "route_ges_row_2": "ges_no",
        "route_ges_row_3": "ges_ns",

        # Botones auxiliares del flujo
        "route_exams_fast_btn_1": "ayuno_si",
        "route_exams_fast_btn_2": "ayuno_no",

        "route_rx_btn_1": "rx_recordatorios_si",
        "route_rx_btn_2": "rx_recordatorios_no",

        "route_urgent_btn_1": "urgent_sapu_si",
        "route_urgent_btn_2": "urgent_sapu_no",

        "route_save_btn_1": "guardar_si",
        "route_save_btn_2": "guardar_no",

        "route_some_site_btn_1": "sede_si",
        "route_some_site_btn_2": "sede_no",

        "route_ges_reminder_btn_1": "ges_reminder_si",
        "route_ges_reminder_btn_2": "ges_reminder_no",

        "route_close_btn_1": "cerrar_guardar_si",
        "route_close_btn_2": "cerrar_guardar_no",

        # Menú principal
        "menu_principal_btn_1": "agendar cita",
        "menu_principal_btn_2": "recordatorio de medicamento",
        "menu_principal_btn_3": "menu_mas",

        # filas del listado "Más opciones"
        "menu_mas_row_1": "orientacion de sintomas",
        "menu_mas_row_2": "guia de ruta",
        "menu_mas_row_3": "stock de medicamentos",
        "menu_mas_row_4": "gestionar recordatorios",

        # Especialidades – página 1
        "cita_especialidad_row_1": "medicina general",
        "cita_especialidad_row_2": "pediatría",
        "cita_especialidad_row_3": "ginecología y obstetricia",
        "cita_especialidad_row_4": "salud mental",
        "cita_especialidad_row_5": "kinesiología",
        "cita_especialidad_row_6": "odontología",
        "cita_especialidad_row_7": "➡️ ver más especialidades",

        # Especialidades – página 2 (hasta 10 filas)
        "cita_especialidad2_row_1":  "oftalmología",
        "cita_especialidad2_row_2":  "dermatología",
        "cita_especialidad2_row_3":  "traumatología",
        "cita_especialidad2_row_4":  "cardiología",
        "cita_especialidad2_row_5":  "nutrición y dietética",
        "cita_especialidad2_row_6":  "fonoaudiología",
        "cita_especialidad2_row_7":  "medicina interna",
        "cita_especialidad2_row_8":  "reumatología",
        "cita_especialidad2_row_9":  "neurología",
        "cita_especialidad2_row_10": "➡️ mostrar más…",

        # Especialidades – página 3 (hasta 10 filas)
        "cita_especialidad3_row_1":  "gastroenterología",
        "cita_especialidad3_row_2":  "endocrinología",
        "cita_especialidad3_row_3":  "urología",
        "cita_especialidad3_row_4":  "infectología",
        "cita_especialidad3_row_5":  "terapias complementarias",
        "cita_especialidad3_row_6":  "toma de muestras",
        "cita_especialidad3_row_7":  "vacunación / niño sano",
        "cita_especialidad3_row_8":  "control crónico",
        "cita_especialidad3_row_9":  "atención domiciliaria",
        "cita_especialidad3_row_10": "otro",

        # Fecha y Hora (button_reply)
        "cita_fecha_btn_1": "elegir fecha y hora",
        "cita_fecha_btn_2": "lo antes posible",

        # Sede (button_reply)
        "cita_sede_btn_1": "sede talca",
        "cita_sede_btn_2": "no, cambiar de sede",

        # Cambio de sede (list_reply)
        "cita_nueva_sede_row_1": "sede talca",
        "cita_nueva_sede_row_2": "sede curicó",
        "cita_nueva_sede_row_3": "sede linares",

        # Confirmación final (button_reply)
        "cita_confirmacion_btn_1": "cita_confirmacion:si",
        "cita_confirmacion_btn_2": "cita_confirmacion:no",

        # Orientación de síntomas – página 1
        "orientacion_categorias_row_1":  "orientacion_respiratorio_extraccion",
        "orientacion_categorias_row_2":  "orientacion_bucal_extraccion",
        "orientacion_categorias_row_3":  "orientacion_infeccioso_extraccion",
        "orientacion_categorias_row_4":  "orientacion_cardiovascular_extraccion",
        "orientacion_categorias_row_5":  "orientacion_metabolico_extraccion",
        "orientacion_categorias_row_6":  "orientacion_neurologico_extraccion",
        "orientacion_categorias_row_7":  "orientacion_musculoesqueletico_extraccion",
        "orientacion_categorias_row_8":  "orientacion_saludmental_extraccion",
        "orientacion_categorias_row_9":  "orientacion_dermatologico_extraccion",
        "orientacion_categorias_row_10": "ver más ➡️",

        # Orientación de síntomas – página 2
        "orientacion_categorias2_row_1": "orientacion_ginecologico_extraccion",
        "orientacion_categorias2_row_2": "orientacion_digestivo_extraccion",

        # --- Stock / Retiro de Medicamentos ---
        "stock_activa_row_1": "stock_si",
        "stock_activa_row_2": "stock_no_se",
        "stock_activa_row_3": "stock_no",
        "stock_freq_row_1": "cada 30 dias",
        "stock_freq_row_2": "cada 15 dias",
        "stock_freq_row_3": "otra frecuencia",
        "stock_pickup_btn_1": "pickup_confirm_si",
        "stock_pickup_btn_2": "pickup_confirm_no",
        "stock_pickup_btn_3": "pickup_cuidador",
        "stock_problem_row_1": "prob_sin_stock",
        "stock_problem_row_2": "prob_retraso",
        "stock_problem_row_3": "prob_no_entendi",
        "stock_problem_row_4": "prob_otro",
        "stock_link_btn_1": "vincular_adherencia_si",
        "stock_link_btn_2": "vincular_adherencia_no",
    }

    # 👉 APLICA EL MAPEO **ANTES** DE CUALQUIER LÓGICA
    if text in ui_mapping:
        text = ui_mapping[text]

    # Mapeo de fechas y horas para citas
    datetime_mapping = {
        "cita_datetime_row_1": "2025-09-02 10:00 AM",
        "cita_datetime_row_2": "2025-09-02 11:30 AM",
        "cita_datetime_row_3": "2025-09-02 02:00 PM",
        "cita_datetime_row_4": "2025-09-03 09:00 AM",
        "cita_datetime_row_5": "2025-09-03 03:00 PM",
        "cita_datetime_row_6": "2025-09-04 10:00 AM",
        "cita_datetime_row_7": "2025-09-04 01:00 PM",
        "cita_datetime_row_8": "2025-09-05 09:30 AM",
        "cita_datetime_row_9": "2025-09-05 11:00 AM",
        "cita_datetime_row_10":"2025-09-05 02:30 PM",
    }

    # -----------------------------------------------------------
    # Flujo de orientación activo (solo orientación de síntomas)
    # -----------------------------------------------------------
    if number in session_states and 'categoria' in session_states[number]:
        state = session_states[number]
        hdr = f"orientacion_{state['categoria']}_{state['paso']}"
        payload = handle_orientacion(f"{hdr}:{text}", number, messageId)
        enviar_Mensaje_whatsapp(payload)
        if state['paso'] == 'extraccion':
            session_states[number]['paso'] = 'confirmacion'
        else:
            session_states.pop(number, None)
        return

    disclaimer = (
        "\n\n*IMPORTANTE: Soy un asistente virtual con información general. "
        "Esta información NO reemplaza el diagnóstico ni la consulta con un profesional de la salud.*"
    )

    # Simular lectura
    time.sleep(random.uniform(0.5, 1.5))

    reacciones_ack = ["👍", "👌", "✅", "🩺"]
    emojis_saludo   = ["👋", "😊", "🩺", "🧑‍⚕️"]
    despedidas     = [
        f"¡Cuídate mucho, {name}! Aquí estoy si necesitas más. 😊" + disclaimer,
        "Espero haberte ayudado. ¡Hasta pronto! 👋" + disclaimer,
        "¡Que tengas un buen día! Recuerda consultar a tu médico si persisten. 🙌" + disclaimer,
    ]
    agradecimientos = [
        "De nada. ¡Espero que te sirva!" + disclaimer,
        f"Un placer ayudarte, {name}. ¡Cuídate!" + disclaimer,
        "Estoy aquí para lo que necesites." + disclaimer,
    ]
    respuesta_no_entendido = (
        "Lo siento, no entendí tu consulta. Puedes elegir:\n"
        "• Agendar Cita Médica\n"
        "• Recordatorio de Medicamento\n"
        "• Orientación de Síntomas"
        + disclaimer
    )

    # --- Lógica principal ---

    # 1) Emergencias
    if any(w in text for w in ["ayuda urgente", "urgente", "accidente", "samu", "131"]):
        body = (
            "🚨 *EMERGENCIA MÉDICA DETECTADA* 🚨\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ *LLAMA INMEDIATAMENTE* ⚠️\n\n"
            
            "� *NÚMEROS DE EMERGENCIA:*\n"
            "🚑 SAMU: *131*\n"
            "🔥 Bomberos: *132*\n"
            "👮 Carabineros: *133*\n\n"
            
            "🔴 *IMPORTANTE:*\n"
            "• NO esperes respuesta del chatbot\n"
            "• Actúa de inmediato\n"
            "• Si es posible, busca ayuda cercana\n\n"
            
            "💙 *Tu seguridad es lo primero*"
        )
        list_responses.append(text_Message(number, body))
        list_responses.append(replyReaction_Message(number, messageId, "🚨"))

    # Saludo y menú principal
    elif any(w in text for w in ["hola", "buenas", "saludos"]):
        body = (
            f"🌟 ¡Hola {name}! Soy *MedicAI* 🩺\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💙 *Tu asistente virtual de salud* 💙\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            
            "✨ *¿En qué puedo ayudarte hoy?*\n\n"
            
            "🔹 *Servicios principales:*\n"
            "🗓️ Agendar Cita Médica\n"
            "💊 Recordatorio de Medicamentos\n"
            "➕ Más opciones de ayuda\n\n"
            
            "💡 *¿Necesitas ayuda?* Escribe *comandos*\n"
            "🚀 *¡Selecciona una opción para comenzar!*"
        )
        footer = "MedicAI • Tu asistente de salud"
        opts = [
            "🗓️ Agendar Cita",
            "💊 Recordatorios",
            "➕ Más Opciones"
        ]
        list_responses.append(
            buttonReply_Message(number, opts, body, footer, "menu_principal", messageId)
        )
        list_responses.append(
            replyReaction_Message(number, messageId, random.choice(emojis_saludo))
        )

    # Menú "Más opciones"
    elif text == "menu_mas":
        body = (
            "✨ *Más Opciones de Ayuda* ✨\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔹 *Servicios adicionales disponibles:*\n\n"
            
            "🩺 Orientación médica personalizada\n"
            "📋 Guía para trámites de salud\n"
            "💊 Gestión completa de medicamentos\n"
            "⏰ Control de recordatorios\n\n"
            
            "💡 *Selecciona la opción que necesites:*"
        )
        footer = "MedicAI • Servicios Extra"
        opciones_mas = [
            "🩺 Orientación de Síntomas",
            "📋 Guía de Ruta / Derivaciones",
            "💊 Stock de Medicamentos",
            "⏰ Gestionar Recordatorios"
        ]
        list_responses.append(
            listReply_Message(number, opciones_mas, body, footer, "menu_mas", messageId)
        )
        # Envía el mensaje y sale para mantener consistencia
        for i, payload in enumerate(list_responses):
            if payload and payload.strip():
                enviar_Mensaje_whatsapp(payload)
            if i < len(list_responses) - 1:
                time.sleep(1)
        return

     # -----------------------------------------------------------
     # 3) Flujo: Agendar Citas
     # -----------------------------------------------------------
    elif "agendar cita" in text or "cita medica" in text:
         appointment_sessions[number] = {}
         body = (
             "🗓️ *¡Excelente decisión!* 🗓️\n"
             "━━━━━━━━━━━━━━━━━━━━━━━━\n"
             "✨ *Agendamiento de Citas Médicas* ✨\n\n"
             
             "👩‍⚕️ *Selecciona el tipo de atención:*\n"
             "� Contamos con profesionales especializados\n"
             "🔹 Horarios flexibles disponibles\n"
             "🔹 Atención de calidad garantizada\n\n"
             
             "💡 *¿Qué especialidad necesitas?*"
         )
         footer = "Agendamiento • MedicAI"
         opts = [
             "🩺 Medicina General",
             "👶 Pediatría",
             "🤰 Ginecología y Obstetricia",
             "🧠 Salud Mental",
             "🏋️‍♂️ Kinesiología",
             "🦷 Odontología",
             "➡️ Ver más Especialidades"
         ]
         list_responses.append(
             listReply_Message(number, opts, body, footer, "cita_especialidad", messageId)
         )

     # 3.1) Listado interactivo de especialidades (página 2)
    elif text == "➡️ ver más especialidades":
         body = "🔍 Otras especialidades – selecciona una opción:"
         footer = "Agendamiento – Especialidades"
         opts2 = [
             "👁️ Oftalmología", "🩸 Dermatología", "🦴 Traumatología",
             "❤️ Cardiología", "🥗 Nutrición y Dietética", "🗣️ Fonoaudiología",
             "🏥 Medicina Interna", "🔧 Reumatología", "🧠 Neurología",
             "➡️ mostrar más…"
         ]
         list_responses.append(
             listReply_Message(number, opts2, body, footer, "cita_especialidad2", messageId)
         )

     # 3.1.1) Paginación: tercera página de especialidades
    elif text == "➡️ mostrar más…":
         body = "🔍 Más especialidades – selecciona una opción:"
         footer = "Agendamiento – Especialidades"
         opts3 = [
             "🍽️ Gastroenterología", "🧬 Endocrinología", "🚻 Urología",
             "🦠 Infectología", "🌿 Terapias Complementarias", "🧪 Toma de Muestras",
             "👶 Vacunación / Niño Sano", "🏠 Atención Domiciliaria",
             "💻 Telemedicina", "❓ Otro / No sé"
         ]
         list_responses.append(
             listReply_Message(number, opts3, body, footer, "cita_especialidad3", messageId)
         )

     # 3.2) Tras elegir especialidad
    elif text in [
         "medicina general", "pediatría", "ginecología y obstetricia", "salud mental",
         "kinesiología", "odontología", "oftalmología", "dermatología",
         "traumatología", "cardiología", "nutrición y dietética", "fonoaudiología",
         "medicina interna", "reumatología", "neurología", "gastroenterología",
         "endocrinología", "urología", "infectología", "terapias complementarias",
         "toma de muestras", "vacunación / niño sano", "atención domiciliaria",
         "telemedicina", "otro", "no sé"
     ]:
         appointment_sessions[number]['especialidad'] = text       # ← MOD: guardo especialidad
         body = "⏰ ¿Tienes preferencia de día y hora para tu atención?"
         footer = "Agendamiento – Fecha y Hora"
         opts = ["📅 Elegir Fecha y Hora", "⚡ Lo antes posible"]
         list_responses.append(
             buttonReply_Message(number, opts, body, footer, "cita_fecha", messageId)
         )

     # 3.3a) Si elige “Elegir fecha y hora”
    elif text == "elegir fecha y hora":
         body   = "Por favor selecciona fecha y hora para tu cita:"
         footer = "Agendamiento – Fecha y Hora"
         opciones = list(datetime_mapping.values())
         list_responses.append(
             listReply_Message(number, opciones, body, footer, "cita_datetime", messageId)
         )

     # 3.3b) Si elige “Lo antes posible”
    elif text == "lo antes posible":
         appointment_sessions[number]['datetime'] = "Lo antes posible"  # ← MOD: guardo genérico
         body   = "¿Atenderás en la misma sede de siempre?"
         footer = "Agendamiento – Sede"
         opts   = ["Sí", "No, cambiar de sede"]
         list_responses.append(
             buttonReply_Message(number, opts, body, footer, "cita_sede", messageId)
         )

     # 3.4) Tras escoger fecha/hora de calendario
    elif text.startswith("cita_datetime_row_"):
         selected = datetime_mapping.get(text)
         appointment_sessions[number]['datetime'] = selected       # ← MOD: guardo fecha exacta
         body     = f"Has seleccionado *{selected}*. ¿Atenderás en la misma sede de siempre?"
         footer   = "Agendamiento – Sede"
         opts     = ["Sí", "No, cambiar de sede"]
         list_responses.append(
             buttonReply_Message(number, opts, body, footer, "cita_sede", messageId)
         )

     # 3.5) Cambio de sede
    elif text == "no, cambiar de sede":
         body   = "Selecciona tu nueva sede:\n• Sede Talca\n• Sede Curicó\n• Sede Linares"
         footer = "Agendamiento – Nueva Sede"
         opts   = ["Sede Talca", "Sede Curicó", "Sede Linares"]
         list_responses.append(
             listReply_Message(number, opts, body, footer, "cita_nueva_sede", messageId)
         )

     # 3.6) Confirmación final
    elif text in ["sede talca", "sede curicó", "sede linares"]:
         appointment_sessions[number]['sede'] = text
         esp  = appointment_sessions[number]['especialidad'].capitalize()
         dt   = appointment_sessions[number].get('datetime', 'día y hora')
         sede = appointment_sessions[number]['sede'].capitalize()
         # formateo fecha y hora si vienen como "YYYY-MM-DD HH:MM"
         if " " in dt:
             fecha, hora = dt.split(" ", 1)
             horario = f"{fecha} a las {hora}"
         else:
             horario = dt
         body = (
             f"🎉 *¡Cita Agendada Exitosamente!* 🎉\n"
             f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
             f"✅ *Confirmación de Agendamiento* ✅\n\n"
             
             f"📅 *Fecha y Hora:* {horario}\n"
             f"👩‍⚕️ *Especialidad:* {esp}\n"
             f"🏥 *Sede:* {sede}\n\n"
             
             f"📲 *¿Deseas recibir un recordatorio?*\n"
             f"🔹 Te enviaremos una notificación\n"
             f"🔹 El día anterior a tu cita\n"
             f"🔹 Para que no se te olvide\n\n"
             
             f"💙 *¡Nos vemos pronto!*"
         )
         footer = "Confirmación • MedicAI"
         opts   = ["✅ Sí, recordarme", "❌ No, gracias"]
         list_responses.append(
             buttonReply_Message(number, opts, body, footer, "cita_confirmacion", messageId)
         )

     # 3.7) Respuesta al recordatorio y cierre
    elif text.startswith("cita_confirmacion"):
         body = (
             "🌟 *¡Proceso Completado con Éxito!* 🌟\n"
             "━━━━━━━━━━━━━━━━━━━━━━━━\n"
             "💙 *Gracias por confiar en MedicAI* 💙\n\n"
             
             "✅ *Tu cita está confirmada y guardada*\n"
             "🩺 *Nuestro equipo te espera*\n"
             "📱 *Mantén tu teléfono activo para recordatorios*\n\n"
             
             "💡 *Recuerda:*\n"
             "🔹 Llegar 15 minutos antes\n"
             "🔹 Traer tu cédula de identidad\n"
             "🔹 Cualquier examen previo relacionado\n\n"
             
             "🚀 *¡Que tengas un excelente día!* ✨"
         )
         list_responses.append(text_Message(number, body))
         appointment_sessions.pop(number, None)


     # -----------------------------------------------------------
    # 4) Flujo de Recordatorio y Monitoreo de Medicamentos
    # -----------------------------------------------------------

    # 4.1) Inicio de nueva sesión de recordatorio
    elif "recordatorio de medicamento" in text:
        # Inicializar estado de recordatorio
        medication_sessions[number] = {}
        session_states[number]   = {"flow": "med", "step": "ask_name"}

        body = (
            "💊 *¡Cuidemos tu salud juntos!* 💊\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⏰ *Sistema de Recordatorios* ⏰\n\n"
            
            "🌟 *¿Sabías que?*\n"
            "• El 90% de los tratamientos exitosos\n"
            "  dependen de la adherencia terapéutica\n\n"
            
            "💡 *Configuremos tu recordatorio:*\n"
            "🔹 Notificaciones automáticas\n"
            "🔹 Horarios personalizados\n"
            "🔹 Seguimiento de tu progreso\n\n"
            
            "📝 *¿Cuál es el nombre del medicamento?*"
        )
        list_responses.append(text_Message(number, body))

    # 4.2) Continuar el flujo de recordatorio existente
    elif number in session_states and session_states[number].get("flow") == "med":
        flow = session_states[number]
        step = flow["step"]

        if step == "ask_name":
            # Guardar nombre del medicamento
            medication_sessions[number]["name"] = text
            flow["step"] = "ask_freq"

            body = "Perfecto. ¿Con qué frecuencia debes tomarlo?"
            opts = [
                "Una vez al día",
                "Dos veces al día",
                "Cada 8 horas",
                "Otro horario personalizado"
            ]
            # Usamos lista en lugar de botones para permitir 4 opciones
            list_responses.append(
                listReply_Message(
                    number,
                    opts,
                    body,
                    "Recordatorio Medicamentos",
                    "med_freq",
                    messageId
                )
            )

        elif step == "ask_freq":
            # Guardar frecuencia
            medication_sessions[number]["freq"] = text
            flow["step"] = "ask_times"

            body = (
                "Anotaré tus tomas. ¿A qué hora quieres que te lo recuerde? "
                "(por ejemplo: 08:00 y 20:00)"
            )
            list_responses.append(text_Message(number, body))

        elif step == "ask_times":
            # Guardar horarios y configurar recordatorio automático
            medication_sessions[number]["times"] = text
            med   = medication_sessions[number]["name"]
            times = medication_sessions[number]["times"]

            # Procesar horarios para el sistema de recordatorios
            try:
                # Extraer horarios del texto (formatos: "08:00 y 20:00", "8:00", "08:00, 14:00, 20:00")
                import re
                time_pattern = r'\b(\d{1,2}):(\d{2})\b'
                matches = re.findall(time_pattern, times)
                
                if matches:
                    # Convertir a formato HH:MM
                    times_list = []
                    for hour, minute in matches:
                        formatted_time = f"{hour.zfill(2)}:{minute}"
                        times_list.append(formatted_time)
                    
                    # Registrar recordatorio en el sistema
                    register_medication_reminder(number, med, times_list)
                    
                    times_str = ", ".join(times_list)
                    body = (
                        f"¡Listo! ✅ He configurado tus recordatorios de *{med}* para las {times_str}.\n\n"
                        "🔔 Recibirás notificaciones automáticas en esos horarios.\n"
                        "📌 Recuerda que tomar tus medicamentos es un paso hacia sentirte mejor 💊💙"
                    )
                else:
                    # Si no se pueden extraer horarios válidos
                    body = (
                        f"He guardado tu recordatorio de *{med}* para: {times}\n\n"
                        "📝 Para recordatorios automáticos, asegúrate de usar formato 24h (ej: 08:00, 14:00)\n"
                        "📌 Recuerda que tomar tus medicamentos es un paso hacia sentirte mejor 💊💙"
                    )
            except Exception as e:
                print(f"Error procesando horarios: {e}")
                body = (
                    f"He guardado tu recordatorio de *{med}* para: {times}\n"
                    "📌 Recuerda que tomar tus medicamentos es un paso hacia sentirte mejor 💊💙"
                )
            
            list_responses.append(text_Message(number, body))
            session_states.pop(number, None)

    # 4.3) Gestión de recordatorios existentes
    elif text in ["mis recordatorios", "ver recordatorios", "recordatorios"]:
        with REMINDERS_LOCK:
            if number in MED_REMINDERS and MED_REMINDERS[number]:
                reminders_list = []
                for i, reminder in enumerate(MED_REMINDERS[number], 1):
                    times_str = ", ".join(reminder["times"])
                    reminders_list.append(f"{i}. *{reminder['name']}* - {times_str}")
                
                body = "📋 *Tus recordatorios activos:*\n\n" + "\n".join(reminders_list)
                body += "\n\n💡 Para eliminar un recordatorio, escribe: *eliminar recordatorio [número]*"
            else:
                body = (
                    "📭 No tienes recordatorios activos.\n\n"
                    "💊 Para crear uno nuevo, escribe: *recordatorio de medicamento*"
                )
        list_responses.append(text_Message(number, body))

    elif text in ["comandos", "comando", "ayuda comandos", "ver comandos"]:
        body = (
            "📚 *GUÍA COMPLETA DE COMANDOS* 📚\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "✨ *MedicAI - Tu Asistente de Salud* ✨\n\n"
            
            "💊 *MEDICAMENTOS & RECORDATORIOS*\n"
            "• *recordatorio de medicamento*\n"
            "• *mis recordatorios*\n"
            "• *eliminar recordatorio [N°]*\n"
            "• *gestionar recordatorios*\n"
            "• *vincular tomas [med] HH:MM*\n\n"
            
            "🏥 *STOCK & RETIROS*\n"
            "• *stock de medicamentos*\n"
            "• *mis retiros* / *ver retiros*\n"
            "• *retire [medicamento] si|no*\n"
            "• *programar retiro [med] [fecha] [hora]*\n"
            "• *programar ciclo [med] [fecha] [hora] cada [días]*\n"
            "• *stock agregar [med] [cantidad]*\n"
            "• *stock bajar [med] [cantidad]*\n"
            "• *stock ver [medicamento]*\n\n"
            
            "🗓️ *CITAS MÉDICAS*\n"
            "• *agendar cita* / *cita medica*\n\n"
            
            "🩺 *ORIENTACIÓN & GUÍAS*\n"
            "• *orientación de síntomas*\n"
            "• *guía de ruta* / *derivacion*\n\n"
            
            "🚨 *EMERGENCIAS*\n"
            "• *ayuda urgente* / *urgente*\n"
            "• *samu* / *131*\n\n"
            
            "🔧 *UTILIDADES*\n"
            "• *hola* - Menú principal\n"
            "• *gracias* - Agradecimiento\n"
            "• *adiós* / *chao* - Despedida\n\n"
            
            "⚡ *¡Escribe cualquier comando para empezar!*"
        )
        list_responses.append(text_Message(number, body))

    elif text == "debug hora":
        ahora = _now_hhmm_local()
        list_responses.append(text_Message(number, f"🕒 Hora servidor usada para recordatorios: {ahora} ({DEFAULT_TZ})"))

    elif text == "test en 1 min":
        from datetime import timedelta
        # calcula HH:MM + 1 minuto, redondeando al minuto siguiente
        if ZoneInfo is not None:
            tz = ZoneInfo(DEFAULT_TZ)
            now = datetime.now(tz)
        elif pytz is not None:
            tz = pytz.timezone(DEFAULT_TZ)
            now = datetime.now(tz)
        else:
            tz = timezone.utc
            now = datetime.now(tz)

        target = (now + timedelta(minutes=1)).strftime("%H:%M")
        register_medication_reminder(number, "PRUEBA", [target])
        list_responses.append(text_Message(number, f"⏰ Programado recordatorio de PRUEBA para las {target}"))

    elif text.startswith("eliminar recordatorio"):
        try:
            # Extraer número del recordatorio a eliminar
            parts = text.split()
            if len(parts) >= 3 and parts[2].isdigit():
                index = int(parts[2]) - 1
                with REMINDERS_LOCK:
                    if (number in MED_REMINDERS and 
                        0 <= index < len(MED_REMINDERS[number])):
                        removed = MED_REMINDERS[number].pop(index)
                        body = f"✅ Recordatorio de *{removed['name']}* eliminado correctamente."
                        
                        # Si no quedan recordatorios, limpiar la entrada
                        if not MED_REMINDERS[number]:
                            del MED_REMINDERS[number]
                    else:
                        body = "❌ Número de recordatorio no válido. Usa *mis recordatorios* para ver la lista."
            else:
                body = "❌ Formato incorrecto. Ejemplo: *eliminar recordatorio 1*"
        except Exception as e:
            print(f"Error eliminando recordatorio: {e}")
            body = "❌ Error eliminando recordatorio. Inténtalo de nuevo."
        
        list_responses.append(text_Message(number, body))

            
    # 5) Inicio de orientación de síntomas
    elif "orientacion de sintomas" in text:
        body = (
            "🩺 *Orientación Médica Inteligente* 🩺\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔍 *Análisis de Síntomas* 🔍\n\n"
            
            "⚠️ *Importante:*\n"
            "• Esta es una orientación informativa\n"
            "• NO reemplaza la consulta médica\n"
            "• En emergencias, contacta al 131\n\n"
            
            "📋 *Selecciona la categoría que mejor\n"
            "describe tus síntomas:*\n\n"
            
            "💡 *Te ayudaré a entender mejor tu situación*"
        )
        footer = "Sistema de Orientación • MedicAI"
        opts = [
            "🫁 Respiratorias",
            "🦷 Bucales",
            "🦠 Infecciosas",
            "❤️ Cardiovasculares",
            "⚖️ Metabólicas",
            "🧠 Neurológicas",
            "💪 Musculoesqueléticas",
            "🧘 Salud Mental",
            "🩹 Dermatológicas",
            "➡️ Ver más categorías",
        ]
        enviar_Mensaje_whatsapp(
            listReply_Message(number, opts, body, footer, "orientacion_categorias", messageId)
        )
        return

    # 5.1) Paginación: si el usuario elige "Ver más ➡️", mostramos las categorías adicionales
    elif text == "ver más ➡️":
        opts2 = [
            "Ginecológicas 👩‍⚕️",
            "Digestivas 🍽️",
        ]
        footer2 = "Orient. Síntomas"
        enviar_Mensaje_whatsapp(
            listReply_Message(number, opts2, "Otras categorías:", footer2, "orientacion_categorias2", messageId)
        )
        return


    # 6) Usuario selecciona categoría: arrancamos orientación
    elif text.startswith("orientacion_") and text.endswith("_extraccion"):
        _, categoria, _ = text.split("_", 2)
        session_states[number] = {"categoria": categoria, "paso": "extraccion"}

        display = {
            "respiratorio": "Respiratorias",
            "bucal": "Bucales",
            "infeccioso": "Infecciosas",
            "cardiovascular": "Cardiovasculares",
            "metabolico": "Metabólicas/Endocrinas",
            "neurologico": "Neurológicas",
            "musculoesqueletico": "Musculoesqueléticas",
            "saludmental": "Salud Mental",
            "dermatologico": "Dermatológicas",
            "ginecologico": "Ginecológicas/Urológicas",
            "digestivo": "Digestivas"
        }.get(categoria, categoria)

        ejemplo = EJEMPLOS_SINTOMAS.get(
            categoria,
            "tos seca, fiebre alta, dificultad para respirar"
        )

        prompt = (
            f"Por favor describe tus síntomas para enfermedades {display}.\n"
            f"Ejemplo: '{ejemplo}'"
        )
        enviar_Mensaje_whatsapp(text_Message(number, prompt))
        return

    # Nuevas opciones del menú "Más opciones"
    elif text == "stock de medicamentos":
        stock_sessions[number] = {"step": "activate"}
        body = (
            "💊 *Gestión Inteligente de Medicamentos* 💊\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📋 *Control de Retiros y Stock* 📋\n\n"
            
            "🔹 *Servicios disponibles:*\n"
            "• Verificación de disponibilidad\n"
            "• Programación de retiros\n"
            "• Recordatorios automáticos\n"
            "• Vinculación con adherencia\n\n"
            
            "📝 *Para empezar, necesito saber:*\n"
            "¿Tienes una *receta médica activa*\n"
            "que aún no has retirado?\n\n"
            
            "💡 *Selecciona tu situación:*"
        )
        opts = ["✅ Sí, tengo receta", "🤔 No estoy seguro/a", "❌ No tengo receta"]
        list_responses.append(listReply_Message(number, opts, body, "Gestión de Medicamentos • MedicAI", "stock_activa", messageId))

    # 6.2) Secuencia del flujo de stock
    elif number in stock_sessions:
        ss = stock_sessions[number]
        step = ss.get("step")
        
        # MÓDULO 1 → respuesta de activación
        if step == "activate":
            if text in ("stock_si", "stock_no_se"):
                ss["step"] = "ask_drug"
                list_responses.append(text_Message(
                    number,
                    "💊 Dime el *nombre del medicamento* o envía *foto clara de la receta*."
                ))
            else:
                list_responses.append(text_Message(number,
                    "Entendido. Cuando tengas una receta activa, vuelve a escribirme."))
                stock_sessions.pop(number, None)
        
        # MÓDULO 2 → identificación del fármaco
        elif step == "ask_drug":
            ss["drug_name"] = text
            ss["step"] = "check_availability"
            list_responses.append(text_Message(number, "🔍 Estoy revisando disponibilidad…"))
            status = check_stock_api(ss["drug_name"])
            
            # MÓDULO 3 → verificación
            if status == "available":
                list_responses.append(text_Message(number, f"✅ *{ss['drug_name']}* está *disponible*."))
            elif status == "low":
                list_responses.append(text_Message(number, f"⚠️ Queda *poco stock* de *{ss['drug_name']}*. Se recomienda acudir pronto."))
            elif status == "none":
                list_responses.append(text_Message(number, f"❌ No hay stock de *{ss['drug_name']}* por ahora. ¿Quieres que te avise cuando haya?"))
            else:
                list_responses.append(text_Message(
                    number,
                    ("🤷‍♂️ No tengo acceso en línea al sistema de farmacia. "
                     "¿Quieres que *programe recordatorios* para no olvidar el retiro?")
                ))
            
            # Configurar frecuencia
            ss["step"] = "ask_freq"
            opts = ["Cada 30 días", "Cada 15 días", "Otra frecuencia"]
            list_responses.append(listReply_Message(number, opts, "¿Cada cuánto te corresponde retirar?", "Frecuencia de retiro", "stock_freq", messageId))
        
        # MÓDULO 4 → frecuencia y hora
        elif step == "ask_freq":
            ss["freq_days"] = _parse_freq_to_days(text)
            ss["step"] = "ask_hour"
            list_responses.append(text_Message(number, "⏰ ¿A qué *hora* te recuerdo? (24h, ej: 08:00)"))
        
        elif step == "ask_hour":
            hour = _hhmm_or_default(text, "08:00")
            ss["hour"] = hour
            # Programación inicial vía DB: primera fecha = hoy + freq_days
            from datetime import timedelta as _td
            first_date = (_safe_today_tz() + _td(days=ss["freq_days"]))
            pickup_schedule_cycle(number, ss["drug_name"], first_date.isoformat(), hour, ss["freq_days"])
            list_responses.append(text_Message(
                number,
                f"✅ Listo. Te recordaré *{ss['drug_name']}* cada *{ss['freq_days']} días* a las *{hour}*.\n"
                "📢 Aviso *3 días antes* y el *día del retiro*."
            ))
            ss["step"] = "wait_pickup"
            list_responses.append(text_Message(
                number,
                "📝 Cuando llegue la fecha, te preguntaré: *¿Pudiste retirar?*\n"
                "También puedes registrar manual: *retire [nombre] si|no*."
            ))
        
        elif step == "wait_pickup":
            if text.startswith("retire "):
                list_responses.append(text_Message(number, "✅ Ok, registraré tu respuesta."))
            else:
                list_responses.append(text_Message(number, "👍 Perfecto. Te avisaré en la fecha programada."))
            stock_sessions.pop(number, None)

    elif text == "gestionar recordatorios":
        with REMINDERS_LOCK:
            if number in MED_REMINDERS and MED_REMINDERS[number]:
                reminders_list = []
                for i, reminder in enumerate(MED_REMINDERS[number], 1):
                    times_str = ", ".join(reminder["times"])
                    reminders_list.append(f"{i}. *{reminder['name']}* - {times_str}")
                
                body = (
                    "⏰ *Gestión de Recordatorios*\n\n"
                    "📋 *Tus recordatorios activos:*\n" + "\n".join(reminders_list) +
                    "\n\n💡 *Opciones disponibles:*\n"
                    "• *recordatorio de medicamento* - Crear nuevo\n"
                    "• *eliminar recordatorio [número]* - Eliminar específico\n"
                    "• *mis recordatorios* - Ver lista completa"
                )
            else:
                body = (
                    "⏰ *Gestión de Recordatorios*\n\n"
                    "📭 No tienes recordatorios activos.\n\n"
                    "💡 *Para empezar:*\n"
                    "• Escribe: *recordatorio de medicamento*\n"
                    "• Te guiaré paso a paso para configurar recordatorios automáticos\n"
                    "• Recibirás notificaciones en los horarios que elijas 🔔"
                )
        list_responses.append(text_Message(number, body))

    # === COMANDOS DE STOCK Y RETIROS ===
    
    # === STOCK: Alta/Resta/Consulta ===
    elif text.startswith("stock agregar "):
        try:
            _, _, rest = text.partition("stock agregar ")
            parts = rest.rsplit(" ", 1)
            name = parts[0].strip()
            qty = int(parts[1])
            stock_add_or_update(name, qty)
            list_responses.append(text_Message(number, f"📈 Stock de *{name}* incrementado en {qty}."))
        except Exception:
            list_responses.append(text_Message(number, "❌ Formato: *stock agregar [nombre] [cantidad]*"))

    elif text.startswith("stock bajar "):
        try:
            _, _, rest = text.partition("stock bajar ")
            parts = rest.rsplit(" ", 1)
            name = parts[0].strip()
            qty = int(parts[1])
            stock_decrement(name, qty)
            row = stock_get(name)
            s = row[1] if row else 0
            list_responses.append(text_Message(number, f"📉 Stock de *{name}* decrementado en {qty}. Queda: {s}."))
        except Exception:
            list_responses.append(text_Message(number, "❌ Formato: *stock bajar [nombre] [cantidad]*"))

    elif text.startswith("stock ver "):
        name = text.replace("stock ver", "", 1).strip()
        row = stock_get(name)
        if row:
            name, s, loc, price = row
            body = f"💊 *{name}*\nStock: {s}\nSede: {loc or 'N/D'}\nPrecio: {price or 'N/D'}"
        else:
            body = "❌ No tengo ese medicamento. Usa: *stock agregar [nombre] [cantidad]*"
        list_responses.append(text_Message(number, body))

    # === Programar retiro por fecha exacta ===
    elif text.startswith("programar retiro "):
        try:
            _, _, rest = text.partition("programar retiro ")
            parts = rest.split()
            hour = parts[-1]
            date_txt = parts[-2]
            drug = " ".join(parts[:-2])
            from datetime import datetime as _dt
            date_iso = None
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
                try:
                    d = _dt.strptime(date_txt, fmt).date()
                    date_iso = d.isoformat()
                    break
                except:
                    pass
            if not date_iso:
                list_responses.append(text_Message(number, "❌ Fecha inválida. Usa YYYY-MM-DD o DD-MM-YYYY."))
            else:
                hour = _hhmm_or_default(hour, "08:00")
                pickup_schedule_day(number, drug, date_iso, hour)
                list_responses.append(text_Message(number, f"📅 Agendado retiro de *{drug}* para *{date_iso}* a las *{hour}*."))
        except Exception as e:
            list_responses.append(text_Message(number, "❌ Formato: *programar retiro [medicamento] [fecha] [hora]*"))

    # === Programar ciclo (15/30 días) ===
    elif text.startswith("programar ciclo "):
        try:
            _, _, rest = text.partition("programar ciclo ")
            tokens = rest.split()
            if "cada" in tokens:
                idx = tokens.index("cada")
                freq = int(tokens[idx+1])
                hour = tokens[idx-1]
                date_txt = tokens[idx-2]
                drug = " ".join(tokens[:idx-2])
                from datetime import datetime as _dt
                date_iso = None
                for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
                    try:
                        date_iso = _dt.strptime(date_txt, fmt).date().isoformat()
                        break
                    except:
                        pass
                if not date_iso:
                    list_responses.append(text_Message(number, "❌ Fecha inválida. Usa YYYY-MM-DD o DD-MM-YYYY."))
                else:
                    hour = _hhmm_or_default(hour, "08:00")
                    pickup_schedule_cycle(number, drug, date_iso, hour, freq)
                    list_responses.append(text_Message(number, f"🔄 Ciclo creado: *{drug}* cada *{freq} días*, primera *{date_iso}* a las *{hour}*."))
            else:
                list_responses.append(text_Message(number, "❌ Formato: *programar ciclo [medicamento] [fecha] [hora] cada [días]*"))
        except Exception as e:
            list_responses.append(text_Message(number, "❌ Formato: *programar ciclo [medicamento] [fecha] [hora] cada [días]*"))

    # === Confirmar retiro (y ofrecer vinculación a tomas) ===
    elif text.startswith("retire "):
        parts = text.split()
        if len(parts) >= 3:
            drug = " ".join(parts[1:-1])
            ans = parts[-1]
            done = ans in ("si", "sí")
            ok = pickup_mark(number, drug, done)
            if not ok:
                list_responses.append(text_Message(number, f"❌ No encuentro retiro pendiente para *{drug}*."))
            else:
                if done:
                    list_responses.append(text_Message(number, f"✅ Retiro registrado para *{drug}*."))
                    LAST_RETIRED_DRUG[number] = drug
                    list_responses.append(
                        buttonReply_Message(
                            number,
                            ["Sí, vincular", "No, gracias"],
                            "¿Deseas *vincular este medicamento* a recordatorios de *toma diaria*?",
                            "Vincular con adherencia",
                            "stock_link",
                            messageId
                        )
                    )
                else:
                    list_responses.append(text_Message(number, f"📝 Marcado como no retirado: *{drug}*."))
        else:
            list_responses.append(text_Message(number, "❌ Usa: *retire [medicamento] si|no*"))

    # === Vinculación a adherencia (tomas) ===
    elif text == "vincular_adherencia_si":
        med = LAST_RETIRED_DRUG.get(number)
        if not med:
            list_responses.append(text_Message(number, "❌ No tengo contexto. Usa: *vincular tomas [medicamento] HH:MM [HH:MM]*"))
        else:
            medication_sessions[number] = {"name": med}
            session_states[number] = {"flow": "med", "step": "ask_freq"}
            body = f"✅ Perfecto. Configuraremos tomas para *{med}*.\n¿Con qué frecuencia?"
            opts = ["Una vez al día", "Dos veces al día", "Cada 8 horas", "Otro horario personalizado"]
            list_responses.append(
                listReply_Message(number, opts, body, "Recordatorio Medicamentos", "med_freq", messageId)
            )

    elif text == "vincular_adherencia_no":
        list_responses.append(text_Message(number, "👍 Entendido. Mantendré solo el plan de *retiro*."))

    elif text.startswith("vincular tomas "):
        try:
            raw = text.replace("vincular tomas", "", 1).strip()
            parts = raw.split()
            import re
            times = [p for p in parts if re.match(r"^\d{1,2}:\d{2}$", p)]
            name_tokens = [p for p in parts if p not in times]
            med = " ".join(name_tokens).strip()
            if not med or not times:
                raise ValueError
            times = [f"{h if len(h)==5 else h.zfill(5)}" for h in times]  # 8:00 -> 08:00
            register_medication_reminder(number, med, times)
            list_responses.append(text_Message(number, f"🔗 Vinculado. Recordatorios de *{med}* a las: {', '.join(times)}"))
        except Exception:
            list_responses.append(text_Message(number, "❌ Formato: *vincular tomas [medicamento] HH:MM [HH:MM]*"))

    # === Ver agenda de retiros ===
    elif text in ("mis retiros", "ver retiros"):
        rows = pickup_list(number)
        if not rows:
            list_responses.append(text_Message(number, "📭 No tienes retiros programados. Usa: *programar retiro ...* o *programar ciclo ...*"))
        else:
            lines = []
            for drug, date_iso, hour, freq, status in rows:
                extra = f" (cada {freq} días)" if freq else ""
                lines.append(f"• {drug} – {date_iso} {hour}{extra} – {status}")
            body = "📋 *Tus retiros:*\n" + "\n".join(lines)
            list_responses.append(text_Message(number, body))

    # 7) Agradecimientos y despedidas
    elif any(w in text for w in ["gracias", "muchas gracias"]):
        list_responses.append(text_Message(number, random.choice(agradecimientos)))
        list_responses.append(replyReaction_Message(number, messageId, random.choice(reacciones_ack)))

    elif any(w in text for w in ["adiós", "chao", "hasta luego"]):
        list_responses.append(text_Message(number, random.choice(despedidas)))
        list_responses.append(replyReaction_Message(number, messageId, "👋"))

    # -----------------------------------------------------------
    # Manejo del flujo de Guía de Ruta 
    # -----------------------------------------------------------
    elif ("guia de ruta" in text or "derivacion" in text or "ruta de atencion" in text):
        list_responses.append(start_route_flow(number, messageId))

    # Si el usuario ya está dentro del flujo de ruta
    elif number in route_sessions:
        st = route_sessions[number]
        step = st.get("step")

        # Paso: elegir tipo
        if step == "choose_type":
            if text == "interconsulta":
                st["doc_type"] = "interconsulta"
                list_responses.append(text_Message(number, "Perfecto. Recibiste una *interconsulta médica*."))
                list_responses.append(ask_ges(number, messageId))

            elif text == "examenes":
                st["doc_type"] = "examenes"
                st["step"] = "exams"
                list_responses.append(text_Message(number, exams_steps()))
                list_responses.append(
                    buttonReply_Message(
                        number,
                        ["Sí, ver ayuno", "No, gracias"],
                        "¿Tu examen requiere ayuno?",
                        "Orden de exámenes",
                        "route_exams_fast",
                        messageId
                    )
                )

            elif text == "receta":
                st["doc_type"] = "receta"
                st["step"] = "rx"
                list_responses.append(text_Message(
                    number,
                    "💊 Detecté *receta/indicaciones*. ¿Configuro recordatorios de tomas?"
                ))
                list_responses.append(
                    buttonReply_Message(
                        number,
                        ["Sí, configurar", "No, gracias"],
                        "Adherencia terapéutica",
                        "Receta",
                        "route_rx",
                        messageId
                    )
                )

            elif text == "derivacion_urgente":
                st["doc_type"] = "derivacion_urgente"
                st["step"] = "urgent"
                list_responses.append(text_Message(number, urgent_referral_steps()))
                list_responses.append(
                    buttonReply_Message(
                        number,
                        ["Sí, indicar SAPU", "No por ahora"],
                        "Derivación urgente",
                        "Guía de Ruta",
                        "route_urgent",
                        messageId
                    )
                )

            else:
                st["doc_type"] = "no_seguro"
                st["step"] = "requirements"
                list_responses.append(text_Message(number, "No te preocupes. Te dejo *requisitos y pasos* útiles:"))
                list_responses.append(text_Message(number, req_docs_steps()))
                list_responses.append(
                    buttonReply_Message(
                        number,
                        ["Sí, guardar", "No, gracias"],
                        "Guardar / Recordatorios",
                        "Guía de Ruta",
                        "route_save",
                        messageId
                    )
                )

        # Paso: pregunta GES
        elif step == "ask_ges":
            if text == "ges_si":
                st["ges"] = "sí"
                list_responses.append(text_Message(number, interconsulta_instructions("Sí, es GES")))
                list_responses.append(
                    buttonReply_Message(
                        number,
                        ["Sí, recordarme GES", "No, gracias"],
                        "Recordatorios",
                        "Interconsulta GES",
                        "route_ges_reminder",
                        messageId
                    )
                )
                st["step"] = "requirements"

            elif text == "ges_no" or text == "ges_ns":
                st["ges"] = "no/nd"
                list_responses.append(text_Message(number, interconsulta_instructions("No")))
                list_responses.append(
                    buttonReply_Message(
                        number,
                        ["Sí, indicar sede", "No, gracias"],
                        "SOME CESFAM",
                        "Interconsulta",
                        "route_some_site",
                        messageId
                    )
                )
                st["step"] = "requirements"

            else:
                # Respuesta libre: tratamos como no sabe
                st["ges"] = "nd"
                list_responses.append(text_Message(number, interconsulta_instructions("No")))
                list_responses.append(
                    buttonReply_Message(
                        number,
                        ["Sí, indicar sede", "No, gracias"],
                        "SOME CESFAM",
                        "Interconsulta",
                        "route_some_site",
                        messageId
                    )
                )
                st["step"] = "requirements"

        # Paso: exámenes -> ayuno sí/no
        elif step == "exams":
            if text == "ayuno_si":
                list_responses.append(text_Message(
                    number,
                    "💡 Tip general: muchos perfiles requieren *8–12 h* de ayuno (verifica en tu orden o SOME)."
                ))
            else:
                list_responses.append(text_Message(
                    number,
                    "👍 Ok. Si dudas, confírmalo al agendar en SOME/laboratorio."
                ))
            st["step"] = "requirements"
            list_responses.append(text_Message(number, req_docs_steps()))
            list_responses.append(
                buttonReply_Message(
                    number,
                    ["Sí, guardar", "No, gracias"],
                    "Guardar / Recordatorios",
                    "Guía de Ruta",
                    "route_save",
                    messageId
                )
            )

        # Paso: receta -> puente a adherencia
        elif step == "rx":
            if text == "rx_recordatorios_si":
                list_responses.append(text_Message(
                    number,
                    "✅ Perfecto. Para configurarlos escribe: *recordatorio de medicamento*."
                ))
            else:
                list_responses.append(text_Message(
                    number,
                    "👍 Entendido. Si más tarde quieres recordatorios, escribe: *recordatorio de medicamento*."
                ))
            st["step"] = "close"
            list_responses.append(
                buttonReply_Message(
                    number,
                    ["Sí, guardar", "No, gracias"],
                    "Guardar / Recordatorios",
                    "Guía de Ruta",
                    "route_close",
                    messageId
                )
            )

        # Paso: urgente
        elif step == "urgent":
            if text == "urgent_sapu_si":
                list_responses.append(text_Message(
                    number,
                    "📍 Envíame tu *comuna o dirección aproximada* y te indico el SAPU más cercano."
                ))
            else:
                list_responses.append(text_Message(
                    number,
                    "⚠️ Recuerda: en una urgencia, acude *de inmediato* o llama al 131."
                ))
            st["step"] = "requirements"
            list_responses.append(text_Message(number, req_docs_steps()))
            list_responses.append(
                buttonReply_Message(
                    number,
                    ["Sí, guardar", "No, gracias"],
                    "Guardar / Recordatorios",
                    "Guía de Ruta",
                    "route_save",
                    messageId
                )
            )

        # Paso: guardar/cerrar
        elif step in ("requirements", "close"):
            if text in ("guardar_si", "cerrar_guardar_si", "ges_reminder_si", "sede_si"):
                list_responses.append(text_Message(
                    number,
                    "✅ Perfecto. Guardado correctamente. Puedo recordarte revisar SOME o el estado de tu trámite cuando lo indiques."
                ))
            else:
                list_responses.append(text_Message(
                    number,
                    "👍 Entendido. Si necesitas volver a la *Guía de Ruta*, escribe: *guía de ruta*."
                ))
            route_sessions.pop(number, None)

        # 👉 ENVÍA Y SALE (importante para no procesar más)
        for i, payload in enumerate(list_responses):
            if payload and payload.strip():
                enviar_Mensaje_whatsapp(payload)
            if i < len(list_responses) - 1:
                time.sleep(1)
        return

    # 8) Default
    else:
        list_responses.append(text_Message(number, respuesta_no_entendido))
        list_responses.append(replyReaction_Message(number, messageId, "❓"))

    # Envío de respuestas acumuladas
    for i, payload in enumerate(list_responses):
        if payload and payload.strip():
            enviar_Mensaje_whatsapp(payload)
        if i < len(list_responses) - 1:
            time.sleep(1)


# ===================================================================
# SISTEMA DE RECORDATORIOS DE MEDICAMENTOS
# ===================================================================

def _reminder_scheduler_loop():
    """Hilo en segundo plano que verifica recordatorios cada minuto."""
    print("🕐 Reminder loop corriendo (1m)…")
    while True:
        try:
            now = _now_hhmm_local()  # respeta TZ Chile si hay pytz
            with REMINDERS_LOCK:
                for number, items in list(MED_REMINDERS.items()):
                    for r in items:
                        if now in r["times"] and r.get("last") != now:
                            med_name = r["name"]
                            msg = (
                                f"⏰ *Recordatorio de medicamento*\n"
                                f"Es hora de tomar: *{med_name}*."
                            )
                            try:
                                enviar_Mensaje_whatsapp(text_Message(number, msg))
                                r["last"] = now
                            except Exception as e:
                                print(f"[reminder-thread] error al enviar: {e}")
            
            # === 3) Recordatorios de RETIRO (DB) ===
            try:
                now_hhmm = now  # ya calculado arriba
                today_date = _safe_today_tz()
                day_str = today_date.isoformat()
                
                with db_conn() as cx:
                    # a) 3 días antes
                    cur = cx.execute("""
                        SELECT number, drug, date, hour FROM pickups
                        WHERE status='pending'
                    """)
                    for number, drug, date_iso, hour in cur.fetchall():
                        from datetime import datetime as _dt, timedelta as _td
                        dd = _dt.fromisoformat(date_iso).date()
                        if (dd - today_date).days == 3 and now_hhmm == hour:
                            enviar_Mensaje_whatsapp(text_Message(
                                number,
                                f"📢 En 3 días te corresponde retirar: *{drug}*. ¿Quieres que te recuerde el mismo día a las {hour}?"
                            ))
                    
                    # b) Día del retiro a la hora
                    cur2 = cx.execute("""
                        SELECT number, drug, date, hour FROM pickups
                        WHERE status='pending' AND date=?
                    """, (day_str,))
                    for number, drug, date_iso, hour in cur2.fetchall():
                        if now_hhmm == hour:
                            enviar_Mensaje_whatsapp(text_Message(
                                number,
                                f"🚨 *Hoy corresponde retirar* *{drug}*.\n"
                                "Responde: *retire {drug} si* o *retire {drug} no*."
                            ))
                    
                    # c) Marcar "missed" a los 7 días (y avisar)
                    cur3 = cx.execute("""
                        SELECT id, number, drug, date FROM pickups
                        WHERE status='pending'
                    """)
                    for pid, number, drug, date_iso in cur3.fetchall():
                        from datetime import datetime as _dt, timedelta as _td
                        dd = _dt.fromisoformat(date_iso).date()
                        if (today_date - dd).days == 7:
                            cx.execute("UPDATE pickups SET status='missed' WHERE id=?", (pid,))
                            enviar_Mensaje_whatsapp(text_Message(
                                number,
                                f"⚠️ No registras el retiro de *{drug}*. ¿Reprogramo una nueva fecha?"
                            ))
            except Exception as e:
                print("[scheduler-pickups] error:", e)
                
        except Exception as e:
            print(f"[reminder-thread] excepción: {e}")
        
        time.sleep(60)  # revisar cada minuto


def _start_reminder_scheduler_once():
    """Arranca el hilo del scheduler solo una vez (idempotente)."""
    global REMINDER_THREAD_STARTED
    if not REMINDER_THREAD_STARTED:
        REMINDER_THREAD_STARTED = True
        t = threading.Thread(target=_reminder_scheduler_loop, daemon=True)
        t.start()
        print("🕐 Hilo de recordatorios iniciado.")


def start_reminder_scheduler():
    """Arranca el hilo del scheduler (idempotente)."""
    _start_reminder_scheduler_once()


def register_medication_reminder(number, med_name, times_list):
    """
    Registra un recordatorio de medicamento.
    
    Args:
        number (str): Número de WhatsApp
        med_name (str): Nombre del medicamento
        times_list (list): Lista de horarios en formato "HH:MM"
    """
    _start_reminder_scheduler_once()  # auto-start
    
    with REMINDERS_LOCK:
        if number not in MED_REMINDERS:
            MED_REMINDERS[number] = []
        
        # Verificar si ya existe este medicamento
        for item in MED_REMINDERS[number]:
            if item["name"] == med_name:
                item["times"] = times_list
                item["last"] = ""
                return
        
        # Agregar nuevo recordatorio
        MED_REMINDERS[number].append({
            "name": med_name,
            "times": times_list,
            "last": ""
        })


def send_due_reminders():
    """
    Ejecuta UNA pasada de verificación/envío de recordatorios pendientes.
    Es la versión 'sin hilo' para ser llamada por un CRON o endpoint HTTP.
    """
    try:
        now = _now_hhmm_local() if 'DEFAULT_TZ' in globals() else datetime.now().strftime("%H:%M")
        with REMINDERS_LOCK:
            for number, items in list(MED_REMINDERS.items()):
                for r in items:
                    if now in r["times"] and r.get("last") != now:
                        med_name = r["name"]
                        msg = (
                            f"⏰ *Recordatorio de medicamento*\n"
                            f"Es hora de tomar: *{med_name}*."
                        )
                        try:
                            enviar_Mensaje_whatsapp(text_Message(number, msg))
                            r["last"] = now
                        except Exception as e:
                            print(f"[cron-reminders] error al enviar: {e}")
    except Exception as e:
        print(f"[cron-reminders] excepción: {e}")
        raise

