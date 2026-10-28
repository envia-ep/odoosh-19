from abc import ABC, abstractmethod

from .dto import CreateShipmentRequest, CreateShipmentResponse, QuoteRequest, QuoteResponse


class EnviaAdapterBase(ABC):
    @abstractmethod
    def quote(self, request: QuoteRequest) -> QuoteResponse:
        pass

    @abstractmethod
    def create_shipment(self, request: CreateShipmentRequest) -> CreateShipmentResponse:
        pass
