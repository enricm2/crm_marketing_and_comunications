/** @odoo-module **/

import { Component, useRef, onPatched } from "@odoo/owl";

export class MessageThread extends Component {
    static template = "uniasser_whatsapp.MessageThread";
    static props = {
        messages: Array,
        loading: Boolean,
    };

    setup() {
        this.threadEl = useRef("threadEl");
        // Auto-scroll al fondo cuando llegan mensajes nuevos o se carga el thread
        onPatched(() => {
            const el = this.threadEl.el;
            if (el) {
                el.scrollTop = el.scrollHeight;
            }
        });
    }

    /**
     * Formatea un ISO datetime string a HH:MM para la burbuja del mensaje.
     */
    formatTime(isoString) {
        if (!isoString) {
            return "";
        }
        const date = new Date(isoString);
        if (isNaN(date.getTime())) {
            return "";
        }
        return date.toLocaleTimeString("es-ES", {
            hour: "2-digit",
            minute: "2-digit",
        });
    }

    /**
     * Devuelve el icono de estado del mensaje saliente.
     * pending → reloj, sent → check simple, delivered → doble check, read → doble check azul, failed → X.
     */
    statusIcon(status) {
        const icons = {
            pending: "🕐",
            sent: "✓",
            delivered: "✓✓",
            read: "✓✓",
            failed: "✗",
        };
        return icons[status] || "";
    }

    /**
     * Icono para tipos de mensaje no-texto.
     */
    mediaIcon(type) {
        const icons = {
            image: "🖼️",
            document: "📄",
            audio: "🎵",
            video: "🎥",
            location: "📍",
            sticker: "😊",
            template: "📋",
            reaction: "👍",
        };
        return icons[type] || "📎";
    }

    /**
     * Etiqueta textual para tipos de mensaje no-texto.
     */
    mediaLabel(type) {
        const labels = {
            image: " Imagen",
            document: " Documento",
            audio: " Audio",
            video: " Vídeo",
            location: " Ubicación",
            sticker: " Sticker",
            template: " Plantilla",
            reaction: " Reacción",
        };
        return labels[type] || " Archivo";
    }
}
