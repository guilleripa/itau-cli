import asyncio
import csv
import datetime
import json
import logging
import re
from pathlib import Path
from urllib.parse import urlencode

import aiohttp
import requests
from dateutil.relativedelta import relativedelta
from lxml import html

logger = logging.getLogger("itau.client")

ITAU_DOMAIN = "https://www.itaulink.com.uy"


class ItauClient:

    LOGIN_URL = ITAU_DOMAIN + "/trx/doLogin"
    MAIN_URL = ITAU_DOMAIN + "/trx/home"
    HISTORY_ACCOUNT_URL = (
        ITAU_DOMAIN + "/trx/cuentas/{type}/{hash}/{month}/{year}/consultaHistorica"
    )
    TRANSACTION_DETAIL_URL = (
        ITAU_DOMAIN
        + "/trx/cuentas/{type}/{id}/{hash}/{day}/{month}/{year}/cargarComprobante"
    )
    CURRENT_ACCOUNT_URL = ITAU_DOMAIN + "/trx/cuentas/{type}/{hash}/mesActual"
    CREDIT_CARD_URL = ITAU_DOMAIN + "/trx/tarjetas/credito"
    CREDIT_CARD_MOV_URL = (
        ITAU_DOMAIN + "/trx/tarjetas/credito/{}/movimientos_actuales/{}"
    )

    ACCOUNT_TYPES = {
        "savings_account": "caja_de_ahorro",
        "transactional_account": "cuenta_corriente",
        "collections_account": "cuenta_recaudadora",
        "junior_savings_account": "cuenta_de_ahorro_junior",
    }

    CURRENCIES = {
        "URGP": {"iso": "UYU", "display": "$"},
        "US.D": {"iso": "USD", "display": "U$S"},
    }

    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.login()

    def parse_accounts(self, accounts):
        accounts_json = accounts["cuentas"]
        self.accounts = []
        for account_type, account_key in self.ACCOUNT_TYPES.items():
            for account_json in accounts_json.get(account_key, []):
                currency = self.CURRENCIES[account_json["moneda"]]
                cleaned_account = {
                    "type": account_type,
                    "currency_id": currency["iso"],
                    "currency_display": currency["display"],
                    "id": account_json["idCuenta"],
                    "name": account_json["nombreTitular"],
                    "hash": account_json["hash"],
                    "balance": account_json["saldo"],
                    "account_type_id": account_json["tipoCuenta"],
                    "original": account_json,
                }
                self.accounts.append(cleaned_account)

    def parse_date(self, date_json):
        return datetime.date(
            date_json["year"], date_json["monthOfYear"], date_json["dayOfMonth"]
        )

    def parse_credit_cards(self, ccs_json):
        ccs_json = ccs_json["itaulink_msg"]["data"]["objetosTarjetaCredito"][
            "tarjetaImagen"
        ]

        self.credit_cards = []
        for cc_json, image in ccs_json:
            cleaned_cc = {
                "brand": cc_json["sello"],
                "number": (
                    cc_json["nroTarjetaTitular"][:4]
                    + "X" * 8
                    + cc_json["nroTarjetaTitular"][-4:]
                ),
                "expiration_date": self.parse_date(cc_json["fechaVencimiento"]),
                "name": cc_json["nombreTitular"],
                "id": cc_json["id"],
                "hash": cc_json["hash"],
            }

            self.credit_cards.append(cleaned_cc)

    def parse_transaction_details(self, tx_detail):
        return tx_detail["itaulink_msg"]["data"]["form"]["beneficiario"].split(None, 1)[
            -1
        ]

    def get_transaction_details(self, tx, account, month_date):
        url = self.TRANSACTION_DETAIL_URL.format(
            type=account["account_type_id"],
            id=tx["description"],
            hash=account["hash"],
            day=tx["date"].strftime("%d"),
            month=tx["date"].strftime("%b").upper(),
            year=tx["date"].strftime("%Y"),
        )
        try:
            payload = bytes("{}", "utf-8")
            cookies = dict(self.cookies)
            r = requests.post(
                url,
                data=payload,
                headers={"Accept": "application/json, text/javascript, */*; q=0.01"},
                cookies=cookies,
            )
            return self.parse_transaction_details(r.json())
        except Exception as e:
            logger.debug(
                "Error fetching {} details. Ignoring".format(month_date.isoformat()[:8])
            )
            return ""

    def parse_transaction(self, raw_tx, account, month_date):
        if raw_tx["tipo"] == "D":
            transaction_type = "debit"
        elif raw_tx["tipo"] == "C":
            transaction_type = "credit"
        else:
            logger.warning("Invalid trasaction type: {}".format(raw_tx))
            return

        tx = {
            "description": " ".join(raw_tx["descripcion"].split()),
            "additional_description": " ".join(raw_tx["descripcionAdicional"].split()),
            "type": transaction_type,
            "amount": raw_tx["importe"],
            "balance": raw_tx["saldo"],
            "date": self.parse_date(raw_tx["fecha"]),
            "meta": {},
        }

        if "DEB. CAMBIOSS" in tx["description"]:
            tx["meta"]["beneficiary"] = self.get_transaction_details(
                tx, account, month_date
            )

        if tx["description"].startswith("COMPRA "):
            # Debit card purchase
            tx["meta"]["debit_card_purchase"] = True

        if tx["description"].startswith("RETIRO "):
            # ATM
            tx["meta"]["atm"] = True
            tx["description"] = "RETIRO BANRED"

        if tx["description"].startswith("DEBITO BANKING CARD"):
            tx["meta"]["bank_costs"] = True

        if tx["description"].startswith("TRASPASO DE"):
            tx["meta"]["bank_transfer"] = True
            tx["meta"]["bank_transfer_from"] = self.only_num(tx["description"])

        if tx["description"].startswith("TRASPASO A"):
            tx["meta"]["bank_transfer"] = True
            tx["meta"]["bank_transfer_to"] = self.only_num(tx["description"])

        if tx["description"].startswith("REDIVA 1921"):
            tx["meta"]["tax_return"] = True

        return tx

    def only_num(self, txt):
        return re.sub("[^0-9]", "", txt)

    def parse_transactions(self, details_json, account, month_date):
        transactions = []
        data = details_json["itaulink_msg"]["data"]
        if "mapaHistoricos" in data:
            movements = data["mapaHistoricos"]["movimientosHistoricos"]["movimientos"]
        elif "movimientosMesActual" in data:
            movements = data["movimientosMesActual"]["movimientos"]

        for raw_transaction in movements:
            tx = self.parse_transaction(raw_transaction, account, month_date)
            if tx:
                transactions.append(tx)

        return transactions

    def parse_cc_movements(self, cc_mov_json):
        movements = []
        json_movs = cc_mov_json["itaulink_msg"]["data"]["datosMovs"]["movimientos"]
        for json_mov in json_movs:
            if json_mov["moneda"].lower() in ["Dolares", "dolares"]:
                currency_id = "USD"
            elif json_mov["moneda"].lower() == "pesos":
                currency_id = "UYU"
            else:
                logger.warning(
                    "Unknown currency {} from {}".format(json_mov["moneda"], json_mov)
                )
                continue

            mov = {
                "type": json_mov["tipo"].lower().strip(),
                "description": " ".join(json_mov["nombreComercio"].split()),
                "date": self.parse_date(json_mov["fecha"]),
                "amount": json_mov["importe"],
                "currency": currency_id,
                "coupon_id": json_mov["idCupon"],
                "meta": {},
            }

            if mov["type"] == "recibo de pago":
                continue

            if mov["amount"] < 0:
                mov["type"] = "credit"
                mov["amount"] *= -1
            else:
                mov["type"] = "debit"

            if mov["description"].startswith("REDUC. IVA LEY") or mov[
                "description"
            ].startswith("DEVOLUCION DE IVA LEY"):
                mov["meta"]["tax_return"] = True

            if mov["description"].startswith("COSTO DE TARJETA"):
                mov["meta"]["bank_costs"] = True

            if mov["description"].startswith("SEGURO DE VIDA SOBRE SALDO"):
                mov["meta"]["life_insurance"] = True

            movements.append(mov)

        return movements

    def get_credit_cards(self):
        try:
            r = requests.post(self.CREDIT_CARD_URL, cookies=self.cookies)
            credit_cards_json = r.json()
            self.parse_credit_cards(credit_cards_json)
        except Exception as e:
            self.credit_cards = []

        logger.debug("Found {} credit cards.".format(len(self.credit_cards)))

        for cc in self.credit_cards:
            from_date = datetime.date(2012, 5, 1)

            today = datetime.date.today()
            movements = []

            tasks = []
            while today > from_date:
                tasks.append(self.get_month_credit_card(cc, today))
                today -= relativedelta(months=1)

            loop = asyncio.get_event_loop()
            monthly_movements = loop.run_until_complete(asyncio.gather(*tasks))

            for month_movements in monthly_movements:
                movements.extend(month_movements)

            by_currency_id = {}
            for mov in sorted(movements, key=lambda x: x["date"]):
                by_currency_id.setdefault(mov["currency"], []).append(mov)

            cc["movements"] = by_currency_id

    async def get_month_credit_card(self, cc, month_date):
        today = datetime.date.today()
        if month_date.month == today.month and month_date.year == today.year:
            url_code = "00000000"
        else:
            url_code = month_date.strftime("%Y%m01")

        logger.debug(
            "Fetching month={}-{} for {}".format(
                month_date.year, month_date.month, cc["number"]
            )
        )

        url = self.CREDIT_CARD_MOV_URL.format(cc["hash"], url_code)

        try:
            cookies = dict(self.cookies)
            async with aiohttp.ClientSession(cookies=cookies) as session:
                async with session.post(url) as r:
                    movements_json = await r.json()
                    return self.parse_cc_movements(movements_json)
        except Exception as e:
            logger.debug(
                "Error fetching {}. {}, Ignoring".format(month_date.isoformat()[:10], e)
            )
            return []

    async def get_month_account_details(self, account, month_date):
        today = datetime.date.today()
        if month_date.month == today.month and month_date.year == today.year:
            url = self.CURRENT_ACCOUNT_URL.format(
                type=account["account_type_id"],
                hash=account["hash"],
            )
        else:
            url = self.HISTORY_ACCOUNT_URL.format(
                type=account["account_type_id"],
                hash=account["hash"],
                month=month_date.strftime("%m"),
                year=month_date.strftime("%y"),
            )

        logger.debug(
            "Fetching month={}-{} for {}".format(
                month_date.year, month_date.month, account["id"]
            )
        )

        payload = "0:{}:{}:{}-{}:".format(
            account["original"]["moneda"],
            account["hash"],
            today.strftime("%m"),
            today.strftime("%y"),
        )
        try:
            payload = bytes(payload, "utf-8")
            cookies = dict(self.cookies)
            async with aiohttp.ClientSession(
                cookies=cookies,
                headers={"Accept": "application/json, text/javascript, */*; q=0.01"},
            ) as session:
                async with session.post(
                    url,
                    data=payload,
                ) as r:
                    trans_json = await r.json()
                    return self.parse_transactions(trans_json, account, month_date)
        except Exception as e:
            logger.debug(
                "Error fetching {}. Ignoring".format(month_date.isoformat()[:8])
            )
            return []

    def account_detail(self, account, from_date=None):
        if not from_date:
            from_date = datetime.date(2016, 5, 1)

        today = datetime.date.today()
        transactions = []

        tasks = []
        while today > from_date:
            tasks.append(self.get_month_account_details(account, today))
            today -= relativedelta(months=1)

        loop = asyncio.get_event_loop()
        monthly_transactions = loop.run_until_complete(asyncio.gather(*tasks))

        for month_transactions in monthly_transactions:
            transactions.extend(month_transactions)

        return transactions

    def save(self, path="."):
        path = Path(path)
        for account in self.accounts:
            filename = "{}-{}.csv".format(account["id"], account["currency_id"])
            with open(path / filename, "w") as f:
                writer = csv.writer(f, delimiter="\t")
                writer.writerow(
                    [
                        "account",
                        "currency",
                        "date",
                        "description",
                        "additional_description",
                        "type",
                        "debit",
                        "credit",
                        "balance",
                        "debit card purchase",
                        "atm",
                        "bank transfer",
                        "tax return",
                        "beneficiary",
                    ]
                )
                for tx in account["transactions"]:
                    debit = ""
                    credit = ""
                    amount = "{:.2f}".format(tx["amount"])
                    if tx["type"] == "debit":
                        debit = amount
                    elif tx["type"] == "credit":
                        credit = amount

                    writer.writerow(
                        [
                            account["id"],
                            account["currency_id"],
                            tx["date"].isoformat(),
                            tx["description"],
                            tx["additional_description"],
                            tx["type"],
                            debit,
                            credit,
                            tx["balance"],
                            tx["meta"].get("debit_card_purchase"),
                            tx["meta"].get("atm"),
                            tx["meta"].get("bank_transfer"),
                            tx["meta"].get("tax_return"),
                            tx["meta"].get("beneficiary"),
                        ]
                    )

        for cc in self.credit_cards:
            for currency, movements in cc["movements"].items():
                filename = "{}-{}-{}.csv".format(cc["brand"], currency, cc["number"])
                with open(path / filename, "w") as f:
                    writer = csv.writer(f, delimiter="\t")
                    writer.writerow(
                        [
                            "coupon",
                            "currency",
                            "date",
                            "description",
                            "type",
                            "debit",
                            "credit",
                            "tax return",
                            "bank costs",
                            "life insurance",
                        ]
                    )

                    for mov in movements:
                        debit = ""
                        credit = ""
                        amount = "{:.2f}".format(mov["amount"])
                        if mov["type"] == "debit":
                            debit = amount
                        elif mov["type"] == "credit":
                            credit = amount

                        writer.writerow(
                            [
                                mov["coupon_id"],
                                mov["currency"],
                                mov["date"].isoformat(),
                                mov["description"],
                                mov["type"],
                                debit,
                                credit,
                                mov["meta"].get("tax_return"),
                                mov["meta"].get("bank_costs"),
                                mov["meta"].get("life_insurance"),
                            ]
                        )

    def login(self):
        data = {
            "segmento": "panelPersona",
            "empresa_aux": self.username,
            "pwd_empresa": self.password,
            "usuario_aux": "",
            "tipo_documento": 1,
            "nro_documento": self.username,
            "pass": self.password,
            "password": self.password,
            "pwd_usuario": "",
            "empresa": "",
            "usuario": "",
            "id": "login",
            "tipo_usuario": "R",
        }

        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "es-ES,es;q=0.8,en;q=0.6,pt;q=0.4",
            "Cache-Control": "max-age=0",
            "Connection": "keep-alive",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.itau.com.uy",
            "Referer": "https://www.itau.com.uy/inst/",
            "Upgrade-Insecure-Requests": "1",
        }

        login_data = urlencode(data)

        r = requests.post(self.LOGIN_URL, data=login_data, headers=headers)

        self.cookies = r.history[0].cookies

        accounts = json.loads(
            re.search(
                r"var mensajeUsuario = JSON.parse\(\'(.*?)\'", r.text.replace("\n", "")
            ).group(1)
        )

        self.parse_accounts(accounts)
        self.get_credit_cards()

        logger.info("{} accounts found.".format(len(self.accounts)))
        for account in self.accounts:
            logger.info(
                "{} {} in {} - {} {:.2f}".format(
                    account["id"],
                    account["type"],
                    account["currency_id"],
                    account["currency_display"],
                    account["balance"],
                )
            )

        total_transactions = 0
        for account in self.accounts:
            account["transactions"] = sorted(
                self.account_detail(account), key=lambda x: x["date"]
            )
            total_transactions += len(account["transactions"])

        logger.info(
            "Downloaded {} transactions from {} accounts.".format(
                total_transactions, len(self.accounts)
            )
        )
        for account in self.accounts:
            logger.info(
                "{} {} in {} - {} transactions".format(
                    account["id"],
                    account["type"],
                    account["currency_id"],
                    len(account["transactions"]),
                )
            )
            logger.info(
                "{:10s} | {:24s} | {:30s} | {:8s} | {:8s} | {:8s}".format(
                    "date",
                    "description",
                    "additional description",
                    "beneficiary",
                    "debit",
                    "credit",
                    "balance",
                )
            )

            for tx in account["transactions"]:
                debit = ""
                credit = ""
                amount = "{:.2f}".format(tx["amount"])
                if tx["type"] == "debit":
                    debit = amount
                elif tx["type"] == "credit":
                    credit = amount

                logger.info(
                    "{:10s} | {:24s} | {:30s} | {:8s} | {:8s} | {:8s} | {:8s}".format(
                        tx["date"].isoformat(),
                        tx["description"],
                        tx["additional_description"],
                        tx["meta"].get("beneficiary", ""),
                        debit,
                        credit,
                        "{:.2f}".format(tx["balance"]),
                    )
                )
