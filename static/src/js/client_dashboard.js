/** @odoo-module **/
/**
 * Fawtara Client Dashboard
 * Client-scoped view of their own invoice usage and billing
 */

import { registry }   from "@web/core/registry";
import { Component, onWillStart, useState, onMounted } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { loadJS }     from "@web/core/assets";

const JSON_FIELDS = [
    "invoices_by_day",
    "invoices_by_type",
    "clearance_trend",
    "recent_invoices",
];

const NUMERIC_FIELDS = [
    "invoices_this_month",
    "cleared_this_month",
    "failed_this_month",
    "queued_count",
    "clearance_rate",
    "vat_this_month",
    "quota_invoices",
    "invoices_used",
    "invoices_remaining",
    "usage_percent",
    "overage_invoices",
    "overage_amount_omr",
    "overage_rate_bzs",
];

const STRING_FIELDS = ["plan_name"];

function safeParse(val) {
    if (Array.isArray(val) || (val && typeof val === "object")) return val;
    try { return JSON.parse(val || "[]"); } catch { return []; }
}

class FawtaraClientDashboard extends Component {
    setup() {
        this.orm    = useService("orm");
        this.action = useService("action");

        this.state = useState({
            ...Object.fromEntries(NUMERIC_FIELDS.map(f => [f, 0])),
            ...Object.fromEntries(JSON_FIELDS.map(f => [f, []])),
            ...Object.fromEntries(STRING_FIELDS.map(f => [f, ""])),
            loading:      true,
            dashboardId:  null,
            dateFrom:     null,
            dateTo:       null,
            quickFilters: {
                today:     false,
                thisWeek:  false,
                thisMonth: false,
                lastMonth: false,
                thisYear:  false,
            },
        });

        onWillStart(async () => {
            await loadJS("/web/static/lib/Chart/Chart.js");
            await this.loadData();
        });

        onMounted(() => {
            if (!this.state.loading) this.renderCharts();
        });
    }

    _allFields() {
        return [...NUMERIC_FIELDS, ...JSON_FIELDS, ...STRING_FIELDS];
    }

    _applyData(record) {
        for (const f of NUMERIC_FIELDS) {
            if (f in record) this.state[f] = record[f] || 0;
        }
        for (const f of JSON_FIELDS) {
            if (f in record) this.state[f] = safeParse(record[f]);
        }
        for (const f of STRING_FIELDS) {
            if (f in record) this.state[f] = record[f] || "";
        }
    }

    async loadData() {
        try {
            const ids = await this.orm.call(
                "fawtara.client.dashboard", "search", [[]]);
            const id = ids.length
                ? ids[0]
                : await this.orm.call(
                    "fawtara.client.dashboard", "create", [{}]);
            this.state.dashboardId = id;
            await this._readData(id);
            this.state.loading = false;
            setTimeout(() => this.renderCharts(), 80);
        } catch (err) {
            console.error("Client Dashboard Error", err);
            this.state.loading = false;
        }
    }

    async _readData(id) {
        const data = await this.orm.call(
            "fawtara.client.dashboard", "read",
            [[id], this._allFields()]);
        if (data && data.length) this._applyData(data[0]);
    }

    async applyDateFilter() {
        if (!this.state.dashboardId) return;
        this.state.loading = true;
        try {
            await this.orm.call(
                "fawtara.client.dashboard", "write",
                [[this.state.dashboardId], {
                    date_from: this.state.dateFrom || false,
                    date_to:   this.state.dateTo   || false,
                }]);
            await this._readData(this.state.dashboardId);
            this.state.loading = false;
            setTimeout(() => this.renderCharts(), 80);
        } catch (err) {
            this.state.loading = false;
        }
    }

    async clearDateFilter() {
        this.state.dateFrom = null;
        this.state.dateTo   = null;
        this.resetQuickFilters();
        await this.applyDateFilter();
    }

