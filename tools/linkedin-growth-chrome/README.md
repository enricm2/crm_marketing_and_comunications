# LinkedIn Growth — extensión de Chrome

Añade un botón **Importar a LinkedIn Growth** en tus publicaciones de LinkedIn.
Al pulsarlo recoge quién ha reaccionado y comentado —con su **URL de perfil**,
que es lo que el copiar y pegar no da— y lo manda a Odoo.

## Qué hace y qué no

- Lee **solo lo que ya está en tu pantalla**, y solo cuando pulsas el botón.
- No consulta a LinkedIn por su cuenta, no recorre perfiles, no hace nada en
  segundo plano y no programa tareas.
- Despliega la lista de reacciones a ritmo humano, con pausas, como harías tú.

Extraer datos de LinkedIn de forma automatizada va contra sus condiciones de
uso, aunque sea desde tu propio navegador. Esta extensión automatiza el copiar
y pegar que ya harías a mano; no obtiene nada que no puedas ver. La decisión de
usarla es tuya.

## Instalación

1. `chrome://extensions` → activa **Modo de desarrollador**.
2. **Cargar descomprimida** → elige esta carpeta.
3. Pulsa el icono de la extensión → **Opciones**.
4. Rellena la URL de tu Odoo y el token de tu perfil de LinkedIn
   (Odoo → LinkedIn Growth → Configuración → Perfiles → pestaña
   *Integración con n8n*, con botón de copiar).
5. **Probar conexión** debe responder con el nombre de tu perfil.

## Uso

1. Abre una publicación tuya.
2. **Abre tú la lista de reacciones** — pulsa donde pone «N reacciones».
   Espera a que aparezcan los nombres.
3. Pulsa **Importar a LinkedIn Growth**.

La extensión lee la lista que ya tienes abierta, la despliega hasta el final y
la manda a Odoo junto con los comentarios de la publicación.

### Por qué no abre la lista sola

Lo hacía, y estaba mal. En español el botón de reaccionar lleva
`aria-label="Reaccionar Recomendar"`, que se parece demasiado al contador de
reacciones: la extensión acabó dando "me gusta" a la publicación del propio
usuario. Y aun acertando, el clic dispara la navegación interna de LinkedIn y
deja la lista a medio cargar.

Ahora el único clic que da la extensión es «Cargar más» **dentro de la lista
que tú has abierto**, para paginar lo que se está leyendo. Nada más.

## Si deja de funcionar

LinkedIn cambia su HTML cada pocos meses y ofusca los nombres de clase. Esta
extensión se ancla en lo más estable que hay —los enlaces `/in/` y los
`urn:li:activity:` de la propia página— pero aun así puede romperse.

Cuando pase, abre la consola del navegador (F12) en la publicación: los
mensajes van con el prefijo `[LinkedIn Growth]` y dicen en qué paso falló.
