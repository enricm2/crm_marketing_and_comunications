# LinkedIn Growth — contrato de integración con n8n

Todo lo que el flujo de n8n necesita para hablar con este Odoo. Los mismos datos
están dentro de Odoo, resueltos con las URLs y el token reales, en:

- **Ajustes → Marketing Campaigns → LinkedIn Growth** (tabla de endpoints)
- **LinkedIn Growth → Configuración → Perfiles → [tu perfil] → Integración con n8n**
  (la guía con tu token ya puesto)

---

## 1. Autenticación

Todas las llamadas entrantes llevan la cabecera:

```
X-Vantis-Token: <token del perfil>
```

También se acepta `Authorization: Bearer <token>`, por si el nodo de n8n está
configurado con autenticación genérica.

El token **identifica además de autenticar**. n8n no manda ningún `profile_id`
ni ningún usuario: Odoo deduce de qué perfil viene cada señal a partir del
token, y por eso un flujo no puede escribir en el perfil de otro usuario por
error. Cada perfil de LinkedIn tiene el suyo (**Regenerar token** lo invalida).

Sin token → `401`. Token desconocido → `403`. Perfil archivado → `403`.

---

## 2. Endpoints que llama n8n (n8n → Odoo)

Base: `https://<tu-odoo>` (en producción, `https://odoo19.uniasser.net`).

### `POST /vantis/linkedin/signals` — minero de red

El endpoint del CSV de LinkedIn. **Es el único nodo que hace falta del lado de
n8n para el minero de red**: no hay que buscar contactos, decidir ramas, crear
leads ni incrementar puntuaciones desde el flujo. Por cada fila del array, Odoo
hace todo esto:

1. Normaliza claves (acepta castellano e inglés) y fechas.
2. Deduplica por huella *perfil + persona + tipo + post + día*.
3. Evalúa el encaje ICP con las keywords del perfil.
4. Calcula los puntos: peso del tipo de señal + bonus ICP.
5. Busca lead existente: URL de LinkedIn → email → nombre+empresa → contacto.
6. Si no hay, busca contacto en la agenda; si tampoco, **crea el `crm.lead`**
   con origen *LinkedIn Signal*, medio LinkedIn, etiqueta por tipo de señal,
   equipo, comercial, etapa inicial y el contexto de la señal en las notas.
7. **Incrementa `x_linkedin_score`**, actualiza la fecha de última señal,
   añade la etiqueta y deja nota en el chatter del lead.
8. Si el lead cruza el umbral: lo mueve a la etapa de cualificado, planifica la
   actividad de llamada y avisa (chatter + WhatsApp).
9. Registra el lote con contadores y el detalle de las filas que fallaron.

**Manda todas las filas en una sola llamada**, no una llamada por fila: el
endpoint recibe un array. En n8n eso significa que **no hace falta *Loop Over
Items* ni *Split In Batches***.

**Una fila mala no tumba el lote.** Cada fila va en su propio savepoint: las que
fallan vuelven en `error_details` con el motivo y el resto entra igual.

```json
{
  "source": "n8n_csv",
  "source_file": "comentarios-2026-08.csv",
  "rows": [
    {
      "nombre": "María López",
      "linkedin_url": "https://www.linkedin.com/in/maria-lopez-ceramica/",
      "empresa": "Cerámicas Altea SL",
      "cargo": "Directora General",
      "tipo_senal": "comentario",
      "fecha_senal": "2026-08-06T09:12:00Z",
      "post_id": "urn:li:activity:7001",
      "comentario": "Justo lo que nos pasa. ¿Cómo lo medís?"
    }
  ]
}
```

`tipo_senal` acepta: `comentario`/`comment`, `like`/`reaction`, `compartido`/
`share`, `mencion`, `vista_perfil`, `conexion`, `seguidor`, `mensaje`. También
valen las claves en inglés (`name`, `company`, `title`, `type`, `date`).
`fecha_senal` acepta ISO 8601, `YYYY-MM-DD`, `DD-MM-YYYY` y epoch.

Respuesta:

```json
{
  "ok": true,
  "batch_id": 1, "batch_reference": "LI-LOTE-2026-0001",
  "received": 6, "created": 4, "duplicated": 0, "errors": 2,
  "hot_signals": 2, "leads_created": 3, "leads_updated": 1,
  "error_details": [{"row": 4, "error": "Tipo de señal no reconocido: telepatia"}]
}
```

Usa `error_details` para el nodo de aviso por email: dice exactamente qué fila
falló y por qué, sin tener que reproducir la exportación.

**Deduplicación.** La huella es *perfil + persona + tipo + post + día*. La
persona se identifica por la URL de LinkedIn normalizada, así que
`es.linkedin.com/in/x?trk=abc` y `www.linkedin.com/in/x/` son la misma. Reenviar
el mismo CSV no duplica nada ni infla la puntuación: se puede reprocesar sin
miedo.

