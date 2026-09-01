"""Application-specific exceptions."""


class OzonParserError(Exception):
    """Base class for expected application errors."""


class ConfigurationError(OzonParserError):
    """Required configuration is missing or invalid."""


class OzonLoginError(OzonParserError):
    """Browser authorization did not complete successfully."""


class GmailError(OzonParserError):
    """Base class for Gmail integration errors."""


class GmailMessageNotFoundError(GmailError):
    """No fresh Ozon verification message arrived before the timeout."""


class GmailCodeNotFoundError(GmailError):
    """An Ozon message was found but did not contain a verification code."""


class CookiesNotFoundError(OzonParserError):
    """The cookies file does not exist or cannot be read."""


class CookiesExpiredError(OzonParserError):
    """Stored cookies no longer grant access to a product page."""


class ProductPageError(OzonParserError):
    """A product page could not be fetched or was unexpected."""


class ProductParseError(OzonParserError):
    """A product page could not be converted to the domain model."""


class StorageError(OzonParserError):
    """A parsed product could not be persisted."""

