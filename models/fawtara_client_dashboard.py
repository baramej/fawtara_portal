import json
import logging
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class FawtaraClientDashboard(models.Model):
    _name = 'fawtara.client.dashboard'
    _description = 'Fawtara Client Dashboard'
    _check_company_auto = False

    name = fields.Char(default='My Dashboard')
    client_id = fields.Many2one(
        'fawtara.client', string='Client')
    date_from = fields.Date(string='Date From')
    date_to = fields.Date(string='Date To')

    # ── KPIs ──────────────────────────────────────────────────────
    invoices_this_month = fields.Integer(compute='_compute_kpis')
    cleared_this_month = fields.Integer(compute='_compute_kpis')
    failed_this_month = fields.Integer(compute='_compute_kpis')
    queued_count = fields.Integer(compute='_compute_kpis')
    clearance_rate = fields.Float(
        compute='_compute_kpis', digits=(5, 1))
    vat_this_month = fields.Float(
        compute='_compute_kpis', digits=(16, 3))

    # Billing
    plan_name = fields.Char(compute='_compute_billing')
    quota_invoices = fields.Integer(compute='_compute_billing')
    invoices_used = fields.Integer(compute='_compute_billing')
    invoices_remaining = fields.Integer(compute='_compute_billing')
    usage_percent = fields.Float(
        compute='_compute_billing', digits=(5, 1))
    overage_invoices = fields.Integer(compute='_compute_billing')
    overage_amount_omr = fields.Float(
        compute='_compute_billing', digits=(16, 3))
    overage_rate_bzs = fields.Float(compute='_compute_billing')

    # JSON chart fields
    invoices_by_day = fields.Text(compute='_compute_charts')
    invoices_by_type = fields.Text(compute='_compute_charts')
    clearance_trend = fields.Text(compute='_compute_charts')
    recent_invoices = fields.Text(compute='_compute_charts')

    def _get_client(self):
        """Get client record for the current user's context."""
        # Never trust a client_id stored on the (user-writable) dashboard
        # record — always derive it from the company the user works in.
        return self.env['fawtara.client']._get_current_client()

    def _date_domain(self, field='created_at'):
        domain = []
        if self.date_from:
            domain.append((field, '>=', datetime.combine(
                self.date_from, datetime.min.time())))
        if self.date_to:
            domain.append((field, '<=', datetime.combine(
                self.date_to, datetime.max.time())))
        return domain

    def _month_range(self, months=6):
        result = []
        today = datetime.today()
        for i in range(months - 1, -1, -1):
            m = today - relativedelta(months=i)
            start = m.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0)
            end = (start + relativedelta(months=1)) - timedelta(seconds=1)
            result.append((start, end, m.strftime('%b %Y')))
        return result

    def _compute_kpis(self):
        today = date.today()
        month_start = datetime.combine(
            today.replace(day=1), datetime.min.time())
        inv = self.env['fawtara.invoice'].sudo()

        for rec in self:
            client = rec._get_client()
            if not client:
                rec.invoices_this_month = 0
                rec.cleared_this_month = 0
                rec.failed_this_month = 0
                rec.queued_count = 0
                rec.clearance_rate = 0.0
                rec.vat_this_month = 0.0
                continue

            domain = rec._date_domain('created_at')
            if not domain:
                domain = [('created_at', '>=', month_start)]
            domain += [('fawtara_client_id', '=', client.id)]

            total = inv.search_count(domain)
            cleared = inv.search(
                domain + [('status', '=', 'CLEARED')])
            failed = inv.search_count(
                domain + [('status_group', '=', 'failed')])
            queued = inv.search_count([
                ('fawtara_client_id', '=', client.id),
                ('status_group', 'in', ['queued', 'processing']),
            ])

            rec.invoices_this_month = total
            rec.cleared_this_month = len(cleared)
            rec.failed_this_month = failed
            rec.queued_count = queued
            rec.clearance_rate = round(
                len(cleared) / total * 100, 1) if total else 0.0
            rec.vat_this_month = round(
                sum(cleared.mapped('total_tax')), 3)

    def _compute_billing(self):
        sub_model = self.env['fawtara.client.subscription'].sudo()
        for rec in self:
            client = rec._get_client()
            if not client:
                rec.plan_name = 'No Plan'
                rec.quota_invoices = 0
                rec.invoices_used = 0
                rec.invoices_remaining = 0
                rec.usage_percent = 0.0
                rec.overage_invoices = 0
                rec.overage_amount_omr = 0.0
                rec.overage_rate_bzs = 50.0
                continue

            sub = sub_model.search([
                ('client_id', '=', client.id),
                ('state', '=', 'active'),
            ], limit=1, order='start_date desc')

            if sub:
                rec.plan_name = sub.plan_id.name
                rec.quota_invoices = sub.quota_invoices
                rec.invoices_used = sub.invoices_used
                rec.invoices_remaining = sub.invoices_remaining
                rec.usage_percent = sub.usage_percent
                rec.overage_invoices = sub.overage_invoices
                rec.overage_amount_omr = sub.overage_amount_omr
                rec.overage_rate_bzs = sub.overage_rate_bzs
            else:
                rec.plan_name = 'No Plan'
                rec.quota_invoices = 0
                rec.invoices_used = 0
                rec.invoices_remaining = 0
                rec.usage_percent = 0.0
                rec.overage_invoices = 0
                rec.overage_amount_omr = 0.0
                rec.overage_rate_bzs = 50.0

    def _compute_charts(self):
        inv = self.env['fawtara.invoice'].sudo()
        today = date.today()

        for rec in self:
            client = rec._get_client()
            cid = client.id if client else None

            # Invoices by day (last 30 days)
            day_data = []
            for i in range(29, -1, -1):
                d = today - timedelta(days=i)
                d_start = datetime.combine(d, datetime.min.time())
                d_end = datetime.combine(d, datetime.max.time())
                count = inv.search_count([
                    ('fawtara_client_id', '=', cid),
                    ('created_at', '>=', d_start),
                    ('created_at', '<=', d_end),
                ]) if cid else 0
                day_data.append({
                    'day': d.strftime('%d %b'),
                    'count': count,
                })
            rec.invoices_by_day = json.dumps(day_data)

            # By type
            type_map = {
                '380': 'Tax Invoice',
                '381': 'Credit Note',
                '389': 'Self-Billing',
                '261': 'SB Credit Note',
            }
            month_start = datetime.combine(
                today.replace(day=1), datetime.min.time())
            type_data = []
            for code, label in type_map.items():
                count = inv.search_count([
                    ('fawtara_client_id', '=', cid),
                    ('created_at', '>=', month_start),
                    ('invoice_type_code', '=', code),
                ]) if cid else 0
                type_data.append({'label': label, 'count': count})
            rec.invoices_by_type = json.dumps(type_data)

            # Clearance trend (6 months)
            trend = []
            for start, end, label in rec._month_range(6):
                cleared = inv.search_count([
                    ('fawtara_client_id', '=', cid),
                    ('created_at', '>=', start),
                    ('created_at', '<=', end),
                    ('status', '=', 'CLEARED'),
                ]) if cid else 0
                failed = inv.search_count([
                    ('fawtara_client_id', '=', cid),
                    ('created_at', '>=', start),
                    ('created_at', '<=', end),
                    ('status_group', '=', 'failed'),
                ]) if cid else 0
                trend.append({
                    'month': label,
                    'cleared': cleared,
                    'failed': failed,
                })
            rec.clearance_trend = json.dumps(trend)

            # Recent invoices (last 10)
            recent = inv.search([
                ('fawtara_client_id', '=', cid),
            ], order='created_at desc', limit=10) if cid else inv.browse()
            recent_list = []
            for r in recent:
                recent_list.append({
                    'uuid':   r.invoice_uuid or '',
                    'number': r.invoice_number or '',
                    'type':   r.invoice_type_code or '',
                    'date':   str(r.issue_date or ''),
                    'total':  round(r.total_incl_tax or 0, 3),
                    'vat':    round(r.total_tax or 0, 3),
                    'status': r.status or '',
                })
            rec.recent_invoices = json.dumps(recent_list)