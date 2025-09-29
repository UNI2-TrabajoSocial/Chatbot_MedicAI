# 📋 DOCUMENTACIÓN TÉCNICA - CHATBOT MEDICAI

## 📊 INFORMACIÓN GENERAL

**Nombre del Proyecto:** MedicAI - Asistente Virtual de Salud  
**Versión:** 2.0  
**Fecha de Documentación:** 29 de Septiembre, 2025  
**Desarrollado para:** Sistema de Salud Primaria - CESFAM  
**Plataforma:** WhatsApp Business API  
**Lenguaje:** Python 3.9+  
**Framework:** Flask  

---

## 🎯 PROPÓSITO Y ALCANCE

### Objetivo Principal
MedicAI es un chatbot inteligente diseñado para optimizar la gestión de servicios de salud primaria a través de WhatsApp, proporcionando asistencia automatizada 24/7 para:

- Agendamiento de citas médicas
- Gestión de recordatorios de medicamentos
- Orientación médica inicial basada en síntomas
- Guía de ruta para trámites y derivaciones médicas
- Control de stock y retiro de medicamentos
- Gestión de adherencia terapéutica

### Beneficiarios
- **Pacientes:** Acceso inmediato a servicios de salud
- **Personal CESFAM:** Reducción de carga administrativa
- **Sistema de Salud:** Optimización de recursos y tiempos

---

## 🏗️ ARQUITECTURA DEL SISTEMA

### Componentes Principales

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   WhatsApp      │    │     Flask       │    │    SQLite       │
│   Business API  │◄──►│   Application   │◄──►│   Database      │
│                 │    │                 │    │                 │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         ▲                       │                       ▲
         │                       ▼                       │
         │              ┌─────────────────┐              │
         │              │   Scheduler     │              │
         └──────────────│   (Threading)   │──────────────┘
                        └─────────────────┘
```

### Estructura de Archivos

```
Chabot_Ampara-main/
├── app.py                 # Aplicación Flask principal
├── services.py            # Lógica del chatbot y servicios
├── sett.py               # Configuraciones y variables de entorno
├── requirements.txt      # Dependencias de Python
├── Procfile             # Configuración para despliegue (Heroku)
├── README.md            # Documentación básica
└── medicai.db           # Base de datos SQLite (generada automáticamente)
```

---

## 🔧 CONFIGURACIÓN TÉCNICA

### Variables de Entorno Requeridas

```env
# WhatsApp Business API
WHATSAPP_TOKEN=your_whatsapp_business_token
WHATSAPP_URL=https://graph.facebook.com/v18.0/phone_number_id/messages
VERIFY_TOKEN=your_webhook_verification_token

# Base de Datos
MEDICAI_DB=medicai.db

# Zona Horaria
APP_TZ=America/Santiago

