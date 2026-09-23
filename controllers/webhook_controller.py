import json
import logging
from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)

WEBHOOK_SECRET = 'fawtara-webhook-secret-2026'


class FawtaraWebhookController(http.Controller):

    @http.route(
        '/fawtara/webhook/invoice-status',
        type='http',
        auth='none',
        methods=['POST'],
        csrf=False)
    def invoice_status_webhook(self, **kwargs):
        """
        Receive invoice status push from Spring Boot.
        Updates both fawtara.portal.invoice and fawtara.invoice records.
        """
        try:
            # Verify webhook secret
            webhook_key = request.httprequest.headers.get(
                'X-Fawtara-Webhook-Key', '')
            config = request.env[
                'ir.config_parameter'].sudo().get_param(
                'fawtara.webhook_key', WEBHOOK_SECRET)

            if webhook_key != config:
                _logger.warning(
                    'Webhook received with invalid key — rejected')
                return request.make_response(
                    json.dumps({'error': 'Unauthorized'}),
                    status=401,
                    headers={'Content-Type': 'application/json'})

            # Parse body
            body = request.httprequest.get_data(as_text=True)
            data = json.loads(body)

            invoice_uuid = data.get('invoiceUuid')
            status       = data.get('status', '')
            error_detail = data.get('errorDetail', '')
            clearance_id = data.get('clearanceId', '')

            if not invoice_uuid:
                return request.make_response(
                    json.dumps({'error': 'Missing invoiceUuid'}),
                    status=400,
                    headers={'Content-Type': 'application/json'})

            _logger.info(
                'Webhook received — uuid: %s, status: %s',
                invoice_uuid, status)

            # Map Spring Boot status to Odoo portal state
            status_map = {
                'QUEUED':            'queued',
                'PROCESSING':        'queued',
                'CLEARED':           'cleared',
                'FAILED':            'failed',
                'VALIDATION_FAILED': 'failed',
                'REJECTED':          'failed',
            }
            new_state = status_map.get(status, 'queued')

            # ── Update fawtara.portal.invoice ──────────────────────
            portal_inv = request.env[
                'fawtara.portal.invoice'].sudo().search(
                [('invoice_uuid', '=', invoice_uuid)], limit=1)

            if portal_inv:
                old_state = portal_inv.state
                portal_inv.write({
                    'state':            new_state,
                    'submission_error': error_detail or False,
                })

                # Log to chatter
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
                elif old_state != new_state:
                    portal_inv.message_post(
                        body=f"🔄 Status updated: "
                             f"{old_state} → {new_state}")

                _logger.info(
                    'Portal invoice updated — uuid: %s, '
                    'state: %s → %s',
                    invoice_uuid, old_state, new_state)
            else:
                _logger.debug(
                    'No portal invoice found for uuid: %s',
                    invoice_uuid)

            # ── Update fawtara.invoice (admin view) ────────────────
            admin_inv = request.env[
                'fawtara.invoice'].sudo().search(
                [('invoice_uuid', '=', invoice_uuid)], limit=1)

            if admin_inv:
                admin_inv.write({
                    'status':       status,
                    'error_detail': error_detail or False,
                    'clearance_id': clearance_id or False,
                })
                _logger.info(
                    'Admin invoice updated — uuid: %s, status: %s',
                    invoice_uuid, status)

            return request.make_response(
                json.dumps({'ok': True, 'uuid': invoice_uuid}),
                headers={'Content-Type': 'application/json'})

        except json.JSONDecodeError:
            _logger.error('Webhook received invalid JSON')
            return request.make_response(
                json.dumps({'error': 'Invalid JSON'}),
                status=400,
                headers={'Content-Type': 'application/json'})

        except Exception as e:
            _logger.error(
                'Webhook processing error: %s', str(e), exc_info=True)
            return request.make_response(
                json.dumps({'error': str(e)}),
                status=500,
                headers={'Content-Type': 'application/json'})