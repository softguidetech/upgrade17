# -*- coding: utf-8 -*-
from odoo import models, fields

class ResCompany(models.Model):
    _inherit = 'res.company'
    
    fax = fields.Char(string='Fax')
    name_in_report_ar = fields.Char(string='Name in Report Arabic',)
    name_in_report_en = fields.Char(string='Name in Report English',)

# class BaseDocumentLayout(models.TransientModel):
#     _inherit = 'base.document.layout'
#
#     fax = fields.Char(related='company_id.fax', readonly=True)
#     name_in_report_ar = fields.Many2one(related="company_id.name_in_report_ar", readonly=True)
#     name_in_report_en = fields.Many2one(related="company_id.name_in_report_en", readonly=True)
#     street = fields.Many2one(related="company_id.street", readonly=True)
#     zip = fields.Many2one(related="company_id.zip", readonly=True)



class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def calc_vat(self):
        total_tax_amount = 0.0
        for tax in self.tax_ids:
            total_tax_amount += (tax.amount / 100) * self.price_subtotal
        return total_tax_amount

    def calc_total_after_vat(self):
        total_after_vat = self.calc_vat() + self.price_subtotal
        return total_after_vat

    def calc_discount(self):
        discount = 0.0
        for rec in self:
            if rec.discount:
                discount = rec.price_unit * rec.quantity * (rec.discount / 100)
        return discount






