# -*- coding: utf-8 -*-

from __future__ import annotations

from typing_extensions import override

from datetime import timedelta
import logging

from odoo import api, _
from odoo.models import fields
from odoo.addons.payment.models import payment_transaction, payment_token
from odoo.exceptions import ValidationError
from odoo.http import request

from monero import MoneroSubaddress, MoneroUtils

from ..const import ACCEPT_URL
from ..utils import MoneroExchangeRateConverter, MoneroExchangeRateConverterFactory

from .payment_provider import MoneroPaymentProvider

_logger = logging.getLogger(__name__)


class MoneroPaymentTransaction(payment_transaction.PaymentTransaction):
    _inherit = 'payment.transaction'
    _provider_key = 'monero'

    # missing
    id: str
    token_id: payment_token.PaymentToken

    # override
    provider_id: MoneroPaymentProvider

    #region Odoo Fields
    created_at = fields.Datetime(
        string="Created At", readonly=True, default=fields.Datetime.now)
    fully_paid = fields.Boolean(
        string="Fully Paid", help="Indicates if transaction is fully paid",
        default=False
    )
    currency_monero_id = fields.Many2one(
        'res.currency', string='Currency', required=True,
        default=lambda self: self.env['res.currency'].search([('name', '=', 'XMR')], limit=1).id
    )
    exchange_rate = fields.Monetary(
        string="Exchange Rate", currency_field='currency_id', readonly=True, required=True)
    amount_xmr = fields.Monetary(
        string="Amount XMR", currency_field='currency_monero_id', readonly=True, required=True)

    amount_remaining_xmr = fields.Monetary(
        string="Amount remaining to be paid XMR", currency_field='currency_monero_id', readonly=True, required=True)

    amount_paid_xmr = fields.Monetary(
        string="Amount paid XMR", currency_field='currency_monero_id', readonly=False, required=False,
        default=0
    )
    confirmations_required = fields.Integer(
        string="Number of network confirmations required", default = 0
    )
    expiration = fields.Datetime(string='Order Expiration Date', required=True, readonly=True, index=True, states={'draft': [('readonly', False)], 'sent': [('readonly', False)]}, copy=False, default=fields.Datetime.now, help="Expiration date of the order.")

    #endregion

    #region Private Methods

    def _cron_check_status(self):
        """
            Cron to send invoice that where not ready to be send directly after posting
        """
        self.env["sale.order"]

    def _set_listener(self, token: payment_token.PaymentToken | None = None) -> None:
        # set queue channel and max_retries settings
        # for queue depending on num conf settings
        num_conf_req = self.provider_id.get_num_confirmations_required()
        if num_conf_req == 0:
            queue_channel = "monero_zeroconf_processing"
            queue_max_retries = 44
        else:
            queue_channel = "monero_secure_processing"
            queue_max_retries = num_conf_req * 25

        # Add payment token and sale order to transaction processing queue
        _logger.warning("_set_listener(): request: {}".format(request))
        _logger.warning("_set_listener(): session: {}".format(request.session))
        last_order_id = request.session['sale_last_order_id']
        _logger.warning(f"_set_listener(): last_order_id: {last_order_id}")
        order = request.env['sale.order'].sudo().browse(last_order_id).exists()
        # order = request.website.sale_get_order()
        _logger.warning("order: {}".format(order))

        order.with_delay(
            channel=queue_channel, max_retries=queue_max_retries
        ).update_transaction(transaction=self, token=token, num_confirmation_required=num_conf_req)

        order.with_delay(
            channel=queue_channel, max_retries=queue_max_retries
        ).process_transaction(transaction=self, token=token, num_confirmation_required=num_conf_req)

    def _monero_tokenize_from_notification_data(self, data: dict) -> payment_token.PaymentToken:
        """ Create a token from feedback data.

            :param dict data: The feedback data sent by the provider
            :return: Token
            """
        _logger.warning("In tokenize")
        wallet_sub_address: MoneroSubaddress = self.provider_id.create_subaddress()
        _logger.warning("wallet_sub_address: {}".format(wallet_sub_address.address))
        _logger.warning("provider_id: {}".format(self.provider_id))
        #token_name = wallet_sub_address.__repr__()
        token_name = wallet_sub_address.address
        partner_id = self.partner_id.id # type: ignore
        token: payment_token.PaymentToken = self.env['payment.token'].create({
            'provider_ref': self.reference,
            'provider_id': self.provider_id.id,
            'payment_method_id': self.payment_method_id.id, # type: ignore
            'payment_details': token_name,  # Already padded with 'X's
            'partner_id': partner_id,
            'active': True, # The payment shall only be used once
        })
        self.write({
            'token_id': token.id, # type: ignore
            'tokenize': False,
        })
        self.token_id.toggle_active()
        _logger.info(
            "created token with id %s for partner with id %s", token.id, partner_id # type: ignore
        )

        return token

    def _get_rate_converter(self, provider=None) -> MoneroExchangeRateConverter:
        api_type = self.provider_id.get_exchange_rate_api() if provider is None else provider.get_exchange_rate_api()
        _logger.warning(f"--------- API TYPE {api_type}")
        
        return MoneroExchangeRateConverterFactory.create(api_type)
    
    #endregion

    #region Override Methods

    @override
    def _finalize_post_processing(self):
        super()._finalize_post_processing()

        self.is_post_processed = self.is_expired()

    @override
    def _get_specific_rendering_values(self, processing_values: dict) -> dict:
        """ Override of payment to return Transfer-specific rendering values.

        Note: self.ensure_one() from `_get_processing_values`

        :param dict processing_values: The generic and specific processing values of the transaction
        :return: The dict of provider-specific processing values
        :rtype: dict
        """

        _logger.warning("In Monero Transaction _get_specific_rendering_values")
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != self._provider_key:
            return res
        #         wallet = self.provider_id.get_wallet()
        return {
            'api_url': ACCEPT_URL,
            'reference': self.reference,
        }

    @override
    def _process_notification_data(self, notification_data: dict) -> None:
        """ Override of payment to process the transaction based on transfer data.

        Note: self.ensure_one()

        :param dict notification_data: The transfer notification data
        :return: None
        """
        _logger.warning("In _process_notification_data")
        _logger.warning("IDs: {}".format(self._ids))
        _logger.warning("References: {}".format(self.reference))
        super()._process_notification_data(notification_data)
        if self.provider_code != self._provider_key:
            return
        _logger.warning("data: {}".format(notification_data))
        _logger.info(
            "validated transfer payment for tx with reference %s: set as pending", self.reference
        )
        self._set_pending()
        token = self._monero_tokenize_from_notification_data(notification_data)
        self._set_listener(token=token)

    @api.model
    @override
    def _get_tx_from_notification_data(self, provider_code: str, notification_data: dict) -> MoneroPaymentTransaction:
        """ Override of payment to find the transaction based on transfer data.

        :param str provider: The provider of the provider that handled the transaction
        :param dict data: The transfer feedback data
        :return: The transaction if found
        :rtype: recordset of `payment.transaction`
        :raise: ValidationError if the data match no transaction
        """
        tx = super()._get_tx_from_notification_data(provider_code, notification_data)
        if provider_code != self._provider_key:
            return tx

        reference = notification_data.get('reference')
        tx = self.search([('reference', '=', reference), ('provider_id.code', '=', self._provider_key)])
        _logger.warning(tx)

        if not isinstance(tx, MoneroPaymentTransaction):
            raise ValidationError(
                "Monero Transaction: " + _("No transaction found matching reference %s.", reference)
            )
        return tx

    @api.model
    @override
    def create(self, vals):
        provider = self.env['payment.provider'].browse(vals['provider_id'])

        if 'exchange_rate' not in vals:
            try:
                vals['exchange_rate'] = self.get_current_exchange_rate(provider)
            except Exception as e:
                raise ValueError(f"Could not get exchange rate: {e}")
        if 'amount_xmr' not in vals:
            try:
                amount = vals['amount']
                amount_xmr = self.usd_to_xmr(amount, provider)
                if amount_xmr == 0 and amount > 0:
                    raise ValueError(f"Could not convert amount to xmr, converted amount is 0")
                
                vals['amount_xmr'] = amount_xmr
            except Exception as e:
                raise ValueError(f"Could not convert usd to xmr: {e}")
        
        if 'amount_paid_xmr' not in vals:
            vals['amount_paid_xmr'] = 0

        if 'amount_remaining_xmr' not in vals:
            vals['amount_remaining_xmr'] = vals['amount_xmr']

        if 'created_at' not in vals:
            vals['created_at'] = fields.Datetime.now()

        if 'expiration' not in vals:
            minutes = provider.get_payment_expiration()
            vals['expiration'] = vals['created_at'] + timedelta(minutes=minutes)

        return super().create(vals)

    #endregion

    #region Convenience Methods

    def get_amount(self) -> float:
        return float(self.amount) # type: ignore

    def get_amount_xmr(self) -> float:
        return float(self.amount_xmr) # type: ignore
    
    def get_amount_paid_xmr(self) -> float:
        return float(self.amount_paid_xmr) # type: ignore

    def get_amount_remaining_xmr(self) -> float:
        return float(self.amount_remaining_xmr) # type: ignore

    def get_amount_xmr_atomic_units(self) -> int:
        return MoneroUtils.xmr_to_atomic_units(self.get_amount_xmr())

    def get_amount_paid_xmr_atomic_units(self) -> int:
        return MoneroUtils.xmr_to_atomic_units(self.get_amount_paid_xmr())
    
    def get_amount_remaining_xmr_atomic_units(self) -> int:
        return MoneroUtils.xmr_to_atomic_units(self.get_amount_remaining_xmr())

    def get_confirmations_required(self) -> int:
        return int(self.confirmations_required) # type: ignore

    def is_fully_paid(self) -> bool:
        return bool(self.fully_paid)

    def get_decimal_places(self) -> float:
        return float(self.currency_id.decimal_places) # type: ignore

    def get_current_exchange_rate(self, provider=None) -> float:
        return self._get_rate_converter(provider).get_exchange_rate()

    def usd_to_xmr(self, usd: float, provider=None) -> float:
        return self._get_rate_converter(provider).usd_to_xmr(usd)
    
    def is_expired(self) -> bool:
        date_order = self.created_at
        if date_order is False:
            return False
        
        minutes = self.provider_id.get_payment_expiration()
        now = fields.Datetime.now()
        res = now - date_order > timedelta(minutes=minutes) # type: ignore
        _logger.warning(f"is_expired(): now: {str(now)}, date order, {str(date_order)}, minutes: {minutes}, expired: {res}")

        return now - date_order > timedelta(minutes=minutes) # type: ignore

    #endregion
