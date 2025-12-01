# -*- coding: utf-8 -*-

import json
import logging
import time

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)


class APIController(http.Controller):
    """REST API Controller for webhook endpoint"""

    # ========== Helper Methods ==========

    def _get_header_or_body(self, data, *keys):
        """
        Extract value from headers or request body using multiple possible key names

        :param data: Request body dictionary
        :param keys: Possible header/body key names to check
        :return: First matching value or None
        """
        # Check headers first
        for key in keys:
            value = request.httprequest.headers.get(key)
            if value:
                return value

        # Check body
        if data:
            for key in keys:
                value = data.get(key)
                if value:
                    return value

        return None

    def _parse_request_body(self):
        """Parse and validate JSON request body"""
        if not request.httprequest.data:
            return None, "Request body is required"

        try:
            return json.loads(request.httprequest.data.decode("utf-8")), None
        except (ValueError, UnicodeDecodeError) as e:
            return None, f"Invalid JSON format: {str(e)}"

    def _create_webhook_log(self, body_data, idempotency_key=None):
        """Create webhook log entry"""
        try:
            # Use savepoint to isolate potential duplicate key errors
            with request.env.cr.savepoint():
                return request.env["karage.pos.webhook.log"].sudo().create_log(
                    webhook_body=body_data,
                    idempotency_key=idempotency_key,
                    request_info={
                        "ip_address": request.httprequest.remote_addr,
                        "user_agent": request.httprequest.headers.get("User-Agent"),
                        "http_method": request.httprequest.method,
                    },
                )
        except Exception as e:
            _logger.warning(f"Failed to create webhook log: {str(e)}")
            return None

    def _update_log(self, webhook_log, status_code, message, success=False,
                    pos_order=None, idempotency_record=None, start_time=None):
        """Update webhook log with processing result"""
        if not webhook_log:
            return

        try:
            webhook_log.update_log_result(
                status_code=status_code,
                response_message=message,
                success=success,
                pos_order_id=pos_order,
                idempotency_record_id=idempotency_record,
                processing_time=time.time() - start_time if start_time else None,
            )
        except Exception as e:
            _logger.warning(f"Error updating webhook log: {str(e)}")

    def _error_response(self, status, error_msg, webhook_log=None,
                        idempotency_record=None, start_time=None):
        """
        Create error response and update logs

        :param status: HTTP status code
        :param error_msg: Error message
        :param webhook_log: Webhook log record to update
        :param idempotency_record: Idempotency record to mark as failed
        :param start_time: Request start time for duration calculation
        :return: JSON error response
        """
        # Update idempotency record if exists
        if idempotency_record:
            try:
                idempotency_record.mark_failed(error_message=error_msg)
            except Exception as e:
                _logger.warning(f"Error updating idempotency record: {str(e)}")

        # Update webhook log
        self._update_log(webhook_log, status, error_msg, False,
                         idempotency_record=idempotency_record, start_time=start_time)

        # Return JSON response
        return self._json_response(None, status=status, error=error_msg)

    def _json_response(self, data, status=200, error=None):
        """Return standardized JSON response"""
        response_data = {
            "status": "success" if status == 200 else "error",
            "data": data if status == 200 else None,
            "error": error if error else None,
            "count": len(data) if isinstance(data, list) else (1 if data else 0),
        }
        return request.make_response(
            json.dumps(response_data, default=str),
            headers=[("Content-Type", "application/json")],
            status=status,
        )

    def _authenticate_api_key(self, api_key):
        """
        Authenticate using Odoo's built-in API key system

        :param api_key: API key to validate
        :return: Tuple of (success: bool, error_message: str or None)
        """
        if not api_key:
            return False, "Invalid or missing API key"

        try:
            user_id = (
                request.env["res.users.apikeys"]
                .with_user(1)
                ._check_credentials(scope="rpc", key=api_key)
            )
            request.update_env(user=user_id)
            _logger.info(f"API request authenticated for user ID: {user_id}")
            return True, None
        except Exception as e:
            _logger.warning(f"Invalid API key attempt: {str(e)}")
            return False, "Invalid or missing API key"

    def _check_idempotency(self, idempotency_key, order_id, webhook_log, start_time):
        """
        Check idempotency and return cached response if duplicate

        :param idempotency_key: Idempotency key from request
        :param order_id: External order ID
        :param webhook_log: Webhook log record
        :param start_time: Request start time
        :return: Tuple of (should_process: bool, response_or_record)
                 - If should_process=False, response_or_record is the HTTP response to return
                 - If should_process=True, response_or_record is the idempotency record
        """
        if not idempotency_key:
            return True, None

        # Get or create idempotency record atomically
        idempotency_record, created = (
            request.env["karage.pos.webhook.log"]
            .sudo()
            .get_or_create_log(
                idempotency_key,
                order_id=str(order_id),
                status="processing",
            )
        )

        if created:
            # New request - proceed with processing
            _logger.info(f"New request with idempotency key: {idempotency_key[:20]}...")
            return True, idempotency_record

        # Record already exists - check status
        if idempotency_record.status == "completed":
            # Return cached response
            _logger.info(
                f"Duplicate request detected: {idempotency_key[:20]}... "
                f"Returning cached response for OrderID: {idempotency_record.order_id}"
            )
            self._update_log(
                webhook_log, 200, "Duplicate request - returning cached response",
                True, idempotency_record.pos_order_id, idempotency_record, start_time
            )

            # Parse and return cached response
            if idempotency_record.response_data:
                try:
                    cached_data = json.loads(idempotency_record.response_data)
                    return False, self._json_response(cached_data, status=200)
                except (ValueError, TypeError):
                    pass

            # Fallback response
            return False, self._json_response({
                "id": idempotency_record.pos_order_id.id if idempotency_record.pos_order_id else None,
                "name": idempotency_record.pos_order_id.name if idempotency_record.pos_order_id else None,
                "message": "Request already processed",
                "idempotency_key": idempotency_key,
            }, status=200)

        elif idempotency_record.status == "processing":
            # Check if stuck (timeout)
            if idempotency_record.create_date:
                from datetime import datetime, timedelta

                timeout_minutes = int(
                    request.env["ir.config_parameter"]
                    .sudo()
                    .get_param("karage_pos.idempotency_processing_timeout", default="5")
                )
                processing_timeout = timedelta(minutes=timeout_minutes)

                if datetime.now() - idempotency_record.create_date > processing_timeout:
                    _logger.warning(
                        f"Idempotency record stuck in processing state: {idempotency_key[:20]}... "
                        f"Allowing retry."
                    )
                    idempotency_record.write({"status": "processing"})
                    return True, idempotency_record

            # Still processing
            _logger.warning(f"Request already being processed: {idempotency_key[:20]}...")
            self._update_log(
                webhook_log, 409, "Request is already being processed. Please wait.",
                False, idempotency_record=idempotency_record, start_time=start_time
            )
            return False, self._json_response(
                None, status=409, error="Request is already being processed. Please wait."
            )

        elif idempotency_record.status == "failed":
            # Allow retry
            _logger.info(f"Retrying previously failed request: {idempotency_key[:20]}...")
            idempotency_record.mark_processing()
            return True, idempotency_record

        return True, idempotency_record

    # ========== Main Endpoint ==========

    @http.route(
        "/api/v1/webhook/pos-order",
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
        cors="*",
    )
    def webhook_pos_order(self, **kwargs):
        """
        Webhook endpoint to create POS order from external system

        Expected JSON format:
        {
            "OrderID": 639,
            "AmountPaid": "92.0",
            "AmountTotal": 80.0,
            "GrandTotal": 92.0,
            "Tax": 12.0,
            "TaxPercent": 15.0,
            "CheckoutDetails": [...],
            "OrderItems": [...]
        }
        """
        start_time = time.time()
        webhook_log = None
        idempotency_record = None

        try:
            # 1. Validate HTTP method
            if request.httprequest.method != "POST":
                return self._json_response(
                    None, status=405, error="Method not allowed. Only POST requests are accepted."
                )

            # 2. Parse request body
            data, error = self._parse_request_body()
            if error:
                webhook_log = self._create_webhook_log(
                    request.httprequest.data.decode("utf-8", errors="ignore") if request.httprequest.data else "{}"
                )
                return self._error_response(400, error, webhook_log, start_time=start_time)

            # 3. Extract headers
            idempotency_key = self._get_header_or_body(
                data, "Idempotency-Key", "X-Idempotency-Key", "idempotency_key", "IdempotencyKey"
            )
            api_key = self._get_header_or_body(data, "X-API-KEY", "X-API-Key", "api_key")

            # 4. Create webhook log
            webhook_log = self._create_webhook_log(data, idempotency_key)

            # 5. Authenticate API key
            authenticated, auth_error = self._authenticate_api_key(api_key)
            if not authenticated:
                return self._error_response(401, auth_error, webhook_log, start_time=start_time)

            # 6. Check idempotency
            should_process, result = self._check_idempotency(
                idempotency_key, data.get("OrderID", ""), webhook_log, start_time
            )
            if not should_process:
                return result  # Return cached response or error

            idempotency_record = result

            # 7. Validate required fields
            required_fields = ["OrderID", "OrderItems", "CheckoutDetails", "AmountTotal", "AmountPaid"]
            missing_fields = [field for field in required_fields if field not in data]
            if missing_fields:
                return self._error_response(
                    400, f'Missing required fields: {", ".join(missing_fields)}',
                    webhook_log, idempotency_record, start_time
                )

            # 8. Process the order
            pos_order, order_error = self._process_pos_order(data)
            if order_error:
                return self._error_response(
                    order_error.get("status", 500), order_error.get("message"),
                    webhook_log, idempotency_record, start_time
                )

            # 9. Prepare success response
            response_data = {
                "id": pos_order.id,
                "name": pos_order.name,
                "pos_reference": pos_order.pos_reference,
                "amount_total": pos_order.amount_total,
                "amount_paid": pos_order.amount_paid,
                "amount_tax": pos_order.amount_tax,
                "state": pos_order.state,
                "date_order": str(pos_order.date_order),
                "external_order_id": data.get("OrderID"),
            }

            # 10. Update idempotency record
            if idempotency_record:
                try:
                    idempotency_record.mark_completed(
                        pos_order_id=pos_order, response_data=json.dumps(response_data)
                    )
                except Exception as e:
                    _logger.warning(f"Error updating idempotency record: {str(e)}")

            # 11. Update webhook log with success
            self._update_log(
                webhook_log, 200, json.dumps(response_data), True,
                pos_order, idempotency_record, start_time
            )

            return self._json_response(response_data)

        except Exception as e:
            _logger.error(f"Unexpected error in webhook_pos_order: {str(e)}", exc_info=True)
            return self._error_response(
                500, f"Internal server error: {str(e)}",
                webhook_log, idempotency_record, start_time
            )

    @http.route(
        "/api/v1/webhook/pos-order/bulk",
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
        cors="*",
    )
    def webhook_pos_order_bulk(self, **kwargs):
        """
        Bulk webhook endpoint to create multiple POS orders

        Expected JSON format:
        {
            "orders": [
                { /* order 1 data */ },
                { /* order 2 data */ },
                ...
            ]
        }

        Returns HTTP 200 if all succeed
        Returns HTTP 207 (Multi-Status) if partial success
        Returns HTTP 400 if all fail
        """
        start_time = time.time()
        webhook_log = None

        try:
            # 1. Validate HTTP method
            if request.httprequest.method != "POST":
                return self._json_response(
                    None, status=405, error="Method not allowed. Only POST requests are accepted."
                )

            # 2. Parse request body
            data, error = self._parse_request_body()
            if error:
                return self._json_response(None, status=400, error=error)

            # 3. Extract API key
            api_key = self._get_header_or_body(data, "X-API-KEY", "X-API-Key", "api_key")

            # 4. Create webhook log
            webhook_log = self._create_webhook_log(data)

            # 5. Authenticate API key
            authenticated, auth_error = self._authenticate_api_key(api_key)
            if not authenticated:
                self._update_log(webhook_log, 401, auth_error, False, start_time=start_time)
                return self._json_response(None, status=401, error=auth_error)

            # 6. Validate required fields
            if "orders" not in data:
                error_msg = 'Missing required field: "orders"'
                self._update_log(webhook_log, 400, error_msg, False, start_time=start_time)
                return self._json_response(None, status=400, error=error_msg)

            orders_data = data.get("orders", [])
            if not isinstance(orders_data, list):
                error_msg = '"orders" must be an array'
                self._update_log(webhook_log, 400, error_msg, False, start_time=start_time)
                return self._json_response(None, status=400, error=error_msg)

            # 7. Check bulk size limit
            max_orders = int(
                request.env["ir.config_parameter"]
                .sudo()
                .get_param("karage_pos.bulk_sync_max_orders", default="1000")
            )
            if len(orders_data) > max_orders:
                error_msg = f"Too many orders: {len(orders_data)}. Maximum allowed: {max_orders}"
                self._update_log(webhook_log, 400, error_msg, False, start_time=start_time)
                return self._json_response(None, status=400, error=error_msg)

            # 8. Process orders
            results = self._process_bulk_orders(orders_data)

            # 9. Determine overall status
            total = len(results)
            successful = sum(1 for r in results if r["status"] == "success")
            failed = total - successful

            if successful == total:
                status_code = 200
                status_text = "success"
            elif successful == 0:
                status_code = 400
                status_text = "error"
            else:
                status_code = 207  # Multi-Status
                status_text = "partial_success"

            # 10. Prepare response
            response_data = {
                "total": total,
                "successful": successful,
                "failed": failed,
                "results": results,
            }

            # 11. Update webhook log
            processing_time = time.time() - start_time
            self._update_log(
                webhook_log,
                status_code,
                json.dumps(response_data),
                successful > 0,
                start_time=start_time,
            )

            return self._json_response(
                response_data,
                status=status_code if status_code == 200 else 200,  # Always return 200, status in data
            )

        except Exception as e:
            _logger.error(f"Unexpected error in webhook_pos_order_bulk: {str(e)}", exc_info=True)
            if webhook_log:
                self._update_log(
                    webhook_log, 500, f"Internal server error: {str(e)}", False, start_time=start_time
                )
            return self._json_response(None, status=500, error=f"Internal server error: {str(e)}")

    def _process_bulk_orders(self, orders_data):
        """
        Process multiple orders independently

        :param orders_data: List of order data dicts
        :return: List of result dicts, one per order
        """
        results = []

        for idx, order_data in enumerate(orders_data):
            order_id = order_data.get("OrderID", f"unknown_{idx}")

            try:
                # Use savepoint for atomic per-order processing
                with request.env.cr.savepoint():
                    # Validate required fields for this order
                    required_fields = ["OrderID", "OrderItems", "CheckoutDetails", "AmountTotal", "AmountPaid"]
                    missing_fields = [field for field in required_fields if field not in order_data]

                    if missing_fields:
                        results.append({
                            "external_order_id": order_id,
                            "status": "error",
                            "error": f'Missing required fields: {", ".join(missing_fields)}'
                        })
                        continue

                    # Process the order
                    pos_order, order_error = self._process_pos_order(order_data)

                    if order_error:
                        results.append({
                            "external_order_id": order_id,
                            "status": "error",
                            "error": order_error.get("message", "Unknown error")
                        })
                    else:
                        results.append({
                            "external_order_id": order_id,
                            "status": "success",
                            "pos_order_id": pos_order.id,
                            "pos_order_name": pos_order.name,
                            "amount_total": pos_order.amount_total,
                        })

            except Exception as e:
                _logger.error(f"Error processing order {order_id}: {str(e)}", exc_info=True)
                results.append({
                    "external_order_id": order_id,
                    "status": "error",
                    "error": str(e)
                })

        return results

    def _process_pos_order(self, data):
        """
        Process POS order from webhook data

        :param data: Validated webhook data
        :return: Tuple of (pos_order, error_dict or None)
        """
        try:
            # Get or create POS session for external sync
            pos_session = self._get_or_create_external_session()

            if not pos_session:
                return None, {
                    "status": 400,
                    "message": "No POS configuration found for external sync. Please configure a POS for webhook integration."
                }

            # Validate payment methods
            payment_methods = pos_session.payment_method_ids
            if not payment_methods:
                return None, {
                    "status": 400,
                    "message": "No payment methods configured for this POS session."
                }

            missing_journals = [
                pm.name for pm in payment_methods if not pm.journal_id
            ]
            if missing_journals:
                return None, {
                    "status": 400,
                    "message": f'Journal not found for payment method(s): {", ".join(missing_journals)}'
                }

            # Check for duplicate external order ID
            external_order_id = str(data.get("OrderID", ""))
            if external_order_id:
                existing = request.env["pos.order"].sudo().search([
                    ("external_order_id", "=", external_order_id),
                    ("external_order_source", "=", "karage_pos_webhook"),
                ], limit=1)
                if existing:
                    return None, {
                        "status": 400,
                        "message": f"Duplicate order: OrderID {external_order_id} already exists as {existing.name}"
                    }

            # Validate OrderStatus (only accept completed orders)
            order_status = data.get("OrderStatus")
            if order_status is not None:
                # Get valid statuses from config (default to 103)
                valid_statuses_str = request.env["ir.config_parameter"].sudo().get_param(
                    "karage_pos.valid_order_statuses", "103"
                )
                valid_statuses = [int(s.strip()) for s in valid_statuses_str.split(",") if s.strip().isdigit()]

                if order_status not in valid_statuses:
                    return None, {
                        "status": 400,
                        "message": f"Invalid OrderStatus: {order_status}. Only completed orders ({', '.join(map(str, valid_statuses))}) are accepted."
                    }

            # Parse OrderDate from payload (use external timestamp)
            from datetime import datetime
            order_date = data.get("OrderDate")
            if order_date:
                try:
                    # Handle ISO format with or without timezone
                    order_datetime = datetime.fromisoformat(order_date.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    _logger.warning(f"Could not parse OrderDate: {order_date}. Using current time.")
                    order_datetime = fields.Datetime.now()
            else:
                order_datetime = fields.Datetime.now()

            # Parse amounts
            amount_paid = float(str(data.get("AmountPaid", 0)).replace(",", ""))
            grand_total = float(data.get("GrandTotal", 0))
            tax = float(data.get("Tax", 0))
            tax_percent = float(data.get("TaxPercent", 0))
            balance_amount = float(data.get("BalanceAmount", 0))

            # Validate data consistency
            currency = pos_session.config_id.currency_id
            rounding = currency.rounding

            calculated_total, calculated_tax = self._calculate_totals(
                data.get("OrderItems", []), tax_percent, tax
            )

            if abs(calculated_total - grand_total) > rounding * 10:
                return None, {
                    "status": 400,
                    "message": f"Data inconsistency: Calculated total ({calculated_total}) does not match GrandTotal ({grand_total})"
                }

            if abs(amount_paid - grand_total) > rounding * 10 and balance_amount > rounding:
                return None, {
                    "status": 400,
                    "message": f"Data inconsistency: AmountPaid ({amount_paid}) + BalanceAmount ({balance_amount}) should equal GrandTotal ({grand_total})"
                }

            # Prepare order lines
            order_lines, lines_error = self._prepare_order_lines(
                data.get("OrderItems", []), pos_session
            )
            if lines_error:
                return None, lines_error

            # Prepare payment lines
            payment_lines, payment_error = self._prepare_payment_lines(
                data.get("CheckoutDetails", []), pos_session, amount_paid, rounding
            )
            if payment_error:
                return None, payment_error

            # Calculate final totals
            final_total = sum(line[2]["price_subtotal_incl"] for line in order_lines)
            final_tax = sum(
                line[2]["price_subtotal_incl"] - line[2]["price_subtotal"]
                for line in order_lines
            )
            total_paid = sum(line[2]["amount"] for line in payment_lines)

            # Create POS order
            order_vals = {
                "session_id": pos_session.id,
                "config_id": pos_session.config_id.id,
                "company_id": pos_session.config_id.company_id.id,
                "pricelist_id": pos_session.config_id.pricelist_id.id,
                "fiscal_position_id": (
                    pos_session.config_id.default_fiscal_position_id.id
                    if pos_session.config_id.default_fiscal_position_id
                    else False
                ),
                "user_id": pos_session.user_id.id,
                "date_order": order_datetime,  # Use external timestamp
                "partner_id": False,
                "to_invoice": False,
                "general_note": f'External Order ID: {data.get("OrderID")}',
                "lines": order_lines,
                "payment_ids": payment_lines,
                "amount_total": final_total,
                "amount_tax": final_tax,
                "amount_paid": total_paid,
                "amount_return": max(0.0, total_paid - final_total),
                # External order tracking fields
                "external_order_id": external_order_id,
                "external_order_source": "karage_pos_webhook",
                "external_order_date": order_datetime,
            }

            pos_order = request.env["pos.order"].sudo().create(order_vals)

            # Confirm the order
            try:
                pos_order.action_pos_order_paid()
            except Exception as e:
                _logger.error(f"Error confirming POS order: {str(e)}", exc_info=True)
                return None, {"status": 500, "message": f"Failed to confirm order: {str(e)}"}

            # Create picking for inventory
            try:
                pos_order._create_order_picking()
            except Exception as e:
                _logger.warning(f"Could not create picking for order {pos_order.name}: {str(e)}")

            return pos_order, None

        except Exception as e:
            _logger.error(f"Error processing POS order: {str(e)}", exc_info=True)
            return None, {"status": 500, "message": str(e)}

    def _calculate_totals(self, order_items, tax_percent, tax):
        """Calculate expected totals from order items"""
        calculated_total = 0.0
        for item in order_items:
            item_price = float(item.get("Price", 0))
            item_qty = float(item.get("Quantity", 1))
            item_discount = float(item.get("DiscountAmount", 0))
            calculated_total += (item_price * item_qty) - item_discount

        if tax_percent > 0:
            calculated_tax = calculated_total * (tax_percent / 100.0)
            calculated_total_with_tax = calculated_total + calculated_tax
        else:
            calculated_total_with_tax = calculated_total + tax

        return calculated_total_with_tax, calculated_tax if tax_percent > 0 else tax

    def _get_or_create_external_session(self):
        """
        Get or create a POS session for external webhook integration

        Strategy:
        1. Look for an open session for a POS config with 'External' or 'Webhook' in the name
        2. If no open session found, look for the newest POS config for external sync
        3. Create a new session if needed
        4. Return the session

        :return: pos.session record or None
        """
        pos_session_env = request.env["pos.session"].sudo()
        pos_config_env = request.env["pos.config"].sudo()

        # 1. Try to find an open session for external sync
        # Look for POS config with 'External', 'Webhook', 'API', or 'Integration' in the name
        # Check for both 'opened' and 'opening_control' states
        pos_session = pos_session_env.search([
            ("state", "in", ["opened", "opening_control"]),
            "|", "|", "|",
            ("config_id.name", "ilike", "external"),
            ("config_id.name", "ilike", "webhook"),
            ("config_id.name", "ilike", "api"),
            ("config_id.name", "ilike", "integration"),
        ], limit=1, order="id desc")

        if pos_session:
            _logger.info(f"Using existing external POS session: {pos_session.name}")
            return pos_session

        # 2. If no open session, find the POS config for external sync
        pos_config = pos_config_env.search([
            "|", "|", "|",
            ("name", "ilike", "external"),
            ("name", "ilike", "webhook"),
            ("name", "ilike", "api"),
            ("name", "ilike", "integration"),
        ], limit=1, order="id desc")

        # 3. If no dedicated external config, try to use any available POS config
        if not pos_config:
            _logger.warning("No dedicated external POS config found. Looking for any POS config...")
            pos_config = pos_config_env.search([], limit=1, order="id desc")

        if not pos_config:
            _logger.error("No POS configuration found in the system")
            return None

        # 4. Check if there's already an open session for this config
        # Check for both 'opened' and 'opening_control' states
        existing_session = pos_session_env.search([
            ("config_id", "=", pos_config.id),
            ("state", "in", ["opened", "opening_control"]),
        ], limit=1)

        if existing_session:
            _logger.info(f"Found existing open session for config {pos_config.name}: {existing_session.name}")
            return existing_session

        # 5. Create a new session for external sync
        try:
            _logger.info(f"Creating new POS session for external sync using config: {pos_config.name}")
            new_session = pos_session_env.create({
                "config_id": pos_config.id,
                "user_id": request.env.user.id,
            })

            # Open the session
            new_session.action_pos_session_open()
            _logger.info(f"Successfully created and opened POS session: {new_session.name}")
            return new_session

        except Exception as e:
            _logger.error(f"Failed to create POS session for external sync: {str(e)}", exc_info=True)
            return None

    def _find_product_by_id(self, odoo_item_id, item_id, item_name, pos_session):
        """
        Find product by OdooItemID (preferred), ItemID, or ItemName

        Priority order:
        1. OdooItemID (direct product_id)
        2. ItemID (legacy support)
        3. ItemName (exact match)
        4. ItemName (fuzzy match with warning)

        :param odoo_item_id: Direct Odoo product_id (preferred)
        :param item_id: Legacy ItemID
        :param item_name: Product name for fallback
        :param pos_session: POS session for company context
        :return: Tuple of (product, lookup_method) or (None, None)
        """
        product_env = request.env["product.product"].sudo()
        company_id = pos_session.config_id.company_id.id

        # Priority 1: OdooItemID (direct product_id)
        if odoo_item_id and odoo_item_id > 0:
            product = product_env.browse(odoo_item_id)
            if product.exists():
                _logger.debug(f"Product found by OdooItemID: {odoo_item_id}")
                return product, "OdooItemID"
            else:
                _logger.warning(f"OdooItemID {odoo_item_id} does not exist")

        # Priority 2: ItemID (legacy support)
        if item_id and item_id > 0:
            product = product_env.browse(item_id)
            if product.exists():
                _logger.info(f"Product found by ItemID (legacy): {item_id}")
                return product, "ItemID"

        # Priority 3: ItemName exact match
        if item_name:
            product = product_env.search([
                ("name", "=", item_name),
                ("sale_ok", "=", True),
                ("available_in_pos", "=", True),
                "|",
                ("company_id", "=", False),
                ("company_id", "=", company_id),
            ], limit=1)

            if product:
                _logger.warning(
                    f"Product found by exact ItemName match: '{item_name}'. "
                    f"Consider using OdooItemID for better performance."
                )
                return product, "ItemName (exact)"

            # Priority 4: ItemName fuzzy match
            product = product_env.search([
                ("name", "ilike", item_name),
                ("sale_ok", "=", True),
                ("available_in_pos", "=", True),
                "|",
                ("company_id", "=", False),
                ("company_id", "=", company_id),
            ], limit=1)

            if product:
                _logger.warning(
                    f"Product found by fuzzy ItemName match: '{item_name}' -> '{product.name}'. "
                    f"Consider using OdooItemID for accuracy."
                )
                return product, "ItemName (fuzzy)"

        return None, None

    def _validate_product_for_pos(self, product, pos_session):
        """
        Validate product is suitable for POS order

        Checks:
        - Product exists and is active
        - Product.sale_ok = True
        - Product.available_in_pos = True
        - Product company matches session company

        :param product: Product to validate
        :param pos_session: POS session for company context
        :return: None if valid, error dict if invalid
        """
        if not product:
            return {
                "status": 404,
                "message": "Product not found"
            }

        if not product.active:
            return {
                "status": 400,
                "message": f"Product '{product.name}' (ID: {product.id}) is not active"
            }

        if not product.sale_ok:
            return {
                "status": 400,
                "message": f"Product '{product.name}' (ID: {product.id}) is not available for sale"
            }

        if not product.available_in_pos:
            return {
                "status": 400,
                "message": f"Product '{product.name}' (ID: {product.id}) is not available in POS"
            }

        # Check company match
        company_id = pos_session.config_id.company_id.id
        if product.company_id and product.company_id.id != company_id:
            return {
                "status": 400,
                "message": f"Product '{product.name}' (ID: {product.id}) belongs to a different company"
            }

        return None

    def _prepare_order_lines(self, order_items, pos_session):
        """Prepare order lines from order items"""
        order_lines = []

        for order_item in order_items:
            item_name = order_item.get("ItemName", "").strip()
            item_id = order_item.get("ItemID", 0)
            odoo_item_id = order_item.get("OdooItemID", 0)
            price = float(order_item.get("Price", 0))
            quantity = float(order_item.get("Quantity", 1))
            discount_amount = float(order_item.get("DiscountAmount", 0))

            # Find product using new helper
            product, lookup_method = self._find_product_by_id(
                odoo_item_id, item_id, item_name, pos_session
            )

            if not product:
                return None, {
                    "status": 404,
                    "message": f'Product not found: OdooItemID={odoo_item_id}, ItemID={item_id}, ItemName="{item_name}"'
                }

            # Validate product for POS
            validation_error = self._validate_product_for_pos(product, pos_session)
            if validation_error:
                return None, validation_error

            # Calculate discount percentage
            discount_percent = 0.0
            if price > 0 and discount_amount > 0:
                discount_percent = (discount_amount / (price * quantity)) * 100.0

            # Get taxes
            taxes = product.taxes_id.filtered(
                lambda t: t.company_id.id == pos_session.config_id.company_id.id
            )

            fiscal_position = pos_session.config_id.default_fiscal_position_id
            if fiscal_position:
                taxes = fiscal_position.map_tax(taxes)

            # Calculate tax amount
            tax_results = taxes.compute_all(
                price * (1 - discount_percent / 100.0),
                pos_session.config_id.currency_id,
                quantity,
                product=product,
                partner=False,
            )

            order_lines.append((0, 0, {
                "product_id": product.id,
                "qty": quantity,
                "price_unit": price,
                "discount": discount_percent,
                "price_subtotal": tax_results["total_excluded"],
                "price_subtotal_incl": tax_results["total_included"],
                "tax_ids": [(6, 0, taxes.ids)],
            }))

        if not order_lines:
            return None, {"status": 400, "message": "No valid order lines created"}

        return order_lines, None

    def _prepare_payment_lines(self, checkout_details, pos_session, expected_amount, rounding):
        """Prepare payment lines from checkout details"""
        payment_lines = []
        total_paid = 0.0

        # Payment mode mapping
        payment_mode_mapping = {
            1: "Cash",
            2: "Card",
            3: "Credit",
            5: "Tabby",
            6: "Tamara",
            7: "StcPay",
            8: "Bank Transfer",
        }

        for checkout in checkout_details:
            payment_mode = checkout.get("PaymentMode", 1)
            amount = float(str(checkout.get("AmountPaid", 0)).replace(",", ""))
            card_type = checkout.get("CardType", "Cash")

            if amount <= 0:
                continue

            # Find payment method
            payment_method = None
            journal_search_name = payment_mode_mapping.get(payment_mode)

            if journal_search_name:
                payment_method = pos_session.payment_method_ids.filtered(
                    lambda p: p.journal_id and journal_search_name.lower() in p.journal_id.name.lower()
                )[:1]

            if not payment_method and card_type:
                payment_method = pos_session.payment_method_ids.filtered(
                    lambda p: p.journal_id and card_type.lower() in p.journal_id.name.lower()
                )[:1]

            if not payment_method and payment_mode == 1:
                payment_method = pos_session.payment_method_ids.filtered(lambda p: p.is_cash_count)[:1]

            if not payment_method:
                return None, {
                    "status": 400,
                    "message": f'No payment method found for PaymentMode={payment_mode}, CardType={card_type}'
                }

            if not payment_method.journal_id:
                return None, {
                    "status": 400,
                    "message": f"Journal not found for payment method: {payment_method.name}"
                }

            total_paid += amount
            payment_lines.append((0, 0, {
                "payment_method_id": payment_method.id,
                "amount": amount,
                "payment_date": fields.Datetime.now(),
            }))

        if not payment_lines:
            return None, {"status": 400, "message": "No valid payment lines created"}

        if abs(total_paid - expected_amount) > rounding:
            return None, {
                "status": 400,
                "message": f"Payment inconsistency: Sum of CheckoutDetails ({total_paid}) does not match AmountPaid ({expected_amount})"
            }

        return payment_lines, None
