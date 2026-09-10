# MANUAL DE USUARIO — UNIASSER MARKETING CAMPAIGNS (v19)
> **Módulo:** `crm_marketing_and_comunications`  
> **Área:** CRM, Outbound Marketing, Inbound Social Selling, Inteligencia Artificial  
> **Versión del Sistema:** Odoo 19 (con integración de n8n, Anthropic Claude, Gemini y OpenAI)

---

## 🎯 Introducción y Objetivos de Negocio
El módulo **Uniasser Marketing Campaigns** no es un enviador de correos masivos convencional; es un **motor inteligente de captación y nutrición B2B** diseñado para automatizar el ciclo completo de adquisición de clientes. Combina dos estrategias altamente eficaces en un único flujo de trabajo:

1. **Outbound Inteligente (Campañas Frías):** Captación proactiva de prospectos mediante correos y WhatsApp altamente personalizados por Inteligencia Artificial (IA) atacando los puntos de dolor específicos de su sector, con control estricto de entregabilidad (envío por goteo) y cumplimiento legal estricto de la normativa de protección de datos europea (RGPD y Lista Robinson).
2. **Inbound & Social Selling (LinkedIn Growth):** Escucha activa de señales de interés en LinkedIn (likes, comentarios, vistas de perfil, mensajes directos), puntuación acumulada de leads, cualificación automática, alertas por WhatsApp/Chatter y preparación de llamadas comerciales mediante asistentes de IA.

**Objetivo principal:** Lograr reuniones de demostración, generar un flujo predecible de oportunidades en el CRM y cerrar más ventas reduciendo el esfuerzo manual de prospección en un 80%.

---

## 🧭 Estructura del Menú en Odoo 19

El módulo divide estrictamente las operaciones en dos flujos independientes para evitar mezclar la prospección fría (*Outbound*) con el interés entrante (*Inbound*):

### A. Gestión de Campañas Outbound (En el menú CRM)
Accede desde **CRM → Configuración**:
*   📥 **Buzón de Respuestas:** El centro de mando donde la IA clasifica y procesa las respuestas entrantes de los clientes.
*   📢 **Campañas de marketing:** Creación, configuración, generación y envío de campañas de correo y WhatsApp.
*   🔌 **Proveedores ESP (email):** Configuración de plataformas de envío seguro (ej. Acumbamail).
*   💡 **Puntos de dolor (campañas):** Base de datos de reglas de dolor sectoriales para la personalización por IA.
*   🚫 **Exclusiones RGPD:** Lista unificada de exclusiones (bajas, rebotes, rechazos).
*   🌐 **Canales de marketing:** Gestión de canales disponibles (Email, WhatsApp).
*   🧠 **Plantillas IA (revisión):** Repositorio para la edición y aprobación humana de los copies generados por IA.
*   🔒 **Caché Lista Robinson (Sistemas):** Almacén cifrado (SHA-256) de consultas a la Lista Robinson de España.

### B. LinkedIn Growth (Menú Principal Independiente)
Accede desde la pantalla de aplicaciones en el menú **LinkedIn Growth** (icono de LinkedIn):
*   📊 **Señales:** Registro de todas las interacciones de LinkedIn capturadas.
*   🔥 **Señales calientes:** Leads que han superado el umbral de interés y requieren atención comercial inmediata.
*   📥 **Importar CSV o pegado:** Herramienta ágil para cargar interacciones de herramientas externas.
*   👥 **Leads desde LinkedIn:** Acceso directo a los registros del CRM cuyo origen es LinkedIn.
*   📝 **Publicaciones:** Registro de tus posts en LinkedIn y sus métricas (impresiones, clics, etc.).
*   🛣️ **Router de embudo:** Diagnósticos semanales automáticos sobre la salud de tu marca y embudo en LinkedIn.
*   ⚙️ **Configuración → Perfiles de LinkedIn:** Configuración multicuenta de tus perfiles con sus tokens n8n, audiencias ICP, pesos de puntuación y automatizaciones.

---

## 🚀 Bloque 1: Campañas Outbound (Captación de Clientes en Frío)

Este bloque explica cómo lanzar una campaña desde cero, personalizarla con IA para cada cliente y enviarla de manera segura sin caer en la bandeja de spam.

