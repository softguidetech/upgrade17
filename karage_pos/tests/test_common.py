# -*- coding: utf-8 -*-


class KaragePosTestCommon:
    """Common test class for Karage POS module"""

    @classmethod
    def setup_common(cls):
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))

        # Create test company
        cls.company = cls.env["res.company"].create(
            {
                "name": "Test Company",
            }
        )

        # Create test user
        cls.user = cls.env["res.users"].create(
            {
                "name": "Test User",
                "login": "test_user",
                "email": "test@example.com",
                "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])],
            }
        )

        # Create test products
        cls.product1 = cls.env["product.product"].create(
            {
                "name": "Test Product 1",
                "type": "consu",
                "sale_ok": True,
                "available_in_pos": True,
                "list_price": 100.0,
            }
        )

        cls.product2 = cls.env["product.product"].create(
            {
                "name": "Test Product 2",
                "type": "consu",
                "sale_ok": True,
                "available_in_pos": True,
                "list_price": 50.0,
            }
        )

        # Create account for payment methods
        cls.account_receivable = cls.env["account.account"].create(
            {
                "name": "Test Receivable",
                "code": "TESTREC",
                "account_type": "asset_receivable",
            }
        )

        cls.account_cash = cls.env["account.account"].create(
            {
                "name": "Test Cash",
                "code": "TESTCASH",
                "account_type": "asset_cash",
            }
        )

        # Create payment journals
        cls.journal_cash = cls.env["account.journal"].create(
            {
                "name": "Cash",
                "type": "cash",
                "code": "CASH",
                "default_account_id": cls.account_cash.id,
            }
        )

        cls.journal_card = cls.env["account.journal"].create(
            {
                "name": "Card",
                "type": "bank",
                "code": "CARD",
                "default_account_id": cls.account_receivable.id,
            }
        )

        # Create POS config
        cls.pos_config = cls.env["pos.config"].create(
            {
                "name": "Test POS",
                "pricelist_id": cls.env["product.pricelist"]
                .create(
                    {
                        "name": "Test Pricelist",
                    }
                )
                .id,
            }
        )

        # Create payment methods
        cls.payment_method_cash = cls.env["pos.payment.method"].create(
            {
                "name": "Cash",
                "journal_id": cls.journal_cash.id,
                "is_cash_count": True,
            }
        )

        cls.payment_method_card = cls.env["pos.payment.method"].create(
            {
                "name": "Card",
                "journal_id": cls.journal_card.id,
            }
        )

        cls.pos_config.write(
            {
                "payment_method_ids": [
                    (6, 0, [cls.payment_method_cash.id, cls.payment_method_card.id])
                ],
            }
        )

        # Create POS session
        cls.pos_session = cls.env["pos.session"].create(
            {
                "config_id": cls.pos_config.id,
                "user_id": cls.user.id,
            }
        )
        cls.pos_session.action_pos_session_open()

        # Set Karage POS API key in system parameters
        cls.env['ir.config_parameter'].sudo().set_param('karage_pos.api_key', 'test_api_key_12345')
        cls.api_key = 'test_api_key_12345'

        # Sample webhook data
        cls.sample_webhook_data = {
            "OrderID": 12345,
            "AmountDiscount": 0.0,
            "AmountPaid": "100.0",
            "AmountTotal": 100.0,
            "BalanceAmount": 0.0,
            "GrandTotal": 100.0,
            "Tax": 0.0,
            "TaxPercent": 0.0,
            "OrderStatus": 103,
            "PaymentMode": 1,
            "CheckoutDetails": [
                {
                    "PaymentMode": 1,
                    "AmountPaid": "100.0",
                    "CardType": "Cash",
                    "ReferenceID": "REF123",
                }
            ],
            "OrderItems": [
                {
                    "ItemID": cls.product1.id,
                    "ItemName": cls.product1.name,
                    "Price": 100.0,
                    "Quantity": 1.0,
                    "DiscountAmount": 0.0,
                }
            ],
        }
