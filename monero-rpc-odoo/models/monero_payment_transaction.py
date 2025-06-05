# -*- coding: utf-8 -*-

from __future__ import annotations

from typing_extensions import override

import logging

from odoo import api, _
from odoo.models import fields
from odoo.exceptions import ValidationError
from odoo.addons.payment.models import payment_transaction, payment_token
from odoo.http import request

from monero import MoneroSubaddress, MoneroUtils

from ..controllers.monero_controller import MoneroController
from ..utils import MoneroExchangeRateConverter, MoneroKrakenRateConverter

from .payment_provider import MoneroPaymentProvider

_logger = logging.getLogger(__name__)


class MoneroPaymentTransaction(payment_transaction.PaymentTransaction):
    _inherit = 'payment.transaction'
    _provider_key = 'monero'
    _rate_converter: MoneroExchangeRateConverter = MoneroKrakenRateConverter()

    # missing
    id: str

    # override
    provider_id: MoneroPaymentProvider
    token_id: payment_token.PaymentToken

    #region Odoo Fields

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
        _logger.warning("provider_id: {}".format(self.provider_id.id))
        #token_name = wallet_sub_address.__repr__()
        token_name = wallet_sub_address.address
        partner_id = self.partner_id.id # type: ignore
        _logger.warning("BEFORE CREATE TOKEN")
        token: payment_token.PaymentToken = self.env['payment.token'].create({
            'provider_ref': self.reference,
            'provider_id': self.provider_id.id,
            'payment_method_id': self.payment_method_id.id, # type: ignore
            'payment_details': token_name,  # Already padded with 'X's
            'partner_id': partner_id,
            'active': True, # The payment shall only be used once
        })
        _logger.warning(f"AFTER CREATE TOKEN: self.token_id: {str(self.token_id)}, self.toke_id.id {str(self.token_id.id)}, token: {token}, token id {token.id}")
        self.write({
            'token_id': token.id,
            'tokenize': False,
        })
        if self.token_id.active:
            self.token_id.toggle_active()
    
        _logger.info(
            "created token with id %s for partner with id %s", token.id, partner_id
        )

        return token

    #endregion

    #region Override Methods

    @override
    def _get_processing_values(self) -> dict:
        _logger.warning(f"MoneroPaymentTransaction._get_processing_values(): operation: {str(self.operation)}")
        values = super()._get_processing_values()
        return values

    @override
    def _get_specific_rendering_values(self, processing_values: dict) -> dict:
        """ Override of payment to return Transfer-specific rendering values.

        Note: self.ensure_one() from `_get_processing_values`

        :param dict processing_values: The generic and specific processing values of the transaction
        :return: The dict of provider-specific processing values
        :rtype: dict
        """

        _logger.warning(f"In Monero Transaction _get_specific_rendering_values: provider code {str(self.provider_code)}, provider key {str(self._provider_key)}")
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != self._provider_key:
            return res
        return {
            'api_url': MoneroController._accept_url,
            'reference': self.reference,
            #             'wallet_address': wallet.new_address()[0],
        }

    @override
    def _process_notification_data(self, notification_data: dict, order_id=None) -> None:
        """ Override of payment to process the transaction based on transfer data.

        Note: self.ensure_one()

        :param dict data: The transfer feedback data
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
        #self._set_authorized()
        token = self._monero_tokenize_from_notification_data(notification_data)
        self._set_listener(token=token)

    @api.model
    @override
    def _get_tx_from_notification_data(self, provider_code: str, notification_data: dict) -> MoneroPaymentTransaction:
        """ Override of payment to find the transaction based on transfer data.

        :param str provider_code: The provider of the acquirer that handled the transaction
        :param dict notification_data: The transfer feedback data
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
        if 'exchange_rate' not in vals:
            try:
                vals['exchange_rate'] = self.get_current_exchange_rate()
            except Exception as e:
                raise ValueError(f"Could not get exchange rate: {e}")
        if 'amount_xmr' not in vals:
            try:
                vals['amount_xmr'] = self.usd_to_xmr(vals['amount'])
            except Exception as e:
                raise ValueError(f"Could not convert usd to xmr: {e}")
        
        if 'amount_paid_xmr' not in vals:
            vals['amount_paid_xmr'] = 0

        if 'amount_remaining_xmr' not in vals:
            vals['amount_remaining_xmr'] = vals['amount_xmr']

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

    def get_current_exchange_rate(self) -> float:
        return self._rate_converter.get_exchange_rate()

    def usd_to_xmr(self, usd: float) -> float:
        return self._rate_converter.usd_to_xmr(usd)

    #endregion
