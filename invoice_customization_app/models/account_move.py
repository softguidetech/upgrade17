# -*- coding: utf-8 -*-
from odoo import models

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






