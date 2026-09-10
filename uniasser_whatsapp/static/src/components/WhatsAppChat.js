/** @odoo-module **/

import { Component, useState, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { ConversationList } from "./WhatsAppConversationList";
import { MessageThread } from "./WhatsAppMessageThread";
import { WhatsAppComposer } from "./WhatsAppComposer";
import { NewMessageModal } from "./WhatsAppNewMessage";

export class WhatsAppChat extends Component {
    static template = "uniasser_whatsapp.WhatsAppChat";
    static components = { ConversationList, MessageThread, WhatsAppComposer, NewMessageModal };

    setup() {
        this.rpc = rpc;
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            accounts: [],
            selectedAccountId: null,
            conversations: [],
            loadingConversations: false,
            // conversación abierta
            selectedConvKey: null,       // 'p-{partnerId}' o 'g-{groupId}'
            selectedPartnerId: null,
            selectedGroupId: null,
            selectedPartnerName: "",
            selectedPartnerPhone: "",
            selectedPartnerEmail: "",
            selectedSecondaryInfo: "",  // teléfono o group JID
            selectedIsGroup: false,
            selectedLeadId: null,
            selectedLeadName: "",
            pendingPhone: "",
            // mensajes
            messages: [],
            loadingMessages: false,
            sending: false,
            // búsqueda
            searchQuery: "",
            // modal
            showNewMessage: false,
            // panel info contacto
            showContactInfo: false,
        });

        this._pollInterval = null;
        this._lastMessageId = 0;

        onMounted(async () => {
            await this.loadAccounts();
            await this.loadConversations();
            this._pollInterval = setInterval(() => this.pollNewMessages(), 5000);
        });

        onWillUnmount(() => {
            if (this._pollInterval) {
                clearInterval(this._pollInterval);
            }
        });
    }

    // ── Carga inicial ──────────────────────────────────────────────────────

    async loadAccounts() {
        try {
            const accounts = await this.rpc("/whatsapp/api/accounts", {});
            this.state.accounts = accounts;
            // No pre-seleccionar ninguna — mostrar conversaciones de todas
        } catch (e) {
            console.error("WhatsApp: error cargando cuentas", e);
        }
    }

    async loadConversations() {
        this.state.loadingConversations = true;
        try {
            const result = await this.rpc("/whatsapp/api/conversations", {
                account_id: this.state.selectedAccountId,
                search: this.state.searchQuery,
            });
            this.state.conversations = result.conversations || [];
        } catch (e) {
            console.error("WhatsApp: error cargando conversaciones", e);
        } finally {
            this.state.loadingConversations = false;
        }
    }

    // ── Selección de conversación ──────────────────────────────────────────

    async selectConversation(conv) {
        this.state.selectedConvKey = conv.conv_key || (conv.partner_id ? `p-${conv.partner_id}` : null);
        this.state.selectedPartnerId = conv.partner_id || null;
        this.state.selectedGroupId = conv.group_id || null;
        this.state.selectedIsGroup = !!conv.is_group;
        this.state.selectedPartnerName = conv.display_name || conv.partner_name || "";
        this.state.selectedPartnerPhone = conv.partner_phone || "";
        this.state.selectedSecondaryInfo = conv.secondary_info || conv.partner_phone || "";
        this.state.selectedPartnerEmail = "";
        this.state.selectedLeadId = conv.lead_id || null;
        this.state.selectedLeadName = conv.lead_name || "";
        this.state.pendingPhone = "";
        this.state.showContactInfo = false;
        this._lastMessageId = 0;
        await this.loadMessages();
        if (conv.partner_id) {
            this._loadPartnerEmail(conv.partner_id);
        }
    }

    async _loadPartnerEmail(partnerId) {
        try {
            const res = await this.rpc("/web/dataset/call_kw", {
                model: "res.partner",
                method: "read",
                args: [[partnerId], ["email"]],
                kwargs: {},
            });
            if (res && res[0]) {
                this.state.selectedPartnerEmail = res[0].email || "";
            }
        } catch (e) {
            // silencioso
        }
    }

    async loadMessages() {
        if (!this.state.selectedPartnerId && !this.state.selectedGroupId) return;
        this.state.loadingMessages = true;
        try {
            const result = await this.rpc("/whatsapp/api/messages", {
                partner_id: this.state.selectedPartnerId,
                group_id: this.state.selectedGroupId,
                account_id: this.state.selectedAccountId,
                limit: 50,
            });
            this.state.messages = result.messages || [];
            if (result.messages && result.messages.length > 0) {
                this._lastMessageId = result.messages[result.messages.length - 1].id;
            }
            if (this.state.selectedPartnerId) {
                this._updateConvUnread(this.state.selectedPartnerId, 0);
            }
        } catch (e) {
            console.error("WhatsApp: error cargando mensajes", e);
        } finally {
            this.state.loadingMessages = false;
        }
    }

    // ── Polling ───────────────────────────────────────────────────────────

    async pollNewMessages() {
        if (!this.state.selectedPartnerId && !this.state.selectedGroupId) {
            await this.loadConversations();
            return;
        }
        try {
            const newMsgs = await this.rpc("/whatsapp/api/poll", {
                partner_id: this.state.selectedPartnerId,
                group_id: this.state.selectedGroupId,
                after_id: this._lastMessageId,
                account_id: this.state.selectedAccountId,
            });
            if (newMsgs && newMsgs.length > 0) {
                this.state.messages = [...this.state.messages, ...newMsgs];
                this._lastMessageId = newMsgs[newMsgs.length - 1].id;
                await this.loadConversations();
            }
        } catch (e) {
            // silencioso — errores temporales de red
        }
    }

    // ── Envío ─────────────────────────────────────────────────────────────

    async sendMessage(msg) {
        // msg puede ser string (texto) u objeto {type, body, name, languageCode}
        const isTemplate = msg && typeof msg === "object" && msg.type === "template";
        const body = isTemplate ? "" : (typeof msg === "string" ? msg : (msg.body || ""));

        if (!isTemplate && !body.trim()) return;
        const hasTarget = this.state.selectedPartnerId || this.state.pendingPhone;
        if (!hasTarget) return;

        this.state.sending = true;
        try {
            const payload = {
                body: body.trim(),
                account_id: this.state.selectedAccountId,
                message_type: isTemplate ? "template" : "text",
            };
            if (isTemplate) {
                payload.template_name = msg.name;
                payload.language_code = msg.languageCode || "es";
            }
            if (this.state.selectedPartnerId) {
                payload.partner_id = this.state.selectedPartnerId;
            } else {
                payload.phone_direct = this.state.pendingPhone;
            }

            const result = await this.rpc("/whatsapp/api/send", payload);

            if (result.error) {
                this.notification.add(result.error, { type: "danger" });
            } else {
                this.state.messages = [...this.state.messages, result];
                this._lastMessageId = result.id;
                // Si se creó partner automáticamente, guardar su id
                if (result.partner_id && !this.state.selectedPartnerId) {
                    this.state.selectedPartnerId = result.partner_id;
                    this.state.pendingPhone = "";
                }
                await this.loadConversations();
            }
        } catch (e) {
            this.notification.add("Error al enviar el mensaje.", { type: "danger" });
        } finally {
            this.state.sending = false;
        }
    }

    // ── Nuevo mensaje ─────────────────────────────────────────────────────

    openNewMessage() {
        this.state.showNewMessage = true;
    }

    closeNewMessage() {
        this.state.showNewMessage = false;
    }

    async startNewConversation(partnerId, phone, name) {
        this.state.showNewMessage = false;

        // Si ya hay conversación con este partner, abrirla
        if (partnerId) {
            const existing = this.state.conversations.find(
                (c) => c.partner_id === partnerId
            );
            if (existing) {
                await this.selectConversation(existing);
                return;
            }
        }

        // Nueva conversación — pueden llegar mensajes pero aún no hay historial
        this.state.selectedPartnerId = partnerId || null;
        this.state.selectedPartnerName = name || phone;
        this.state.selectedPartnerPhone = phone || "";
        this.state.selectedPartnerEmail = "";
        this.state.selectedLeadId = null;
        this.state.selectedLeadName = "";
        this.state.messages = [];
        this.state.pendingPhone = partnerId ? "" : phone;
        this.state.showContactInfo = false;
        this._lastMessageId = 0;

        if (partnerId) {
            await this.loadMessages();
        }
    }

    // ── Controles ─────────────────────────────────────────────────────────

    onSearch(query) {
        this.state.searchQuery = query;
        this.loadConversations();
    }

    onAccountChange(ev) {
        this.state.selectedAccountId = parseInt(ev.target.value, 10) || null;
        this.state.selectedPartnerId = null;
        this.state.pendingPhone = "";
        this.state.messages = [];
        this.loadConversations();
    }

    toggleContactInfo() {
        this.state.showContactInfo = !this.state.showContactInfo;
    }

    openPartner() {
        if (!this.state.selectedPartnerId) return;
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "res.partner",
            res_id: this.state.selectedPartnerId,
            views: [[false, "form"]],
        });
    }

    openLead() {
        if (!this.state.selectedLeadId) return;
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "crm.lead",
            res_id: this.state.selectedLeadId,
            views: [[false, "form"]],
        });
    }

    _updateConvUnread(partnerId, count) {
        const conv = this.state.conversations.find((c) => c.partner_id === partnerId);
        if (conv) {
            conv.unread_count = count;
        }
    }
}

registry.category("actions").add("uniasser_whatsapp.action_whatsapp_chat", WhatsAppChat);
