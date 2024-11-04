# -*- coding: utf-8 -*-
{
    'name': "Custom Report Template",
    'summary': "Custom Report Templatee",
    'license': 'LGPL-3',
    'description': """Custom Report Template""",
    'author': "MAyat Samir",
    'category': 'Invoicing',
    'version': '0.1',
    'depends': ['base','account'],
    'data': [
        'views/res_company.xml',
        'views/report_template.xml',
        'views/account_invoice.xml',
        
    ],
}

