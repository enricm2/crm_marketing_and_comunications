/** @odoo-module **/

import { Component, useState, useRef } from "@odoo/owl";
import { rpc } from "@web/core/network/rpc";

export class WhatsAppComposer extends Component {
    static template = "uniasser_whatsapp.WhatsAppComposer";
    static props = {
        onSend: Function,
        sending: Boolean,
        accountId: { type: [Number, { value: null }], optional: true },
    };

    setup() {
        this.inputEl = useRef("inputEl");
        this.state = useState({
            showTemplates: false,
            templates: [],
            loadingTemplates: false,
        });
    }

    async toggleTemplates() {
        this.state.showTemplates = !this.state.showTemplates;
        if (this.state.showTemplates && this.state.templates.length === 0) {
            this.state.loadingTemplates = true;
            try {
                this.state.templates = await rpc("/whatsapp/api/templates", {
                    account_id: this.props.accountId || null,
                });
            } catch (e) {
                this.state.templates = [];
            } finally {
                this.state.loadingTemplates = false;
            }
        }
    }

    selectTemplate(tpl) {
        this.state.showTemplates = false;
        this.props.onSend({ type: "template", name: tpl.name, body: tpl.body, languageCode: tpl.language_code });
    }

    /**
     * Enter sin Shift → enviar; Enter + Shift → nueva línea.
     * Auto-resize del textarea.
     */
    onKeyDown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.onSend();
        }
        // Auto-resize
        const el = ev.target;
        el.style.height = "auto";
        el.style.height = Math.min(el.scrollHeight, 120) + "px";
    }

    onSend() {
        const el = this.inputEl.el;
        if (!el || !el.value.trim()) {
            return;
        }
        this.props.onSend({ type: "text", body: el.value });
        el.value = "";
        el.style.height = "auto";
    }
}
