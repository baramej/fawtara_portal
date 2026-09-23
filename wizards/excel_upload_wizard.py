import base64
import io
import logging
import requests
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ExcelUploadWizard(models.TransientModel):
    _name = 'fawtara.excel.upload'
    _description = 'Bulk Invoice Upload via Excel'

    client_id = fields.Many2one(
        'fawtara.client',
        string='Client', required=True,
        domain="[('status','=','ACTIVE')]")
    excel_file = fields.Binary(
        string='Excel File', required=True)
    excel_filename = fields.Char(string='File Name')
    environment = fields.Selection([
        ('PRODUCTION', 'Production'),
        ('SANDBOX',    'Sandbox'),
    ], string='Environment', default='PRODUCTION', required=True)

    # Results
    state = fields.Selection([
        ('upload', 'Upload'),
        ('result', 'Result'),
    ], default='upload')
    total_rows = fields.Integer(string='Total Rows', readonly=True)
    accepted = fields.Integer(string='Accepted', readonly=True)
    rejected = fields.Integer(string='Rejected', readonly=True)
    result_details = fields.Text(string='Result Details', readonly=True)

    def action_upload(self):
        self.ensure_one()

        if not self.excel_file:
            raise UserError('Please select an Excel file.')

        try:
            import openpyxl
        except ImportError:
            raise UserError(
                'openpyxl is required. Run: pip install openpyxl')

        # Read Excel
        file_data = base64.b64decode(self.excel_file)
        wb = openpyxl.load_workbook(
            io.BytesIO(file_data), read_only=True)
        ws = wb.active

        rows = list(ws.iter_rows(min_row=2, values_only=True))
        if not rows:
            raise UserError('Excel file is empty or has no data rows.')

        invoices = []
        errors = []

        for i, row in enumerate(rows, start=2):
            try:
                invoice = self._parse_row(row, i)
                if invoice:
                    invoices.append(invoice)
            except Exception as e:
                errors.append(f"Row {i}: {str(e)}")

        if not invoices:
            raise UserError(
                f"No valid invoices found.\n" + "\n".join(errors))

        # Choose API key
        if self.environment == 'SANDBOX':
            api_key = self.client_id.sandbox_api_key
        else:
            api_key = self.client_id.production_api_key

        if not api_key:
            raise UserError(
                f"No {self.environment} API key for this client.")

        config = self.env['ir.config_parameter'].sudo()
        base_url = config.get_param('fawtara.api_url', 'http://localhost:8081')

        # Submit batch
        try:
            resp = requests.post(
                f"{base_url}/api/v1/invoice/batch",
                headers={
                    'X-API-Key': api_key,
                    'Content-Type': 'application/json',
                },
                json={
                    'batchReference': self.excel_filename or 'excel-upload',
                    'invoices': invoices,
                },
                timeout=120)

            if resp.status_code in (200, 202):
                data = resp.json()
                details = []
                for r in data.get('results', []):
                    if r['status'] == 'QUEUED':
                        details.append(
                            f"✅ Row {r['rowNumber']}: "
                            f"{r['invoiceNumber']} — {r['invoiceUuid']}")
                    else:
                        details.append(
                            f"❌ Row {r['rowNumber']}: "
                            f"{r['invoiceNumber']} — {r['errorMessage']}")

                self.write({
                    'state':          'result',
                    'total_rows':     data.get('totalSubmitted', 0),
                    'accepted':       data.get('accepted', 0),
                    'rejected':       data.get('rejected', 0),
                    'result_details': "\n".join(details),
                })
            else:
                raise UserError(
                    f"Batch submission failed: {resp.status_code} — {resp.text}")

        except requests.exceptions.RequestException as e:
            raise UserError(f"Cannot reach Fawtara API: {str(e)}")

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'fawtara.excel.upload',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _parse_row(self, row, row_num):
        """
        Expected Excel columns (A to S):
        A: Invoice Number
        B: Type Code (380/381/389/261)
        C: Transaction Type (B2B/B2C/B2G)
        D: Issue Date (YYYY-MM-DD)
        E: Currency (OMR)
        F: Buyer Name
        G: Buyer VATIN
        H: Buyer City
        I: Contract Reference
        J: Item Name
        K: Quantity
        L: Unit Code
        M: Unit Price
        N: VAT Category (S/Z/E/O)
        O: VAT Rate
        P: Service Type Code
        Q: ISIC Code
        R: Preceding Invoice Number (for credit notes)
        S: Preceding Invoice UUID (for credit notes)
        """
        if not row or not row[0]:
            return None  # Skip empty rows

        def cell(idx, default=''):
            val = row[idx] if idx < len(row) else None
            return str(val).strip() if val is not None else default

        invoice_number = cell(0)
        if not invoice_number:
            return None

        return {
            'invoiceNumber':   invoice_number,
            'invoiceTypeCode': cell(1, '380'),
            'transactionType': cell(2, 'B2B'),
            'issueDate':       cell(3),
            'currencyCode':    cell(4, 'OMR'),
            'buyerName':       cell(5),
            'buyerVatin':      cell(6),
            'contractReference': cell(8),
            'sellerName':      self.client_id.name,
            'sellerVatin':     self.client_id.vatin,
            'sellerAddress': {
                'addressLine1': 'Muscat',
                'city': 'Muscat',
                'postCode': '100',
                'countryCode': 'OM',
            },
            'buyerAddress': {
                'addressLine1': cell(5),
                'city': cell(7, 'Muscat'),
                'postCode': '100',
                'countryCode': 'OM',
            },
            'lines': [{
                'itemName':        cell(9, 'Service'),
                'quantity':        float(cell(10, '1')),
                'unitCode':        cell(11, 'EA'),
                'unitPrice':       float(cell(12, '0')),
                'vatCategoryCode': cell(13, 'S'),
                'vatRate':         float(cell(14, '5')),
                'serviceTypeCode': cell(15, '72000000'),
                'isicCode':        cell(16, '620101'),
            }],
            **(
                {'precedingInvoice': {
                    'precedingInvoiceNumber': cell(17),
                    'precedingInvoiceDate': cell(3),
                    'precedingInvoiceUuid': cell(18),
                    'creditNoteReasonCode': 'OTHER',
                }} if cell(1) in ('381', '261') else {}
            ),
        }

    def action_close(self):
        return {'type': 'ir.actions.act_window_close'}