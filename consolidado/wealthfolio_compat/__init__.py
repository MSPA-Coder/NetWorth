"""Contrato interno, read-only, para a superfície compatível com Wealthfolio.

Este pacote transforma envelopes publicados por Controle Bancário e Controle
Renda Variável em DTOs imutáveis. Ele não consulta banco, não grava nada e não
contém entidades espelho dos sistemas de origem.
"""

from .models import (
    AccountDTO,
    Capabilities,
    Coverage,
    FlowDTO,
    GainDTO,
    IncomeDTO,
    Money,
    PerformanceDTO,
    PositionDTO,
    SnapshotDTO,
)
from .normalize import CompatibilityError, normalize_payload, normalize_payloads

__all__ = [
    "AccountDTO",
    "Capabilities",
    "CompatibilityError",
    "Coverage",
    "FlowDTO",
    "GainDTO",
    "IncomeDTO",
    "Money",
    "PerformanceDTO",
    "PositionDTO",
    "SnapshotDTO",
    "normalize_payload",
    "normalize_payloads",
]