### Paso 1: Configurar las bases (Una sola vez)
Antes de crear campañas, asegúrate de tener configurado lo siguiente en **CRM → Configuración**:
1.  **Proveedores ESP (email):** Crea tu proveedor de correo masivo (ej: Acumbamail o Mailgun). Esto asegura que los correos salgan firmados correctamente con SPF y DKIM desde un servidor autorizado, protegiendo tu reputación de dominio.
2.  **Puntos de dolor (campañas):** Agrega reglas por sector. Por ejemplo:
    *   *Sector:* "inmobiliaria"
    *   *Punto de dolor:* "Pierden horas respondiendo dudas repetitivas por WhatsApp a leads fríos que nunca compran."
    La IA usará estas reglas para redactar el párrafo inicial de cada correo personalizado basándose en el sector del destinatario.

### Paso 2: Crear una Campaña de Marketing
Ve a **CRM → Configuración → Campañas de marketing** y pulsa **Nuevo**.

1.  **Identificación y Briefing:**
    *   **Nombre de la campaña:** Ej. *"Agencias de Marketing de Madrid – wa-manager"*.
    *   **Propósito de la campaña:** Define qué buscas conseguir. Ej: *"Conseguir que agencias de marketing agenden una demo para automatizar la atención a sus clientes por WhatsApp."*
    *   **Público objetivo:** Ej: *"Agencias de marketing digital medianas que usan WhatsApp Business."*
    *   **Producto / Servicio:** Selecciona el producto de Odoo que vas a promocionar.
2.  **Segmentación:**
    *   **Etapa CRM origen:** Elige de qué etapa del CRM deseas importar tus leads (ej: *Prospectos Identificados*).
    *   Haz clic en el botón **"Cargar leads desde etapa"** para capturar un snapshot de los leads en esa etapa de manera instantánea.
3.  **Canales y Autorizaciones:**
    *   Selecciona los **Canales** (Email y/o WhatsApp).
    *   *Regla de canal:* El correo tiene máxima prioridad. WhatsApp solo se utiliza de manera alternativa para leads que no tengan correo electrónico registrado.
    *   **Autorizo el uso de WhatsApp:** Marca esta casilla si deseas permitir envíos automáticos de WhatsApp. Si la dejas desmarcada, los leads sin email se excluirán por seguridad.
4.  **Mapeo de respuestas en el CRM:**
    *   **Etapa si muestra interés:** Etapa del CRM a la que Odoo moverá el lead si responde positivamente (ej: *Interesado*).
    *   **Etapa si solicita demo/reunión:** Etapa a la que se moverá si pide una reunión (ej: *Reunión Agendada*).
    *   **Etapa si rechaza o pide baja:** Etapa para mover si responde pidiendo que no le escribamos más (ej: *Perdido / Baja*).
5.  **Umbral de confianza IA:** (Por defecto 0.85). Si la IA clasifica una respuesta con una seguridad menor a este porcentaje, no moverá el lead de etapa automáticamente; en su lugar, planificará una actividad de revisión humana en el chatter para que tú decidas.

---

### Paso 3: Generar la Plantilla Base con IA
En la ficha de tu campaña, haz clic en el botón **"Generar Plantilla IA"**.
*   El asistente de IA (Claude) leerá el propósito de la campaña, el público objetivo y la identidad de marca de Uniasser.
*   Creará automáticamente una plantilla de email estructurada en HTML (con cabecera, cuerpo para el párrafo dinámico, llamada a la acción clara y un pie de página profesional).
*   En la pestaña **Plantillas IA**, haz clic sobre la plantilla de email creada para ver el diseño.
*   **Editor Visual e Identidad de Marca:** Puedes cambiar el logo de la cabecera, ajustar los colores hexadecimales (Fondo cabecera, Botón CTA, etc.) y pulsar **"Aplicar colores y logo"** para reconstruir el HTML dinámicamente. 
*   *Nota de entregabilidad:* El logo se redimensiona automáticamente a un tamaño óptimo para evitar que el peso en base64 del email supere los 100 KB, límite en el cual Gmail corta los correos y oculta el enlace de baja.
*   Introduce el **Asunto del email** e inserta la etiqueta `{{OPENING_PARAGRAPH}}` donde desees que se sitúe el párrafo de apertura ultra-personalizado.
*   Cambia el estado de la plantilla a **"Aprobada"**.

---

### Paso 4: Generar Líneas de Campaña e Hiper-personalización
Una vez aprobada la campaña base, pulsa **"Generar líneas de campaña"** en la ficha principal.

