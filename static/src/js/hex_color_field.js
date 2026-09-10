/** @odoo-module **/
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, xml } from "@odoo/owl";

class HexColorField extends Component {
    static template = xml`
        <div class="d-flex align-items-center gap-2">
            <input
                type="color"
                t-att-value="value"
                t-on-input="onColorInput"
                t-att-disabled="!props.record.isInEdition"
                style="width:40px; height:32px; padding:2px; border:1px solid #dee2e6;
                       border-radius:4px; cursor:pointer; background:none;"
            />
            <input
                type="text"
                t-att-value="value"
                t-on-change="onTextChange"
                t-att-readonly="!props.record.isInEdition"
                class="o_input"
                style="width:90px; font-family:monospace; font-size:13px;"
                placeholder="#000000"
            />
            <span t-attf-style="
                display:inline-block; width:24px; height:24px;
                border-radius:50%; border:1px solid #ccc;
                background-color:{{value}}; flex-shrink:0;"
            />
        </div>
    `;

    static props = {
        ...standardFieldProps,
    };

    get value() {
        return this.props.record.data[this.props.name] || "#000000";
    }

    onColorInput(ev) {
        this.props.record.update({ [this.props.name]: ev.target.value });
    }

    onTextChange(ev) {
        const val = ev.target.value.trim();
        if (/^#[0-9A-Fa-f]{3,6}$/.test(val)) {
            this.props.record.update({ [this.props.name]: val });
        }
    }
}

registry.category("fields").add("hex_color", {
    component: HexColorField,
    supportedTypes: ["char"],
    extractProps: () => ({}),
});
