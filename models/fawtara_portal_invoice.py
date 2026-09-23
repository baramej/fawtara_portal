import logging

import psycopg2
import requests
from datetime import date
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


def portal_state_for(fawtara_status):
    """Spring Boot status → portal submission state (None if unknown/empty)."""
    if not fawtara_status:
        return None
    if fawtara_status in ('QUEUED', 'PROCESSING'):
        return 'queued'
    if fawtara_status == 'CLEARED':
        return 'cleared'
    return 'failed'   # FAILED, OTA_FAILED, SCHEMATRON_FAILED, REJECTED, ...


class PortalInvoice(models.Model):
    """
    Invoice submission form for clients.
    Clients fill this form and submit to Spring Boot via API.
    """
    _name = 'fawtara.portal.invoice'
    _description = 'Fawtara Portal Invoice Submission'
    _order = 'create_date desc'
    _check_company_auto = False
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(
        string='Invoice Number',
        compute='_compute_name',
        store=True)

    # ── Client ────────────────────────────────────────────────────
    client_id = fields.Many2one(
        'fawtara.client',
        string='Client',
        required=True,
        index=True,
        default=lambda self: self._default_client())
    company_id = fields.Many2one(
        'res.company', string='Company',
        related='client_id.company_id', store=True, index=True)

    environment = fields.Selection([
        ('PRODUCTION', '🔴 Production'),
        ('SANDBOX', '🟡 Sandbox'),
    ], string='Environment', default='PRODUCTION', required=True, tracking=True)

    api_key = fields.Char(
        string='API Key',
        compute='_compute_api_key')

    # ── Invoice Header ────────────────────────────────────────────
    invoice_number = fields.Char(
        string='Invoice Number', required=True, tracking=True)
    invoice_type_code = fields.Selection([
        ('380', '380 — Tax Invoice'),
        ('381', '381 — Credit Note'),
        ('389', '389 — Self-Billing Invoice'),
        ('261', '261 — Self-Billing Credit Note'),
    ], string='Invoice Type', required=True, default='380')
    transaction_type = fields.Selection([
        ('B2B', 'B2B — Business to Business'),
        ('B2C', 'B2C — Business to Consumer'),
        ('B2G', 'B2G — Business to Government'),
    ], string='Transaction Type', required=True, default='B2B')
    issue_date = fields.Date(
        string='Issue Date', required=True,
        default=fields.Date.today)
    issue_time = fields.Char(
        string='Issue Time', default='09:00:00',
        help='Format: HH:MM:SS')
    currency_code = fields.Selection([
        ('OMR', 'OMR — Omani Rial'),
        ('USD', 'USD — US Dollar'),
        ('EUR', 'EUR — Euro'),
        ('AED', 'AED — UAE Dirham'),
        ('SAR', 'SAR — Saudi Riyal'),
    ], string='Currency', required=True, default='OMR')
    invoice_note = fields.Text(string='Note')
    contract_reference = fields.Char(string='Contract Reference')
    purchase_order_reference = fields.Char(string='PO Reference')

    # ── Reversal link ─────────────────────────────────────────────
    reversed_invoice_id = fields.Many2one(
        'fawtara.portal.invoice',
        string='Reversal Of',
        readonly=True,
        help='The original invoice this credit note reverses')
    reversal_ids = fields.One2many(
        'fawtara.portal.invoice',
        'reversed_invoice_id',
        string='Credit Notes')
    reversal_count = fields.Integer(
        string='Credit Notes',
        compute='_compute_reversal_count')

    # ── Seller ────────────────────────────────────────────────────
    seller_name = fields.Char(
        string='Seller Name', required=True,
        compute='_compute_seller', store=True, readonly=False,
        precompute=True)
    seller_vatin = fields.Char(
        string='Seller VATIN', required=True,
        compute='_compute_seller', store=True, readonly=False,
        precompute=True)
    seller_address_line1 = fields.Char(
        string='Address Line 1', required=True)
    seller_address_line2 = fields.Char(string='Address Line 2')
    seller_address_line3 = fields.Char(string='Address Line 3')
    seller_city = fields.Char(
        string='City', required=True, default='Muscat')
    seller_post_code = fields.Char(
        string='Post Code', required=True)
    seller_country_code = fields.Char(
        string='Country', required=True, default='OM')
    seller_contact_name = fields.Char(string='Contact Name')
    seller_contact_phone = fields.Char(string='Contact Phone')
    seller_contact_email = fields.Char(string='Contact Email')

    # ── Buyer ─────────────────────────────────────────────────────
    buyer_name = fields.Char(string='Buyer Name', required=True)
    buyer_vatin = fields.Char(string='Buyer VATIN')
    buyer_address_line1 = fields.Char(
        string='Buyer Address Line 1', required=True)
    buyer_address_line2 = fields.Char(string='Buyer Address Line 2')
    buyer_address_line3 = fields.Char(string='Buyer Address Line 3')
    buyer_city = fields.Char(
        string='Buyer City', required=True, default='Muscat')
    buyer_post_code = fields.Char(
        string='Buyer Post Code', required=True)
    buyer_country_code = fields.Char(
        string='Buyer Country', required=True, default='OM')

    # ── Preceding Invoice (Credit Notes) ──────────────────────────
    preceding_invoice_number = fields.Char(
        string='Original Invoice Number')
    preceding_invoice_date = fields.Date(
        string='Original Invoice Date')
    preceding_invoice_uuid = fields.Char(
        string='Original Invoice UUID')
    credit_note_reason = fields.Selection([
        ('VAT', 'VAT Correction'),
        ('RETURN', 'Goods Return'),
        ('CANCEL', 'Cancellation'),
        ('OTHER', 'Other'),
    ], string='Credit Note Reason')

    # ── Lines ─────────────────────────────────────────────────────
    line_ids = fields.One2many(
        'fawtara.portal.invoice.line',
        'invoice_id',
        string='Invoice Lines')

    # ── Totals (computed) ─────────────────────────────────────────
    total_excl_tax = fields.Float(
        string='Total excl. VAT',
        compute='_compute_totals', store=True, digits=(16, 3))
    total_tax = fields.Float(
        string='VAT Amount',
        compute='_compute_totals', store=True, digits=(16, 3))
    total_incl_tax = fields.Float(
        string='Total incl. VAT',
        compute='_compute_totals', store=True, digits=(16, 3))

    # ── Submission Status ─────────────────────────────────────────
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('queued', 'Queued'),
        ('cleared', 'Cleared'),
        ('failed', 'Failed'),
    ], string='State', default='draft', tracking=True)

    invoice_uuid = fields.Char(
        string='Fawtara UUID', readonly=True, tracking=True)
    submission_error = fields.Text(
        string='Submission Error', readonly=True, tracking=True)

    qr_image_base64 = fields.Binary(
        string='QR Code',
        compute='_compute_qr_image',
        help='QR PNG fetched from Fawtara API')

    clearance_id = fields.Char(
        string='Clearance ID', readonly=True, tracking=True)

    # ── Defaults & Computes ───────────────────────────────────────

    def _default_client(self):
        # The client linked to the company the user is working in
        return self.env['fawtara.client']._get_current_client()

    @api.depends('invoice_number')
    def _compute_name(self):
        for rec in self:
            rec.name = rec.invoice_number or 'Draft'

    @api.depends('invoice_uuid', 'state')
    def _compute_qr_image(self):
        for rec in self:
            if rec.state != 'cleared' or not rec.invoice_uuid:
                rec.qr_image_base64 = False
                continue
            try:
                base_url, api_key = rec._api_target()

                import requests as req_lib
                import base64
                resp = req_lib.get(
                    f"{base_url}/api/v1/invoice/{rec.invoice_uuid}/qr",
                    headers={'X-API-Key': api_key},
                    timeout=10)

                if resp.status_code == 200:
                    rec.qr_image_base64 = base64.b64encode(
                        resp.content)
                else:
                    rec.qr_image_base64 = False
            except Exception as e:
                _logger.warning(
                    'QR fetch failed for %s: %s',
                    rec.invoice_uuid, str(e))
                rec.qr_image_base64 = False

    @api.depends('client_id')
    def _compute_seller(self):
        for rec in self:
            if rec.client_id:
                rec.seller_name = rec.client_id.name
                rec.seller_vatin = rec.client_id.vatin
                # Pre-fill address if empty
                if not rec.seller_address_line1:
                    rec.seller_address_line1 = 'Muscat'
                if not rec.seller_city:
                    rec.seller_city = 'Muscat'
                if not rec.seller_post_code:
                    rec.seller_post_code = '100'
                if not rec.seller_country_code:
                    rec.seller_country_code = 'OM'
            else:
                rec.seller_name = False
                rec.seller_vatin = False

    @api.depends('client_id', 'environment')
    def _compute_api_key(self):
        for rec in self:
            if not rec.client_id:
                rec.api_key = False
            elif rec.environment == 'SANDBOX':
                rec.api_key = rec.client_id.sudo().sandbox_api_key
            else:
                rec.api_key = rec.client_id.sudo().production_api_key

    @api.depends('line_ids.line_total', 'line_ids.vat_amount')
    def _compute_totals(self):
        for rec in self:
            rec.total_excl_tax = sum(rec.line_ids.mapped('line_total'))
            rec.total_tax = sum(rec.line_ids.mapped('vat_amount'))
            rec.total_incl_tax = rec.total_excl_tax + rec.total_tax

    @api.depends('reversal_ids')
    def _compute_reversal_count(self):
        for rec in self:
            rec.reversal_count = len(rec.reversal_ids)

    @api.constrains('invoice_type_code', 'preceding_invoice_number')
    def _check_credit_note_fields(self):
        for rec in self:
            if rec.invoice_type_code in ('381', '261'):
                if not rec.preceding_invoice_number:
                    raise ValidationError(
                        'Original Invoice Number is required for credit notes.')
                if not rec.preceding_invoice_uuid:
                    raise ValidationError(
                        'Original Invoice UUID is required for credit notes.')

    # ── Reversal Action ───────────────────────────────────────────

    def action_reverse(self):
        """
        Create a pre-filled credit note from a cleared invoice.
        Opens the new credit note form for review before submission.
        """
        self.ensure_one()

        if self.state != 'cleared':
            raise UserError(
                'Only cleared invoices can be reversed.')

        if self.invoice_type_code in ('381', '261'):
            raise UserError(
                'Credit notes cannot be reversed.')

        if self.reversal_count > 0:
            raise UserError(
                f'This invoice already has {self.reversal_count} '
                f'credit note(s). Open them from the Credit Notes button.')

        if not self.invoice_uuid:
            raise UserError(
                'Invoice UUID not found. Cannot create reversal.')

        # Determine credit note type
        # 380 → 381 (Credit Note)
        # 389 → 261 (Self-Billing Credit Note)
        cn_type = '261' if self.invoice_type_code == '389' else '381'

        # Generate credit note number
        cn_number = f"CN-{self.invoice_number}"

        # Copy lines with negative quantities for full reversal
        line_vals = []
        for line in self.line_ids:
            line_vals.append((0, 0, {
                'item_name': line.item_name,
                'quantity': line.quantity,  # client can adjust
                'unit_code': line.unit_code,
                'unit_price': line.unit_price,
                'vat_category_code': line.vat_category_code,
                'vat_rate': line.vat_rate,
                'service_type_code': line.service_type_code,
                'isic_code': line.isic_code,
            }))

        # Create the credit note in draft
        credit_note = self.create({
            'client_id': self.client_id.id,
            'invoice_number': cn_number,
            'invoice_type_code': cn_type,
            'transaction_type': self.transaction_type,
            'issue_date': date.today(),
            'issue_time': '09:00:00',
            'currency_code': self.currency_code,
            'invoice_note': f'Credit note for invoice {self.invoice_number}',
            'contract_reference': self.contract_reference,
            # Seller — same as original
            'seller_name': self.seller_name,
            'seller_vatin': self.seller_vatin,
            'seller_address_line1': self.seller_address_line1,
            'seller_address_line2': self.seller_address_line2,
            'seller_city': self.seller_city,
            'seller_post_code': self.seller_post_code,
            'seller_country_code': self.seller_country_code,
            'seller_contact_name': self.seller_contact_name,
            'seller_contact_phone': self.seller_contact_phone,
            'seller_contact_email': self.seller_contact_email,
            # Buyer — same as original
            'buyer_name': self.buyer_name,
            'buyer_vatin': self.buyer_vatin,
            'buyer_address_line1': self.buyer_address_line1,
            'buyer_city': self.buyer_city,
            'buyer_post_code': self.buyer_post_code,
            'buyer_country_code': self.buyer_country_code,
            # Preceding invoice — links back to original
            'preceding_invoice_number': self.invoice_number,
            'preceding_invoice_date': self.issue_date,
            # Must be the original's UUIDv5 (OTA rule), not the v4 transport UUID
            'preceding_invoice_uuid': self.env['fawtara.invoice'].sudo().search(
                [('invoice_uuid', '=', self.invoice_uuid)], limit=1
            ).invoice_uuid_v5 or self.invoice_uuid,
            'credit_note_reason': 'OTHER',
            # Reversal link
            'reversed_invoice_id': self.id,
            # Lines copied from original
            'line_ids': line_vals,
            'state': 'draft',
        })

        _logger.info(
            'Credit note created — original: %s, cn: %s, type: %s',
            self.invoice_number, cn_number, cn_type)

        # Open the new credit note for review
        return {
            'type': 'ir.actions.act_window',
            'name': f'Credit Note — {cn_number}',
            'res_model': 'fawtara.portal.invoice',
            'res_id': credit_note.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_reversals(self):
        """Open all credit notes linked to this invoice."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Credit Notes for {self.invoice_number}',
            'res_model': 'fawtara.portal.invoice',
            'view_mode': 'list,form',
            'domain': [('reversed_invoice_id', '=', self.id)],
        }

    # ── Submit Action ─────────────────────────────────────────────

    def _api_target(self):
        """(base_url, api_key) for this submission's environment."""
        self.ensure_one()
        config = self.env['ir.config_parameter'].sudo()
        client = self.client_id.sudo()
        if self.environment == 'SANDBOX':
            return (config.get_param('fawtara.sandbox_url', 'http://localhost:8081'),
                    client.sandbox_api_key)
        return (config.get_param('fawtara.api_url', 'http://localhost:8081'),
                client.production_api_key)

    def action_submit(self):
        self.ensure_one()

        if not self.line_ids:
            raise UserError('Add at least one invoice line before submitting.')

        base_url, api_key = self._api_target()
        if not api_key:
            raise UserError(
                f'No {self.environment.lower()} API key configured for this client.')

        payload = self._build_payload()

        try:
            resp = requests.post(
                f"{base_url}/api/v1/invoice",
                headers={
                    'X-API-Key': api_key,
                    'Content-Type': 'application/json',
                    'X-Fawtara-Source': 'PORTAL',
                },
                json=payload,
                timeout=30)

            if resp.status_code in (200, 202):
                data = resp.json()
                self.write({
                    'state': 'queued',
                    'invoice_uuid': data.get('invoiceUuid'),
                    'submission_error': False,
                })
                self._create_admin_record(data)
                env_label = '🟡 Sandbox' if self.environment == 'SANDBOX' else '🔴 Production'
                self.message_post(
                    body=f"✅ Invoice submitted to <b>{env_label}</b>.<br/>"
                         f"<b>UUID:</b> {data.get('invoiceUuid')}<br/>"
                         f"<b>Status:</b> QUEUED")
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': f'Submitted to {env_label}',
                        'message': f"UUID: {data.get('invoiceUuid')}",
                        'type': 'success',
                    }
                }
            else:
                error_msg = f"{resp.status_code}: {resp.text}"
                self.write({
                    'state': 'failed',
                    'submission_error': error_msg,
                })
                self.message_post(
                    body=f"❌ Submission failed.<br/><b>Error:</b> {error_msg}")
                raise UserError(f"Submission failed: {error_msg}")

        except requests.exceptions.RequestException as e:
            self.write({
                'state': 'failed',
                'submission_error': str(e),
            })
            self.message_post(
                body=f"❌ Cannot reach Fawtara API.<br/><b>Error:</b> {str(e)}")
            raise UserError(f"Cannot reach Fawtara API: {str(e)}")

    def action_download_pdf(self):
        self.ensure_one()
        if not self.invoice_uuid:
            raise UserError('No UUID — invoice not submitted.')
        return {
            'type': 'ir.actions.act_url',
            'url': f'/fawtara/invoice/{self.invoice_uuid}/pdf',
            'target': 'new',
        }

    def action_download_qr(self):
        self.ensure_one()
        if not self.invoice_uuid:
            raise UserError('No UUID — invoice not submitted.')
        return {
            'type': 'ir.actions.act_url',
            'url': f'/fawtara/invoice/{self.invoice_uuid}/qr',
            'target': 'new',
        }

    def action_refresh_status(self):
        self.ensure_one()
        if not self.invoice_uuid:
            raise UserError('Invoice not yet submitted.')

        base_url, api_key = self._api_target()

        try:
            resp = requests.get(
                f"{base_url}/api/v1/invoice/{self.invoice_uuid}",
                headers={'X-API-Key': api_key},
                timeout=15)

            if resp.status_code == 200:
                data = resp.json()
                old_state = self.state
                new_state = portal_state_for(data.get('status')) or self.state
                error = data.get('errorDetail')

                self.write({
                    'state': new_state,
                    'submission_error': error,
                    'clearance_id': data.get('clearanceId') or self.clearance_id,
                })

                if new_state != old_state:
                    if new_state == 'cleared':
                        self.message_post(
                            body=f"✅ Invoice CLEARED.<br/>"
                                 f"<b>UUID:</b> {self.invoice_uuid}")
                    elif new_state == 'failed':
                        self.message_post(
                            body=f"❌ Invoice FAILED.<br/>"
                                 f"<b>Reason:</b> {error or 'Unknown'}")
                    else:
                        self.message_post(
                            body=f"🔄 Status: {old_state} → {new_state}")
                else:
                    self.message_post(
                        body=f"🔄 Status refreshed: {new_state} (no change)")
            else:
                raise UserError(f"Status check failed: {resp.status_code}")

        except requests.exceptions.RequestException as e:
            raise UserError(f"Cannot reach Fawtara API: {str(e)}")

    def action_reset_draft(self):
        self.ensure_one()
        self.write({
            'state': 'draft',
            'invoice_uuid': False,
            'submission_error': False,
        })

    def _build_payload(self):
        lines = []
        for line in self.line_ids:
            lines.append({
                'itemName': line.item_name,
                'quantity': line.quantity,
                'unitCode': line.unit_code,
                'unitPrice': line.unit_price,
                'vatCategoryCode': line.vat_category_code,
                'vatRate': line.vat_rate,
                'serviceTypeCode': line.service_type_code or '72000000',
                'isicCode': line.isic_code or '620101',
            })

        payload = {
            'invoiceNumber': self.invoice_number,
            'invoiceTypeCode': self.invoice_type_code,
            'transactionType': self.transaction_type,
            'issueDate': str(self.issue_date),
            'issueTime': self.issue_time or '09:00:00',
            'currencyCode': self.currency_code,
            'invoiceNote': self.invoice_note or '',
            'contractReference': self.contract_reference or '',
            'sellerName': self.seller_name,
            'sellerVatin': self.seller_vatin,
            'sellerAddress': {
                'addressLine1': self.seller_address_line1 or '',
                'addressLine2': self.seller_address_line2 or '',
                'addressLine3': self.seller_address_line3 or '',
                'city': self.seller_city or 'Muscat',
                'postCode': self.seller_post_code or '',
                'countryCode': self.seller_country_code or 'OM',
            },
            'sellerContactName': self.seller_contact_name or '',
            'sellerContactPhone': self.seller_contact_phone or '',
            'sellerContactEmail': self.seller_contact_email or '',
            'buyerName': self.buyer_name,
            'buyerVatin': self.buyer_vatin or '',
            'buyerAddress': {
                'addressLine1': self.buyer_address_line1 or '',
                'addressLine2': self.buyer_address_line2 or '',
                'addressLine3': self.buyer_address_line3 or '',
                'city': self.buyer_city or 'Muscat',
                'postCode': self.buyer_post_code or '',
                'countryCode': self.buyer_country_code or 'OM',
            },
            'lines': lines,
        }

        if self.invoice_type_code in ('381', '261'):
            payload['precedingInvoice'] = {
                'precedingInvoiceNumber': self.preceding_invoice_number,
                'precedingInvoiceDate':
                    str(self.preceding_invoice_date or ''),
                'precedingInvoiceUuid':
                    self.preceding_invoice_uuid or '',
                'creditNoteReasonCode':
                    self.credit_note_reason or 'OTHER',
            }

        return payload

    def _create_admin_record(self, api_response):
        """Register the submission in the canonical fawtara.invoice ledger.

        Spring Boot's webhook will usually create the same record a moment
        later (or even first) — upsert_from_fawtara is idempotent on the UUID,
        so whichever arrives second just updates it.
        """
        payload = self._build_payload()
        payload.update({
            'invoiceUuid': api_response.get('invoiceUuid'),
            'clientId': self.client_id.sudo().client_id,
            'environment': self.environment,
            'source': 'PORTAL',
            'status': 'QUEUED',
            'totalAmountWithoutTax': self.total_excl_tax,
            'totalTaxAmount': self.total_tax,
            'totalAmountWithTax': self.total_incl_tax,
            'createdAt': fields.Datetime.to_string(fields.Datetime.now()),
        })
        payload.pop('lines', None)   # Spring Boot's webhook sends the computed lines
        try:
            with self.env.cr.savepoint():
                self.env['fawtara.invoice'].sudo().upsert_from_fawtara(payload)
        except psycopg2.IntegrityError:
            # Spring Boot's webhook created the same UUID in a parallel
            # transaction a split second earlier — that record wins.
            _logger.info('Ledger record for %s already created by webhook',
                         payload.get('invoiceUuid'))