# Configuración de Email (opcional)
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USER=your_email@gmail.com
EMAIL_PASS=your_app_password
```

### Dependencias de Python

```
Flask==2.0.3              # Framework web
requests==2.31.0          # Cliente HTTP para API de WhatsApp
gunicorn==20.1.0          # Servidor WSGI para producción
python-dotenv==1.0.0      # Manejo de variables de entorno
blinker==1.6.2            # Sistema de señales para Flask
```

---

## 🗄️ ESTRUCTURA DE BASE DE DATOS

### Tabla: `meds` (Medicamentos)
```sql
CREATE TABLE meds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE COLLATE NOCASE,    -- Nombre del medicamento
    stock INTEGER DEFAULT 0,            -- Cantidad disponible
    location TEXT,                      -- Sede donde está disponible
    price INTEGER                       -- Precio (opcional)
);
```

### Tabla: `pickups` (Retiros Programados)
```sql
CREATE TABLE pickups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number TEXT,                        -- Número de teléfono del usuario
    drug TEXT,                          -- Nombre del medicamento
    date TEXT,                          -- Fecha programada (ISO format)
    hour TEXT,                          -- Hora programada (HH:MM)
    freq_days INTEGER,                  -- Frecuencia en días (NULL = una vez)
    status TEXT,                        -- Estado: 'pendiente', 'completado', 'no_retirado'
    created_at TEXT                     -- Timestamp de creación
);
```

### Índices Optimizados
```sql
CREATE INDEX idx_meds_name ON meds(name);
CREATE INDEX idx_pickups_num ON pickups(number);
CREATE INDEX idx_pickups_date ON pickups(date);
```

---

## 🤖 FLUJOS DE CONVERSACIÓN

### 1. Flujo Principal (Menú Inicial)

**Trigger:** `hola`, `buenas`, `saludos`

**Opciones:**
- 🗓️ Agendar Cita Médica
- 💊 Recordatorio de Medicamentos  
- ➕ Más Opciones

### 2. Flujo de Agendamiento de Citas

**Trigger:** `agendar cita`, `cita medica`

**Pasos:**
1. Selección de especialidad (3 páginas con paginación)
2. Elección de fecha y hora
3. Confirmación de sede
4. Configuración de recordatorios
5. Confirmación final

**Especialidades Disponibles:**
- Medicina General, Pediatría, Ginecología
- Salud Mental, Kinesiología, Odontología
- Oftalmología, Dermatología, Traumatología
- Cardiología, Nutrición, Fonoaudiología
- Y 10 especialidades adicionales

### 3. Flujo de Recordatorios de Medicamentos

**Trigger:** `recordatorio de medicamento`

**Pasos:**
1. Captura del nombre del medicamento
2. Definición de frecuencia de tomas
3. Configuración de horarios específicos
4. Registro automático en el sistema

**Sistema de Recordatorios:**
- Almacenamiento en memoria con respaldo persistente
- Verificación cada minuto mediante scheduler
- Envío automático de notificaciones

### 4. Flujo de Orientación de Síntomas

**Trigger:** `orientacion de sintomas`

**Categorías Médicas:**
- Respiratorias, Bucales, Infecciosas
- Cardiovasculares, Metabólicas, Neurológicas
- Musculoesqueléticas, Salud Mental
- Dermatológicas, Ginecológicas, Digestivas

**Proceso:**
1. Selección de categoría médica
2. Extracción de síntomas del usuario
3. Análisis mediante funciones especializadas
4. Orientación médica personalizada con disclaimer

### 5. Flujo de Guía de Ruta (Derivaciones)

**Trigger:** `guia de ruta`, `derivacion`

**Tipos de Documentos:**
- 📄 Interconsulta médica (GES/No GES)
- 🧾 Orden de exámenes/procedimientos
- 💊 Receta o indicación de tratamiento
- 🚨 Derivación urgente
- ❓ Documento no identificado

**Gestión Especializada por Documento:**
- Instrucciones paso a paso específicas
- Verificación de requisitos GES
- Configuración de recordatorios
- Orientación sobre SAPU/urgencias

### 6. Flujo de Stock y Retiro de Medicamentos

**Trigger:** `stock de medicamentos`

**Funcionalidades:**
- Verificación de disponibilidad en tiempo real
- Programación de retiros únicos o cíclicos
- Confirmación de retiros realizados
- Vinculación con adherencia terapéutica
- Gestión de inventario básico

---

## 🔧 FUNCIONES TÉCNICAS PRINCIPALES

### Manejo de Sesiones
```python
# Sesiones globales para mantener estado
appointment_sessions = {}    # Agendamiento de citas
medication_sessions = {}     # Recordatorios de medicamentos
session_states = {}         # Estados de orientación médica
route_sessions = {}         # Guía de ruta y derivaciones
stock_sessions = {}         # Gestión de stock y retiros
```

### Sistema de Recordatorios
```python
MED_REMINDERS = {}          # Recordatorios activos en memoria
REMINDERS_LOCK = threading.Lock()  # Thread safety

def start_reminder_scheduler():
    """Inicia scheduler para verificar recordatorios cada minuto"""
    
def check_and_send_reminders():
    """Verifica y envía recordatorios programados"""
