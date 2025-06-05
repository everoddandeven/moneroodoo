{
    "name": "Monero RPC",
    "summary": "A free and open source payment gateway to accept online Monero payments.",
    "author": "Monero Integrations",
    "website": "https://monerointegrations.com/",
    # Categories can be used to filter modules in modules listing
    # for the full list
    "category": "Accounting",
    "version": "16.0",
    "license": "AGPL-3",
    # any module necessary for this one to work correctly
    "depends": [
        "website_sale",
        "website_payment",
        "website",
        "payment",
        "base_setup",
        "web",
        "queue_job",
    ],
    "external_dependencies": {"python": ["monero"]},
    # always loaded
    "data": [
        "views/payment_monero_templates.xml",
        "views/payment_provider_views.xml",

        "data/payment_method_data.xml",
        "data/payment_provider_data.xml",
        "data/currency.xml",
        "data/queue.xml",
    ],
    # only loaded in demonstration mode
    # TODO add demo data
    "demo": [
        "demo/demo.xml",
    ],
    "installable": True,
    "application": True,
    "classifiers": ["License :: OSI Approved :: MIT License"],
} # type: ignore