class PortalInvoiceLine(models.Model):
    _name = 'fawtara.portal.invoice.line'
    _description = 'Portal Invoice Line'
    _check_company_auto = False

    invoice_id = fields.Many2one(
        'fawtara.portal.invoice',
        string='Invoice', ondelete='cascade')

    item_name = fields.Char(string='Item / Service', required=True)
    quantity = fields.Float(
        string='Quantity', required=True, default=1.0)
    unit_code = fields.Selection([
        ('HUR', 'Hours'),
        ('EA', 'Each'),
        ('KGM', 'Kilogram'),
        ('MTR', 'Meter'),
        ('LTR', 'Litre'),
        ('DAY', 'Day'),
        ('MON', 'Month'),
    ], string='Unit', required=True, default='EA')
    unit_price = fields.Float(
        string='Unit Price', required=True, digits=(16, 3))
    vat_category_code = fields.Selection([
        ('S', 'S — Standard (5%)'),
        ('Z', 'Z — Zero Rated'),
        ('E', 'E — Exempt'),
        ('O', 'O — Out of Scope'),
    ], string='VAT Category', required=True, default='S')
    vat_rate = fields.Float(string='VAT Rate %', default=5.0)
    service_type_code = fields.Char(
        string='Service Type Code', default='72000000')
    isic_code = fields.Char(
        string='ISIC Code', default='620101')

    line_total = fields.Float(
        string='Line Total (excl. VAT)',
        compute='_compute_line_total', store=True, digits=(16, 3))
    vat_amount = fields.Float(
        string='VAT Amount',
        compute='_compute_line_total', store=True, digits=(16, 3))
    line_total_incl = fields.Float(
        string='Line Total (incl. VAT)',
        compute='_compute_line_total', store=True, digits=(16, 3))

    @api.depends('quantity', 'unit_price', 'vat_rate', 'vat_category_code')
    def _compute_line_total(self):
        for line in self:
            line.line_total = line.quantity * line.unit_price
            rate = line.vat_rate if line.vat_category_code == 'S' else 0.0
            line.vat_amount = line.line_total * rate / 100
            line.line_total_incl = line.line_total + line.vat_amount