    setQuickFilter(filter) {
        this.resetQuickFilters();
        this.state.quickFilters[filter] = true;
        const today = new Date();
        let df, dt;
        switch (filter) {
            case "today":
                df = dt = this._fmt(today); break;
            case "thisWeek": {
                const f = new Date(today);
                f.setDate(today.getDate() - today.getDay());
                const l = new Date(today);
                l.setDate(today.getDate() - today.getDay() + 6);
                df = this._fmt(f); dt = this._fmt(l); break;
            }
            case "thisMonth":
                df = this._fmt(new Date(
                    today.getFullYear(), today.getMonth(), 1));
                dt = this._fmt(new Date(
                    today.getFullYear(), today.getMonth() + 1, 0));
                break;
            case "lastMonth":
                df = this._fmt(new Date(
                    today.getFullYear(), today.getMonth() - 1, 1));
                dt = this._fmt(new Date(
                    today.getFullYear(), today.getMonth(), 0));
                break;
            case "thisYear":
                df = this._fmt(new Date(today.getFullYear(), 0, 1));
                dt = this._fmt(new Date(today.getFullYear(), 11, 31));
                break;
        }
        this.state.dateFrom = df;
        this.state.dateTo   = dt;
        this.applyDateFilter();
    }

    resetQuickFilters() {
        Object.keys(this.state.quickFilters).forEach(
            k => this.state.quickFilters[k] = false);
    }

    _fmt(d) {
        return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
    }

    onDateFromChange(ev) {
        this.state.dateFrom = ev.target.value;
        this.resetQuickFilters();
    }
    onDateToChange(ev) {
        this.state.dateTo = ev.target.value;
        this.resetQuickFilters();
    }

    _chart(id, type, data, options) {
        const canvas = document.getElementById(id);
        if (!canvas) return;
        const ex = Chart.getChart(canvas);
        if (ex) ex.destroy();
        new Chart(canvas, { type, data, options });
    }

    renderCharts() {
        if (this.state.loading) return;
        try {
            // Invoices by day
            const d1 = this.state.invoices_by_day;
            this._chart("cd-chart-day", "line", {
                labels: d1.map(x => x.day),
                datasets: [{
                    label: "Invoices",
                    data: d1.map(x => x.count),
                    borderColor: "#1d4ed8",
                    backgroundColor: "rgba(29,78,216,0.12)",
                    tension: 0.4, fill: true,
                    pointBackgroundColor: "#1d4ed8",
                }],
            }, {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } },
            });

            // By type
            const d2 = this.state.invoices_by_type;
            this._chart("cd-chart-type", "doughnut", {
                labels: d2.map(x => x.label),
                datasets: [{
                    data: d2.map(x => x.count),
                    backgroundColor: [
                        "#1d4ed8","#10b981","#f59e0b","#8b5cf6"],
                }],
            }, {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { position: "bottom" } },
            });

            // Clearance trend
            const d3 = this.state.clearance_trend;
            this._chart("cd-chart-trend", "line", {
                labels: d3.map(x => x.month),
                datasets: [
                    {
                        label: "Cleared",
                        data: d3.map(x => x.cleared),
                        borderColor: "#10b981",
                        backgroundColor: "rgba(16,185,129,0.12)",
                        tension: 0.4, fill: true,
                    },
                    {
                        label: "Failed",
                        data: d3.map(x => x.failed),
                        borderColor: "#ef4444",
                        backgroundColor: "rgba(239,68,68,0.10)",
                        tension: 0.4, fill: true,
                    },
                ],
            }, {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { position: "top" } },
            });
        } catch (e) { console.error("Chart error", e); }
    }

    usageBarColor() {
        const p = this.state.usage_percent;
        if (p >= 100) return "#ef4444";
        if (p >= 80)  return "#f59e0b";
        return "#1d4ed8";
    }

    usageBarWidth() {
        return Math.min(this.state.usage_percent, 100) + "%";
    }

    openInvoices(domain = []) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "My Invoices",
            res_model: "fawtara.portal.invoice",
            views: [[false, "list"], [false, "form"]],
            domain,
        });
    }

    newInvoice() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "New Invoice",
            res_model: "fawtara.portal.invoice",
            views: [[false, "form"]],
            context: { default_state: "draft" },
        });
    }

    statusColor(status) {
        const map = {
            CLEARED: "#10b981",
            FAILED: "#ef4444",
            VALIDATION_FAILED: "#ef4444",
            QUEUED: "#f59e0b",
            PROCESSING: "#3b82f6",
        };
        return map[status] || "#94a3b8";
    }

    fmt(val) {
        if (!val) return "0.000";
        return val.toFixed(3);
    }
}

FawtaraClientDashboard.template = "fawtara_portal.ClientDashboard";
registry.category("actions").add(
    "fawtara_client_dashboard", FawtaraClientDashboard);