import hmac
import json
import logging

from odoo import http
from odoo.http import request

from ..models.fawtara_portal_invoice import portal_state_for

_logger = logging.getLogger(__name__)

WEBHOOK_SECRET = 'fawtara-webhook-secret-2026'


def _json(body, status=200):
    return request.make_response(
        json.dumps(body), status=status,
        headers={'Content-Type': 'application/json'})


class FawtaraWebhookController(http.Controller):

    @http.route(
        '/fawtara/webhook/invoice-status',
        type='http',
        auth='none',
        methods=['POST'],
        csrf=False)
    def invoice_status_webhook(self, **kwargs):
        """
        Receive an invoice push from Spring Boot.

        New payload (Spring Boot ≥ this change): top-level legacy keys plus
        an `invoice` object with the full snapshot. The fawtara.invoice
        ledger record is CREATED if it doesn't exist yet — this is what
        makes invoices submitted directly through the API (or Excel)
        visible in Odoo. Old payloads (no `invoice` key) still update
        existing records only.
        """
        try:
            webhook_key = request.httprequest.headers.get(
                'X-Fawtara-Webhook-Key', '')
            expected = request.env['ir.config_parameter'].sudo().get_param(
                'fawtara.webhook_key', WEBHOOK_SECRET)
            if not hmac.compare_digest(webhook_key or '', expected or ''):
                _logger.warning('Webhook received with invalid key — rejected')
                return _json({'error': 'Unauthorized'}, 401)

            data = json.loads(request.httprequest.get_data(as_text=True))
            snapshot = data.get('invoice') or {}
            invoice_uuid = data.get('invoiceUuid') or snapshot.get('invoiceUuid')
            status = data.get('status') or snapshot.get('status') or ''
            error_detail = data.get('errorDetail') or snapshot.get('errorDetail') or ''
            clearance_id = data.get('clearanceId') or snapshot.get('clearanceId') or ''

            if not invoice_uuid:
                return _json({'error': 'Missing invoiceUuid'}, 400)

            _logger.info('Webhook received — uuid: %s, status: %s, source: %s',
                         invoice_uuid, status, snapshot.get('source', '-'))

            env = request.env(su=True)

            # ── 1. Canonical ledger (all channels) ──────────────────
            Ledger = env['fawtara.invoice']
            if snapshot:
                snapshot.setdefault('invoiceUuid', invoice_uuid)
                ledger_id = Ledger.upsert_from_fawtara(snapshot)
            else:
                ledger = Ledger.search([('invoice_uuid', '=', invoice_uuid)], limit=1)
                if ledger:
                    Ledger.upsert_from_fawtara({
                        'invoiceUuid': invoice_uuid, 'status': status,
                        'errorDetail': error_detail, 'clearanceId': clearance_id,
                    })
                ledger_id = ledger.id or None

            # ── 2. Portal submission record (only if it came from the form) ─
            portal_inv = env['fawtara.portal.invoice'].search(
                [('invoice_uuid', '=', invoice_uuid)], limit=1)
            new_state = portal_state_for(status)
            if portal_inv and new_state:
                old_state = portal_inv.state
                vals = {'state': new_state,
                        'submission_error': error_detail or False}
                if clearance_id:
                    vals['clearance_id'] = clearance_id
                portal_inv.write(vals)

                if old_state != new_state:
                    if new_state == 'cleared':
                        portal_inv.message_post(
                            body=f"✅ <b>Invoice CLEARED by OTA</b><br/>"
                                 f"UUID: {invoice_uuid}<br/>"
                                 f"Clearance ID: {clearance_id or 'N/A'}")
                    elif new_state == 'failed':
                        portal_inv.message_post(
                            body=f"❌ <b>Invoice FAILED</b><br/>"
                                 f"Status: {status}<br/>"
                                 f"Reason: {error_detail or 'Unknown'}")
                    else:
                        portal_inv.message_post(
                            body=f"🔄 Status updated: {old_state} → {new_state}")

            return _json({'ok': True, 'uuid': invoice_uuid, 'odooId': ledger_id})

        except json.JSONDecodeError:
            request.env.cr.rollback()
            _logger.error('Webhook received invalid JSON')
            return _json({'error': 'Invalid JSON'}, 400)

        except Exception as e:  # noqa: BLE001
            request.env.cr.rollback()   # never commit a half-applied push
            _logger.error('Webhook processing error: %s', e, exc_info=True)
            return _json({'error': str(e)}, 500)
