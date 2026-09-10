/** @odoo-module **/

import { Component } from "@odoo/owl";

export class ConversationList extends Component {
    static template = "uniasser_whatsapp.ConversationList";
    static props = {
        conversations: Array,
        selectedConvKey: { type: [String, { value: null }], optional: true },
        selectedPartnerId: { type: [Number, { value: null }], optional: true },
        loading: Boolean,
        onSelect: Function,
        onSearch: Function,
        onNewMessage: Function,
    };

    onSearchInput(ev) {
        this.props.onSearch(ev.target.value);
    }

    /**
     * Formatea un ISO datetime string a formato legible tipo WhatsApp:
     * - Hoy: HH:MM
     * - Ayer: "Ayer"
     * - Esta semana: nombre del día abreviado
     * - Más antiguo: DD/MM
     */
    formatTime(isoString) {
        if (!isoString) {
            return "";
        }
        const date = new Date(isoString);
        if (isNaN(date.getTime())) {
            return "";
        }
        const now = new Date();
        const diffMs = now - date;
        const diffDays = Math.floor(diffMs / 86400000);

        if (diffDays === 0) {
            return date.toLocaleTimeString("es-ES", {
                hour: "2-digit",
                minute: "2-digit",
            });
        } else if (diffDays === 1) {
            return "Ayer";
        } else if (diffDays < 7) {
            return date.toLocaleDateString("es-ES", { weekday: "short" });
        } else {
            return date.toLocaleDateString("es-ES", {
                day: "2-digit",
                month: "2-digit",
            });
        }
    }
}
