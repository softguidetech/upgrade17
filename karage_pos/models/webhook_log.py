# -*- coding: utf-8 -*-

import json
import logging

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class WebhookLog(models.Model):
    """Unified model to log webhook requests and handle idempotency"""

    _name = "karage.pos.webhook.log"
    _description = "Karage POS Webhook Log"
    _rec_name = "id"
    _order = "receive_date desc"

    # Request metadata
    receive_date = fields.Datetime(
        string="Receive Date",
        required=True,
        default=fields.Datetime.now,
        help="Date and time when the webhook was received",
    )
    webhook_body = fields.Text(
        string="Webhook Body",
        required=True,
        help="Full JSON body of the incoming webhook request",
    )
    idempotency_key = fields.Char(
        string="Idempotency Key",
        index=True,
        help="Unique key to identify duplicate requests",
    )
    order_id = fields.Char(
        string="Order ID",
        index=True,
        help="External Order ID from the webhook"
    )
    ip_address = fields.Char(
        string="IP Address",
        help="IP address of the request sender"
    )
    user_agent = fields.Char(
        string="User Agent",
        help="User agent of the request"
    )
    http_method = fields.Char(
        string="HTTP Method",
        default="POST",
        help="HTTP method of the request"
    )

    # Processing status (idempotency)
    status = fields.Selection(
        [
            ("pending", "Pending"),
            ("processing", "Processing"),
            ("completed", "Completed"),
            ("failed", "Failed"),
        ],
        string="Status",
        default="pending",
        required=True,
        help="Processing status for idempotency tracking"
    )
    processed_at = fields.Datetime(
        string="Processed At",
        help="Timestamp when the request was processed"
    )

    # Response details
    status_code = fields.Integer(
        string="Status Code",
        help="HTTP status code returned"
    )
    response_message = fields.Text(
        string="Response Message",
        help="Response message or error"
    )
    response_data = fields.Text(
        string="Response Data",
        help="JSON response data for caching"
    )
    error_message = fields.Text(
        string="Error Message",
        help="Error message if processing failed"
    )
    processing_time = fields.Float(
        string="Processing Time (seconds)",
        help="Time taken to process the webhook"
    )
    success = fields.Boolean(
        string="Success",
        default=False,
        help="Whether the webhook was processed successfully"
    )

    # Result
    pos_order_id = fields.Many2one(
        "pos.order",
        string="Created POS Order",
        help="POS order created from this webhook (if successful)",
    )

    _sql_constraints = [
        (
            "idempotency_key_unique",
            "unique(idempotency_key)",
            "Idempotency key must be unique!",
        ),
    ]

    @api.model
    def create_log(self, webhook_body, idempotency_key=None, request_info=None, status="pending"):
        """
        Create a webhook log entry

        :param webhook_body: The webhook request body (dict or JSON string)
        :param idempotency_key: Idempotency key if provided
        :param request_info: Dictionary with request metadata (ip_address, user_agent, etc.)
        :param status: Initial status (default: pending)
        :return: Created log record
        """
        # Convert dict to JSON string if needed
        if isinstance(webhook_body, dict):
            webhook_body_str = json.dumps(webhook_body, indent=2, default=str)
        else:
            webhook_body_str = str(webhook_body)

        # Extract order ID from body if it's a dict
        order_id = None
        if isinstance(webhook_body, dict):
            order_id = str(webhook_body.get("OrderID", ""))
        elif isinstance(webhook_body, str):
            try:
                body_dict = json.loads(webhook_body)
                order_id = str(body_dict.get("OrderID", ""))
            except (ValueError, TypeError):
                pass

        request_info = request_info or {}

        return self.create(
            {
                "webhook_body": webhook_body_str,
                "idempotency_key": idempotency_key,
                "order_id": order_id,
                "ip_address": request_info.get("ip_address"),
                "user_agent": request_info.get("user_agent"),
                "http_method": request_info.get("http_method", "POST"),
                "status": status,
            }
        )

    @api.model
    def get_or_create_log(self, idempotency_key, order_id=None, webhook_body=None,
                          request_info=None, status="processing"):
        """
        Atomically get or create a webhook log with proper locking for idempotency

        This method prevents race conditions by:
        1. Using SELECT FOR UPDATE to lock existing records
        2. Handling unique constraint violations gracefully
        3. Returning the existing record if created by another transaction

        :param idempotency_key: The idempotency key
        :param order_id: External Order ID (optional)
        :param webhook_body: Request body (optional)
        :param request_info: Request metadata (optional)
        :param status: Initial status (default: processing)
        :return: Tuple of (record, created) where created is True if newly created
        """
        if not idempotency_key:
            # No idempotency key - create regular log
            if webhook_body:
                return self.create_log(webhook_body, None, request_info, status), True
            raise ValidationError("Either idempotency_key or webhook_body is required")

        # First, try to find existing record with lock
        with self.env.cr.savepoint():
            try:
                # Try to acquire lock on existing record
                self.env.cr.execute(
                    """
                    SELECT id
                    FROM karage_pos_webhook_log
                    WHERE idempotency_key = %s
                    FOR UPDATE NOWAIT
                """,
                    (idempotency_key,),
                )

                result = self.env.cr.fetchone()
                if result:
                    # Record exists and is locked
                    return self.browse(result[0]), False

            except Exception as e:
                # Lock not acquired or record doesn't exist yet
                _logger.debug(
                    f"Could not acquire lock for idempotency key {idempotency_key[:20]}...: {e}"
                )

                # Try regular search
                existing = self.search(
                    [("idempotency_key", "=", idempotency_key)], limit=1
                )
                if existing:
                    return existing, False

        # No existing record found, try to create
        with self.env.cr.savepoint():
            try:
                if webhook_body:
                    record = self.create_log(webhook_body, idempotency_key, request_info, status)
                else:
                    record = self.create({
                        "idempotency_key": idempotency_key,
                        "order_id": order_id,
                        "status": status,
                        "webhook_body": "{}",
                    })
                return record, True

            except Exception as e:
                # Unique constraint violation - another transaction created it
                _logger.info(
                    f"Log already created by another transaction: {idempotency_key[:20]}..."
                )
                existing = self.search(
                    [("idempotency_key", "=", idempotency_key)], limit=1
                )
                if existing:
                    return existing, False
                else:
                    # Should not happen, but re-raise if it does
                    raise

    def update_log_result(
        self,
        status_code=None,
        response_message=None,
        success=False,
        pos_order_id=None,
        processing_time=None,
        response_data=None,
        error_message=None,
        status=None,
    ):
        """Update log with processing results"""
        update_vals = {
            "status_code": status_code,
            "response_message": response_message,
            "success": success,
            "pos_order_id": pos_order_id.id if pos_order_id else False,
        }

        if processing_time is not None:
            update_vals["processing_time"] = processing_time

        if response_data is not None:
            update_vals["response_data"] = response_data

        if error_message is not None:
            update_vals["error_message"] = error_message

        if status is not None:
            update_vals["status"] = status

        if success or status == "completed":
            update_vals["processed_at"] = fields.Datetime.now()

        self.write(update_vals)

    def mark_completed(self, pos_order_id=None, response_data=None):
        """Mark the log as completed"""
        self.write(
            {
                "status": "completed",
                "success": True,
                "pos_order_id": pos_order_id.id if pos_order_id else False,
                "response_data": response_data,
                "processed_at": fields.Datetime.now(),
            }
        )

    def mark_failed(self, error_message=None):
        """Mark the log as failed"""
        self.write(
            {
                "status": "failed",
                "success": False,
                "error_message": error_message,
                "processed_at": fields.Datetime.now(),
            }
        )

    def mark_processing(self):
        """Mark the log as processing"""
        self.write({"status": "processing"})

    @api.model
    def cleanup_old_records(self, retention_days=None):
        """
        Clean up old webhook logs

        Deletes completed and failed records older than retention_days.
        Processing records are never deleted automatically.

        :param retention_days: Number of days to retain records (default from config)
        :return: Number of records deleted
        """
        if retention_days is None:
            retention_days = int(
                self.env["ir.config_parameter"]
                .sudo()
                .get_param("karage_pos.idempotency_retention_days", default="30")
            )

        if retention_days <= 0:
            _logger.info(
                "Webhook log cleanup is disabled (retention_days = 0)"
            )
            return 0

        from datetime import datetime, timedelta

        cutoff_date = datetime.now() - timedelta(days=retention_days)

        # Find old completed/failed records
        old_records = self.search(
            [
                ("status", "in", ["completed", "failed"]),
                ("receive_date", "<", cutoff_date.strftime("%Y-%m-%d %H:%M:%S")),
            ]
        )

        count = len(old_records)
        if count > 0:
            _logger.info(
                f"Cleaning up {count} old webhook logs (older than {retention_days} days)"
            )
            old_records.unlink()

        return count

    @api.model
    def cleanup_stuck_processing_records(self, timeout_minutes=None):
        """
        Reset stuck processing records to failed status

        :param timeout_minutes: Timeout in minutes (default from config)
        :return: Number of records reset
        """
        if timeout_minutes is None:
            timeout_minutes = int(
                self.env["ir.config_parameter"]
                .sudo()
                .get_param("karage_pos.idempotency_processing_timeout", default="5")
            )

        from datetime import datetime, timedelta

        cutoff_date = datetime.now() - timedelta(minutes=timeout_minutes)

        # Find stuck processing records
        stuck_records = self.search(
            [
                ("status", "=", "processing"),
                ("receive_date", "<", cutoff_date.strftime("%Y-%m-%d %H:%M:%S")),
            ]
        )

        count = len(stuck_records)
        if count > 0:
            _logger.warning(
                f"Found {count} stuck processing records (older than {timeout_minutes} minutes). "
                f"Marking as failed."
            )
            stuck_records.write(
                {
                    "status": "failed",
                    "error_message": f"Processing timeout exceeded ({timeout_minutes} minutes)",
                    "processed_at": fields.Datetime.now(),
                }
            )

        return count
