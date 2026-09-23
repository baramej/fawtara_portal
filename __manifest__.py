{
    'name': 'Fawtara Client Portal',
    'version': '1.0.0',
    'summary': 'Client-facing portal for invoice submission and tracking',
    'author': 'Baramej Business and Digital Technology Center LLC',
    'category': 'Accounting',
    'depends': ['base', 'mail', 'fawtara_admin'],
    'data': [
        'security/ir.model.access.csv',
        'views/portal_invoice_views.xml',
        'views/client_dashboard_views.xml',
        'views/excel_wizard_views.xml',
        'views/menu_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'fawtara_portal/static/src/js/client_dashboard.js',
            'fawtara_portal/static/src/css/client_dashboard.css',
            'fawtara_portal/static/src/xml/client_dashboard.xml',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
