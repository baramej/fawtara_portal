from datetime import date

from odoo import models, fields, api
from odoo.exceptions import UserError


class FawtaraInvoicePortal(models.Model):
    """Client-portal actions on the canonical invoice ledger, so invoices
    that arrived by API can be downloaded and reversed like portal ones."""
    _inherit = 'fawtara.invoice'

    portal_invoice_id = fields.Many2one(
        'fawtara.portal.invoice', string='Portal Submission',
        compute='_compute_portal_invoice_id')
    reversal_count = fields.Integer(
        string='Credit Notes', compute='_compute_reversal_count')

    def _compute_portal_invoice_id(self):
        Portal = self.env['fawtara.portal.invoice']
        for rec in self:
            rec.portal_invoice_id = Portal.search(
                [('invoice_uuid', '=', rec.invoice_uuid)], limit=1
            ) if rec.invoice_uuid else Portal

    def _reversal_domain(self):
        self.ensure_one()
        refs = [u for u in (self.invoice_uuid, self.invoice_uuid_v5) if u]
        return [('preceding_invoice_uuid', 'in', refs)] if refs else [('id', '=', 0)]

    def _compute_reversal_count(self):
        for rec in self:
            rec.reversal_count = self.search_count(rec._reversal_domain())

    # ── Downloads (proxied through the portal controller) ─────────
    def action_download_pdf(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'target': 'new',
                'url': f'/fawtara/invoice/{self.invoice_uuid}/pdf'}

    def action_download_qr(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'target': 'new',
                'url': f'/fawtara/invoice/{self.invoice_uuid}/qr'}

    def action_open_portal_invoice(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window',
                'res_model': 'fawtara.portal.invoice',
                'res_id': self.portal_invoice_id.id,
                'view_mode': 'form'}

    def action_view_reversals(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window',
                'name': f'Credit Notes for {self.invoice_number}',
                'res_model': 'fawtara.invoice',
                'view_mode': 'list,form',
                'domain': self._reversal_domain()}

    # ── Reversal ──────────────────────────────────────────────────
    def action_create_credit_note(self):
        """Draft a credit note in the portal from any cleared invoice,
        including ones that were submitted directly through the API."""
        self.ensure_one()
        if self.status != 'CLEARED':
            raise UserError('Only cleared invoices can be reversed.')
        if self.invoice_type_code in ('381', '261'):
            raise UserError('Credit notes cannot be reversed.')
        if self.portal_invoice_id:
            return self.portal_invoice_id.action_reverse()
        if not self.fawtara_client_id:
            raise UserError('This invoice is not linked to a Fawtara client.')

        cn_type = '261' if self.invoice_type_code == '389' else '381'
        cn_number = f'CN-{self.invoice_number}'

        line_fields = self.env['fawtara.portal.invoice.line']._fields
        unit_codes = set(line_fields['unit_code'].get_values(self.env))
        vat_cats = set(line_fields['vat_category_code'].get_values(self.env))

        lines = [(0, 0, {
            'item_name': l.item_name or 'Item',
            'quantity': l.quantity,
            'unit_code': l.unit_code if l.unit_code in unit_codes else 'EA',
            'unit_price': l.unit_price,
            'vat_category_code': l.vat_category_code if l.vat_category_code in vat_cats else 'S',
            'vat_rate': l.vat_rate,
            'service_type_code': l.service_type_code,
            'isic_code': l.isic_code,
        }) for l in self.line_ids]
        if not lines:
            raise UserError('This invoice has no lines in Odoo yet — use '
                            '"Sync from Fawtara" or wait for the next webhook.')

        cn = self.env['fawtara.portal.invoice'].create({
            'client_id': self.fawtara_client_id.id,
            'environment': self.environment or 'PRODUCTION',
            'invoice_number': cn_number,
            'invoice_type_code': cn_type,
            'transaction_type': self.transaction_type or 'B2B',
            'issue_date': date.today(),
            'issue_time': '09:00:00',
            'currency_code': self.currency_code or 'OMR',
            'invoice_note': f'Credit note for invoice {self.invoice_number}',
            'contract_reference': self.contract_reference,
            'seller_name': self.seller_name,
            'seller_vatin': self.seller_vatin,
            'seller_address_line1': self.seller_address_line1 or 'Muscat',
            'seller_address_line2': self.seller_address_line2,
            'seller_city': self.seller_city or 'Muscat',
            'seller_post_code': self.seller_post_code or '100',
            'seller_country_code': self.seller_country_code or 'OM',
            'seller_contact_name': self.seller_contact_name,
            'seller_contact_phone': self.seller_contact_phone,
            'seller_contact_email': self.seller_contact_email,
            'buyer_name': self.buyer_name,
            'buyer_vatin': self.buyer_vatin,
            'buyer_address_line1': self.buyer_address_line1 or self.buyer_name,
            'buyer_city': self.buyer_city or 'Muscat',
            'buyer_post_code': self.buyer_post_code or '100',
            'buyer_country_code': self.buyer_country_code or 'OM',
            'preceding_invoice_number': self.invoice_number,
            'preceding_invoice_date': self.issue_date,
            # BTOM: must be the v5 UUID of the original, not the v4 transport UUID
            'preceding_invoice_uuid': self.invoice_uuid_v5 or self.invoice_uuid,
            'credit_note_reason': 'OTHER',
            'line_ids': lines,
            'state': 'draft',
        })
        return {'type': 'ir.actions.act_window',
                'name': f'Credit Note — {cn_number}',
                'res_model': 'fawtara.portal.invoice',
                'res_id': cn.id, 'view_mode': 'form', 'target': 'current'}
