# Uniasser WhatsApp — Odoo 19 Community Edition

Integración completa de Meta WhatsApp Business Cloud API para Odoo 19 CE.
Bandeja de entrada estilo WhatsApp Web, envío de mensajes y plantillas,
recepción por webhook, integración con CRM leads y contactos.

---

## 1. Prerrequisitos

- Odoo 19 Community Edition instalado
- Módulos `mail`, `crm`, `contacts` instalados
- Cuenta de **Meta Business Manager** con acceso a WhatsApp Business API
- Certificado SSL válido en la URL de Odoo (Meta exige HTTPS para webhooks)
- Python: `requests` disponible en el venv de Odoo (ya incluido normalmente)

---

## 2. Configuración en Meta Business Manager

### 2.1 Crear una App de Meta

1. Accede a [developers.facebook.com](https://developers.facebook.com)
2. → **Mis Apps** → **Crear App** → tipo **Business**
3. Añade el producto **WhatsApp** a la app

### 2.2 Obtener el Phone Number ID

1. En la app → **WhatsApp** → **Configuración de la API**
2. En el panel *From*, selecciona tu número de teléfono
3. Copia el **Phone Number ID** (no es el número visible, es un ID numérico largo)

### 2.3 Obtener el WhatsApp Business Account ID (WABA ID)

1. En **Meta Business Manager** → **Configuración del negocio** → **Cuentas de WhatsApp**
2. Copia el **ID de la cuenta** (WABA ID)

### 2.4 Crear un token permanente (System User)

> Los tokens de desarrollador caducan en 24h. Para producción usa un **System User**:

1. En **Meta Business Manager** → **Configuración del negocio** → **Usuarios** → **Usuarios del sistema**
2. Crea o selecciona un System User con rol **Admin**
3. Pulsa **Generar nuevo token** → selecciona tu App → activa el permiso `whatsapp_business_messaging`
4. Copia el token generado: es permanente hasta que lo revoques

### 2.5 Obtener el App Secret

1. En [developers.facebook.com](https://developers.facebook.com) → tu App → **Configuración** → **Básico**
2. Copia el **App Secret** (pulsa "Mostrar")

---

## 3. Configuración del Webhook en Meta Developer Dashboard

1. En tu App → **WhatsApp** → **Configuration** → **Webhook**
2. Pulsa **Edit**:
   - **Callback URL**: `https://TU-ODOO.com/whatsapp/webhook`
   - **Verify Token**: el mismo valor que pondrás en el campo *Webhook Verify Token* en Odoo
3. Pulsa **Verify and Save** — Odoo debe estar accesible públicamente en ese momento
4. En **Webhook Fields** suscríbete a: `messages`

---

## 4. Configuración en Odoo

1. Instala el módulo `uniasser_whatsapp` desde el menú de Apps
2. Ve a **WhatsApp** → **Configuración** → **Cuentas**
3. Crea una nueva cuenta:
   - **Nombre**: nombre descriptivo (ej: *Soporte Uniasser*)
   - **Phone Number ID**: el ID obtenido en el paso 2.2
   - **WABA ID**: el ID obtenido en el paso 2.3
   - **Access Token**: el token permanente del paso 2.4
   - **App Secret**: el secret del paso 2.5 (opcional pero recomendado para validar firmas)
   - **Webhook Verify Token**: elige una cadena aleatoria segura (ej: `mi_token_secreto_123`)
4. Pulsa **Probar conexión** — debe mostrar el nombre verificado del número
5. Pulsa **Sincronizar plantillas** para importar las plantillas aprobadas de Meta

---

## 5. Test end-to-end

1. Envía un WhatsApp desde un móvil al número conectado
2. En Odoo → **WhatsApp** → **Bandeja de entrada**: debe aparecer la conversación
3. Responde desde la bandeja (texto libre, dentro de la ventana de 24h)
4. Verifica que el mensaje aparece también en el chatter del Lead / Contacto correspondiente
5. Para enviar fuera de la ventana de 24h, usa **Enviar WhatsApp** desde el Lead/Contacto y selecciona una plantilla aprobada

---

## 6. Limitación de la ventana de 24h (política de Meta)

Meta solo permite enviar **mensajes de texto libre** cuando el usuario te ha escrito
en las últimas **24 horas**. Fuera de esa ventana, solo puedes iniciar conversaciones
con **plantillas de mensaje aprobadas** (HSM — Highly Structured Messages).

El wizard de envío muestra automáticamente si estás dentro o fuera de la ventana
y bloquea el texto libre cuando corresponde.

Para evitar fricciones:
- Configura plantillas de bienvenida, recordatorio de cita, etc. en Meta Business Manager
- Sincronízalas en Odoo con **Sincronizar plantillas**
- Úsalas desde el wizard o desde la bandeja de entrada

---

## 7. Arquitectura del módulo

```
uniasser_whatsapp/
├── models/
│   ├── whatsapp_api.py        # Clase Python pura: wrapper HTTP de Meta Graph API
│   ├── whatsapp_account.py    # Modelo Odoo: configuración de cuenta WA
│   ├── whatsapp_message.py    # Modelo Odoo: mensajes (in/out), procesamiento webhook
│   ├── whatsapp_template.py   # Modelo Odoo: plantillas sincronizadas desde Meta
│   ├── crm_lead.py            # Herencia: stat button + wizard en leads
│   └── res_partner.py         # Herencia: stat button + wizard en contactos
├── wizard/
│   └── whatsapp_send_wizard.py # Wizard de envío (texto libre / plantilla)
├── controllers/
│   ├── whatsapp_webhook.py    # GET verify + POST receive webhook
│   └── whatsapp_chat_api.py   # JSON API para el frontend OWL
├── static/src/
│   ├── components/
│   │   ├── WhatsAppChat.js         # Componente raíz: layout + lógica
│   │   ├── WhatsAppConversationList.js  # Panel izquierdo
│   │   ├── WhatsAppMessageThread.js    # Thread de burbujas
│   │   └── WhatsAppComposer.js         # Textarea + botón enviar
│   ├── xml/whatsapp_chat.xml   # Templates QWeb/OWL
│   └── css/whatsapp_chat.css   # Estilos tipo WhatsApp Web
├── security/
│   ├── security.xml           # Grupos + reglas multi-empresa
│   └── ir.model.access.csv    # ACLs por modelo
├── data/
│   └── mail_message_subtype.xml  # Subtipo para chatter
├── views/                     # Vistas Odoo 19 (<list> no <tree>)
└── tests/
    └── test_whatsapp_webhook.py  # Tests HttpCase completos
```