```

### Mapeo de Interfaces
```python
ui_mapping = {
    # Mapeo de IDs de botones y listas a comandos de texto
    "menu_principal_btn_1": "agendar cita",
    "route_type_row_1": "interconsulta",
    # ... 200+ mapeos para navegación fluida
}
```

### Manejo de Zona Horaria
```python
def _now_hhmm_local(tz_name: str = DEFAULT_TZ) -> str:
    """Manejo robusto de zona horaria con fallbacks"""
    # Prioridad: zoneinfo > pytz > UTC
```

### Normalización de Texto
```python
def normalize_text(t: str) -> str:
    """Normaliza texto para procesamiento uniforme"""
    # Convierte a minúsculas y elimina acentos
```

---

## 📨 INTEGRACIÓN CON WHATSAPP BUSINESS API

### Tipos de Mensajes Soportados

**1. Mensajes de Texto Simple**
```python
def text_Message(number, text):
    return json.dumps({
        "messaging_product": "whatsapp",
        "to": number,
        "type": "text",
        "text": {"body": text}
    })
```

**2. Mensajes con Botones (máximo 3)**
```python
def buttonReply_Message(number, options, body, footer, sedd, messageId):
    # Botones interactivos para navegación rápida
```

**3. Mensajes con Listas (máximo 10 items)**
```python
def listReply_Message(number, options, body, footer, sedd, messageId):
    # Listas desplegables para múltiples opciones
```

**4. Reacciones y Confirmaciones**
```python
def replyReaction_Message(number, messageId, emoji):
    # Reacciones con emojis para feedback
    
def markRead_Message(messageId):
    # Marca mensajes como leídos
```

### Límites de WhatsApp API
- **Mensaje de texto:** 4,096 caracteres máximo
- **Footer:** 60 caracteres máximo  
- **Botones:** 3 máximo por mensaje
- **Listas:** 10 items máximo por lista
- **Título de botón:** 20 caracteres máximo

---

## 🔒 SEGURIDAD Y VALIDACIÓN

### Validación de Webhook
```python
@app.route('/webhook', methods=['GET'])
def verificar_token():
    # Verifica token de WhatsApp para autenticación
```

### Manejo de Errores
```python
try:
    # Procesamiento de mensajes
except Exception as e:
    print(f"Error procesando mensaje: {e}")
    # Log de errores para debugging