Odoo creará un registro en **Líneas de campaña** para cada lead, donde realizará los siguientes filtros automáticos de seguridad:
1.  **Exclusión RGPD Local:** Comprobará si el correo o el teléfono están en la lista de exclusión (bajas anteriores o rechazos).
2.  **Lista Robinson:** Si el lead es de España, consultará de forma segura (mediante hash SHA-256) la API oficial de la Lista Robinson. Si el cliente está inscrito, quedará excluido automáticamente marcando la causa en la ficha del lead para proteger la empresa contra multas.

Para las líneas que pasen el filtro (Estado: *Validado*), haz clic en **"Generar todo el contenido IA"**:
*   La IA analizará individualmente la información de enriquecimiento de cada lead (sector, resumen de actividad, puntos de dolor previos, tamaño de empresa).
*   Buscará si coincide alguna palabra clave con tus reglas de **Puntos de Dolor**.
*   Redactará un primer párrafo único (de 2 a 4 frases) empático, profesional y directo al grano, que conecte el dolor de su sector con nuestro producto.
*   Unirá este párrafo personalizado con la plantilla base y generará un enlace de baja seguro personalizado para este lead (`{{UNSUBSCRIBE_URL}}`).

*Revisión Humana:* Puedes entrar en cada línea de campaña para revisar el texto final redactado y hacer ajustes manuales si lo deseas. Finalmente, haz clic en **"Aprobar todo el contenido"**.

---

### Paso 5: Envío Seguro por Goteo (Drip)
Enviar 500 correos de golpe desde una cuenta corporativa normal garantiza acabar en la carpeta de spam y ver tu cuenta bloqueada por Google o Microsoft. Por ello, este módulo cuenta con un **controlador de goteo (Drip)**.

1.  Introduce un **Email de prueba** en la campaña y pulsa **"Enviar email de prueba"**. Verifica en tu buzón que todo se ve perfecto (diseño, firmas, enlaces).
2.  Pulsa **"Iniciar envío por goteo"**.
3.  El sistema activará un planificador interno que procesará los envíos en pequeños lotes periódicos a lo largo del día.
4.  **Tope de seguridad:** El sistema limita el envío diario a un máximo estricto de **50 correos/día por campaña** (configurable hasta un tope duro) para imitar el comportamiento humano y calentar el dominio de envío de manera segura.
5.  Puedes pulsar **"Pausar goteo"** en cualquier momento si deseas detener los envíos provisionalmente.

---

## 📈 Bloque 2: Seguimiento, Clasificación y Cierre (Lograr Clientes)

Lanzar los correos es solo el 30% del trabajo. El verdadero éxito reside en el seguimiento inmediato y automatizado de las respuestas.

### Clasificación Automática de Respuestas por IA
Cuando un lead responde a tu campaña, el módulo procesa el correo entrante en el **Buzón de Respuestas** y utiliza IA para clasificarlo en una de estas categorías de forma autónoma:

| Clasificación IA | Significado | Acción Automática de Odoo |
| :--- | :--- | :--- |
| `interested` *(Interesado)* | Muestra interés real en el producto/servicio. | Mueve el lead a la **Etapa si muestra interés** y añade una etiqueta verde. |
| `wants_demo` *(Pide demo)* | Solicita explícitamente una reunión, llamada o demostración. | Mueve el lead a la **Etapa de Demo**, planifica una actividad urgente de llamada y te notifica. |
| `unsubscribe_request` *(Baja)* | Pide que no se le contacte más (ej. "dar de baja", "borrar"). | Responde automáticamente con el texto de baja, añade el email/teléfono a la **Lista de Exclusión RGPD** y mueve el lead a la etapa de Perdido. |
| `not_interested` *(No interesado)* | Rechazo educado (ej. "no nos encaja ahora mismo"). | Registra la respuesta en el chatter, detiene los correos de seguimiento, pero no bloquea el email de por vida (por si cambia de empresa o contexto). |
| `out_of_office` *(Ausente)* | Respuesta automática de vacaciones o fuera de la oficina. | Pausa temporalmente el lead en la campaña para reintentarlo semanas después. |
| `wrong_person` *(Contacto erróneo)* | Indica que él no lleva ese departamento (ej. "escríbele a compras"). | Añade nota en el chatter y pide intervención humana para cambiar el contacto del lead. |

> ℹ️ **Revisión Humana por seguridad:** Si la confianza de clasificación es inferior a tu umbral configurado (ej: 0.85), Odoo detiene la automatización, etiqueta la respuesta como *"Requiere revisión humana"* y te crea una tarea para evitar malentendidos comerciales o errores en la base de datos.

---

