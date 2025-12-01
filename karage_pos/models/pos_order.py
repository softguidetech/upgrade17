# -*- coding: utf-8 -*-

from odoo import fields, models


class PosOrder(models.Model):
    _inherit = "pos.order"

    external_order_id = fields.Char(
        string="External Order ID",
        index=True,
        help="Order ID from external POS system"
    )
    external_order_source = fields.Char(
        string="External Source",
        help="Source system (e.g., 'karage_pos_webhook')"
    )
    external_order_date = fields.Datetime(
        string="External Order Date",
        help="Order date from external system"
    )
