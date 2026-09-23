import io
import logging
from odoo import http
from odoo.http import request, Response

_logger = logging.getLogger(__name__)


class ExcelTemplateController(http.Controller):

    @http.route(
        '/fawtara/download/invoice-template',
        type='http',
        auth='user',
        methods=['GET'])
    def download_invoice_template(self, **kwargs):
        """Generate and return the Fawtara invoice Excel template."""
        try:
            import openpyxl
            from openpyxl.styles import (
                PatternFill, Font, Alignment, Border, Side
            )
            from openpyxl.data_validation import DataValidation
        except ImportError:
            return Response(
                "openpyxl not installed. Run: pip install openpyxl",
                status=500)

        wb = openpyxl.Workbook()

        # ── Main Sheet ────────────────────────────────────────────
        ws = wb.active
        ws.title = "Invoices"

        # Colors
        HEADER_FILL = PatternFill("solid", fgColor="1E3A8A")
        REQUIRED_FILL = PatternFill("solid", fgColor="DBEAFE")
        OPTIONAL_FILL = PatternFill("solid", fgColor="F0F9FF")
        EXAMPLE_FILL = PatternFill("solid", fgColor="F0FDF4")

        HEADER_FONT = Font(bold=True, color="FFFFFF",
                           name="Calibri", size=10)
        REQUIRED_FONT = Font(bold=True, color="1E3A8A",
                             name="Calibri", size=9)
        BODY_FONT = Font(name="Calibri", size=9)
        CENTER = Alignment(horizontal="center",
                           vertical="center", wrap_text=True)
        LEFT = Alignment(horizontal="left",
                         vertical="center")

        thin = Side(style="thin", color="CBD5E1")
        border = Border(
            left=thin, right=thin, top=thin, bottom=thin)

        # Columns definition
        columns = [
            # (Header, Width, Required, Dropdown values or None, Example)
            ("Invoice Number", 18, True, None,
             "INV-2026-001"),
            ("Type Code", 12, True,
             ["380", "381", "389", "261"],
             "380"),
            ("Transaction Type", 14, True,
             ["B2B", "B2C", "B2G"],
             "B2B"),
            ("Issue Date (YYYY-MM-DD)", 18, True, None,
             "2026-09-17"),
            ("Currency", 10, True,
             ["OMR", "USD", "EUR", "AED", "SAR"],
             "OMR"),
            ("Buyer Name", 20, True, None,
             "Ministry of Finance"),
            ("Buyer VATIN", 16, False, None,
             "OM1187654321"),
            ("Buyer City", 14, False, None,
             "Muscat"),
            ("Contract Reference", 18, False, None,
             "CONTRACT-001"),
            ("Item Name", 22, True, None,
             "Software Development"),
            ("Quantity", 10, True, None,
             "10"),
            ("Unit Code", 10, True,
             ["HUR", "EA", "KGM", "MTR", "LTR", "DAY", "MON"],
             "HUR"),
            ("Unit Price", 12, True, None,
             "50.000"),
            ("VAT Category", 12, True,
             ["S", "Z", "E", "O"],
             "S"),
            ("VAT Rate %", 10, True, None,
             "5.0"),
            ("Service Type Code", 16, False, None,
             "72000000"),
            ("ISIC Code", 12, False, None,
             "620101"),
            ("Preceding Invoice #", 18, False, None,
             "INV-2026-000"),
            ("Preceding Invoice UUID", 36, False, None,
             "uuid-of-original-invoice"),
        ]

        # ── Title row ─────────────────────────────────────────────
        ws.merge_cells("A1:S1")
        title_cell = ws["A1"]
        title_cell.value = "Fawtara E-Invoicing — Bulk Invoice Upload Template"
        title_cell.font = Font(
            bold=True, color="FFFFFF", name="Calibri", size=13)
        title_cell.fill = PatternFill("solid", fgColor="0F172A")
        title_cell.alignment = CENTER
        ws.row_dimensions[1].height = 30

        # ── Sub-title row ─────────────────────────────────────────
        ws.merge_cells("A2:S2")
        sub_cell = ws["A2"]
        sub_cell.value = (
            "🔵 Required fields  |  ⚪ Optional fields  |  "
            "Columns R-S only needed for Credit Notes (381/261)  |  "
            "One invoice per row — multiple lines: repeat same Invoice Number")
        sub_cell.font = Font(italic=True, color="475569",
                             name="Calibri", size=9)
        sub_cell.fill = PatternFill("solid", fgColor="E0F2FE")
        sub_cell.alignment = CENTER
        ws.row_dimensions[2].height = 20

        # ── Header row ────────────────────────────────────────────
        for col_idx, (header, width, required, _, _) in \
                enumerate(columns, start=1):
            cell = ws.cell(row=3, column=col_idx)
            prefix = "🔵 " if required else "⚪ "
            cell.value = prefix + header
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = CENTER
            cell.border = border
            ws.column_dimensions[
                cell.column_letter].width = width
        ws.row_dimensions[3].height = 45

        # ── Example row ───────────────────────────────────────────
        for col_idx, (_, _, required, _, example) in \
                enumerate(columns, start=1):
            cell = ws.cell(row=4, column=col_idx)
            cell.value = example
            cell.font = Font(
                italic=True, color="15803D",
                name="Calibri", size=9)
            cell.fill = EXAMPLE_FILL
            cell.alignment = LEFT
            cell.border = border
        ws.row_dimensions[4].height = 18

        # ── Data rows (rows 5-104 = 100 invoices) ─────────────────
        for row_idx in range(5, 105):
            for col_idx, (_, _, required, _, _) in \
                    enumerate(columns, start=1):
                cell = ws.cell(row=row_idx, column=col_idx)
                cell.fill = REQUIRED_FILL if required else OPTIONAL_FILL
                cell.font = BODY_FONT
                cell.alignment = LEFT
                cell.border = border
            ws.row_dimensions[row_idx].height = 16

        # ── Dropdowns ─────────────────────────────────────────────
        for col_idx, (_, _, _, dropdown, _) in \
                enumerate(columns, start=1):
            if not dropdown:
                continue
            col_letter = ws.cell(
                row=3, column=col_idx).column_letter
            dv = DataValidation(
                type="list",
                formula1='"' + ','.join(dropdown) + '"',
                showDropDown=False,
                showErrorMessage=True,
                error="Please select a valid value from the list",
                errorTitle="Invalid Value")
            ws.add_data_validation(dv)
            dv.add(f"{col_letter}5:{col_letter}104")

        # ── Freeze panes ──────────────────────────────────────────
        ws.freeze_panes = "A5"

        # ── Instructions Sheet ────────────────────────────────────
        ws2 = wb.create_sheet("Instructions")
        ws2.column_dimensions["A"].width = 25
        ws2.column_dimensions["B"].width = 60

        instructions = [
            ("FAWTARA EXCEL UPLOAD GUIDE", ""),
            ("", ""),
            ("REQUIRED FIELDS", ""),
            ("Invoice Number", "Unique invoice number e.g. INV-2026-001"),
            ("Type Code",
             "380=Tax Invoice, 381=Credit Note, "
             "389=Self-Billing Invoice, 261=Self-Billing Credit Note"),
            ("Transaction Type",
             "B2B=Business to Business, B2C=Business to Consumer, "
             "B2G=Business to Government"),
            ("Issue Date",
             "Format: YYYY-MM-DD e.g. 2026-09-17"),
            ("Currency",
             "OMR (Omani Rial) is default. Also: USD, EUR, AED, SAR"),
            ("Buyer Name",
             "Full legal name of the buyer"),
            ("Item Name",
             "Description of the product or service"),
            ("Quantity",
             "Numeric quantity e.g. 10"),
            ("Unit Code",
             "HUR=Hours, EA=Each, KGM=Kg, MTR=Meter, "
             "LTR=Litre, DAY=Day, MON=Month"),
            ("Unit Price",
             "Price per unit excluding VAT e.g. 50.000"),
            ("VAT Category",
             "S=Standard (5%), Z=Zero Rated, E=Exempt, O=Out of Scope"),
            ("VAT Rate %",
             "Use 5.0 for standard VAT, 0.0 for others"),
            ("", ""),
            ("OPTIONAL FIELDS", ""),
            ("Buyer VATIN",
             "Buyer VAT number e.g. OM1187654321 "
             "(required for B2B invoices)"),
            ("Buyer City",
             "City of the buyer e.g. Muscat"),
            ("Contract Reference",
             "Your contract or PO number"),
            ("Service Type Code",
             "UNSPSC code e.g. 72000000 for IT services"),
            ("ISIC Code",
             "ISIC activity code e.g. 620101"),
            ("", ""),
            ("CREDIT NOTE FIELDS (381 / 261 only)", ""),
            ("Preceding Invoice #",
             "Invoice number of the original invoice being reversed"),
            ("Preceding Invoice UUID",
             "UUID of the original invoice "
             "(get from Fawtara after it was cleared)"),
            ("", ""),
            ("MULTI-LINE INVOICES", ""),
            ("How to add multiple lines",
             "Repeat the same Invoice Number on consecutive rows "
             "with different items. All rows with the same Invoice Number "
             "will be combined into one invoice."),
            ("", ""),
            ("LIMITS", ""),
            ("Max invoices per upload", "100"),
            ("Max rows", "100 (one row per line item)"),
        ]

        for row_idx, (key, val) in enumerate(instructions, start=1):
            k_cell = ws2.cell(row=row_idx, column=1, value=key)
            v_cell = ws2.cell(row=row_idx, column=2, value=val)
            if key and not val:
                k_cell.font = Font(
                    bold=True, color="1E3A8A",
                    name="Calibri", size=10)
                k_cell.fill = PatternFill(
                    "solid", fgColor="DBEAFE")
            else:
                k_cell.font = Font(
                    bold=True, name="Calibri", size=9,
                    color="374151")
                v_cell.font = Font(
                    name="Calibri", size=9, color="475569")
            k_cell.alignment = LEFT
            v_cell.alignment = Alignment(
                horizontal="left", wrap_text=True)
            ws2.row_dimensions[row_idx].height = 18

        # ── Save and return ────────────────────────────────────────
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        return request.make_response(
            output.read(),
            headers=[
                ('Content-Type',
                 'application/vnd.openxmlformats-officedocument'
                 '.spreadsheetml.sheet'),
                ('Content-Disposition',
                 'attachment; filename="fawtara_invoice_template.xlsx"'),
                ('Cache-Control', 'no-cache'),
            ])

    # ── Secure document proxy ─────────────────────────────────────
    # Looks the invoice up WITHOUT sudo so record rules apply: a client user
    # can only download documents of their own company's invoices (before,
    # any logged-in user could fetch any client's PDF/QR by UUID).
    @staticmethod
    def _resolve_target(invoice_uuid):
        inv = request.env['fawtara.invoice'].search(
            [('invoice_uuid', '=', invoice_uuid)], limit=1)
        if not inv:
            return None, None, None
        config = request.env['ir.config_parameter'].sudo()
        client = inv.sudo().fawtara_client_id
        if inv.environment == 'SANDBOX':
            base_url = config.get_param('fawtara.sandbox_url', 'http://localhost:8081')
            api_key = (client.sandbox_api_key
                       or config.get_param('fawtara.sandbox_admin_api_key'))
        else:
            base_url = config.get_param('fawtara.api_url', 'http://localhost:8081')
            api_key = (client.production_api_key
                       or config.get_param('fawtara.admin_api_key'))
        return inv, base_url, api_key

    def _proxy(self, invoice_uuid, kind, content_type, filename):
        inv, base_url, api_key = self._resolve_target(invoice_uuid)
        if not inv:
            return Response("Invoice not found", status=404)
        try:
            import requests as req_lib
            resp = req_lib.get(
                f"{base_url}/api/v1/invoice/{invoice_uuid}/{kind}",
                headers={'X-API-Key': api_key},
                timeout=30)
            if resp.status_code == 200:
                return request.make_response(
                    resp.content,
                    headers=[
                        ('Content-Type', content_type),
                        ('Content-Disposition',
                         f'attachment; filename="{filename(inv)}"'),
                    ])
            return Response(
                f"{kind.upper()} not available yet. Status: {resp.status_code}",
                status=404)
        except Exception as e:  # noqa: BLE001
            _logger.warning('Fawtara %s proxy failed for %s: %s', kind, invoice_uuid, e)
            return Response("Fawtara API unreachable", status=502)

    @http.route(
        '/fawtara/invoice/<string:invoice_uuid>/qr',
        type='http',
        auth='user',
        methods=['GET'])
    def download_qr(self, invoice_uuid, **kwargs):
        """Proxy QR download from Spring Boot."""
        return self._proxy(invoice_uuid, 'qr', 'image/png',
                           lambda inv: f"{invoice_uuid}-qr.png")

    @http.route(
        '/fawtara/invoice/<string:invoice_uuid>/pdf',
        type='http',
        auth='user',
        methods=['GET'])
    def download_pdf(self, invoice_uuid, **kwargs):
        """Proxy PDF download from Spring Boot."""
        return self._proxy(invoice_uuid, 'pdf', 'application/pdf',
                           lambda inv: f"invoice-{inv.invoice_number or invoice_uuid}.pdf")

    @http.route(
        '/fawtara/invoice/<string:invoice_uuid>/xml',
        type='http',
        auth='user',
        methods=['GET'])
    def download_xml(self, invoice_uuid, **kwargs):
        """Proxy signed UBL XML download from Spring Boot."""
        return self._proxy(invoice_uuid, 'xml', 'application/xml',
                           lambda inv: f"invoice-{inv.invoice_number or invoice_uuid}.xml")