## 🔗 Bloque 3: LinkedIn Growth (Inbound y Social Selling)

El flujo de LinkedIn Growth está diseñado para capturar la interacción de valor en tu perfil personal de LinkedIn, calentar el lead en Odoo y preparar la llamada comercial de manera quirúrgica para cerrar la venta.

```
[Señal en LinkedIn] ──(n8n / CSV)──> [Odoo: Puntuación de Interés] 
                                                    │
                                                    ▼
[Notificación WhatsApp] <── [Mover a Cualificado] <── (¿Cruza Umbral?)
         │
         ▼
[Botón "Preparar Llamada"] ──(IA)──> [Guión de Venta Personalizado]
```

### Paso 1: Configurar tu Perfil de LinkedIn
Ve a **LinkedIn Growth → Configuración → Perfiles de LinkedIn** y crea tu perfil.
1.  **Contexto de Negocio e ICP:**
    *   **Rol:** Tu cargo (ej: *CEO y Fundador de Uniasser*).
    *   **Contexto de negocio:** Describe detalladamente qué vendes y cuál es tu propuesta de valor.
    *   **Definición de ICP (Cliente Ideal):** Describe a tu cliente ideal (sectores, cargos, dolores).
    *   **Keywords del ICP:** Introduce palabras clave separadas por comas para sectores (ej: *marketing, consultoría*) y cargos (ej: *CEO, director, fundador*).
2.  **Configurar Puntuación de Señales (Engagement Scoring):**
    Asigna puntos a cada acción de un contacto en tu LinkedIn para medir su interés real:
    *   *Dar Me gusta (Reaction):* 1 punto.
    *   *Ver tu perfil:* 1 punto.
    *   *Agregar como conexión / Seguir:* 2 puntos.
    *   *Comentar una publicación:* 2 puntos.
    *   *Mencionarte:* 3 puntos.
    *   *Enviar un mensaje directo:* 3 puntos.
    *   **Bonus ICP:** Suma puntos adicionales (ej: +2 puntos) automáticamente si el cargo o sector del contacto coincide con tus keywords de ICP.
3.  **Umbral Caliente (Hot Threshold):**
    Establece cuántos puntos acumulados necesita un lead para considerarse "Caliente" (ej: **5 puntos**). Un prospecto que te agrega, ve tu perfil y comenta dos publicaciones tuyas cruzará el umbral de inmediato.

---

### Paso 2: Ingesta de Señales (Minería de Red)
Existen dos formas de capturar las interacciones de LinkedIn hacia Odoo:

#### Opción A: Automatización Total con n8n (Recomendada)
1.  En tu ficha de Perfil de LinkedIn en Odoo, ve a la pestaña **Integración con n8n**. Copia tu **Token de Perfil** de forma segura.
2.  Configura un flujo de n8n para descargar interacciones (por ejemplo, monitorizando una carpeta de Google Drive donde dejes los CSVs exportados de LinkedIn o conectando herramientas de scraping).
3.  Utiliza el endpoint `POST /vantis/linkedin/signals` enviando las filas. Odoo se encargará de:
    *   Normalizar los datos y las URL de perfil de LinkedIn.
    *   Deduplicar interacciones repetidas para no inflar la puntuación falsamente.
    *   Identificar al lead existente en Odoo (por su URL de LinkedIn, email o coincidencia de nombre).
    *   Si no existe, **crear un nuevo lead en el CRM** de forma totalmente autónoma.

#### Opción B: Carga Manual Rápida
Si prefieres no usar n8n, puedes importar interacciones directamente desde Odoo:
1.  Ve a **LinkedIn Growth → Importar CSV o pegado**.
2.  Selecciona tu Perfil de LinkedIn.
3.  Pega el texto del CSV exportado o sube el archivo directamente y haz clic en **Importar señales**.
4.  El sistema procesará el lote inmediatamente mostrando estadísticas de los registros creados, actualizados y errores de formato.

---

### Paso 3: Automatización del CRM y Alertas al Instante
Cuando un lead cruza tu **Umbral Caliente**:
1.  Odoo marca la casilla **"Señal Caliente"** en su ficha del CRM y lo mueve automáticamente a tu **Etapa de Cualificados**.
2.  Planifica una **Actividad de Llamada urgente** asignada al comercial responsable del lead.
3.  **Aviso Inmediato por WhatsApp:** Si tienes marcado *"Notificar por WhatsApp"* en tu perfil, Odoo te enviará un mensaje de WhatsApp a tu teléfono móvil con un resumen del lead, su puntuación, su encaje ICP y el enlace directo al CRM en Odoo para que puedas actuar al instante.

