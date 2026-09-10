"""Versión única de la Graph API de Meta para todo el módulo.

Antes había literales sueltos y contradictorios: v19.0 en whatsapp_group,
v20.0 en whatsapp_api y whatsapp_template, v21.0 en whatsapp_contact_profile.
Es decir, partes del mismo módulo hablaban con versiones distintas de la API
sin que nadie lo hubiera decidido.

── Por qué está FIJADA y no se resuelve "la última" ──
Meta introduce cambios incompatibles entre versiones. Saltar solo porque salga
una nueva significaría cambiar el comportamiento del envío de mensajes en
producción sin haberlo probado. Se sube a mano, se prueba y se despliega.

── Caducidad (esto ya nos ha mordido) ──
Cada versión vive un mínimo de 2 años. Cuando caduca, Meta NO devuelve error:
redirige la petición a la versión utilizable más antigua. O sea, falla en
silencio y acabas en una versión que no elegiste.

    v19.0 → CADUCADA el 21 may 2026  (whatsapp_group.py estuvo así)
    v21.0 → hasta el 21 ene 2027
    v23.0 → la que usamos; soporta también la Calling API
    v26.0 → la más nueva (29 jul 2026)

Revisar https://developers.facebook.com/docs/graph-api/changelog al menos una
vez al año y subir esta constante antes de que caduque.

Se puede sobrescribir sin tocar código con el parámetro de sistema
`uniasser_whatsapp.graph_version` (Ajustes → Técnico → Parámetros del sistema),
útil para probar una versión nueva sin desplegar.
"""

GRAPH_VERSION_DEFAULT = 'v23.0'

# Mismo valor por defecto que en wa-manager (backend/src/config/graph.js),
# para que ambos sistemas hablen con la misma versión de la API.
GRAPH_BASE_DEFAULT = f'https://graph.facebook.com/{GRAPH_VERSION_DEFAULT}'


def get_graph_version(env=None):
    """Versión activa. Con `env` consulta el parámetro de sistema; sin él,
    devuelve el valor por defecto (para código sin acceso al entorno)."""
    if env is not None:
        try:
            value = env['ir.config_parameter'].sudo().get_param(
                'uniasser_whatsapp.graph_version'
            )
            if value:
                return value.strip()
        except Exception:  # noqa: BLE001 - nunca romper por un parámetro
            pass
    return GRAPH_VERSION_DEFAULT


def get_graph_base(env=None):
    """URL base ya versionada, sin barra final."""
    return f'https://graph.facebook.com/{get_graph_version(env)}'
