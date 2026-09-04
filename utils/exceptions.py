"""Application-specific exceptions."""


class OzonParserError(Exception):
    """Base class for expected application errors."""


class ConfigurationError(OzonParserError):
    """Required configuration is missing or invalid."""


class OzonLoginError(OzonParserError):
    """Browser authorization did not complete successfully."""


class OzonCaptchaError(OzonLoginError):
    """Ozon anti-bot protection requires or failed manual completion."""


class GmailError(OzonParserError):
    """Base class for Gmail integration errors."""


class GmailAuthenticationError(GmailError):
    """Gmail OAuth credentials could not be loaded or refreshed."""


class GmailApiError(GmailError):
    """A Gmail API request failed."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class GmailMessageNotFoundError(GmailError):
    """No fresh Ozon verification message arrived before the timeout."""


class GmailCodeNotFoundError(GmailError):
    """An Ozon message was found but did not contain a verification code."""


class CookiesNotFoundError(OzonParserError):
    """The cookies file does not exist or cannot be read."""


class CookiesInvalidError(OzonParserError):
    """The cookies file has an invalid or unsafe structure."""


class CookiesExpiredError(OzonParserError):
    """Stored cookies no longer grant access to a product page."""


class ProductPageError(OzonParserError):
    """A product page could not be fetched or was unexpected."""


class ProductNotFoundError(ProductPageError):
    """The requested Ozon SKU does not exist."""


class OzonAntiBotError(ProductPageError):
    """Ozon returned an anti-bot or rate-limit page."""


class ProductParseError(OzonParserError):
    """A product page could not be converted to the domain model."""


class StorageError(OzonParserError):
    """A parsed product could not be persisted."""


def recovery_hint(error: OzonParserError) -> str | None:
    """Return a concise operator hint for an expected application failure."""
    if isinstance(error, ConfigurationError):
        return "Check .env against .env.example, then run `python3 main.py doctor`."
    if isinstance(error, (CookiesNotFoundError, CookiesExpiredError)):
        return (
            "Refresh Ozon cookies with `python3 main.py auth`, or capture an "
            "authorized local Chrome session with `python3 main.py auth "
            "--cdp-url http://127.0.0.1:9223 --capture-only`."
        )
    if isinstance(error, CookiesInvalidError):
        return (
            "Delete the invalid cookies file and refresh it with "
            "`python3 main.py auth`."
        )
    if isinstance(error, (OzonCaptchaError, OzonAntiBotError)):
        return (
            "Complete the Ozon browser check manually, then rerun the parser."
        )
    if isinstance(error, OzonLoginError):
        return (
            "Run `python3 main.py auth` again and keep the browser open until "
            "authorization finishes."
        )
    if isinstance(error, GmailAuthenticationError):
        return (
            "Check credentials.json and refresh Gmail OAuth with "
            "`python3 main.py gmail --auth-only`."
        )
    if isinstance(error, (GmailMessageNotFoundError, GmailCodeNotFoundError)):
        return (
            "Request a fresh Ozon code and retry with "
            "`python3 main.py gmail --lookback-seconds 30 --timeout 120`."
        )
    if isinstance(error, StorageError):
        return (
            "Run `python3 main.py doctor`; for a CSV-only check use "
            "`python3 main.py parse --csv-only`."
        )
    if isinstance(error, ProductPageError):
        return (
            "Check that cookies are fresh with `python3 main.py doctor "
            "--skip-database`, then retry the SKU."
        )
    return None