### `POST /vantis/linkedin/posts` — métricas de publicaciones

```json
{
  "posts": [
    {
      "external_id": "urn:li:activity:7001",
      "titulo": "El Excel que te está costando 20 h al mes",
      "url": "https://www.linkedin.com/feed/update/urn:li:activity:7001/",
      "fecha_publicacion": "2026-08-05T08:30:00Z",
      "impresiones": 8400, "reacciones": 210, "comentarios": 48,
      "compartidos": 12, "clics": 95,
      "formato": "text", "pilar": "Automatización"
    }
  ]
}
```

La identidad es `external_id` dentro del perfil: mandarlo dos veces **actualiza**
en vez de duplicar. Un payload de solo métricas nunca pisa el contenido
editorial que se haya escrito a mano en Odoo.

Las señales que llegaron antes que la publicación se enganchan solas cuando el
post entra con ese `external_id`.

### `GET /vantis/linkedin/metrics?days=30` — datos del router

Devuelve las métricas del embudo y el diagnóstico ya calculado, **sin crear
ningún registro**. Para que el flujo razone con los mismos datos que usa el
router interno.

```json
{
  "ok": true, "diagnosis": "positioning",
  "diagnosis_reasoning": "Engagement medio 2.37% por debajo del objetivo…",
  "bottleneck_stage": "", "next_action": "post-viral-linkedin",
  "ai_context": "=== ROL ===\n…",
  "metrics": {"content": {…}, "pipeline": {…}, "benchmarks": {…}}
}
```

`ai_context` es el rol + contexto + ICP + tono de ese perfil. Es lo que hay que
poner delante del prompt si la redacción del informe la hace n8n.

### `POST /vantis/linkedin/router/run` — lanzar el router desde n8n

`{"days": 30, "notify": true}`. Crea el diagnóstico, redacta el informe y avisa.
Úsalo si prefieres que la periodicidad la mande el cron de n8n en vez del de
Odoo (en ese caso, desactiva el cron semanal en Ajustes).

### `POST /vantis/linkedin/diagnostic` — devolver el informe redactado

`{"diagnostic_id": 12, "report": "<p>…</p>", "notify": true}`

n8n **redacta, no decide**: la fase señalada la calcula Odoo con reglas
reproducibles y este endpoint no la toca.

### `GET /vantis/linkedin/lead-context?lead_id=42` — contexto para la llamada

Datos del lead + comunicaciones + actividades pendientes y cerradas + señales de
LinkedIn, en una sola llamada. Sustituye a encadenar tres nodos HTTP Request.

### `POST /vantis/linkedin/call-prep` — devolver la preparación

`{"lead_id": 42, "prep": "<h4>…</h4>"}`

Odoo la escribe en la pestaña **Preparación de llamada** del lead y publica una
copia en el chatter.

### `GET /vantis/linkedin/ping` — comprobación

Empieza siempre por aquí al montar el flujo. Devuelve el perfil, su motor de IA
y su umbral.

---

## 3. Webhooks que llama Odoo (Odoo → n8n)

Odoo hace `POST` con la misma cabecera `X-Vantis-Token`.

**Regla general: sin ruta configurada, Odoo no llama a n8n.** Vaciar el campo en
Ajustes es la forma de apagar una llamada saliente. Esto importa porque casi
todo el flujo funciona sin que Odoo hable con n8n.

| Ruta | Estado de fábrica | Cuándo se dispara | Payload |
|---|---|---|---|
| `/webhook/ventas-ligero` | configurada, **inactiva** | Botón **Preparar llamada IA**, solo si el perfil tiene el motor de IA en `n8n`. Con el motor en `odoo` (por defecto) no se llama nunca | `action`, `lead_id`, `partner_name`, `stage_id`, `ai_context`, `context` (todo el lead), `callback_url` |
| `/webhook/router-embudo` | configurada, **inactiva** | Router de embudo, solo si el perfil tiene el motor de IA en `n8n`. Sirve para que la **redacción** del informe la haga n8n; la fase diagnosticada siempre la calcula Odoo | `action`, `diagnostic_id`, `diagnosis`, `diagnosis_reasoning`, `metrics`, `ai_context`, `notify_phone`, `callback_url` |
| `/webhook/senal-caliente` | **vacía → desactivada** | Un lead supera el umbral | `action`, `signal_id`, `lead_id`, `lead_url`, datos del contacto, `score`, `icp_fit` |
| `/webhook/vantis-ping` | configurada | Botón **Probar conexión con n8n** | `action: "ping"` |

Todos llevan además `profile_id`, `profile_name` y `odoo_base_url`.

Si un webhook no existe o n8n no responde, Odoo **no se rompe**: el botón da un
error explicando qué revisar, y el router cae al informe por reglas.

### Dos decisiones que hay que tomar una sola vez

