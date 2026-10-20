"""Thin client for Meta's official Instagram Content Publishing API
("Instagram API with Instagram Login" -- host graph.instagram.com,
permissions instagram_business_basic + instagram_business_content_publish,
Instagram User access token). No unofficial libraries, no browser
automation.

The API version is configuration (INSTAGRAM_GRAPH_API_VERSION), not a
constant baked into the code, so it can be moved forward as Meta
deprecates versions without a code change. DEFAULT_API_VERSION is only
the fallback -- the latest version listed in Meta's docs when this was
written.

SECRET SAFETY: the access token is sent as a request parameter (Meta's
documented form), so every error raised from here is built from the
HTTP method, the URL PATH (never the query string) and Meta's own error
message/code -- and anything that still contains the token is redacted.
"""
import requests


DEFAULT_API_VERSION = "v26.0"
DEFAULT_HOST = "https://graph.instagram.com"
DEFAULT_TIMEOUT = 60


class InstagramApiError(RuntimeError):
    """Meta definitively answered with an error -- the request was
    received and rejected, so nothing was created/published by it."""

    def __init__(self, message, status=None, code=None, subcode=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.subcode = subcode


class InstagramUncertainError(RuntimeError):
    """No definitive answer (timeout, connection drop, 5xx). For
    media_publish this means the post MAY exist -- callers must check
    container status before ever retrying."""


class InstagramGraphClient:

    def __init__(
        self,
        access_token,
        ig_user_id,
        api_version=None,
        host=DEFAULT_HOST,
        session=None,
        timeout=DEFAULT_TIMEOUT,
    ):

        self._token = access_token
        self.ig_user_id = str(ig_user_id)
        self.api_version = (api_version or DEFAULT_API_VERSION).strip()
        self.host = host.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout

    # -----------------------------------------------------------------

    def _redact(self, text):

        text = str(text)

        if self._token:
            text = text.replace(self._token, "***")

        return text

    def _request(self, method, path, params=None):

        url = f"{self.host}/{self.api_version}/{path.lstrip('/')}"
        params = dict(params or {})
        params["access_token"] = self._token

        where = f"{method} /{self.api_version}/{path.lstrip('/')}"

        try:
            if method == "GET":
                response = self.session.get(url, params=params, timeout=self.timeout)
            else:
                response = self.session.post(url, data=params, timeout=self.timeout)
        except (requests.Timeout, requests.ConnectionError) as error:
            raise InstagramUncertainError(
                f"{where}: no response ({type(error).__name__})"
            ) from None
        except requests.RequestException as error:
            raise InstagramUncertainError(
                f"{where}: request failed ({type(error).__name__}: {self._redact(error)})"
            ) from None

        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.status_code >= 500:
            raise InstagramUncertainError(
                f"{where}: HTTP {response.status_code}"
            )

        if response.status_code >= 400 or "error" in body:
            error = body.get("error", {}) if isinstance(body, dict) else {}
            raise InstagramApiError(
                self._redact(
                    f"{where}: HTTP {response.status_code} "
                    f"code={error.get('code')} subcode={error.get('error_subcode')} "
                    f"{error.get('message', '')}".strip()
                ),
                status=response.status_code,
                code=error.get("code"),
                subcode=error.get("error_subcode"),
            )

        return body

    # -----------------------------------------------------------------
    # Account
    # -----------------------------------------------------------------

    def get_account(self):
        """GET /me -- user_id is the professional account ID used by the
        publishing endpoints; account_type is BUSINESS or MEDIA_CREATOR
        for accounts that can publish."""

        return self._request(
            "GET", "me", {"fields": "user_id,username,account_type"}
        )

    def get_publishing_limit(self):

        body = self._request(
            "GET",
            f"{self.ig_user_id}/content_publishing_limit",
            {"fields": "quota_usage,config"},
        )

        data = body.get("data") or [{}]
        return data[0]

    # -----------------------------------------------------------------
    # Containers
    # -----------------------------------------------------------------

    def create_carousel_item(self, image_url):

        body = self._request(
            "POST",
            f"{self.ig_user_id}/media",
            {"image_url": image_url, "is_carousel_item": "true"},
        )

        return body["id"]

    def create_carousel(self, children, caption):

        body = self._request(
            "POST",
            f"{self.ig_user_id}/media",
            {
                "media_type": "CAROUSEL",
                "children": ",".join(children),
                "caption": caption,
            },
        )

        return body["id"]

    def get_container_status(self, container_id):
        """EXPIRED | ERROR | FINISHED | IN_PROGRESS | PUBLISHED"""

        body = self._request(
            "GET", str(container_id), {"fields": "status_code,status"}
        )

        return body.get("status_code"), body.get("status")

    # -----------------------------------------------------------------
    # Publish
    # -----------------------------------------------------------------

    def publish(self, creation_id):

        body = self._request(
            "POST",
            f"{self.ig_user_id}/media_publish",
            {"creation_id": creation_id},
        )

        return body["id"]

    def list_recent_media(self, limit=25):

        body = self._request(
            "GET",
            f"{self.ig_user_id}/media",
            {"fields": "id,caption,media_type,permalink,timestamp", "limit": limit},
        )

        return body.get("data", [])

    def get_media(self, media_id):

        return self._request(
            "GET", str(media_id), {"fields": "id,media_type,permalink,timestamp"}
        )
