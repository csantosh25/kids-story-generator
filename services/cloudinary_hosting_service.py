"""Temporary public hosting for carousel JPEGs (Instagram's API only
accepts publicly reachable image URLs -- it fetches them itself).

Uses Cloudinary's signed REST Upload API directly through `requests`
(already a pinned dependency), so no new SDK is needed.

Retention policy (enforced by services/instagram_publisher.py):
  * public_ids are deterministic per story and slide
    (<folder>/<content_id>/<NN>), so a retry OVERWRITES the previous
    attempt's files instead of leaking new ones;
  * assets are deleted only once the story's post is confirmed
    PUBLISHED (Meta has necessarily fetched them by then), or once a
    failed story's containers are long expired
    (HOSTED_ASSET_FAILED_RETENTION_HOURS) -- never while a container
    that references them could still be published.
"""
import hashlib
import time

import requests


DEFAULT_FOLDER = "bedtime01fables/instagram-carousel"
DEFAULT_TIMEOUT = 120


class HostingError(RuntimeError):
    pass


class CloudinaryHostingService:

    def __init__(
        self,
        cloud_name,
        api_key,
        api_secret,
        folder=DEFAULT_FOLDER,
        session=None,
        timeout=DEFAULT_TIMEOUT,
        sleep=time.sleep,
    ):

        self.cloud_name = cloud_name
        self.api_key = api_key
        self._api_secret = api_secret
        self.folder = folder.strip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.sleep = sleep

    def _endpoint(self, action):
        return f"https://api.cloudinary.com/v1_1/{self.cloud_name}/image/{action}"

    def _sign(self, params):
        """Cloudinary signature: sha1 of the alphabetically sorted
        `key=value` pairs joined by '&', with the API secret appended."""

        payload = "&".join(f"{k}={params[k]}" for k in sorted(params))
        return hashlib.sha1((payload + self._api_secret).encode("utf-8")).hexdigest()

    def _signed(self, params):

        params = {k: v for k, v in params.items() if v is not None}
        params["timestamp"] = int(time.time())
        params["signature"] = self._sign(params)
        params["api_key"] = self.api_key
        return params

    def public_id_for(self, content_id, index):
        return f"{self.folder}/{content_id}/{index:02d}"

    def upload(self, path, public_id):

        params = self._signed({
            "public_id": public_id,
            "overwrite": "true",
            "invalidate": "true",
        })

        try:
            with open(path, "rb") as handle:
                response = self.session.post(
                    self._endpoint("upload"),
                    data=params,
                    files={"file": (f"{public_id.rsplit('/', 1)[-1]}.jpg", handle, "image/jpeg")},
                    timeout=self.timeout,
                )
        except requests.RequestException as error:
            raise HostingError(f"Cloudinary upload failed ({type(error).__name__})") from None

        if response.status_code != 200:
            raise HostingError(
                f"Cloudinary upload failed: HTTP {response.status_code} "
                f"{_error_message(response)}"
            )

        body = response.json()

        if body.get("format") not in ("jpg", "jpeg"):
            raise HostingError(
                f"Cloudinary stored {public_id} as {body.get('format')!r}, expected JPEG."
            )

        return {"public_id": body["public_id"], "url": body["secure_url"]}

    def delete(self, public_id):
        """Idempotent: 'not found' counts as deleted."""

        params = self._signed({"public_id": public_id, "invalidate": "true"})

        try:
            response = self.session.post(
                self._endpoint("destroy"), data=params, timeout=self.timeout
            )
        except requests.RequestException as error:
            raise HostingError(f"Cloudinary delete failed ({type(error).__name__})") from None

        if response.status_code != 200:
            raise HostingError(
                f"Cloudinary delete failed: HTTP {response.status_code} "
                f"{_error_message(response)}"
            )

        return response.json().get("result") in ("ok", "not found")

    def wait_until_accessible(self, url, attempts=6, delay=5):
        """Instagram fetches image_url itself; make sure it really
        serves a JPEG before creating a container that points at it."""

        last = "no response"

        for attempt in range(attempts):

            try:
                response = self.session.head(url, timeout=30, allow_redirects=True)
                content_type = response.headers.get("Content-Type", "")

                if response.status_code == 200 and content_type.startswith("image/jpeg"):
                    return True

                last = f"HTTP {response.status_code} {content_type}"
            except requests.RequestException as error:
                last = type(error).__name__

            if attempt < attempts - 1:
                self.sleep(delay)

        raise HostingError(f"Hosted image never became accessible: {url} ({last})")


def _error_message(response):

    try:
        return response.json().get("error", {}).get("message", "")
    except ValueError:
        return ""