---

### Paso 4: Preparación Quirúrgica de Llamada Comercial (AI Call Prep)
No llames a un cliente a puerta fría o sin conocerle. Antes de descolgar el teléfono, aprovecha la Inteligencia Artificial:

1.  Abre la ficha del lead caliente en el CRM.
2.  Haz clic en el botón **"Preparar llamada IA"**.
3.  La IA (Claude) recopilará al instante toda la información disponible en Odoo:
    *   Las publicaciones concretas de LinkedIn que ha comentado el cliente.
    *   Los comentarios literales que ha escrito y los mensajes directos que habéis intercambiado.
    *   El contexto de tu negocio, tu propuesta de valor y su encaje como cliente ideal.
4.  En cuestión de segundos, la IA redactará en la pestaña **Preparación de llamada** un briefing ultra-personalizado:
    *   **Gancho de apertura:** Un rompehielos específico basado en su último comentario en LinkedIn (ej: *"Hola María, vi que comentaste en mi post sobre los dolores de cabeza de gestionar el WhatsApp en equipos de soporte..."*).
    *   **Puntos de dolor clave:** Qué problemas específicos tiene según sus interacciones.
    *   **Objeciones comunes predecibles:** Respuestas preparadas para las posibles excusas del cliente según su sector.
    *   **Propuesta de valor sugerida:** Qué funciones de nuestro producto debes destacar prioritariamente durante la llamada.

---

### Paso 5: Diagnóstico del Embudo y Router de Negocio Semanal
El módulo incluye un **Router de Embudo Semanal** que analiza periódicamente tus métricas de LinkedIn y del CRM para actuar como un consultor de marketing virtual:

*   Se ejecuta automáticamente todos los domingos (vía Cron) o bajo demanda pulsando **"Ejecutar Router de Embudo"** en tu perfil.
*   Analiza métricas como: porcentaje de engagement en posts, volumen de señales recibidas, tasa de conversión de señales a leads calificados y tiempo de estancamiento en etapas del CRM.
*   **Diagnóstico de Cuello de Botella:** La IA redactará un informe que detecta en qué fase estás fallando. Ejemplos de diagnóstico:
    *   *Fase de Atracción (bajo alcance):* Te recomendará publicar contenido más pilar o interactivo.
    *   *Fase de Conversión (muchas señales, pocos leads):* Te sugerirá afinar la llamada a la acción o tus keywords de ICP.
    *   *Fase de Ventas (leads estancados en el CRM):* Te alertará de que hay leads calientes que llevan más de 5 días sin una llamada planificada.
*   El informe se almacena en **LinkedIn Growth → Router de embudo** y se te notifica automáticamente para orientar tu estrategia de contenido de la semana siguiente.

---

## 💡 Consejos de Oro para Lograr Más Clientes con este Módulo

1.  **Mantén actualizadas tus Reglas de Puntos de Dolor:** Tómate 10 minutos cada mes para escribir qué le duele a cada sector. Cuanto más específica sea la regla, más natural y letal será el email personalizado que redactará la IA.
2.  **Calienta tu dominio antes de enviar masivamente:** El goteo de Odoo está configurado por seguridad. No intentes forzar el envío de más de 50 correos diarios por cuenta de correo en frío si tu dominio es joven. La constancia diaria es lo que genera reuniones, no el volumen descontrolado.
3.  **Interactúa en LinkedIn para alimentar el sistema:** Dedica 15 minutos diarios a comentar y publicar en LinkedIn. Cada me gusta o comentario que generes se convertirá, gracias a la integración, en una señal en Odoo, la cual nutrirá de leads automáticos a tu CRM sin que tengas que picar datos a mano.
4.  **Usa la Preparación de Llamada de forma sistemática:** Antes de hacer cualquier llamada de prospección, haz clic en "Preparar llamada IA". Enfocar la conversación demostrando que conoces exactamente qué le interesa al cliente y qué opina en redes sociales multiplica por 3 la probabilidad de agendar una demo técnica de wa-manager.
5.  **Aprovecha el enlace de baja obligatorio para ganar credibilidad:** Cumplir estrictamente con la protección de datos (comprobar la Lista Robinson en España y ofrecer el enlace de baja real mediante `{{UNSUBSCRIBE_URL}}`) no solo evita multas millonarias, sino que proyecta una imagen de empresa profesional y seria ante grandes clientes corporativos (B2B).
