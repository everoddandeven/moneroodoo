import logging

from typing import override

from odoo import http
from odoo.http import request
from odoo.exceptions import ValidationError
from odoo.addons.website_sale.controllers.main import WebsiteSale
from odoo.addons.sale.models.sale_order import SaleOrder

from monero import MoneroSubaddress

_logger = logging.getLogger(__name__)


class MoneroWebsiteSale(WebsiteSale):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @http.route(['/shop/confirmation'], type='http', auth="public", website=True)
    @override
    def shop_payment_confirmation(self, **post):
        _logger.warning(f"")
        order = request.website.sale_get_order()

        if not order or order.state != 'sale':
            return request.redirect('/shop')

        # Odoo 17: usa order.transaction_ids per recuperare la transazione attiva
        payment_tx = order.transaction_ids.filtered(lambda tx: tx.state in ('done', 'authorized', 'pending', ''))[:1]

        return request.render("website_sale.payment_confirmation_status", {
            'order': order,
            'payment_tx': payment_tx,  # <-- la chiave giusta per il template
        })

    @http.route(
        ["/shop/payment"], type="http", auth="public", website=True, sitemap=False
    )
    @override
    def shop_payment(self, **post):
        """
        OVERRIDING METHOD FROM
        odoo/addons/website_sale/controllers/main.py
        Payment step. This page proposes several
        payment means based on available
        payment.provider. State at this point :
         - a draft sales order with lines; otherwise, clean context / session and
           back to the shop
         - no transaction in context / session, or only a draft one, if the customer
           did go to a payment.provider website but closed the tab without
           paying / canceling
        """
        _logger.info("MoneroWebSiteSale: In Payment")
        order: SaleOrder = request.website.sale_get_order()
        redirection = self.checkout_redirection(order)
        if redirection:
            return redirection

        render_values = self._get_shop_payment_values(order, **post)
        render_values["display_submit_button"] = True
        render_values["only_services"] = order and order.only_services or False # type: ignore
        
        _logger.info(f"render values: {str(render_values)}")

        for provider in render_values["providers_sudo"]:
            _logger.info(f"Provider code {provider.code}")
            if "monero" == str(provider.code):
                subaddress: MoneroSubaddress | None = None
                try:
                    subaddress = provider.create_subaddress()
                except Exception as e:
                    _logger.error(
                        f"USER IMPACT: Monero Payment Provider "
                        f"experienced an Error with RPC: {e.__class__.__name__}"
                    )
                    raise ValidationError(
                        "Current technical issues "
                        "prevent Monero from being accepted, "
                        "choose another payment method"
                    )

                if subaddress is None:
                    raise ValidationError(
                        "Could not get an address to recevei payment order"
                    )

                request.wallet_address = subaddress.address
                _logger.info(f"new monero payment subaddress generated {subaddress.address}")

        if render_values["errors"]:
            render_values.pop("providers_sudo", "")
            render_values.pop("tokens_sudo", "")

        return request.render("website_sale.payment", render_values)
