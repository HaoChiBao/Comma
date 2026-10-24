class ApiError(Exception):
    def __init__(self, status: int, error: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.error = error
        self.detail = detail


class IntegrationNotConfigured(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail
