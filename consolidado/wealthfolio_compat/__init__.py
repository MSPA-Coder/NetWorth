"""Contrato interno, read-only, para a superfície compatível com Wealthfolio.

Este pacote transforma envelopes publicados por Controle Bancário e Controle
Renda Variável em DTOs imutáveis. Ele não consulta banco, não grava nada e não
contém entidades espelho dos sistemas de origem.
"""

from .adapters import (
    CompositionResult,
    SourceResult,
    SourceStatus,
    adapt_controle_bancario,
    adapt_payload,
    adapt_renda_variavel,
    adapt_response,
    compose_results,
)
from .contracts import (
    Account,
    Activity,
    CapabilitySet,
    ChartSeries,
    DeepLink,
    Holding,
    PerformancePoint,
    RealizedGain,
    SourceSnapshot,
)
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
from .transport import ReadOnlyTransport, TransportError, TransportResponse, response_from_payload

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
    "SourceSnapshot",
    "Account",
    "Holding",
    "Activity",
    "RealizedGain",
    "CapabilitySet",
    "DeepLink",
    "PerformancePoint",
    "ChartSeries",
    "SourceResult",
    "CompositionResult",
    "SourceStatus",
    "adapt_payload",
    "adapt_response",
    "adapt_controle_bancario",
    "adapt_renda_variavel",
    "compose_results",
    "ReadOnlyTransport",
    "TransportError",
    "TransportResponse",
    "response_from_payload",
    "normalize_payload",
    "normalize_payloads",
]
