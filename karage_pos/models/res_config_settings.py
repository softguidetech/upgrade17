# -*- coding: utf-8 -*-

from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # Idempotency configuration
    idempotency_processing_timeout = fields.Integer(
        string='Idempotency Processing Timeout (minutes)',
        default=5,
        config_parameter='karage_pos.idempotency_processing_timeout',
        help='Maximum time a request can stay in "processing" status before being considered stuck. '
             'After this timeout, the request can be retried. Default: 5 minutes.'
    )

    idempotency_retention_days = fields.Integer(
        string='Idempotency Record Retention (days)',
        default=30,
        config_parameter='karage_pos.idempotency_retention_days',
        help='Number of days to keep idempotency records before automatic cleanup. '
             'Completed and failed records older than this will be deleted. '
             'Default: 30 days. Set to 0 to disable automatic cleanup.'
    )

    # Bulk sync configuration
    bulk_sync_max_orders = fields.Integer(
        string='Bulk Sync Max Orders',
        default=1000,
        config_parameter='karage_pos.bulk_sync_max_orders',
        help='Maximum number of orders allowed in a single bulk sync request. '
             'Default: 1000 orders.'
    )

    # Order validation configuration
    valid_order_statuses = fields.Char(
        string='Valid Order Statuses',
        default='103',
        config_parameter='karage_pos.valid_order_statuses',
        help='Comma-separated list of valid OrderStatus values from external POS system. '
             'Only orders with these status codes will be accepted. '
             'Example: "103,104" to accept multiple statuses. Default: "103" (completed orders).'
    )
