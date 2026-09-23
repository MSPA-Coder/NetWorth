"""Camada de leitura e view-models da superfície do shell.

A foto patrimonial chega pelo `consolidado.leitor` (`Consolidado`) e vira
view-models em `view_models`. Os recursos v3 que não cabem na foto --
atividades individuais e analíticos (renda, desempenho, eventos) -- são lidos
aqui mesmo, em `activities` e `analytics`, como DTOs imutáveis. Nada neste
pacote consulta banco, grava nas fontes ou mantém cópia das entidades delas.
"""

from .activities import (
    ActivityComposition,
    ActivitySourceResult,
    compose_activities,
    fetch_activities,
    normalize_activities_payload,
)
from .analytics import (
    AnalyticsComposition,
    AnalyticsSourceResult,
    EventRecord,
    IncomeRecord,
    PerformanceRecord,
    compose_analytics,
    fetch_analytics,
    normalize_analytics_payload,
)
from .models import ActivityDTO, Coverage, DTOError, Money
from .transport import ReadOnlyTransport, TransportResponse

__all__ = [
    "ActivityComposition",
    "ActivityDTO",
    "ActivitySourceResult",
    "AnalyticsComposition",
    "AnalyticsSourceResult",
    "Coverage",
    "DTOError",
    "EventRecord",
    "IncomeRecord",
    "Money",
    "PerformanceRecord",
    "ReadOnlyTransport",
    "TransportResponse",
    "compose_activities",
    "compose_analytics",
    "fetch_activities",
    "fetch_analytics",
    "normalize_activities_payload",
    "normalize_analytics_payload",
]
