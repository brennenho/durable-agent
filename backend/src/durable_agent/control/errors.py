from durable_agent.contracts import ExecutionError


class ControlError(Exception):
    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.error = ExecutionError(code=code, message=message, retryable=retryable)
