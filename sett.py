# sett.py
import os

# ----------------------------------------
# Configuración de WhatsApp
# ----------------------------------------
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_URL   = os.getenv("WHATSAPP_URL")
VERIFY_TOKEN   = os.getenv("VERIFY_TOKEN")   # webhook verification

if not WHATSAPP_TOKEN or not WHATSAPP_URL or not VERIFY_TOKEN:
    raise RuntimeError(
        "Faltan las variables WHATSAPP_TOKEN, WHATSAPP_URL o VERIFY_TOKEN"
    )

# ----------------------------------------
# Configuración de correo
# ----------------------------------------
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))
EMAIL_USER = os.getenv("EMAIL_USER", "salgadoesteban95@gmail.com")
EMAIL_PASS = os.getenv("EMAIL_PASS", "bzys nuqk rguq ukgb")      # tu contraseña de aplicación

if not EMAIL_USER or not EMAIL_PASS:
    raise RuntimeError(
        "Faltan las variables EMAIL_USER o EMAIL_PASS para el envío de correo"
    )
