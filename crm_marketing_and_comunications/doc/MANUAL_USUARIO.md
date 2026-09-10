# MANUAL DE USUARIO — VANTIS CRM + MARKETING + COMMUNICATIONS
> **Nombre Comercial:** `CRM Marketing Full Suite`  
> **Módulo Técnico:** `crm_marketing_and_comunications`  
> **Área:** CRM, Outbound Marketing, Inbound Social Selling, Inteligencia Artificial, LinkedIn Growth  
> **Versión del Sistema:** Odoo 18 / Odoo 19 (con motor de Inteligencia Artificial Claude, Gemini, OpenAI y pasarela directa Prosp API)

---

## 🎯 Introducción y Objetivos de Negocio
**Vantis CRM + Marketing + Communications** es un **motor inteligente de captación, automatización y nutrición B2B** diseñado para automatizar por completo el ciclo de adquisición de clientes. Esta "Full Suite" unifica el flujo de trabajo de ventas cruzando tres pilares de prospección comercial:

1. **Outbound Inteligente (Campañas de Email y WhatsApp):** Captación proactiva mediante plantillas e hilos hiper-personalizados por Inteligencia Artificial (IA) atacando los puntos de dolor específicos de cada lead en base a su sector de actividad, con envíos por goteo seguro y control riguroso de Lista Robinson y bajas (RGPD).
2. **Multi-User Gmail Synchronization:** Vinculación directa de las bandejas de correo individuales de cada comercial. Los correos entrantes y salientes se asocian de forma inteligente al chatter de los Leads agrupados por el comercial asignado, ofreciendo trazabilidad comercial absoluta.
3. **LinkedIn Growth & Prosp API Directa:** Conexión nativa por API y protocolos JSON-RPC MCP con la herramienta de prospección **Prosp.ai**. Sincroniza campañas y listas directamente en Odoo, y exporta masivamente hilos de chat personalizados con la IA para LinkedIn, dosificando el envío en segundo plano mediante un goteo inteligente (15 leads cada 10 min) para evitar penalizaciones de firewalls o Cloudflare.

**Objetivo principal:** Conseguir reuniones de demostración, generar un flujo predecible de oportunidades de alta calidad en tu CRM y automatizar el 80% de las tareas repetitivas de prospección manual.

---

## 🧭 Estructura del Menú en Odoo

La suite unifica y organiza de forma impecable las operaciones dividiendo la barra de menús en dos secciones funcionales claramente delimitadas:

### 💼 A. Ejecución de Campañas (CRM ➔ Campañas de Márketing)
Es el espacio de trabajo diario del equipo comercial y de marketing:
*   📢 **Campañas:** Panel para crear, estructurar, personalizar con IA y lanzar tus campañas frías.
*   🛣️ **Seguimiento (Followup):** Motor para disparar secuencias de seguimiento en base a respuestas o falta de interacción de los leads.
*   📥 **Buzón de respuestas (Inbox):** Centro de clasificación inteligente de correos entrantes. La IA analiza si un lead está "Interesado", "Pide demo", "Pide baja" o "Está ausente" y actúa de forma autónoma.

### ⚙️ B. Configuración de la Suite (CRM ➔ Configuración ➔ Márketing y Gmail)
Es el espacio administrativo para asentar los cimientos de la automatización:
*   🧠 **Plantillas IA:** Repositorio y editor visual de las plantillas base de Email, WhatsApp y LinkedIn redactadas por la IA.
*   🔌 **Proveedores ESP (Email):** Configuración de las reputaciones y servidores de envío masivo (ej: Acumbamail, Mailjet).
*   💡 **Reglas de dolor (IA Context):** Base de conocimiento sectorial de puntos de dolor para alimentar el motor de personalización de la IA.
*   🚫 **Exclusiones RGPD:** Lista unificada de correos y teléfonos que han solicitado su exclusión (bajas, rebotes).
*   🔒 **Caché Lista Robinson:** Almacén de consultas encriptadas por hash SHA-256 para cumplir estrictamente con la normativa española de Lista Robinson.
*   👥 **Perfiles de LinkedIn:** Configuración de las cuentas individuales de LinkedIn del equipo comercial, incluyendo la integración de sus API Keys de Prosp.

