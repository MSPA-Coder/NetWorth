from decimal import Decimal

import pytest

from consolidado.wealthfolio_compat import Money
from consolidado.wealthfolio_compat.models import DTOError


def test_money_e_serializacao_preservam_decimal_e_moeda():
    money = Money("10.10", "brl")

    assert money.amount == Decimal("10.10")
    assert money.currency == "BRL"
    assert money.to_wire() == {"amount": "10.10", "currency": "BRL"}


def test_money_recusa_float_nao_finito():
    with pytest.raises(DTOError):
        Money(float("nan"), "BRL")
