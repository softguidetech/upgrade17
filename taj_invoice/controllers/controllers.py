# -*- coding: utf-8 -*-
from odoo import http

# class TajInvoice(http.Controller):
#     @http.route('/taj_invoice/taj_invoice/', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/taj_invoice/taj_invoice/objects/', auth='public')
#     def list(self, **kw):
#         return http.request.render('taj_invoice.listing', {
#             'root': '/taj_invoice/taj_invoice',
#             'objects': http.request.env['taj_invoice.taj_invoice'].search([]),
#         })

#     @http.route('/taj_invoice/taj_invoice/objects/<model("taj_invoice.taj_invoice"):obj>/', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('taj_invoice.object', {
#             'object': obj
#         })