---

## 🚀 Bloque 1: Campañas Outbound (Captación de Clientes)

Este bloque detalla el flujo de trabajo para estructurar una campaña outbound, nutrirla de leads y redactar los mensajes hiper-personalizados.

### Paso 1: Identidad Corporativa y Clave de Licencia (Administración)
Accede a **Ajustes ➔ Ajustes Generales ➔ Vantis CRM + Marketing**:
1. **Identidad de Marca:** Define el nombre de tu empresa, tu teléfono de soporte, correo de remitente de marketing y el texto de tu logotipo estilizado. El generador de IA leerá estos parámetros en tiempo real para incrustar tus firmas y pies de página de forma dinámica.
2. **Clave de Licencia:** Introduce tu clave de producto de apps.odoo.com o tu clave permanente de propietario. El sistema la validará de forma segura en segundo plano.

### Paso 2: Configuración IMAP de Gmail (Cada Comercial)
A diferencia de los sistemas tradicionales con un correo de envío único, **Vantis** permite a cada agente comercial vincular su propia cuenta:
1. Cada vendedor debe acceder a su pantalla de **"Mi Perfil / Preferencias"** en la parte superior derecha de Odoo.
2. En la pestaña de **Sincronización Gmail**, introduce los datos de conexión de Gmail (Host IMAP `imap.gmail.com`, puerto `993`, tu usuario de Gmail y tu Contraseña de Aplicación de Google).
3. Haz clic en **`Probar conexión`**. El sistema validará la autenticación en caliente y mostrará un aviso de éxito **`✔ Conexión Exitosa con Gmail`**.
4. El proceso en segundo plano de Odoo agrupará tus leads asignados y sincronizará de forma inteligente tus correos entrantes y salientes en el chatter correspondiente.

### Paso 3: Crear una Campaña de Marketing
Ve a **CRM ➔ Campañas de Márketing ➔ Campañas** y haz clic en **Nuevo**:
1. **Briefing de Campaña:** Rellena el propósito de la campaña (ej: *Conseguir demos del software de facturación*), tu público objetivo y selecciona el producto de Odoo a promocionar.
2. **Carga de Leads:** Elige la Etapa de origen de tus iniciativas del CRM y haz clic en **`Cargar leads desde etapa`**. Odoo importará un snapshot limpio de los contactos.
3. **Filtros de Seguridad Automáticos:** Al generar las líneas, Odoo comprobará de forma proactiva si los leads se encuentran en la lista de exclusión RGPD o inscritos en la **Lista Robinson de España** (mediante SHA-256), excluyéndolos automáticamente para proteger tu empresa de multas.

### Paso 4: Generar Plantilla IA y Personalización de Copias
1. Haz clic en **`Generar Plantilla IA`**. La IA generará una plantilla base estructurada en HTML limpio (con cabecera corporativa, un bloque de variables dinámicas, llamada a la acción y pie de página profesional).
2. Abre la plantilla generada bajo la pestaña **Plantillas IA** y asegúrate de situar la etiqueta `{{OPENING_PARAGRAPH}}` donde desees situar el gancho dinámico.
3. En la ficha de tu campaña, haz clic en **`✨ Generar contenido IA`**. La IA de Vantis analizará la actividad de cada empresa, buscará coincidencias en tu base de datos de **Reglas de Puntos de Dolor** por sector y redactará un párrafo de apertura (2-4 frases) hiper-personalizado y directo al dolor de cada lead.
4. Una vez generadas las copias, revísalas si lo deseas en las líneas de campaña y pulsa **`✅ Aprobar todo el contenido`**.

---

## 💼 Bloque 2: Integración Directa con LinkedIn Prosp API

Vantis cuenta con un conector bidireccional nativo por API con **Prosp.ai** para realizar lanzamientos automáticos de hilos de chat personalizados a tu red de LinkedIn.

