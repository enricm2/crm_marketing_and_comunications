/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { rpc } from "@web/core/network/rpc";

export class NewMessageModal extends Component {
    static template = "uniasser_whatsapp.NewMessageModal";
    static props = {
        onClose: Function,
        onSelect: Function,
    };

    setup() {
        this.state = useState({
            query: "",
            results: [],
            loading: false,
        });
        this._searchTimeout = null;
    }

    get isPhoneQuery() {
        const q = this.state.query.trim();
        return q.length >= 7 && /^[\d\s\+\-\(\)]+$/.test(q);
    }

    onOverlayClick(ev) {
        if (ev.target === ev.currentTarget) {
            this.props.onClose();
        }
    }

    onQueryChange() {
        clearTimeout(this._searchTimeout);
        const q = this.state.query.trim();
        if (q.length < 2) {
            this.state.results = [];
            return;
        }
        this.state.loading = true;
        this._searchTimeout = setTimeout(async () => {
            try {
                const results = await rpc("/whatsapp/api/search_partners", { query: q });
                this.state.results = results;
            } catch (e) {
                this.state.results = [];
            } finally {
                this.state.loading = false;
            }
        }, 300);
    }
}