```

### Thread Safety
```python
REMINDERS_LOCK = threading.Lock()
# Protección de recursos compartidos en entorno multi-thread
```

---

## 📊 COMANDOS DISPONIBLES

### Medicamentos & Recordatorios
- `recordatorio de medicamento` - Crear nuevo recordatorio
- `mis recordatorios` - Ver recordatorios activos
- `eliminar recordatorio [N°]` - Eliminar recordatorio específico
- `gestionar recordatorios` - Panel de gestión
- `vincular tomas [med] HH:MM` - Vincular con adherencia

### Stock & Retiros
- `stock de medicamentos` - Gestión de stock
- `mis retiros` / `ver retiros` - Ver retiros programados
- `retire [medicamento] si|no` - Confirmar retiro
- `programar retiro [med] [fecha] [hora]` - Programar retiro específico
- `programar ciclo [med] [fecha] [hora] cada [días]` - Retiros cíclicos
- `stock agregar [med] [cantidad]` - Aumentar inventario
- `stock bajar [med] [cantidad]` - Reducir inventario
- `stock ver [medicamento]` - Consultar stock específico

### Servicios Médicos
- `agendar cita` / `cita medica` - Agendar cita médica
- `orientación de síntomas` - Análisis de síntomas
- `guía de ruta` / `derivacion` - Gestión de derivaciones

### Emergencias
- `ayuda urgente` / `urgente` - Activar protocolo de emergencia
- `samu` / `131` - Acceso directo a emergencias

### Utilidades
- `hola` - Menú principal
- `comandos` - Lista completa de comandos
- `gracias` - Agradecimiento
- `adiós` / `chao` - Despedida

---

## ⚡ CARACTERÍSTICAS TÉCNICAS AVANZADAS

### Sistema de Paginación Inteligente
- Manejo automático de listas extensas
- Navegación fluida entre páginas
- Preservación de contexto entre páginas

### Gestión de Estado Distribuida
- Múltiples tipos de sesiones simultáneas
- Limpieza automática de sesiones inactivas
- Persistencia de datos críticos

### Mapeo Dinámico de UI
- Traducción automática de interacciones de UI a comandos
- Soporte para botones y listas interactivas
- Manejo unificado de entradas de usuario

### Scheduler Robusto
- Verificación continua de recordatorios
- Manejo de múltiples zonas horarias
- Recuperación automática ante fallos

### Base de Datos Optimizada
- Índices para consultas rápidas
- Transacciones ACID
- Inicialización automática de esquema

---

## 🚀 DESPLIEGUE Y PRODUCCIÓN

### Heroku Deployment
```bash
# Procfile para Heroku
web: gunicorn app:app
```

### Variables de Entorno en Producción
```bash
heroku config:set WHATSAPP_TOKEN=your_token
heroku config:set WHATSAPP_URL=your_url
heroku config:set VERIFY_TOKEN=your_verify_token
```

### Configuración de Base de Datos
- SQLite para desarrollo y pruebas
- PostgreSQL recomendado para producción
- Backup automático recomendado

---

## 🐛 DEBUGGING Y MANTENIMIENTO

### Logs del Sistema
```python
print("💥 LLEGÓ WEBHOOK:", body)  # Entrada de mensajes
print("🔔 Enviando recordatorio")  # Sistema de recordatorios
print("🗄️ DB lista:", DB_PATH)    # Estado de base de datos
```

### Comandos de Debug
- `debug hora` - Verificar hora del servidor
- `test en 1 min` - Probar sistema de recordatorios

### Monitoreo Recomendado
- Logs de WhatsApp API responses
- Métricas de uso por flujo
- Tiempo de respuesta del sistema
- Errores de base de datos

---

## 📈 MÉTRICAS Y ANALÍTICAS

### KPIs Sugeridos
- **Mensajes procesados por día**
- **Citas agendadas exitosamente**
- **Recordatorios enviados vs. confirmados**
- **Tiempo promedio de resolución por flujo**
- **Tasa de abandono por flujo**
- **Uso de comandos más populares**

### Datos de Utilización
- Horarios pico de uso
- Flujos más utilizados
- Errores más frecuentes
- Satisfacción del usuario (mediante feedback)

---

## 🔄 MANTENIMIENTO Y ACTUALIZACIONES

### Actualizaciones Recomendadas
1. **Mensual:** Revisión de logs y optimización
2. **Trimestral:** Actualización de dependencias
3. **Semestral:** Revisión de flujos y UX
4. **Anual:** Migración de tecnologías y mejoras mayores

### Backup y Recuperación
- Backup diario de base de datos
- Versionado de código con Git
- Documentación de cambios críticos
- Plan de rollback para actualizaciones

---

## 👥 CONTACTO Y SOPORTE

**Desarrollador Principal:** Sistema MedicAI  
**Mantenimiento:** Equipo de Desarrollo CESFAM  
**Soporte Técnico:** [Email de soporte técnico]  
**Documentación:** Actualizada al 29/09/2025  

---

## 📜 CHANGELOG

### Versión 2.0 (Septiembre 2025)
- ✅ Eliminación completa de funcionalidad "explicador de documentos"
- ✅ Actualización de fechas de citas (abril → septiembre 2025)
- ✅ Mejora integral de explicaciones en "Guía de Ruta"
- ✅ Corrección de errores en flujo de rutas médicas
- ✅ Mejoras estéticas en todos los flujos
- ✅ Optimización para cumplimiento de límites de WhatsApp API
- ✅ Lista completa de comandos disponibles
- ✅ Sistema robusto de manejo de zona horaria

### Versión 1.0 (Versión Base)
- Implementación inicial de todos los flujos principales
- Integración con WhatsApp Business API
- Sistema básico de recordatorios
- Base de datos SQLite
- Funcionalidades de agendamiento y orientación médica

---

*Documento técnico generado automáticamente para MedicAI v2.0*  
*Fecha: 29 de Septiembre, 2025*  
*Total de líneas de código: 3,031 (services.py) + 73 (app.py) + 30 (sett.py)*