### Paso 1: Sincronización de Opciones Prosp
1. En la ficha de tu campaña de Odoo, activa el check **`LinkedIn (Prosp)`**.
2. Haz clic en el botón **`🔄 Sincronizar Opciones de Prosp`**.
3. Odoo se conectará en tiempo real por el protocolo JSON-RPC MCP de Prosp, leerá tus campañas y listas activas creadas en tu panel de Prosp, y las cargará en los dropdowns Many2one de la campaña:
   * **Elegir Campaña Prosp** (Muestra tus campañas de Prosp)
   * **Elegir Lista Prosp** (Muestra tus listas de contactos de Prosp)
4. Selecciona las opciones deseadas; Odoo resolverá y guardará los UUIDs correspondientes de forma invisible.

### Paso 2: Lanzamiento Seguro por Goteo (Drip Queue)
Dado que LinkedIn y Cloudflare penalizan el tráfico automatizado masivo (DDoS), **Vantis incorpora un loteador de goteo en segundo plano de seguridad**:
1. Pulsa el botón **`Lanzar en Prosp`**.
2. Odoo activará la cola de goteo de la campaña de forma asíncrona, mostrando el banner informativo:
   ➔ **`Lanzamiento en Prosp en proceso (goteo de seguridad activo)`**
3. El planificador asíncrono de Odoo (`ir_cron_prosp_drip`) se ejecutará cada **10 minutos**:
   * Tomará un **lote máximo de 15 contactos aprobados**.
   * Limpiará cualquier código o formato HTML del editor de Odoo (enviando texto plano limpio para el chat de LinkedIn).
   * Enviará las peticiones a la API de Prosp inyectando una **pausa aleatoria de seguridad de entre 0.3 y 0.8 segundos** entre cada contacto individual para emular el comportamiento humano.
   * Marcará las líneas como enviadas (`sent_at = NOW()`).
   * Al finalizar el lote, se pondrá a dormir de forma segura, reiniciándose en 10 minutos para procesar los siguientes 15 leads hasta completar la cola con éxito.

---

## 📊 Bloque 3: Seguimiento y Cierre Comercial

### Clasificación de Respuestas por IA
Cuando un cliente responde a tu campaña de email, la IA de Vantis analiza el contenido de la respuesta en tu **Buzón de Respuestas** y la clasifica en segundos, ejecutando acciones comerciales automatizadas en el CRM:

*   **`interested` (Interesado):** Mueve automáticamente el Lead a tu etapa de interesados asignada en la campaña y añade una etiqueta verde.
*   **`wants_demo` (Pide reunión/demo):** Mueve el lead a tu etapa de demos/reunión, te planifica una actividad de llamada urgente y te notifica por WhatsApp o Chatter.
*   **`unsubscribe_request` (Solicita Baja):** Detiene de inmediato la secuencia de correos, responde con un correo educado de confirmación, agrega el email a la **Lista de Exclusión de Bajas (RGPD)** de Odoo y marca la oportunidad como perdida.
*   **`not_interested` (No le encaja):** Detiene los correos de la campaña, añade una nota informativa en el chatter de Odoo para conocimiento de todo el equipo de ventas, pero no bloquea su contacto de por vida para futuros contextos comerciales.
*   **`out_of_office` (Fuera de la oficina):** Pausa la campaña de forma inteligente para este contacto, programando su reanudación de envío para semanas después.

---

## 🛠️ Soporte y Licencias de Producto

La suite de marketing de Vantis está protegida por la licencia de propiedad de Odoo **OPL-1**. 

* **Soporte Oficial por Email:** Para cualquier duda de integración, soporte de APIs, configuración de servidores de correo o solicitudes de asignación de licencias de prueba gratuitas de 15 días, ponte en contacto con nuestro equipo técnico a través de:
  ➔ **`info@uniasser.com`**
* **Sitio Web de Vantis:** Puedes consultar manuales avanzados de la suite, videotutoriales de uso, solicitar integraciones a medida o contratar consultorías personalizadas en:
  ➔ **`https://vantis.uniasser.net`**
