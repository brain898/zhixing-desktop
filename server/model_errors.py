"""Safe, structured diagnostics for model requests. Never retain response bodies or credentials."""
import json
import socket
import ssl
import urllib.error


class ModelRequestError(RuntimeError):
    def __init__(self, code, message, *, http_status=None, retryable=False, exception_type=None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.retryable = retryable
        self.exception_type = exception_type

    def diagnostic(self):
        return {"error_code": self.code, "error_message": str(self), "http_status": self.http_status,
                "retryable": self.retryable, "cause_type": self.exception_type}


def classify_model_error(exc):
    if isinstance(exc, ModelRequestError):
        return exc
    kind = type(exc).__name__
    if isinstance(exc, urllib.error.HTTPError):
        status = exc.code
        # Inspect only for known error categories; never expose or persist a provider response.
        try:
            body = json.loads(exc.read(16384).decode("utf-8"))
            error = body.get("error", {}) if isinstance(body, dict) else {}
            text = str(error.get("code", "")) + " " + str(error.get("message", "")) if isinstance(error, dict) else ""
            text = text.lower()
        except (ValueError, UnicodeError, OSError):
            text = ""
        if status == 401:
            code, message, retryable = "authentication", "模型服务认证失败（HTTP 401），请检查服务端密钥配置后再重试", False
        elif status == 403:
            code, message, retryable = "permission", "模型服务拒绝访问（HTTP 403），请检查账号或模型权限后再重试", False
        elif status == 402 or (status == 429 and any(v in text for v in ("insufficient_quota", "insufficient balance", "quota_exceeded"))):
            code, message, retryable = "quota", f"模型服务额度不足（HTTP {status}），请检查账户额度后再重试", False
        elif status == 429:
            code, message, retryable = "rate_limit", "模型服务请求过于频繁（HTTP 429），请稍后重试", True
        elif status == 400 and any(v in text for v in ("context_length", "context length", "maximum context", "too many tokens")):
            code, message, retryable = "context_limit", "本次内容超过模型处理长度（HTTP 400），请检查所选 Skill 与引用内容", False
        elif status >= 500:
            code, message, retryable = "service_unavailable", f"模型服务暂时异常（HTTP {status}），请稍后重试", True
        else:
            code, message, retryable = "invalid_request", f"模型服务未接受请求（HTTP {status}），请检查服务端模型与请求配置", status in (408, 409)
        return ModelRequestError(code, message, http_status=status, retryable=retryable, exception_type=kind)
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return ModelRequestError("timeout", "等待模型回复超时，请稍后重试", retryable=True, exception_type=kind)
    if isinstance(reason, ssl.SSLError):
        return ModelRequestError("tls", "模型服务安全连接失败，请检查网络或证书配置", exception_type=kind)
    if isinstance(exc, (urllib.error.URLError, ConnectionError, OSError)):
        return ModelRequestError("network", "无法连接模型服务，请检查网络后重试", retryable=True, exception_type=kind)
    if isinstance(exc, (ValueError, KeyError, IndexError, TypeError)):
        return ModelRequestError("invalid_response", "模型服务返回格式异常，请稍后重试", retryable=True, exception_type=kind)
    return ModelRequestError("unexpected", "模型请求未完成，请稍后重试", retryable=True, exception_type=kind)
