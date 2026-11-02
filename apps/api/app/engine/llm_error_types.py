"""Safe, terminal provider failures. Never eligible for parse repair or failover."""
class LLMTerminalError(RuntimeError):
    code = 'terminal_failure'
    def __init__(self, message=None, *, transport_attempts=0, http_status=None,
                 error_type=None, provider_warning_count=0):
        super().__init__(message or self.code)
        self.transport_attempts = transport_attempts
        self.http_status = http_status
        self.error_type = error_type or self.code
        self.provider_warning_count = provider_warning_count

class LLMAuthenticationError(LLMTerminalError):
    code = 'authentication_failed'

class LLMRequestError(LLMTerminalError):
    code = 'request_rejected'

class LLMConfigurationError(LLMTerminalError):
    code = 'configuration_invalid'

class LLMCapabilityError(LLMConfigurationError):
    code = 'capability_mismatch'

class LLMPolicyBlockedError(LLMTerminalError):
    code = 'provider_policy_block'

# Backwards-compatible internal spelling; public integration uses BlockedError.
LLMPolicyError = LLMPolicyBlockedError

class LLMResponseEvidenceError(LLMTerminalError):
    code = 'native_evidence_invalid'

class LLMTaskDeadlineError(LLMTerminalError):
    code = 'task_deadline_exhausted'
