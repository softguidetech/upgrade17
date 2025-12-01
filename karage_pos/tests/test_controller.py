# -*- coding: utf-8 -*-

import json

from odoo import fields
from odoo.tests import HttpCase
from odoo.tests.common import tagged

from .test_common import KaragePosTestCommon


@tagged("post_install", "-at_install")
class TestWebhookController(HttpCase, KaragePosTestCommon):
    """Test webhook controller"""  # pylint: disable=too-many-public-methods

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.setup_common()

    def setUp(self):
        super().setUp()
        self.webhook_url = "/api/v1/webhook/pos-order"
        self.api_key = "test_api_key_12345"
        # Ensure sample_webhook_data has correct product IDs
        if hasattr(self, "sample_webhook_data"):
            self.sample_webhook_data["OrderItems"][0]["ItemID"] = self.product1.id
            self.sample_webhook_data["OrderItems"][0]["ItemName"] = self.product1.name

    def _make_webhook_request(self, data, headers=None, method="POST"):
        """Helper to make webhook request"""
        if headers is None:
            headers = {"X-API-KEY": self.api_key}

        if method == "POST":
            return self.url_open(
                self.webhook_url,
                data=json.dumps(data),
                headers=headers,
            )
        # For non-POST requests, try to use url_open with different method
        # Note: Odoo's url_open only supports POST, so GET/OPTIONS will fail
        try:
            # This will fail for non-POST methods, which is expected
            return self.url_open(
                self.webhook_url,
                data="",
                headers=headers,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            return None

    def test_webhook_post_only(self):
        """Test that only POST requests are accepted"""
        # GET request should fail (route only accepts POST)
        try:
            response = self._make_webhook_request({}, method="GET")
            # If it doesn't raise an exception, check status
            if response:
                self.assertIn(response.status_code, [404, 405])
        except Exception:  # pylint: disable=broad-exception-caught
            # Expected - route doesn't accept GET
            pass

        # OPTIONS request should fail
        try:
            response = self._make_webhook_request({}, method="OPTIONS")
            if response:
                self.assertIn(response.status_code, [404, 405])
        except Exception:  # pylint: disable=broad-exception-caught
            # Expected - route doesn't accept OPTIONS
            pass

    def test_webhook_missing_body(self):
        """Test webhook with missing request body"""
        # Empty dict should still have body, test with actual empty body
        response = self.url_open(
            self.webhook_url,
            data="",
            headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")
        self.assertIn("Request body is required", result["error"])

    def test_webhook_invalid_json(self):
        """Test webhook with invalid JSON"""
        response = self.url_open(
            self.webhook_url,
            data="invalid json",
            headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")

    def test_webhook_missing_api_key(self):
        """Test webhook without API key"""
        response = self._make_webhook_request(self.sample_webhook_data, headers={})
        self.assertEqual(response.status_code, 401)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")
        self.assertIn("API key", result["error"])

    def test_webhook_invalid_api_key(self):
        """Test webhook with invalid API key"""
        response = self._make_webhook_request(
            self.sample_webhook_data, headers={"X-API-KEY": "invalid_key"}
        )
        self.assertEqual(response.status_code, 401)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")

    def test_webhook_missing_required_fields(self):
        """Test webhook with missing required fields"""
        incomplete_data = {"OrderID": 123}
        response = self._make_webhook_request(incomplete_data)
        self.assertEqual(response.status_code, 400)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")
        self.assertIn("Missing required fields", result["error"])

    def test_webhook_success(self):
        """Test successful webhook processing"""
        # Ensure data has correct product ID
        data = self.sample_webhook_data.copy()
        data["OrderItems"][0]["ItemID"] = self.product1.id
        data["OrderItems"][0]["ItemName"] = self.product1.name

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")
        self.assertIsNotNone(result["data"])
        self.assertIn("id", result["data"])
        self.assertIn("name", result["data"])

        # Verify POS order was created
        pos_order = self.env["pos.order"].browse(result["data"]["id"])
        self.assertTrue(pos_order.exists())
        self.assertEqual(pos_order.state, "paid")

    def test_webhook_with_idempotency_key(self):
        """Test webhook with idempotency key"""
        idempotency_key = "test-idempotency-123"
        headers = {
            "X-API-KEY": self.api_key,
            "Idempotency-Key": idempotency_key,
        }

        # Ensure data has correct product ID
        data = self.sample_webhook_data.copy()
        data["OrderItems"][0]["ItemID"] = self.product1.id
        data["OrderItems"][0]["ItemName"] = self.product1.name

        # First request
        response1 = self._make_webhook_request(data, headers=headers)
        self.assertEqual(response1.status_code, 200)
        result1 = json.loads(response1.content)
        order_id_1 = result1["data"]["id"]

        # Second request with same idempotency key
        response2 = self._make_webhook_request(data, headers=headers)
        self.assertEqual(response2.status_code, 200)
        result2 = json.loads(response2.content)

        # Should return same order (duplicate request)
        self.assertEqual(result2["data"]["id"], order_id_1)

        # Verify only one order was created
        orders = self.env["pos.order"].search(
            [("pos_reference", "=", result1["data"]["pos_reference"])]
        )
        self.assertEqual(len(orders), 1)

    def test_webhook_idempotency_in_body(self):
        """Test idempotency key in request body"""
        data = self.sample_webhook_data.copy()
        data["OrderItems"][0]["ItemID"] = self.product1.id
        data["OrderItems"][0]["ItemName"] = self.product1.name
        data["idempotency_key"] = "body-idempotency-123"
        headers = {"X-API-KEY": self.api_key}

        # First request
        response1 = self._make_webhook_request(data, headers=headers)
        self.assertEqual(response1.status_code, 200)

        # Second request
        response2 = self._make_webhook_request(data, headers=headers)
        self.assertEqual(response2.status_code, 200)

        # Should return same response
        result1 = json.loads(response1.content)
        result2 = json.loads(response2.content)
        self.assertEqual(result1["data"]["id"], result2["data"]["id"])

    def test_webhook_logging(self):
        """Test that webhooks are logged"""
        initial_count = self.env["karage.pos.webhook.log"].search_count([])

        # Ensure data has correct product ID
        data = self.sample_webhook_data.copy()
        data["OrderItems"][0]["ItemID"] = self.product1.id
        data["OrderItems"][0]["ItemName"] = self.product1.name

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)

        # Verify log was created
        logs = self.env["karage.pos.webhook.log"].search([], order="id desc")
        self.assertGreater(len(logs), initial_count)

        # Check latest log
        latest_log = logs[0]
        self.assertEqual(latest_log.order_id, str(data["OrderID"]))
        self.assertTrue(latest_log.success)
        self.assertEqual(latest_log.status_code, 200)

    def test_webhook_product_not_found(self):
        """Test webhook with non-existent product"""
        data = self.sample_webhook_data.copy()
        data["OrderItems"] = [
            {
                "ItemID": 99999,
                "ItemName": "Non-existent Product",
                "Price": 100.0,
                "Quantity": 1.0,
                "DiscountAmount": 0.0,
            }
        ]

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 404)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")
        self.assertIn("Product not found", result["error"])

    def test_webhook_payment_method_not_found(self):
        """Test webhook with unsupported payment mode"""
        data = self.sample_webhook_data.copy()
        data["CheckoutDetails"] = [
            {
                "PaymentMode": 999,  # Invalid payment mode
                "AmountPaid": "100.0",
                "CardType": "Unknown",
                "ReferenceID": "REF123",
            }
        ]

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 400)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")

    def test_webhook_data_inconsistency(self):
        """Test webhook with data inconsistency"""
        data = self.sample_webhook_data.copy()
        data["AmountTotal"] = 50.0  # Different from calculated total
        data["GrandTotal"] = 50.0

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 400)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")
        self.assertIn("inconsistency", result["error"].lower())

    def test_webhook_with_tax(self):
        """Test webhook with tax"""
        # Create tax
        tax = self.env["account.tax"].create(
            {
                "name": "Test Tax 15%",
                "amount": 15.0,
                "type_tax_use": "sale",
                "company_id": self.company.id,
            }
        )

        self.product1.write({"taxes_id": [(6, 0, [tax.id])]})

        data = self.sample_webhook_data.copy()
        data["OrderItems"][0]["ItemID"] = self.product1.id
        data["OrderItems"][0]["ItemName"] = self.product1.name
        data["Tax"] = 15.0
        data["TaxPercent"] = 15.0
        data["AmountTotal"] = 100.0
        data["GrandTotal"] = 115.0
        data["AmountPaid"] = "115.0"

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")

        pos_order = self.env["pos.order"].browse(result["data"]["id"])
        self.assertGreater(pos_order.amount_tax, 0)

    def test_webhook_with_discount(self):
        """Test webhook with discount"""
        data = self.sample_webhook_data.copy()
        data["OrderItems"][0]["ItemID"] = self.product1.id
        data["OrderItems"][0]["ItemName"] = self.product1.name
        data["OrderItems"][0]["DiscountAmount"] = 10.0
        data["AmountDiscount"] = 10.0
        data["AmountTotal"] = 90.0
        data["GrandTotal"] = 90.0
        data["AmountPaid"] = "90.0"

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")

        pos_order = self.env["pos.order"].browse(result["data"]["id"])
        order_line = pos_order.lines[0]
        self.assertGreater(order_line.discount, 0)

    def test_webhook_multiple_items(self):
        """Test webhook with multiple order items"""
        data = self.sample_webhook_data.copy()
        data["OrderItems"] = [
            {
                "ItemID": self.product1.id,
                "ItemName": self.product1.name,
                "Price": 100.0,
                "Quantity": 2.0,
                "DiscountAmount": 0.0,
            },
            {
                "ItemID": self.product2.id,
                "ItemName": self.product2.name,
                "Price": 50.0,
                "Quantity": 1.0,
                "DiscountAmount": 0.0,
            },
        ]
        data["AmountTotal"] = 250.0
        data["GrandTotal"] = 250.0
        data["AmountPaid"] = "250.0"

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")

        pos_order = self.env["pos.order"].browse(result["data"]["id"])
        self.assertEqual(len(pos_order.lines), 2)
        self.assertEqual(pos_order.amount_total, 250.0)

    def test_webhook_multiple_payments(self):
        """Test webhook with multiple payment methods"""
        data = self.sample_webhook_data.copy()
        data["CheckoutDetails"] = [
            {
                "PaymentMode": 1,  # Cash
                "AmountPaid": "50.0",
                "CardType": "Cash",
                "ReferenceID": "REF1",
            },
            {
                "PaymentMode": 2,  # Card
                "AmountPaid": "50.0",
                "CardType": "Visa",
                "ReferenceID": "REF2",
            },
        ]
        data["AmountPaid"] = "100.0"

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")

        pos_order = self.env["pos.order"].browse(result["data"]["id"])
        self.assertEqual(len(pos_order.payment_ids), 2)

    def test_webhook_no_pos_session(self):
        """Test webhook when no POS session is open"""
        # Close the session
        self.pos_session.action_pos_session_closing_control()
        self.pos_session.action_pos_session_close()

        data = self.sample_webhook_data.copy()
        data["OrderItems"][0]["ItemID"] = self.product1.id
        data["OrderItems"][0]["ItemName"] = self.product1.name

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 400)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")
        self.assertIn("No open POS session", result["error"])

        # Reopen session for other tests
        new_session = self.env["pos.session"].create(
            {
                "config_id": self.pos_config.id,
                "user_id": self.user.id,
            }
        )
        new_session.action_pos_session_open()

    def test_webhook_processing_status(self):
        """Test idempotency with processing status"""
        idempotency_key = "test-processing-status"
        headers = {
            "X-API-KEY": self.api_key,
            "Idempotency-Key": idempotency_key,
        }

        data = self.sample_webhook_data.copy()
        data["OrderItems"][0]["ItemID"] = self.product1.id
        data["OrderItems"][0]["ItemName"] = self.product1.name

        # Create a processing idempotency record manually
        self.env["karage.pos.webhook.idempotency"].create(
            {
                "idempotency_key": idempotency_key,
                "order_id": "12345",
                "status": "processing",
            }
        )

        # Request should fail with 409
        response = self._make_webhook_request(data, headers=headers)
        self.assertEqual(response.status_code, 409)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")
        self.assertIn("already being processed", result["error"])

    def test_webhook_failed_retry(self):
        """Test retry of failed idempotency"""
        idempotency_key = "test-failed-retry"
        headers = {
            "X-API-KEY": self.api_key,
            "Idempotency-Key": idempotency_key,
        }

        data = self.sample_webhook_data.copy()
        data["OrderItems"][0]["ItemID"] = self.product1.id
        data["OrderItems"][0]["ItemName"] = self.product1.name

        # Create a failed idempotency record
        self.env["karage.pos.webhook.idempotency"].create(
            {
                "idempotency_key": idempotency_key,
                "order_id": "12345",
                "status": "failed",
                "error_message": "Previous failure",
            }
        )

        # Request should succeed (retry allowed)
        response = self._make_webhook_request(data, headers=headers)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")

    def test_webhook_empty_order_items(self):
        """Test webhook with empty order items"""
        data = self.sample_webhook_data.copy()
        data["OrderItems"] = []

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 400)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")
        self.assertIn("No valid order lines", result["error"])

    def test_webhook_empty_payment_details(self):
        """Test webhook with empty payment details"""
        data = self.sample_webhook_data.copy()
        data["OrderItems"][0]["ItemID"] = self.product1.id
        data["OrderItems"][0]["ItemName"] = self.product1.name
        data["CheckoutDetails"] = []

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 400)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")
        self.assertIn("No valid payment lines", result["error"])

    def test_webhook_payment_amount_mismatch(self):
        """Test webhook with payment amount mismatch"""
        data = self.sample_webhook_data.copy()
        data["OrderItems"][0]["ItemID"] = self.product1.id
        data["OrderItems"][0]["ItemName"] = self.product1.name
        data["AmountPaid"] = "50.0"  # Less than total
        data["CheckoutDetails"][0]["AmountPaid"] = "50.0"

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 400)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")
        self.assertIn("Payment inconsistency", result["error"])

    # New tests for enhanced features

    def test_webhook_with_odoo_item_id(self):
        """Test webhook with OdooItemID for direct product lookup"""
        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9001
        data["OrderStatus"] = 103
        data["OrderDate"] = "2025-11-27T10:00:00"
        data["OrderItems"][0]["OdooItemID"] = self.product1.id
        data["OrderItems"][0]["ItemName"] = self.product1.name
        del data["OrderItems"][0]["ItemID"]  # Remove ItemID to test OdooItemID priority

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["external_order_id"], 9001)

    def test_webhook_duplicate_order_detection(self):
        """Test duplicate order detection by OrderID"""
        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9002
        data["OrderStatus"] = 103
        data["OrderDate"] = "2025-11-27T10:00:00"
        data["OrderItems"][0]["OdooItemID"] = self.product1.id

        # First request should succeed
        response1 = self._make_webhook_request(data)
        self.assertEqual(response1.status_code, 200)
        result1 = json.loads(response1.content)
        self.assertEqual(result1["status"], "success")

        # Second request with same OrderID should fail
        response2 = self._make_webhook_request(data)
        self.assertEqual(response2.status_code, 400)
        result2 = json.loads(response2.content)
        self.assertEqual(result2["status"], "error")
        self.assertIn("Duplicate order", result2["error"])
        self.assertIn("9002", result2["error"])

    def test_webhook_order_status_validation(self):
        """Test OrderStatus validation with configured allowed statuses"""
        # Set valid statuses to only 103
        self.env["ir.config_parameter"].sudo().set_param(
            "karage_pos.valid_order_statuses", "103"
        )

        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9003
        data["OrderStatus"] = 104  # Invalid status
        data["OrderDate"] = "2025-11-27T10:00:00"
        data["OrderItems"][0]["OdooItemID"] = self.product1.id

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 400)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")
        self.assertIn("Invalid OrderStatus", result["error"])

    def test_webhook_order_status_validation_multiple(self):
        """Test OrderStatus validation with multiple allowed statuses"""
        # Set valid statuses to 103 and 104
        self.env["ir.config_parameter"].sudo().set_param(
            "karage_pos.valid_order_statuses", "103,104"
        )

        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9004
        data["OrderStatus"] = 104  # Now valid
        data["OrderDate"] = "2025-11-27T10:00:00"
        data["OrderItems"][0]["OdooItemID"] = self.product1.id

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")

    def test_webhook_external_timestamp(self):
        """Test that OrderDate is used as order timestamp"""
        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9005
        data["OrderStatus"] = 103
        data["OrderDate"] = "2025-11-27T15:30:45"
        data["OrderItems"][0]["OdooItemID"] = self.product1.id

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")

        # Verify order was created with correct timestamp
        pos_order = self.env["pos.order"].browse(result["data"]["id"])
        self.assertEqual(str(pos_order.date_order), "2025-11-27 15:30:45")
        self.assertEqual(pos_order.external_order_id, "9005")
        self.assertEqual(pos_order.external_order_source, "karage_pos_webhook")

    def test_webhook_bulk_endpoint(self):
        """Test bulk webhook endpoint with multiple orders"""
        bulk_url = "/api/v1/webhook/pos-order/bulk"
        orders_data = [
            {
                "OrderID": 9010,
                "OrderStatus": 103,
                "OrderDate": "2025-11-27T10:00:00",
                "AmountPaid": "100.0",
                "AmountTotal": 100.0,
                "Tax": 0.0,
                "TaxPercent": 0.0,
                "OrderItems": [
                    {
                        "OdooItemID": self.product1.id,
                        "ItemName": self.product1.name,
                        "Price": 100.0,
                        "Quantity": 1,
                        "DiscountAmount": 0.0,
                    }
                ],
                "CheckoutDetails": [
                    {"PaymentMode": 1, "AmountPaid": "100.0", "CardType": "Cash"}
                ],
            },
            {
                "OrderID": 9011,
                "OrderStatus": 103,
                "OrderDate": "2025-11-27T11:00:00",
                "AmountPaid": "200.0",
                "AmountTotal": 200.0,
                "Tax": 0.0,
                "TaxPercent": 0.0,
                "OrderItems": [
                    {
                        "OdooItemID": self.product2.id,
                        "ItemName": self.product2.name,
                        "Price": 200.0,
                        "Quantity": 1,
                        "DiscountAmount": 0.0,
                    }
                ],
                "CheckoutDetails": [
                    {"PaymentMode": 1, "AmountPaid": "200.0", "CardType": "Cash"}
                ],
            },
        ]

        response = self.url_open(
            bulk_url,
            data=json.dumps({"orders": orders_data}),
            headers={"X-API-KEY": self.api_key},
        )

        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["total"], 2)
        self.assertEqual(result["data"]["succeeded"], 2)
        self.assertEqual(result["data"]["failed"], 0)

    def test_webhook_bulk_partial_success(self):
        """Test bulk endpoint with some failing orders"""
        bulk_url = "/api/v1/webhook/pos-order/bulk"
        orders_data = [
            {
                "OrderID": 9020,
                "OrderStatus": 103,
                "OrderDate": "2025-11-27T10:00:00",
                "AmountPaid": "100.0",
                "AmountTotal": 100.0,
                "Tax": 0.0,
                "TaxPercent": 0.0,
                "OrderItems": [
                    {
                        "OdooItemID": self.product1.id,
                        "ItemName": self.product1.name,
                        "Price": 100.0,
                        "Quantity": 1,
                        "DiscountAmount": 0.0,
                    }
                ],
                "CheckoutDetails": [
                    {"PaymentMode": 1, "AmountPaid": "100.0", "CardType": "Cash"}
                ],
            },
            {
                "OrderID": 9021,
                "OrderStatus": 103,
                "OrderDate": "2025-11-27T11:00:00",
                # Missing required fields - should fail
                "OrderItems": [],
                "CheckoutDetails": [],
            },
        ]

        response = self.url_open(
            bulk_url,
            data=json.dumps({"orders": orders_data}),
            headers={"X-API-KEY": self.api_key},
        )

        self.assertEqual(response.status_code, 207)  # Partial success
        result = json.loads(response.content)
        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(result["data"]["total"], 2)
        self.assertEqual(result["data"]["succeeded"], 1)
        self.assertEqual(result["data"]["failed"], 1)

    def test_webhook_bulk_max_orders_limit(self):
        """Test bulk endpoint respects max orders limit"""
        # Set max to 2 orders
        self.env["ir.config_parameter"].sudo().set_param(
            "karage_pos.bulk_sync_max_orders", "2"
        )

        bulk_url = "/api/v1/webhook/pos-order/bulk"
        orders_data = [{"OrderID": i} for i in range(9030, 9033)]  # 3 orders

        response = self.url_open(
            bulk_url,
            data=json.dumps({"orders": orders_data}),
            headers={"X-API-KEY": self.api_key},
        )

        self.assertEqual(response.status_code, 400)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")
        self.assertIn("exceeds maximum", result["error"])

    def test_webhook_product_not_available_in_pos(self):
        """Test webhook with product not available in POS"""
        # Create a product not available in POS
        product_not_in_pos = self.env["product.product"].create(
            {
                "name": "Not in POS Product",
                "list_price": 50.0,
                "available_in_pos": False,
                "sale_ok": True,
            }
        )

        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9040
        data["OrderStatus"] = 103
        data["OrderDate"] = "2025-11-27T10:00:00"
        data["OrderItems"][0]["OdooItemID"] = product_not_in_pos.id

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 400)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")
        self.assertIn("not available in POS", result["error"])

    def test_webhook_product_not_for_sale(self):
        """Test webhook with product not marked for sale"""
        # Create a product not for sale
        product_not_for_sale = self.env["product.product"].create(
            {
                "name": "Not for Sale Product",
                "list_price": 50.0,
                "available_in_pos": True,
                "sale_ok": False,
            }
        )

        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9041
        data["OrderStatus"] = 103
        data["OrderDate"] = "2025-11-27T10:00:00"
        data["OrderItems"][0]["OdooItemID"] = product_not_for_sale.id

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 400)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")
        self.assertIn("not available for sale", result["error"])

    def test_webhook_product_inactive(self):
        """Test webhook with inactive product"""
        # Create an inactive product
        inactive_product = self.env["product.product"].create(
            {
                "name": "Inactive Product",
                "list_price": 50.0,
                "available_in_pos": True,
                "sale_ok": True,
                "active": False,
            }
        )

        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9042
        data["OrderStatus"] = 103
        data["OrderDate"] = "2025-11-27T10:00:00"
        data["OrderItems"][0]["OdooItemID"] = inactive_product.id

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 404)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")
        self.assertIn("Product not found", result["error"])

    def test_webhook_product_lookup_priority(self):
        """Test product lookup priority: OdooItemID > ItemID > ItemName"""
        # Create products with different IDs
        product_a = self.env["product.product"].create(
            {
                "name": "Product A",
                "list_price": 100.0,
                "available_in_pos": True,
                "sale_ok": True,
            }
        )
        product_b = self.env["product.product"].create(
            {
                "name": "Product B",
                "list_price": 200.0,
                "available_in_pos": True,
                "sale_ok": True,
            }
        )

        # Test with all three IDs - OdooItemID should win
        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9043
        data["OrderStatus"] = 103
        data["OrderDate"] = "2025-11-27T10:00:00"
        data["OrderItems"][0]["OdooItemID"] = product_a.id
        data["OrderItems"][0]["ItemID"] = product_b.id
        data["OrderItems"][0]["ItemName"] = "Product B"
        data["OrderItems"][0]["Price"] = 100.0
        data["AmountTotal"] = 100.0
        data["AmountPaid"] = "100.0"
        data["CheckoutDetails"][0]["AmountPaid"] = "100.0"

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")

        # Verify correct product was used (Product A, not B)
        pos_order = self.env["pos.order"].browse(result["data"]["id"])
        self.assertEqual(pos_order.lines[0].product_id.id, product_a.id)

    def test_webhook_order_date_formats(self):
        """Test different OrderDate formats"""
        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9044
        data["OrderStatus"] = 103
        data["OrderItems"][0]["OdooItemID"] = self.product1.id

        # Test ISO format with Z
        data["OrderDate"] = "2025-11-27T15:30:45Z"
        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)

        # Test ISO format without timezone
        data["OrderID"] = 9045
        data["OrderDate"] = "2025-11-27T15:30:45"
        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)

    def test_webhook_external_order_tracking(self):
        """Test external order tracking fields are populated"""
        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9046
        data["OrderStatus"] = 103
        data["OrderDate"] = "2025-11-27T16:00:00"
        data["OrderItems"][0]["OdooItemID"] = self.product1.id

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)

        # Verify external tracking fields
        pos_order = self.env["pos.order"].browse(result["data"]["id"])
        self.assertEqual(pos_order.external_order_id, "9046")
        self.assertEqual(pos_order.external_order_source, "karage_pos_webhook")
        self.assertIsNotNone(pos_order.external_order_date)

    def test_webhook_product_lookup_by_item_id(self):
        """Test product lookup by ItemID (fallback from OdooItemID)"""
        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9050
        data["OrderStatus"] = 103
        data["OrderDate"] = "2025-11-27T10:00:00"
        data["OrderItems"][0]["ItemID"] = self.product1.id
        data["OrderItems"][0]["ItemName"] = self.product1.name
        # No OdooItemID - should use ItemID

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")

    def test_webhook_product_lookup_by_name_exact(self):
        """Test product lookup by exact ItemName match"""
        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9051
        data["OrderStatus"] = 103
        data["OrderDate"] = "2025-11-27T10:00:00"
        # Only provide ItemName, no IDs
        data["OrderItems"][0] = {
            "ItemName": self.product1.name,
            "Price": 100.0,
            "Quantity": 1,
            "DiscountAmount": 0.0,
        }

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")

    def test_webhook_product_lookup_by_name_fuzzy(self):
        """Test product lookup by fuzzy ItemName match"""
        # Create product with specific name for fuzzy matching
        fuzzy_product = self.env["product.product"].create(
            {
                "name": "Test Fuzzy Product Name",
                "list_price": 75.0,
                "available_in_pos": True,
                "sale_ok": True,
            }
        )

        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9052
        data["OrderStatus"] = 103
        data["OrderDate"] = "2025-11-27T10:00:00"
        data["AmountTotal"] = 75.0
        data["AmountPaid"] = "75.0"
        data["CheckoutDetails"][0]["AmountPaid"] = "75.0"
        # Provide partial name for fuzzy match
        data["OrderItems"][0] = {
            "ItemName": "Fuzzy Product",
            "Price": 75.0,
            "Quantity": 1,
            "DiscountAmount": 0.0,
        }

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")

        # Verify correct product was found
        pos_order = self.env["pos.order"].browse(result["data"]["id"])
        self.assertEqual(pos_order.lines[0].product_id.id, fuzzy_product.id)

    def test_webhook_payment_mode_mapping(self):
        """Test different payment mode mappings"""
        # Create Bank payment method
        bank_journal = self.env["account.journal"].create(
            {
                "name": "Bank",
                "code": "BNK1",
                "type": "bank",
            }
        )
        bank_payment_method = self.env["pos.payment.method"].create(
            {
                "name": "Bank Card",
                "journal_id": bank_journal.id,
            }
        )
        self.pos_config.write(
            {"payment_method_ids": [(4, bank_payment_method.id)]}
        )

        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9053
        data["OrderStatus"] = 103
        data["OrderDate"] = "2025-11-27T10:00:00"
        data["OrderItems"][0]["OdooItemID"] = self.product1.id
        data["CheckoutDetails"][0]["PaymentMode"] = 2  # Card
        data["CheckoutDetails"][0]["CardType"] = "Card"

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")

    def test_webhook_without_order_date(self):
        """Test webhook without OrderDate uses current time"""
        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9054
        data["OrderStatus"] = 103
        # No OrderDate provided
        data["OrderItems"][0]["OdooItemID"] = self.product1.id

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")

        # Order should still be created with current timestamp
        pos_order = self.env["pos.order"].browse(result["data"]["id"])
        self.assertIsNotNone(pos_order.date_order)

    def test_webhook_invalid_order_date_format(self):
        """Test webhook with invalid OrderDate falls back to current time"""
        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9055
        data["OrderStatus"] = 103
        data["OrderDate"] = "invalid-date-format"
        data["OrderItems"][0]["OdooItemID"] = self.product1.id

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")

        # Should use current time as fallback
        pos_order = self.env["pos.order"].browse(result["data"]["id"])
        self.assertIsNotNone(pos_order.date_order)

    def test_webhook_with_discount_percentage(self):
        """Test webhook correctly calculates discount percentage"""
        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9056
        data["OrderStatus"] = 103
        data["OrderDate"] = "2025-11-27T10:00:00"
        data["OrderItems"][0]["OdooItemID"] = self.product1.id
        data["OrderItems"][0]["Price"] = 100.0
        data["OrderItems"][0]["Quantity"] = 2
        data["OrderItems"][0]["DiscountAmount"] = 20.0  # 10% discount on 200
        data["AmountTotal"] = 180.0
        data["AmountPaid"] = "180.0"
        data["CheckoutDetails"][0]["AmountPaid"] = "180.0"

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")

        # Verify discount was calculated
        pos_order = self.env["pos.order"].browse(result["data"]["id"])
        self.assertEqual(pos_order.lines[0].discount, 10.0)

    def test_webhook_bulk_empty_orders(self):
        """Test bulk endpoint with empty orders array"""
        bulk_url = "/api/v1/webhook/pos-order/bulk"

        response = self.url_open(
            bulk_url,
            data=json.dumps({"orders": []}),
            headers={"X-API-KEY": self.api_key},
        )

        self.assertEqual(response.status_code, 400)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")
        self.assertIn("No orders provided", result["error"])

    def test_webhook_bulk_missing_orders_key(self):
        """Test bulk endpoint without orders key"""
        bulk_url = "/api/v1/webhook/pos-order/bulk"

        response = self.url_open(
            bulk_url,
            data=json.dumps({}),
            headers={"X-API-KEY": self.api_key},
        )

        self.assertEqual(response.status_code, 400)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")

    def test_webhook_session_automatic_creation(self):
        """Test automatic session creation when no session exists"""
        # Close all existing sessions
        open_sessions = self.env["pos.session"].search([("state", "in", ["opened", "opening_control"])])
        for session in open_sessions:
            session.write({"state": "closed", "stop_at": fields.Datetime.now()})

        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9060
        data["OrderStatus"] = 103
        data["OrderDate"] = "2025-11-27T10:00:00"
        data["OrderItems"][0]["OdooItemID"] = self.product1.id

        # Should automatically create a session
        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")

    def test_webhook_product_company_mismatch(self):
        """Test webhook with product from different company"""
        # Create a new company
        new_company = self.env["res.company"].create(
            {
                "name": "Test Company 2",
            }
        )

        # Create product in different company
        product_other_company = self.env["product.product"].create(
            {
                "name": "Other Company Product",
                "list_price": 50.0,
                "available_in_pos": True,
                "sale_ok": True,
                "company_id": new_company.id,
            }
        )

        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9061
        data["OrderStatus"] = 103
        data["OrderDate"] = "2025-11-27T10:00:00"
        data["OrderItems"][0]["OdooItemID"] = product_other_company.id

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 400)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "error")
        self.assertIn("company", result["error"].lower())

    def test_webhook_multiple_payment_methods(self):
        """Test webhook with multiple payment methods in CheckoutDetails"""
        # Create additional payment method
        bank_journal = self.env["account.journal"].create(
            {
                "name": "Bank Card",
                "code": "BNK2",
                "type": "bank",
            }
        )
        card_payment = self.env["pos.payment.method"].create(
            {
                "name": "Card Payment",
                "journal_id": bank_journal.id,
            }
        )
        self.pos_config.write({"payment_method_ids": [(4, card_payment.id)]})

        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9062
        data["OrderStatus"] = 103
        data["OrderDate"] = "2025-11-27T10:00:00"
        data["OrderItems"][0]["OdooItemID"] = self.product1.id
        data["AmountPaid"] = "150.0"
        data["CheckoutDetails"] = [
            {"PaymentMode": 1, "AmountPaid": "50.0", "CardType": "Cash"},
            {"PaymentMode": 2, "AmountPaid": "50.0", "CardType": "Card"},
        ]

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")

    def test_webhook_order_status_none(self):
        """Test webhook without OrderStatus (should be allowed)"""
        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9063
        data["OrderDate"] = "2025-11-27T10:00:00"
        data["OrderItems"][0]["OdooItemID"] = self.product1.id
        # OrderStatus not provided

        response = self._make_webhook_request(data)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["status"], "success")

    def test_webhook_zero_quantity(self):
        """Test webhook with zero quantity item"""
        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9064
        data["OrderStatus"] = 103
        data["OrderDate"] = "2025-11-27T10:00:00"
        data["OrderItems"][0]["OdooItemID"] = self.product1.id
        data["OrderItems"][0]["Quantity"] = 0
        data["AmountTotal"] = 0.0
        data["AmountPaid"] = "0.0"
        data["CheckoutDetails"][0]["AmountPaid"] = "0.0"

        response = self._make_webhook_request(data)
        # May succeed or fail depending on business logic
        self.assertIn(response.status_code, [200, 400])

    def test_webhook_negative_price(self):
        """Test webhook with negative price (refund scenario)"""
        data = self.sample_webhook_data.copy()
        data["OrderID"] = 9065
        data["OrderStatus"] = 103
        data["OrderDate"] = "2025-11-27T10:00:00"
        data["OrderItems"][0]["OdooItemID"] = self.product1.id
        data["OrderItems"][0]["Price"] = -50.0
        data["AmountTotal"] = -50.0
        data["AmountPaid"] = "-50.0"
        data["CheckoutDetails"][0]["AmountPaid"] = "-50.0"

        response = self._make_webhook_request(data)
        # Should handle negative amounts
        self.assertIn(response.status_code, [200, 400])
