class ProcessingError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status

    def as_dict(self):
        return {"code": self.code, "message": self.message}