**1. ¿Quién avisa de una señal caliente?** Cuando un lead supera el umbral, Odoo
ya avisa por su cuenta: nota en el chatter del perfil y, si está activado,
WhatsApp al teléfono de aviso. Si tu flujo del minero de red además tiene su
propio nodo de notificación después de mandar las señales, **elige uno de los
dos** — no dejes los dos activos o recibirás dos avisos por la misma señal.

- Aviso desde Odoo (recomendado, y lo que hay de fábrica): deja
  `/webhook/senal-caliente` vacío y quita el nodo de notificación de n8n. Odoo
  es quien sabe la puntuación acumulada del lead y quién cruza el umbral: n8n
  solo ve la fila del CSV.
- Aviso desde n8n: mantén tu nodo y deja la ruta vacía igualmente. Odoo seguirá
  dejando la nota en el chatter (eso no se puede desactivar: es la traza), pero
  desmarca *Avisar por WhatsApp* en el perfil.
- Enrutar por n8n (Slack, Telegram, agenda): rellena la ruta y quita tu nodo.

**2. ¿Quién programa el router semanal?** Hay dos formas y **solo debe haber
una activa**, o generarás dos diagnósticos e informes por semana:

- Cron de Odoo (activo de fábrica): Ajustes → *Router de embudo semanal*.
- Cron de n8n llamando a `POST /vantis/linkedin/router/run`: si eliges esta,
  **desactiva** el cron de Odoo en Ajustes.

---

## 4. Los dos patrones de integración

**Patrón A — Odoo dispara bajo demanda.** El botón "Preparar llamada IA" de la
ficha del lead. Con el motor de IA en `odoo`, Claude escribe la preparación y
aparece al instante, sin n8n. Con el motor en `n8n`, se dispara el webhook y el
resultado vuelve por `/vantis/linkedin/call-prep`. El usuario ve lo mismo en
los dos casos.

**Patrón B — n8n corre solo.** El minero de red y, si se quiere, el router. n8n
escribe en Odoo por los endpoints de arriba; Odoo "capta" por el simple hecho
de que el `crm.lead` se crea.

La respuesta automática a esos leads (cualificar, planificar llamada, avisar)
**no** necesita una acción automatizada en Ajustes → Técnico: son campos del
perfil (*Automatismos del CRM*), así que cada usuario tiene sus reglas sin tocar
la configuración técnica y sin que un cambio afecte a los demás.

---

## 5. Flujo MVP sugerido en n8n (minero de red)

```
1. Google Drive Trigger — "File Created" en /Minero-LinkedIn/inbox/
2. Google Drive → Download
3. Extract From File (CSV) → filas
4. Code — normaliza cada fila a {nombre, linkedin_url, empresa, cargo,
   tipo_senal, fecha_senal, post_id, comentario}
5. HTTP Request → POST /vantis/linkedin/signals con TODAS las filas en "rows"
   (una sola llamada, sin Loop Over Items ni Split In Batches: Odoo ya
   deduplica, puntúa, busca o crea el lead, cualifica, planifica la llamada
   y avisa)
6. IF errors > 0 → email a Enric con error_details
```

Seis nodos, y el 5 es uno solo.

Los pasos 5a–5d del planteamiento original (buscar duplicados en res.partner /
crm.lead, decidir rama "existe / no existe", crear el lead, crear el contacto,
incrementar `x_linkedin_score`) y el nodo de notificación de señal caliente
**no hacen falta como nodos**: todo eso ocurre dentro del endpoint. Esto no es
podar el final del flujo — es rehacer el tramo del 5 al 11 y sustituirlo por
una única llamada.

Por qué se decidió así, y no repartido: la puntuación es **acumulada por lead**,
no por fila. Si alguien comentó hace tres semanas y hoy reacciona, quien cruza
el umbral es el lead, no la fila que acaba de llegar. n8n solo ve el CSV del
momento; Odoo ve el histórico. Poner la decisión donde están los datos evita
que las dos mitades discrepen.

Cron: semanal mientras la exportación sea manual. Al pasar a una API oficial
(Unipile), se cambia el trigger de Drive por un cron diario que llama a esa API
y se manda el mismo payload al mismo endpoint — el contrato no cambia.

---

## 6. Multiusuario

Nada de lo que es "de Enric" está en el código: el rol, el contexto de negocio,
el ICP, el tono, los pesos de puntuación, el umbral, los objetivos del router y
el teléfono de aviso son campos del **perfil de LinkedIn**. Un cliente instala
el módulo, crea su perfil, escribe su contexto y tiene su propio criterio.

Cada usuario ve solo lo suyo (perfiles, señales, publicaciones, lotes y
diagnósticos), salvo quien esté en el grupo *Marketing Campaigns / Responsable*.
Cada perfil tiene su token y puede apuntar a una instancia de n8n distinta.
