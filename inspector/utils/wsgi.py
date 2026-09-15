"""WSGI middleware wrapped around the app behind the Space's proxy."""

from __future__ import annotations

from collections.abc import Callable, Iterable


class ForceHttpsScheme:
    """Pin the request scheme to https behind the Space's TLS-terminating proxy.

    ``ProxyFix`` is configured to trust one hop, which is correct for the
    direct ``*.hf.space`` host. A custom domain puts another hop in front, so
    the nearest ``X-Forwarded-Proto`` is the plaintext leg *inside* HF's
    network and werkzeug settles on ``http``. Everything derived from the
    scheme then comes out wrong:

    - the OAuth ``redirect_uri`` is announced as ``http://...``, which the
      provider rejects outright (HF answers the authorize call with a 400);
    - ``Secure`` is dropped from our cross-site cookies, and a
      ``SameSite=None`` cookie without ``Secure`` is discarded by the browser.

    Both deployed shapes are reachable over https only, so pinning the scheme
    states a fact about the edge rather than guessing at the hop count — which
    is why this is preferred over widening ``x_proto``, where the right number
    differs between the two hostnames.

    Only mounted when ``INSPECTOR_BEHIND_PROXY=1``; local http development is
    untouched.
    """

    def __init__(self, app: Callable) -> None:
        self.app = app

    def __call__(self, environ: dict, start_response: Callable) -> Iterable[bytes]:
        environ["wsgi.url_scheme"] = "https"
        return self.app(environ, start_response)
